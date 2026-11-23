"""Small named values the Console keeps across restarts (night settings, the night shift).

They live in the ``canvas_state`` table. The name is historical: the table was created for
canvas mode (ADR-0025, retired 2026-11-23), and the night settings and the night shift were
stored beside the secretary's own values. The table is kept, under its old name, so the
stored choices survive; the rows canvas mode wrote there are simply no longer read.
"""

from __future__ import annotations

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS canvas_state (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


class ConsoleStateRepository:
    """Get and set one text value by name. One connection per call."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def init_db(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()

    async def get_state(self, name: str) -> str | None:
        async with aiosqlite.connect(self._db_path) as db:
            rows = list(
                await db.execute_fetchall("SELECT value FROM canvas_state WHERE name = ?", (name,))
            )
        return rows[0][0] if rows else None

    async def set_state(self, name: str, value: str | None) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            if value is None:
                await db.execute("DELETE FROM canvas_state WHERE name = ?", (name,))
            else:
                await db.execute(
                    "INSERT INTO canvas_state (name, value) VALUES (?, ?)"
                    " ON CONFLICT(name) DO UPDATE SET value = excluded.value",
                    (name, value),
                )
            await db.commit()
