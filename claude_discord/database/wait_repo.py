"""Wait repository — the persistent half of :mod:`claude_discord.waits`.

A wait outlives the turn that registered it by design, and often a bot restart
too (a deploy restarts the bot while the session waits on that very deploy).
So waits live in the session database, not in memory.

Times are Unix seconds (``REAL``): the watcher compares them with
``time.time()`` every tick, and SQLite date strings would only add parsing.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import aiosqlite

from ..waits import MAX_ACTIVE_WAITS, MAX_WAITS_PER_THREAD, ProbeResult, WaitSpec

logger = logging.getLogger(__name__)

STATUS_ACTIVE = "active"
STATUS_CANCELLED = "cancelled"


class WaitLimitError(Exception):
    """Too many active waits — for one thread or for the deployment."""


@dataclass(frozen=True)
class Wait:
    """One stored wait, active or finished."""

    id: int
    thread_id: int
    label: str
    argv: tuple[str, ...]
    pending_exit_codes: tuple[int, ...]
    done_pattern: str | None
    interval_seconds: int
    timeout_seconds: int
    note: str | None
    cwd: str | None
    status: str
    created_at: float
    deadline: float
    next_check_at: float
    consecutive_errors: int
    last_exit_code: int | None
    last_output: str | None
    last_error: str | None
    finished_at: float | None

    def to_spec(self) -> WaitSpec:
        return WaitSpec(
            thread_id=self.thread_id,
            argv=self.argv,
            pending_exit_codes=self.pending_exit_codes,
            done_pattern=self.done_pattern,
            interval_seconds=self.interval_seconds,
            timeout_seconds=self.timeout_seconds,
            label=self.label,
            note=self.note,
            cwd=self.cwd,
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "label": self.label,
            "argv": list(self.argv),
            "pending_exit_codes": list(self.pending_exit_codes),
            "done_pattern": self.done_pattern,
            "interval_seconds": self.interval_seconds,
            "timeout_seconds": self.timeout_seconds,
            "note": self.note,
            "cwd": self.cwd,
            "status": self.status,
            "created_at": self.created_at,
            "deadline": self.deadline,
            "next_check_at": self.next_check_at,
            "consecutive_errors": self.consecutive_errors,
            "last_exit_code": self.last_exit_code,
            "last_output": self.last_output,
            "last_error": self.last_error,
            "finished_at": self.finished_at,
        }


def _row_to_wait(row: aiosqlite.Row) -> Wait:
    return Wait(
        id=row["id"],
        thread_id=row["thread_id"],
        label=row["label"],
        argv=tuple(json.loads(row["argv"])),
        pending_exit_codes=tuple(json.loads(row["pending_exit_codes"])),
        done_pattern=row["done_pattern"],
        interval_seconds=row["interval_seconds"],
        timeout_seconds=row["timeout_seconds"],
        note=row["note"],
        cwd=row["cwd"],
        status=row["status"],
        created_at=row["created_at"],
        deadline=row["deadline"],
        next_check_at=row["next_check_at"],
        consecutive_errors=row["consecutive_errors"],
        last_exit_code=row["last_exit_code"],
        last_output=row["last_output"],
        last_error=row["last_error"],
        finished_at=row["finished_at"],
    )


class WaitRepository:
    """CRUD for the ``pending_waits`` table. Short-lived connections, like its siblings."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def _connect(self) -> aiosqlite.Connection:
        return aiosqlite.connect(self._db_path)

    async def create(self, spec: WaitSpec, *, now: float) -> Wait:
        """Store *spec* as an active wait, or raise :class:`WaitLimitError`."""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            # BEGIN IMMEDIATE takes the write lock before counting, so two
            # concurrent registrations cannot both squeeze under a limit.
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT COUNT(*), COALESCE(SUM(thread_id = ?), 0) "
                "FROM pending_waits WHERE status = ?",
                (spec.thread_id, STATUS_ACTIVE),
            ) as cursor:
                row = await cursor.fetchone()
            total, for_thread = (row[0], row[1]) if row else (0, 0)
            if for_thread >= MAX_WAITS_PER_THREAD:
                await db.rollback()
                raise WaitLimitError(
                    f"thread already has {MAX_WAITS_PER_THREAD} active waits; cancel one first"
                )
            if total >= MAX_ACTIVE_WAITS:
                await db.rollback()
                raise WaitLimitError(f"{MAX_ACTIVE_WAITS} waits are already active")
            cursor = await db.execute(
                "INSERT INTO pending_waits (thread_id, label, argv, pending_exit_codes, "
                "done_pattern, interval_seconds, timeout_seconds, note, cwd, status, "
                "created_at, deadline, next_check_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    spec.thread_id,
                    spec.label,
                    json.dumps(list(spec.argv)),
                    json.dumps(list(spec.pending_exit_codes)),
                    spec.done_pattern,
                    spec.interval_seconds,
                    spec.timeout_seconds,
                    spec.note,
                    spec.cwd,
                    STATUS_ACTIVE,
                    now,
                    now + spec.timeout_seconds,
                    now + spec.interval_seconds,
                ),
            )
            wait_id = cursor.lastrowid
            await db.commit()
        wait = await self.get(int(wait_id or 0))
        assert wait is not None
        return wait

    async def get(self, wait_id: int) -> Wait | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM pending_waits WHERE id = ?", (wait_id,)) as cur:
                row = await cur.fetchone()
        return _row_to_wait(row) if row else None

    async def list_active(self, thread_id: int | None = None) -> list[Wait]:
        query = "SELECT * FROM pending_waits WHERE status = ?"
        params: tuple = (STATUS_ACTIVE,)
        if thread_id is not None:
            query += " AND thread_id = ?"
            params = (STATUS_ACTIVE, thread_id)
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query + " ORDER BY id", params) as cur:
                rows = await cur.fetchall()
        return [_row_to_wait(r) for r in rows]

    async def due(self, *, now: float) -> list[Wait]:
        """Active waits to look at now: next check reached, or past the deadline."""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM pending_waits WHERE status = ? "
                "AND (next_check_at <= ? OR deadline <= ?) ORDER BY next_check_at",
                (STATUS_ACTIVE, now, now),
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_wait(r) for r in rows]

    async def active_thread_ids(self) -> set[int]:
        async with (
            self._connect() as db,
            db.execute(
                "SELECT DISTINCT thread_id FROM pending_waits WHERE status = ?",
                (STATUS_ACTIVE,),
            ) as cur,
        ):
            rows = await cur.fetchall()
        return {int(r[0]) for r in rows}

    async def record_probe(
        self,
        wait_id: int,
        result: ProbeResult,
        *,
        next_check_at: float,
        consecutive_errors: int,
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE pending_waits SET last_exit_code = ?, last_output = ?, "
                "last_error = ?, next_check_at = ?, consecutive_errors = ? "
                "WHERE id = ? AND status = ?",
                (
                    result.exit_code,
                    result.output,
                    result.error,
                    next_check_at,
                    consecutive_errors,
                    wait_id,
                    STATUS_ACTIVE,
                ),
            )
            await db.commit()

    async def finish(self, wait_id: int, status: str, *, now: float) -> bool:
        """Move an active wait to *status*. True only for the caller that did it."""
        async with self._connect() as db:
            cursor = await db.execute(
                "UPDATE pending_waits SET status = ?, finished_at = ? WHERE id = ? AND status = ?",
                (status, now, wait_id, STATUS_ACTIVE),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def cancel(self, wait_id: int, *, thread_id: int | None = None) -> bool:
        """Cancel an active wait, optionally only if *thread_id* owns it."""
        query = "UPDATE pending_waits SET status = ? WHERE id = ? AND status = ?"
        params: tuple = (STATUS_CANCELLED, wait_id, STATUS_ACTIVE)
        if thread_id is not None:
            query += " AND thread_id = ?"
            params = (*params, thread_id)
        async with self._connect() as db:
            cursor = await db.execute(query, params)
            await db.commit()
            return cursor.rowcount > 0
