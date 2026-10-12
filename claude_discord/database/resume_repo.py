"""Repository for pending session resumes after bot restart.

A Claude Code session that is about to restart the bot writes a row to
``pending_resumes`` via :meth:`PendingResumeRepository.mark`.  On startup
the bot calls :meth:`get_pending` to collect rows that are still within the
TTL window, processes them (re-spawning Claude in those threads), then calls
:meth:`delete` to prevent duplicate resumes.

Safety guarantees
-----------------
* **Single-fire**: rows are deleted immediately after being read, so a
  second restart within the TTL window does not cause a double-resume.
* **TTL**: rows older than *ttl_minutes* (default 5) are silently ignored
  and pruned, preventing stale resumes from triggering after a long downtime.
* **UNIQUE constraint** on ``thread_id``: only one pending resume per thread
  can exist at a time; writing a second one overwrites the first.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import aiosqlite

logger = logging.getLogger(__name__)

DEFAULT_TTL_MINUTES = 5


@dataclass
class PendingResume:
    """One row from the ``pending_resumes`` table."""

    id: int
    thread_id: int
    session_id: str | None
    reason: str
    resume_prompt: str | None
    created_at: str


class PendingResumeRepository:
    """Async CRUD for the ``pending_resumes`` table."""

    def __init__(self, db_path: str, ttl_minutes: int = DEFAULT_TTL_MINUTES) -> None:
        self._db_path = db_path
        self._ttl_minutes = ttl_minutes

    async def mark(
        self,
        thread_id: int,
        *,
        session_id: str | None = None,
        reason: str = "self_restart",
        resume_prompt: str | None = None,
    ) -> int:
        """Insert (or replace) a pending resume for *thread_id*.

        Returns the row id of the inserted/replaced row.
        The UNIQUE constraint on thread_id means only the latest mark survives.
        """
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                """
                INSERT OR REPLACE INTO pending_resumes
                    (thread_id, session_id, reason, resume_prompt)
                VALUES (?, ?, ?, ?)
                """,
                (thread_id, session_id, reason, resume_prompt),
            )
            await db.commit()
            row_id: int = cursor.lastrowid or 0
        safe_reason = reason.replace("\r", " ").replace("\n", " ")
        logger.info(
            "Marked thread %d for resume (reason=%s, row_id=%d)",
            thread_id,
            safe_reason,
            row_id,
        )
        return row_id

    async def get_pending(self) -> list[PendingResume]:
        """Return all pending resumes within the TTL window.

        Rows outside the TTL are pruned in the same call so they don't
        accumulate indefinitely.
        """
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row

            # Prune expired rows first
            await db.execute(
                """
                DELETE FROM pending_resumes
                WHERE created_at <= datetime('now', :ttl, 'localtime')
                """,
                {"ttl": f"-{self._ttl_minutes} minutes"},
            )
            await db.commit()

            cursor = await db.execute(
                """
                SELECT id, thread_id, session_id, reason, resume_prompt, created_at
                FROM pending_resumes
                ORDER BY created_at ASC, id ASC
                """
            )
            rows = await cursor.fetchall()

        return [
            PendingResume(
                id=row["id"],
                thread_id=row["thread_id"],
                session_id=row["session_id"],
                reason=row["reason"],
                resume_prompt=row["resume_prompt"],
                created_at=row["created_at"],
            )
            for row in rows
        ]

    async def delete(self, row_id: int) -> None:
        """Delete a pending resume by its row id (call after processing)."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("DELETE FROM pending_resumes WHERE id = ?", (row_id,))
            await db.commit()

    async def delete_by_thread(self, thread_id: int) -> None:
        """Delete a pending resume by thread id (alternative cleanup path)."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("DELETE FROM pending_resumes WHERE thread_id = ?", (thread_id,))
            await db.commit()
