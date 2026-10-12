"""Places: the locations and devices work can be bound to ("home", "office", "work PC").

A place is a label on an item (``work_items.place``, or the triage model's
guess in ``ai_triage.place``). This table is the list a person manages: the
labels offered when tagging an item, shown as "I am at" choices, and handed
to the model so it reuses them. Renaming a place renames it on every item;
deleting one removes it from every item, which then counts as doable anywhere.
"""

from __future__ import annotations

from datetime import UTC, datetime

import aiosqlite

from .work_repo import MAX_PLACE_CHARS

_SCHEMA = """
CREATE TABLE IF NOT EXISTS console_places (
    name TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);
"""


class PlaceError(ValueError):
    """A change to the list of places that is refused."""


def clean_name(value: object) -> str:
    """A place name, trimmed and checked; raises PlaceError."""
    if not isinstance(value, str):
        raise PlaceError("name must be a string")
    name = " ".join(value.split())
    if not name:
        raise PlaceError("name is required")
    if len(name) > MAX_PLACE_CHARS:
        raise PlaceError(f"name must be at most {MAX_PLACE_CHARS} characters")
    return name


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _has_table(db: aiosqlite.Connection, table: str) -> bool:
    rows = await db.execute_fetchall(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
    )
    return bool(list(rows))


class PlaceRepository:
    """The managed list of places, and renames/deletes carried to every item."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def init_db(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()

    async def names(self) -> list[str]:
        async with aiosqlite.connect(self._db_path) as db:
            rows = await db.execute_fetchall("SELECT name FROM console_places ORDER BY name")
        return [r[0] for r in rows]

    async def add(self, value: object) -> str:
        """Register a place; adding one that exists is not an error."""
        name = clean_name(value)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO console_places (name, created_at) VALUES (?, ?)",
                (name, _now()),
            )
            await db.commit()
        return name

    async def rename(self, old: str, value: object) -> str:
        """Rename *old* everywhere: the list, items a person tagged, and the model's guesses."""
        new = clean_name(value)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("DELETE FROM console_places WHERE name = ?", (old,))
            await db.execute(
                "INSERT OR IGNORE INTO console_places (name, created_at) VALUES (?, ?)",
                (new, _now()),
            )
            await self._relabel(db, old, new)
            await db.commit()
        return new

    async def delete(self, name: str) -> None:
        """Forget *name*: off the list and off every item (which is then doable anywhere)."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("DELETE FROM console_places WHERE name = ?", (name,))
            await self._relabel(db, name, None)
            await db.commit()

    @staticmethod
    async def _relabel(db: aiosqlite.Connection, old: str, new: str | None) -> None:
        if await _has_table(db, "work_items"):
            await db.execute("UPDATE work_items SET place = ? WHERE place = ?", (new, old))
        if await _has_table(db, "ai_triage"):
            await db.execute("UPDATE ai_triage SET place = ? WHERE place = ?", (new, old))
