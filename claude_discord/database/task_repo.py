"""TaskRepository — CRUD for scheduled_tasks table.

Stores periodic Claude Code tasks registered via REST API or chat.
The scheduler Cog polls this table every 30 seconds and runs due tasks.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import aiosqlite

logger = logging.getLogger(__name__)

# Sentinel for update(): tells "the caller did not mention this field" (leave the
# column alone) apart from "the caller passed None" (clear the pin, so the task
# goes back to following the thread/global setting). Plain None cannot do both
# jobs here the way it does for the other optional fields, because None is
# itself a value worth persisting.
_NOT_PROVIDED = object()

TASK_SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT    NOT NULL UNIQUE,
    prompt           TEXT    NOT NULL,
    interval_seconds INTEGER NOT NULL,
    channel_id       INTEGER NOT NULL,
    working_dir      TEXT,
    enabled          INTEGER NOT NULL DEFAULT 1,
    next_run_at      REAL    NOT NULL,
    last_run_at      REAL,
    created_at       REAL    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_next_run
    ON scheduled_tasks(next_run_at, enabled);
"""

# Migration: add anchor columns (nullable, backward compatible)
_MIGRATION_ANCHOR = """
ALTER TABLE scheduled_tasks ADD COLUMN anchor_hour INTEGER;
ALTER TABLE scheduled_tasks ADD COLUMN anchor_minute INTEGER DEFAULT 0;
"""

# Migration: add follow-up columns (thread_id for existing thread, one_shot for auto-disable)
_MIGRATION_FOLLOWUP = """
ALTER TABLE scheduled_tasks ADD COLUMN thread_id INTEGER;
ALTER TABLE scheduled_tasks ADD COLUMN one_shot INTEGER DEFAULT 0;
"""

# Migration: add the per-task backend/model pin. Both nullable — NULL means
# "follow whatever /backend and /model resolve to at run time", which is the
# behavior every existing row already has.
_MIGRATION_BACKEND = """
ALTER TABLE scheduled_tasks ADD COLUMN backend TEXT;
ALTER TABLE scheduled_tasks ADD COLUMN model TEXT;
"""


class TaskRepository:
    """Async CRUD for scheduled_tasks table."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init_db(self) -> None:
        """Initialize the task schema and run migrations."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(TASK_SCHEMA)
            # Migration: add anchor columns if they don't exist yet
            cursor = await db.execute("PRAGMA table_info(scheduled_tasks)")
            columns = {row[1] for row in await cursor.fetchall()}
            if "anchor_hour" not in columns:
                for stmt in _MIGRATION_ANCHOR.strip().split(";"):
                    stmt = stmt.strip()
                    if stmt:
                        await db.execute(stmt)
                logger.info("Migrated scheduled_tasks: added anchor_hour, anchor_minute")
            if "thread_id" not in columns:
                for stmt in _MIGRATION_FOLLOWUP.strip().split(";"):
                    stmt = stmt.strip()
                    if stmt:
                        await db.execute(stmt)
                logger.info("Migrated scheduled_tasks: added thread_id, one_shot")
            if "backend" not in columns:
                for stmt in _MIGRATION_BACKEND.strip().split(";"):
                    stmt = stmt.strip()
                    if stmt:
                        await db.execute(stmt)
                logger.info("Migrated scheduled_tasks: added backend, model")
            await db.commit()
        logger.info("Task DB initialized at %s", self.db_path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _next_anchor(anchor_hour: int, anchor_minute: int, interval_seconds: int) -> float:
        """Calculate the next future wall-clock occurrence of anchor_hour:anchor_minute.

        Advances by ``interval_seconds`` from the anchor time until the result
        is strictly in the future.  This prevents drift — the schedule always
        snaps to the anchor regardless of how long the previous run took.
        """
        local_tz = datetime.now(timezone.utc).astimezone().tzinfo  # noqa: UP017
        now = datetime.now(local_tz)
        candidate = now.replace(hour=anchor_hour, minute=anchor_minute, second=0, microsecond=0)
        interval = timedelta(seconds=interval_seconds)
        while candidate <= now:
            candidate += interval
        return candidate.timestamp()

    async def _db_execute(self, sql: str, params: tuple = ()) -> None:
        """Execute a DML statement (for tests and internal use)."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(sql, params)
            await db.commit()

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    async def get(self, task_id: int) -> dict | None:
        """Return a single task by ID, or None if not found."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM scheduled_tasks WHERE id = ?", (task_id,))
            row = await cursor.fetchone()
        if row is None:
            return None
        d = dict(row)
        d["enabled"] = bool(d["enabled"])
        d["one_shot"] = bool(d.get("one_shot", 0))
        return d

    async def get_all(self) -> list[dict]:
        """Return all tasks (enabled and disabled)."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute("SELECT * FROM scheduled_tasks ORDER BY created_at")
            rows = await cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["enabled"] = bool(d["enabled"])
            d["one_shot"] = bool(d.get("one_shot", 0))
            result.append(d)
        return result

    async def get_due(self, now: float | None = None) -> list[dict]:
        """Return enabled tasks whose next_run_at is in the past."""
        ts = now if now is not None else time.time()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM scheduled_tasks
                   WHERE enabled = 1 AND next_run_at <= ?
                   ORDER BY next_run_at""",
                (ts,),
            )
            rows = await cursor.fetchall()
        result = []
        for row in rows:
            d = dict(row)
            d["enabled"] = bool(d["enabled"])
            d["one_shot"] = bool(d.get("one_shot", 0))
            result.append(d)
        return result

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    async def create(
        self,
        name: str,
        prompt: str,
        interval_seconds: int,
        channel_id: int,
        *,
        working_dir: str | None = None,
        run_immediately: bool = True,
        anchor_hour: int | None = None,
        anchor_minute: int | None = None,
        thread_id: int | None = None,
        one_shot: bool = False,
        backend: str | None = None,
        model: str | None = None,
    ) -> int:
        """Create a new scheduled task. Returns the created ID.

        Args:
            run_immediately: If True (default), set next_run_at = now so the
                task fires on the next master-loop tick. If False, delay by
                interval_seconds (useful for tasks that should wait one full
                cycle before the first run).
            anchor_hour: Optional wall-clock hour (0-23) to snap to.
            anchor_minute: Optional wall-clock minute (0-59) to snap to.
                When anchor_hour is set, next_run_at is calculated as the
                next future occurrence of that time, preventing drift.
            thread_id: Optional Discord thread ID to post into. When set,
                the scheduler posts to this existing thread instead of
                creating a new one (follow-up mode).
            one_shot: If True, the task auto-disables after a single execution.
            backend: Optional backend name to pin this task to. None (default)
                keeps the existing behavior: the task runs on whichever backend
                is active for its thread/global setting when it fires.
            model: Optional model id to pin, meaningful only alongside
                ``backend`` — a model id belongs to one backend. The caller is
                responsible for that pairing; the repository only stores it.
        """
        now = time.time()
        if anchor_hour is not None and not run_immediately:
            next_run = self._next_anchor(anchor_hour, anchor_minute or 0, interval_seconds)
        elif run_immediately:
            next_run = now
        else:
            next_run = now + interval_seconds
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """INSERT INTO scheduled_tasks
                   (name, prompt, interval_seconds, channel_id, working_dir,
                    enabled, next_run_at, created_at, anchor_hour, anchor_minute,
                    thread_id, one_shot, backend, model)
                   VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    name,
                    prompt,
                    interval_seconds,
                    channel_id,
                    working_dir,
                    next_run,
                    now,
                    anchor_hour,
                    anchor_minute,
                    thread_id,
                    1 if one_shot else 0,
                    backend,
                    model,
                ),
            )
            await db.commit()
            row_id = cursor.lastrowid
        assert row_id is not None
        logger.info(
            "Scheduled task created: id=%d, name=%s, interval=%ds", row_id, name, interval_seconds
        )
        return row_id

    async def update_next_run(self, task_id: int, interval_seconds: int) -> None:
        """Advance next_run_at and record last_run_at.

        When the task has anchor_hour set, snaps to the next wall-clock
        occurrence instead of using ``now + interval_seconds``.
        """
        now = time.time()
        # Check if this task has an anchor
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT anchor_hour, anchor_minute FROM scheduled_tasks WHERE id = ?",
                (task_id,),
            )
            row = await cursor.fetchone()
            if row is not None and row["anchor_hour"] is not None:
                next_run = self._next_anchor(
                    row["anchor_hour"], row["anchor_minute"] or 0, interval_seconds
                )
            else:
                next_run = now + interval_seconds
            await db.execute(
                """UPDATE scheduled_tasks
                   SET next_run_at = ?, last_run_at = ?
                   WHERE id = ?""",
                (next_run, now, task_id),
            )
            await db.commit()

    async def delete_by_name(self, name: str) -> bool:
        """Delete a task by its unique name. Returns True if a row was deleted."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM scheduled_tasks WHERE name = ?", (name,))
            await db.commit()
            return cursor.rowcount > 0

    async def delete(self, task_id: int) -> bool:
        """Delete a task. Returns True if a row was deleted."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM scheduled_tasks WHERE id = ?", (task_id,))
            await db.commit()
            return cursor.rowcount > 0

    async def set_enabled(self, task_id: int, *, enabled: bool) -> bool:
        """Enable or disable a task. Returns True if updated."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "UPDATE scheduled_tasks SET enabled = ? WHERE id = ?",
                (1 if enabled else 0, task_id),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def update(
        self,
        task_id: int,
        *,
        prompt: str | None = None,
        interval_seconds: int | None = None,
        working_dir: str | None = None,
        anchor_hour: int | None = None,
        anchor_minute: int | None = None,
        thread_id: int | None = None,
        backend: str | None | object = _NOT_PROVIDED,
        model: str | None | object = _NOT_PROVIDED,
    ) -> bool:
        """Partially update a task. Returns True if updated.

        Set ``anchor_hour=-1`` to clear the anchor (reset to relative mode).
        Set ``thread_id=-1`` to clear the thread (reset to new-thread mode).
        Pass ``backend=None`` / ``model=None`` to clear those pins; omit them
        entirely to leave the stored values alone.
        """
        fields: list[str] = []
        values: list[object] = []
        if prompt is not None:
            fields.append("prompt = ?")
            values.append(prompt)
        if interval_seconds is not None:
            fields.append("interval_seconds = ?")
            values.append(interval_seconds)
        if working_dir is not None:
            fields.append("working_dir = ?")
            values.append(working_dir)
        if anchor_hour is not None:
            if anchor_hour < 0:
                # Sentinel: clear anchor
                fields.append("anchor_hour = ?")
                values.append(None)
                fields.append("anchor_minute = ?")
                values.append(None)
            else:
                fields.append("anchor_hour = ?")
                values.append(anchor_hour)
                fields.append("anchor_minute = ?")
                values.append(anchor_minute if anchor_minute is not None else 0)
        if thread_id is not None:
            if thread_id < 0:
                fields.append("thread_id = ?")
                values.append(None)
            else:
                fields.append("thread_id = ?")
                values.append(thread_id)
        if backend is not _NOT_PROVIDED:
            fields.append("backend = ?")
            values.append(backend)
        if model is not _NOT_PROVIDED:
            fields.append("model = ?")
            values.append(model)
        if not fields:
            return False
        values.append(task_id)
        sql = f"UPDATE scheduled_tasks SET {', '.join(fields)} WHERE id = ?"  # noqa: S608
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(sql, tuple(values))
            await db.commit()
            return cursor.rowcount > 0
