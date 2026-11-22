"""Turns that are running right now, so a crash does not lose them.

A clean shutdown (SIGTERM, ``bot.close()``) lets ``ClaudeChatCog.cog_unload``
write ``pending_resumes`` for every running turn.  A process killed with
SIGKILL, by the OOM killer or by a power loss runs no Python at all, so those
turns used to vanish: the thread simply stopped, with no message and no resume.

This table is written *before* the work instead of at shutdown:

* :meth:`InflightTurnRepository.begin` records a turn when it starts.
* :meth:`InflightTurnRepository.end` removes it when it ends, however it ends.
* Each process has its own ``boot_id``.  A row with another ``boot_id`` can only
  have been left by a process that died mid-turn, which is what
  :meth:`take_orphans` returns on startup.
* :meth:`heartbeat` refreshes ``heartbeat_at`` while the process lives, so
  startup can tell a crash a minute ago from one last night.

``crash_count`` counts consecutive crashes of the same thread's turn.  A
successful end deletes the row, so it resets; the caller carries the count
into the resumed turn's :meth:`begin` and stops resuming automatically past a
limit — a turn that kills the bot every time must not restart it forever.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import aiosqlite

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class InflightTurn:
    """One row from the ``inflight_turns`` table."""

    thread_id: int
    boot_id: str
    session_id: str | None
    prompt: str | None
    crash_count: int
    started_at: str
    heartbeat_at: str
    #: Seconds between the last heartbeat and the moment the row was read.
    silent_seconds: float


class InflightTurnRepository:
    """Async CRUD for the ``inflight_turns`` table (created by ``init_db``)."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def begin(
        self,
        thread_id: int,
        *,
        boot_id: str,
        session_id: str | None,
        prompt: str | None,
        crash_count: int = 0,
    ) -> None:
        """Record that a turn started in *thread_id* (replacing any older row)."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO inflight_turns
                    (thread_id, boot_id, session_id, prompt, crash_count)
                VALUES (?, ?, ?, ?, ?)
                """,
                (thread_id, boot_id, session_id, prompt, crash_count),
            )
            await db.commit()

    async def end(self, thread_id: int, *, boot_id: str) -> None:
        """Forget the turn in *thread_id*, if this process recorded it."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "DELETE FROM inflight_turns WHERE thread_id = ? AND boot_id = ?",
                (thread_id, boot_id),
            )
            await db.commit()

    async def heartbeat(self, boot_id: str) -> None:
        """Mark every turn of this process as still alive."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                UPDATE inflight_turns
                SET heartbeat_at = datetime('now', 'localtime')
                WHERE boot_id = ?
                """,
                (boot_id,),
            )
            await db.commit()

    async def take_orphans(self, boot_id: str) -> list[InflightTurn]:
        """Return and delete every row left by another process.

        Deleting in the same call keeps recovery single-fire: whatever the
        caller does next, a second startup never sees the same row again.
        """
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """
                SELECT thread_id, boot_id, session_id, prompt, crash_count,
                       started_at, heartbeat_at,
                       (julianday('now', 'localtime') - julianday(heartbeat_at))
                           * 86400.0 AS silent_seconds
                FROM inflight_turns
                WHERE boot_id != ?
                ORDER BY started_at ASC, thread_id ASC
                """,
                (boot_id,),
            )
            rows = await cursor.fetchall()
            await db.execute("DELETE FROM inflight_turns WHERE boot_id != ?", (boot_id,))
            await db.commit()
        return [
            InflightTurn(
                thread_id=row["thread_id"],
                boot_id=row["boot_id"],
                session_id=row["session_id"],
                prompt=row["prompt"],
                crash_count=row["crash_count"],
                started_at=row["started_at"],
                heartbeat_at=row["heartbeat_at"],
                silent_seconds=max(0.0, float(row["silent_seconds"] or 0.0)),
            )
            for row in rows
        ]
