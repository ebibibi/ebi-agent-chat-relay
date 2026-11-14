"""Storage for canvas mode (ADR-0025): widgets, the secretary's notes, its state and its usage.

* ``canvas_widgets`` — what the secretary placed on the screen, by key.
* ``canvas_notes`` — the secretary's short note per thread. Kept here, not only in its
  conversation, so a restarted secretary still knows what it knew.
* ``canvas_changes`` — what the watcher saw change, in order. The digest reads it from the
  secretary's cursor, so a wake reads only what changed.
* ``canvas_state`` — small values: the secretary's conversation, its cursor, paused.
* ``canvas_turns`` — one row per watcher wake (``woken = 1``, written when it is sent, so the
  daily cap counts wakes in flight) and one per finished secretary turn (``woken = 0``, with the
  context it ended on), for the usage line and the daily cap.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite

from ..canvas_widget import WidgetSpec

_SCHEMA = """
CREATE TABLE IF NOT EXISTS canvas_widgets (
    widget_key TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    data TEXT NOT NULL,
    size TEXT NOT NULL,
    position REAL NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    actions TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS canvas_notes (
    thread_id INTEGER PRIMARY KEY,
    note TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS canvas_changes (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    item_id TEXT NOT NULL,
    what TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS canvas_state (
    name TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS canvas_turns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    day TEXT NOT NULL,
    woken INTEGER NOT NULL,
    context_used INTEGER
);
CREATE INDEX IF NOT EXISTS idx_canvas_turns_day ON canvas_turns(day);
"""

#: Changes older than this are dropped at start; a digest never needs them.
CHANGE_RETENTION = timedelta(days=7)
MAX_NOTE = 1500


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Widget:
    key: str
    kind: str
    title: str
    data: dict[str, Any]
    size: str
    position: float
    note: str
    actions: list[dict[str, str]]
    created_at: str
    updated_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "kind": self.kind,
            "title": self.title,
            "data": self.data,
            "size": self.size,
            "position": self.position,
            "note": self.note,
            "actions": self.actions,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def _widget(row: aiosqlite.Row) -> Widget:
    return Widget(
        key=row["widget_key"],
        kind=row["kind"],
        title=row["title"],
        data=json.loads(row["data"]),
        size=row["size"],
        position=row["position"],
        note=row["note"],
        actions=json.loads(row["actions"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class CanvasRepository:
    """CRUD for canvas mode. One connection per call."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def init_db(self) -> None:
        cutoff = (datetime.now(UTC) - CHANGE_RETENTION).strftime("%Y-%m-%dT%H:%M:%SZ")
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
            await db.execute("DELETE FROM canvas_changes WHERE at < ?", (cutoff,))
            await db.commit()

    # -- widgets -----------------------------------------------------------
    async def put_widget(self, key: str, spec: WidgetSpec) -> tuple[Widget, bool]:
        """Place or update *key*. Returns the widget and whether it is new."""
        now = _now()
        async with aiosqlite.connect(self._db_path) as db:
            rows = await db.execute_fetchall(
                "SELECT 1 FROM canvas_widgets WHERE widget_key = ?", (key,)
            )
            created = not list(rows)
            await db.execute(
                "INSERT INTO canvas_widgets (widget_key, kind, title, data, size, position, note,"
                " actions, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(widget_key) DO UPDATE SET kind = excluded.kind,"
                " title = excluded.title, data = excluded.data, size = excluded.size,"
                " position = excluded.position, note = excluded.note,"
                " actions = excluded.actions, updated_at = excluded.updated_at",
                (
                    key,
                    spec.kind,
                    spec.title,
                    json.dumps(spec.data, ensure_ascii=False),
                    spec.size,
                    spec.position,
                    spec.note,
                    json.dumps(list(spec.actions), ensure_ascii=False),
                    now,
                    now,
                ),
            )
            await db.commit()
        widget = await self.get_widget(key)
        assert widget is not None
        return widget, created

    async def get_widget(self, key: str) -> Widget | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = list(
                await db.execute_fetchall(
                    "SELECT * FROM canvas_widgets WHERE widget_key = ?", (key,)
                )
            )
        return _widget(rows[0]) if rows else None

    async def list_widgets(self) -> list[Widget]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                "SELECT * FROM canvas_widgets ORDER BY position, created_at"
            )
        return [_widget(r) for r in rows]

    async def count_widgets(self) -> int:
        async with aiosqlite.connect(self._db_path) as db:
            rows = list(await db.execute_fetchall("SELECT COUNT(*) FROM canvas_widgets"))
        return int(rows[0][0])

    async def delete_widget(self, key: str) -> bool:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute("DELETE FROM canvas_widgets WHERE widget_key = ?", (key,))
            await db.commit()
            return cursor.rowcount > 0

    async def reorder(self, keys: list[str]) -> int:
        """Give *keys* positions 10, 20, … in the order given. Returns how many moved."""
        moved = 0
        async with aiosqlite.connect(self._db_path) as db:
            for index, key in enumerate(keys):
                cursor = await db.execute(
                    "UPDATE canvas_widgets SET position = ? WHERE widget_key = ?",
                    ((index + 1) * 10, key),
                )
                moved += cursor.rowcount
            await db.commit()
        return moved

    # -- notes -------------------------------------------------------------
    async def put_note(self, thread_id: int, note: str) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            if note:
                await db.execute(
                    "INSERT INTO canvas_notes (thread_id, note, updated_at) VALUES (?, ?, ?)"
                    " ON CONFLICT(thread_id) DO UPDATE SET note = excluded.note,"
                    " updated_at = excluded.updated_at",
                    (int(thread_id), note, _now()),
                )
            else:
                await db.execute("DELETE FROM canvas_notes WHERE thread_id = ?", (int(thread_id),))
            await db.commit()

    async def notes(self) -> dict[int, dict[str, str]]:
        async with aiosqlite.connect(self._db_path) as db:
            rows = await db.execute_fetchall("SELECT thread_id, note, updated_at FROM canvas_notes")
        return {int(r[0]): {"note": r[1], "updated_at": r[2]} for r in rows}

    # -- changes -----------------------------------------------------------
    async def add_changes(self, changes: list[tuple[str, str]]) -> int:
        """Append ``(item_id, what)`` rows. Returns the last sequence number."""
        now = _now()
        async with aiosqlite.connect(self._db_path) as db:
            await db.executemany(
                "INSERT INTO canvas_changes (at, item_id, what) VALUES (?, ?, ?)",
                [(now, item_id, what) for item_id, what in changes],
            )
            await db.commit()
        return await self.last_change()

    async def last_change(self) -> int:
        async with aiosqlite.connect(self._db_path) as db:
            rows = list(await db.execute_fetchall("SELECT MAX(seq) FROM canvas_changes"))
        return int(rows[0][0] or 0)

    async def changes_since(self, seq: int) -> list[tuple[int, str, str, str]]:
        """``(seq, at, item_id, what)`` after *seq*, oldest first."""
        async with aiosqlite.connect(self._db_path) as db:
            rows = await db.execute_fetchall(
                "SELECT seq, at, item_id, what FROM canvas_changes WHERE seq > ? ORDER BY seq",
                (int(seq),),
            )
        return [(int(r[0]), r[1], r[2], r[3]) for r in rows]

    # -- state -------------------------------------------------------------
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

    # -- usage -------------------------------------------------------------
    async def record_turn(self, *, day: str, woken: bool, context_used: int | None) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO canvas_turns (at, day, woken, context_used) VALUES (?, ?, ?, ?)",
                (_now(), day, int(woken), context_used),
            )
            await db.commit()

    async def day_usage(self, day: str) -> dict[str, int]:
        """Turns, watcher wakes and the summed end-of-turn context for *day*."""
        async with aiosqlite.connect(self._db_path) as db:
            rows = list(
                await db.execute_fetchall(
                    "SELECT COUNT(*), COALESCE(SUM(woken), 0), COALESCE(SUM(context_used), 0)"
                    " FROM canvas_turns WHERE day = ?",
                    (day,),
                )
            )
        rows_total, woken, tokens = rows[0]
        return {
            "turns": int(rows_total) - int(woken),
            "wakes": int(woken),
            "input_tokens": int(tokens),
        }
