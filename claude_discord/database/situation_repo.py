"""What a parent thread says about where its work stands.

The Console shows a panel beside a thread family's conversation. Most of it is measured
(which child is running, which one waits for the person), but only the parent knows what it
needs from the person once it has weighed its children's reports, and what has been settled.
That part is stored here, per thread, because one project can have several families at once
and a family can exist with no project at all.

A write replaces only the fields it names, so a parent can drop an answered question
without resending every decision.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import aiosqlite

SITUATION_FIELDS = ("asks", "doing", "decided")
MAX_ENTRIES = 40
MAX_ENTRY_CHARS = 600


class SituationError(ValueError):
    """A situation update that cannot be stored. The message is written for the agent."""


def parse_situation(body: Any) -> dict[str, list[str]]:
    """Validate a write: the fields it names, each a list of short non-empty strings.

    An unknown field is refused rather than ignored: ``decisions`` instead of ``decided``
    would otherwise answer 200 and leave the panel silently out of date.
    """
    if not isinstance(body, dict):
        raise SituationError("The body must be a JSON object")
    unknown = sorted(set(body) - set(SITUATION_FIELDS))
    if unknown:
        raise SituationError(f"Unknown field {unknown[0]!r}; use {', '.join(SITUATION_FIELDS)}")
    if not body:
        raise SituationError(f"Name at least one of {', '.join(SITUATION_FIELDS)}")
    return {name: _entries(name, value) for name, value in body.items()}


def _entries(name: str, value: Any) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(entry, str) for entry in value):
        raise SituationError(f"{name} must be an array of strings")
    entries = [entry.strip() for entry in value if entry.strip()]
    if len(entries) > MAX_ENTRIES:
        raise SituationError(f"{name} has more than {MAX_ENTRIES} entries; keep what still matters")
    if any(len(entry) > MAX_ENTRY_CHARS for entry in entries):
        raise SituationError(f"An entry of {name} is over {MAX_ENTRY_CHARS} characters; shorten it")
    return entries


@dataclass(frozen=True)
class Situation:
    """One thread's record; ``updated_at`` is ISO 8601 UTC."""

    thread_id: int
    asks: list[str]
    doing: list[str]
    decided: list[str]
    updated_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            # Thread ids exceed 2**53: as a JSON number a browser would round them.
            "thread_id": str(self.thread_id),
            "asks": self.asks,
            "doing": self.doing,
            "decided": self.decided,
            "updated_at": self.updated_at,
        }


class ThreadSituationRepository:
    """CRUD for the ``thread_situation`` table."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def get(self, thread_id: int) -> Situation | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = list(
                await db.execute_fetchall(
                    "SELECT * FROM thread_situation WHERE thread_id = ?", (int(thread_id),)
                )
            )
        if not rows:
            return None
        row = rows[0]
        return Situation(
            thread_id=row["thread_id"],
            asks=json.loads(row["asks"]),
            doing=json.loads(row["doing"]),
            decided=json.loads(row["decided"]),
            updated_at=row["updated_at"],
        )

    async def update(self, thread_id: int, fields: dict[str, list[str]]) -> Situation:
        """Replace the named fields in one statement, so two writers cannot lose a field."""
        values = [
            json.dumps(fields[name], ensure_ascii=False) if name in fields else None
            for name in SITUATION_FIELDS
        ]
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO thread_situation (thread_id, asks, doing, decided, updated_at)
                VALUES (?, COALESCE(?, '[]'), COALESCE(?, '[]'), COALESCE(?, '[]'), ?)
                ON CONFLICT(thread_id) DO UPDATE SET
                    asks = COALESCE(?, asks),
                    doing = COALESCE(?, doing),
                    decided = COALESCE(?, decided),
                    updated_at = excluded.updated_at
                """,
                (int(thread_id), *values, datetime.now(UTC).isoformat(), *values),
            )
            await db.commit()
        stored = await self.get(thread_id)
        if stored is None:  # pragma: no cover - the row was written just above
            raise RuntimeError(f"situation of {thread_id} was not stored")
        return stored
