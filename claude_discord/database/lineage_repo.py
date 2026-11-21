"""Which thread spawned which — the record behind the family codes in titles.

The codes in the thread titles (see ``claude_discord.thread_marker``) make the
tree readable to a human scanning the channel list.  This table makes the same
tree *queryable*, so a session that manages a fan-out can ask "what did I start,
and is it still running?" instead of parsing titles — and so a lineage survives
a thread being renamed by hand.

Rows are written at spawn time, before any Claude session exists, because a
thread spawned with ``auto_start=false`` has no session row until a human
replies and would otherwise be an orphan in every listing.
"""

from __future__ import annotations

from dataclasses import dataclass

import aiosqlite


@dataclass(frozen=True)
class Lineage:
    """One spawned thread and the thread that spawned it."""

    thread_id: int
    parent_thread_id: int
    family_code: str
    created_at: str


class ThreadLineageRepository:
    """CRUD for the ``thread_lineage`` table."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def record(self, thread_id: int, parent_thread_id: int, family_code: str) -> None:
        """Remember that *parent_thread_id* spawned *thread_id*.

        A thread has exactly one parent, so a repeat write replaces rather than
        duplicates — re-spawning into an existing thread ID (a test, a retry)
        must not leave two conflicting parents behind.
        """
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO thread_lineage (thread_id, parent_thread_id, family_code)
                VALUES (?, ?, ?)
                ON CONFLICT(thread_id) DO UPDATE SET
                    parent_thread_id = excluded.parent_thread_id,
                    family_code = excluded.family_code
                """,
                (int(thread_id), int(parent_thread_id), family_code),
            )
            await db.commit()

    async def parent_of(self, thread_id: int) -> int | None:
        """Return the thread that spawned *thread_id*, or None for a root thread."""
        async with aiosqlite.connect(self._db_path) as db:
            rows = await db.execute_fetchall(
                "SELECT parent_thread_id FROM thread_lineage WHERE thread_id = ?",
                (int(thread_id),),
            )
        rows = list(rows)
        return int(rows[0][0]) if rows else None

    async def list_all(self, limit: int = 500) -> list[Lineage]:
        """Return the most recent links, newest first."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                "SELECT * FROM thread_lineage ORDER BY created_at DESC, thread_id DESC LIMIT ?",
                (max(1, int(limit)),),
            )
        return [
            Lineage(
                thread_id=row["thread_id"],
                parent_thread_id=row["parent_thread_id"],
                family_code=row["family_code"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    async def set_notify_parent(self, thread_id: int, enabled: bool) -> bool:
        """Turn child→parent reports on or off for one spawned thread.

        Returns False when the relay recorded no parent for *thread_id*: the
        opt-in only ever follows a link the relay wrote itself.
        """
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "UPDATE thread_lineage SET notify_parent = ? WHERE thread_id = ?",
                (1 if enabled else 0, int(thread_id)),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def notify_target(self, thread_id: int) -> NotifyTarget | None:
        """Return where a report about *thread_id* goes, or None when it is not opted in."""
        async with aiosqlite.connect(self._db_path) as db:
            rows = await db.execute_fetchall(
                "SELECT parent_thread_id, notified_outcome, notified_at, notified_count "
                "FROM thread_lineage WHERE thread_id = ? AND notify_parent = 1",
                (int(thread_id),),
            )
        rows = list(rows)
        if not rows:
            return None
        parent, outcome, at, count = rows[0]
        return NotifyTarget(
            parent_thread_id=int(parent),
            last_outcome=outcome,
            last_at=float(at) if at is not None else None,
            count=int(count or 0),
        )

    async def record_notified(self, thread_id: int, outcome: str, at: float) -> None:
        """Remember that the parent was told *thread_id* reached *outcome*."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE thread_lineage SET notified_outcome = ?, notified_at = ?, "
                "notified_count = notified_count + 1 WHERE thread_id = ?",
                (outcome, float(at), int(thread_id)),
            )
            await db.commit()


@dataclass(frozen=True)
class NotifyTarget:
    """An opted-in child's parent and what it was last told."""

    parent_thread_id: int
    last_outcome: str | None
    last_at: float | None
    count: int
