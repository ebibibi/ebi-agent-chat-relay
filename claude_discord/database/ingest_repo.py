"""IngestResultRepository — storage for /api/ingest session results.

When an external client (e.g. a Teams browser extension) posts to
``/api/ingest``, a real Discord thread is spawned and a Claude session runs
interactively. The session's final assistant reply is normally only visible
inside the Discord thread.

This repository captures that final reply keyed by a generated ``result_id``
so the original caller can poll ``GET /api/ingest/{result_id}`` and retrieve
the answer to write back to its own system (the Teams thread).

The request body (prompt, attachments) is NOT persisted — only the generated
answer, status, and the spawned ``thread_id`` for traceability. The Discord
thread remains the source of truth for the full interaction history.
"""

from __future__ import annotations

import logging

import aiosqlite

logger = logging.getLogger(__name__)

INGEST_RESULT_SCHEMA = """
CREATE TABLE IF NOT EXISTS ingest_results (
    result_id      TEXT PRIMARY KEY,
    status         TEXT NOT NULL DEFAULT 'running',
    result         TEXT,
    error          TEXT,
    thread_id      TEXT,
    thread_name    TEXT,
    summary_key    TEXT,
    pending_marker TEXT,
    teams_thread_key TEXT,
    teams_base_mid TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_ingest_results_created ON ingest_results(created_at);
"""

# Columns added after the table first shipped. init_db() ALTERs existing DBs so
# an upgrade needs no manual migration (Zero-Config principle).
_ADDED_COLUMNS = {
    "summary_key": "TEXT",
    "pending_marker": "TEXT",
    "teams_thread_key": "TEXT",
    "teams_base_mid": "TEXT",
}


class IngestResultRepository:
    """Async CRUD for the ingest_results table."""

    # Keep at most this many recent results to prevent unbounded growth.
    MAX_STORED_RESULTS = 200

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init_db(self) -> None:
        """Create the ingest_results schema if it does not exist."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(INGEST_RESULT_SCHEMA)
            # Backfill columns added after the initial release on existing DBs.
            cursor = await db.execute("PRAGMA table_info(ingest_results)")
            existing = {row[1] for row in await cursor.fetchall()}
            for name, coltype in _ADDED_COLUMNS.items():
                if name not in existing:
                    await db.execute(
                        f"ALTER TABLE ingest_results ADD COLUMN {name} {coltype}"  # noqa: S608
                    )
            await db.commit()
        logger.info("Ingest result DB initialized at %s", self.db_path)

    async def create(
        self,
        *,
        result_id: str,
        thread_id: str | None = None,
        thread_name: str | None = None,
        summary_key: str | None = None,
        pending_marker: str | None = None,
        teams_thread_key: str | None = None,
        teams_base_mid: str | None = None,
    ) -> dict:
        """Insert a new ingest result in the 'running' state and return it.

        ``summary_key`` / ``pending_marker`` link this run to a running thread
        summary (see ThreadSummaryRepository): when the session later saves an
        updated summary via ``POST /api/ingest/summary`` referencing this
        ``result_id``, ccdb advances the summary's marker to ``pending_marker``.

        ``teams_thread_key`` / ``teams_base_mid`` link this run to the Teams thread
        it answers and the newest message stored when it started (see
        ``ext/teams_reply.py``).
        """
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO ingest_results "
                "(result_id, status, thread_id, thread_name, summary_key, pending_marker, "
                "teams_thread_key, teams_base_mid) "
                "VALUES (?, 'running', ?, ?, ?, ?, ?, ?)",
                (
                    result_id,
                    thread_id,
                    thread_name,
                    summary_key,
                    pending_marker,
                    teams_thread_key,
                    teams_base_mid,
                ),
            )
            # Prune old rows, keeping only the most recent MAX_STORED_RESULTS.
            await db.execute(
                "DELETE FROM ingest_results WHERE result_id NOT IN "
                "(SELECT result_id FROM ingest_results ORDER BY created_at DESC, rowid DESC "
                "LIMIT ?)",
                (self.MAX_STORED_RESULTS,),
            )
            await db.commit()
        logger.info("Ingest result created: result_id=%s thread_id=%s", result_id, thread_id)
        rec = await self.get(result_id)
        if rec is None:
            raise RuntimeError(f"Failed to retrieve ingest result {result_id} after insert")
        return rec

    async def get(self, result_id: str) -> dict | None:
        """Return a single ingest result by id, or None if not found."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM ingest_results WHERE result_id = ?", (result_id,)
            )
            row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def latest_for_teams_thread(self, teams_thread_key: str) -> dict | None:
        """Return the newest run linked to a Teams thread, or None."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM ingest_results WHERE teams_thread_key = ? "
                "ORDER BY created_at DESC, rowid DESC LIMIT 1",
                (teams_thread_key,),
            )
            row = await cursor.fetchone()
        return dict(row) if row is not None else None

    async def set_thread(
        self, result_id: str, thread_id: str, thread_name: str | None = None
    ) -> None:
        """Attach the spawned Discord thread info to an existing result row."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE ingest_results SET thread_id = ?, thread_name = ?, "
                "updated_at = datetime('now', 'localtime') WHERE result_id = ?",
                (thread_id, thread_name, result_id),
            )
            await db.commit()

    async def set_result(self, result_id: str, result: str) -> None:
        """Mark a result done and store the session's final assistant reply."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE ingest_results SET status = 'done', result = ?, "
                "updated_at = datetime('now', 'localtime') WHERE result_id = ?",
                (result, result_id),
            )
            await db.commit()
        logger.info("Ingest result done: result_id=%s (%d chars)", result_id, len(result))

    async def set_error(self, result_id: str, error: str) -> None:
        """Mark a result failed and store the error message."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE ingest_results SET status = 'error', error = ?, "
                "updated_at = datetime('now', 'localtime') WHERE result_id = ?",
                (error, result_id),
            )
            await db.commit()
        logger.warning("Ingest result error: result_id=%s", result_id)

    async def count(self) -> int:
        """Return the number of stored ingest results."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM ingest_results")
            row = await cursor.fetchone()
        return int(row[0]) if row else 0
