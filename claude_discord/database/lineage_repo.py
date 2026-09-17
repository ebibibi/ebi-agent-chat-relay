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
