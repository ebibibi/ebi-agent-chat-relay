"""The ``work_items`` table — what a human adds on top of a thread.

A row either overlays a thread (``thread_id`` set) or stands on its own as a
*capture*: work the human has written down but not yet handed to an agent.
Nothing here duplicates state ccdb already tracks elsewhere. Whose move it is
comes from the thread's outcome marker, whether a turn is running from the
session registry, and who spawned whom from ``thread_lineage``; this table
holds only what no other part of ccdb knows.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

import aiosqlite

#: Priorities run from 0 (drop everything) to 3 (whenever). New work is 2.
MIN_PRIORITY = 0
MAX_PRIORITY = 3
DEFAULT_PRIORITY = 2

#: A human's own verdict on an item, overriding what the thread says.
#: ``done`` closes it, ``someday`` parks it, ``open`` clears the verdict.
STATE_OPEN = "open"
STATE_DONE = "done"
STATE_SOMEDAY = "someday"
STATES = (STATE_OPEN, STATE_DONE, STATE_SOMEDAY)

MAX_TITLE_CHARS = 200
MAX_NOTE_CHARS = 4000
MAX_PROJECT_CHARS = 300

_SCHEMA = """
CREATE TABLE IF NOT EXISTS work_items (
    id TEXT PRIMARY KEY,
    thread_id INTEGER UNIQUE,
    title TEXT,
    note TEXT,
    parent_id TEXT,
    priority INTEGER NOT NULL DEFAULT 2,
    due_at TEXT,
    snoozed_until TEXT,
    project TEXT,
    state TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_work_items_parent ON work_items(parent_id);
"""

#: Fields a client may change. Everything else is owned by the repository.
EDITABLE_FIELDS = (
    "title",
    "note",
    "parent_id",
    "priority",
    "due_at",
    "snoozed_until",
    "project",
    "state",
)


def thread_item_id(thread_id: int) -> str:
    """The stable id of the item that overlays *thread_id*."""
    return f"t{int(thread_id)}"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class WorkItem:
    """One row of ``work_items``."""

    id: str
    thread_id: int | None
    title: str | None
    note: str | None
    parent_id: str | None
    priority: int
    due_at: str | None
    snoozed_until: str | None
    project: str | None
    state: str
    created_at: str
    updated_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "thread_id": str(self.thread_id) if self.thread_id is not None else None,
            "title": self.title,
            "note": self.note,
            "parent_id": self.parent_id,
            "priority": self.priority,
            "due_at": self.due_at,
            "snoozed_until": self.snoozed_until,
            "project": self.project,
            "state": self.state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class WorkItemError(ValueError):
    """A client sent a change the repository refuses."""


def _row_to_item(row: aiosqlite.Row) -> WorkItem:
    return WorkItem(
        id=row["id"],
        thread_id=row["thread_id"],
        title=row["title"],
        note=row["note"],
        parent_id=row["parent_id"],
        priority=row["priority"],
        due_at=row["due_at"],
        snoozed_until=row["snoozed_until"],
        project=row["project"],
        state=row["state"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _optional_text(value: Any, field: str, limit: int) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise WorkItemError(f"{field} must be a string or null")
    text = value.strip()
    if len(text) > limit:
        raise WorkItemError(f"{field} must be at most {limit} characters")
    return text or None


def _optional_timestamp(value: Any, field: str) -> str | None:
    """Accept an ISO 8601 date or datetime; store it normalised to UTC."""
    text = _optional_text(value, field, 64)
    if text is None:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WorkItemError(f"{field} must be an ISO 8601 date or datetime") from exc
    if parsed.tzinfo is None:
        # A bare date or naive time is the human's local wall clock; the
        # client sends offsets, so a naive value is treated as UTC.
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def validate_changes(changes: dict[str, Any]) -> dict[str, Any]:
    """Check a client's changes and return them normalised.

    Unknown fields are an error rather than ignored: a typo'd field name that
    silently does nothing is exactly the bug a triage tool must not have.
    """
    unknown = set(changes) - set(EDITABLE_FIELDS)
    if unknown:
        raise WorkItemError(f"unknown fields: {', '.join(sorted(unknown))}")
    clean: dict[str, Any] = {}
    for field, value in changes.items():
        if field == "priority":
            if isinstance(value, bool) or not isinstance(value, int):
                raise WorkItemError("priority must be an integer")
            if not MIN_PRIORITY <= value <= MAX_PRIORITY:
                raise WorkItemError(f"priority must be {MIN_PRIORITY}..{MAX_PRIORITY}")
            clean[field] = value
        elif field == "state":
            if value not in STATES:
                raise WorkItemError(f"state must be one of {', '.join(STATES)}")
            clean[field] = value
        elif field in ("due_at", "snoozed_until"):
            clean[field] = _optional_timestamp(value, field)
        elif field == "title":
            clean[field] = _optional_text(value, field, MAX_TITLE_CHARS)
        elif field == "note":
            clean[field] = _optional_text(value, field, MAX_NOTE_CHARS)
        elif field == "project":
            clean[field] = _optional_text(value, field, MAX_PROJECT_CHARS)
        elif field == "parent_id":
            clean[field] = _optional_text(value, field, 64)
    return clean


class WorkItemRepository:
    """CRUD for ``work_items``. One short-lived connection per call."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def init_db(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()

    async def list_all(self) -> list[WorkItem]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall("SELECT * FROM work_items")
        return [_row_to_item(r) for r in rows]

    async def get(self, item_id: str) -> WorkItem | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = list(
                await db.execute_fetchall("SELECT * FROM work_items WHERE id = ?", (item_id,))
            )
        return _row_to_item(rows[0]) if rows else None

    async def create_capture(self, changes: dict[str, Any]) -> WorkItem:
        """Write down work that has no thread yet."""
        clean = validate_changes(changes)
        if not clean.get("title"):
            raise WorkItemError("title is required")
        now = _now()
        item = WorkItem(
            id=f"c{uuid.uuid4().hex[:12]}",
            thread_id=None,
            title=clean["title"],
            note=clean.get("note"),
            parent_id=clean.get("parent_id"),
            priority=clean.get("priority", DEFAULT_PRIORITY),
            due_at=clean.get("due_at"),
            snoozed_until=clean.get("snoozed_until"),
            project=clean.get("project"),
            state=clean.get("state", STATE_OPEN),
            created_at=now,
            updated_at=now,
        )
        await self._check_parent(item.id, item.parent_id)
        await self._write(item)
        return item

    async def update(
        self,
        item_id: str,
        changes: dict[str, Any],
        *,
        implicit_parents: dict[str, str] | None = None,
    ) -> WorkItem:
        """Apply *changes* to an item, creating a thread's overlay on first touch.

        *implicit_parents* are parents the item has without an overlay saying
        so (spawn lineage). They take part in the cycle check: a loop through a
        lineage link is still a loop.
        """
        clean = validate_changes(changes)
        current = await self.get(item_id)
        if current is None:
            current = self._blank_thread_item(item_id)
        if "parent_id" in clean:
            await self._check_parent(item_id, clean["parent_id"], implicit_parents)
        updated = replace(current, **clean, updated_at=_now())
        await self._write(updated)
        return updated

    async def attach_thread(self, item_id: str, thread_id: int) -> WorkItem:
        """A capture was started as a thread: carry its fields over to the thread.

        The capture row is replaced by the thread's overlay so the item keeps
        its priority, due date and parent under the id every later read uses.
        """
        capture = await self.get(item_id)
        if capture is None:
            raise WorkItemError("unknown item")
        overlay = replace(
            capture,
            id=thread_item_id(thread_id),
            thread_id=thread_id,
            state=STATE_OPEN,
            updated_at=_now(),
        )
        # One transaction: a crash halfway must not lose the capture.
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("DELETE FROM work_items WHERE id = ?", (item_id,))
            await db.execute(
                "UPDATE work_items SET parent_id = ? WHERE parent_id = ?", (overlay.id, item_id)
            )
            await self._upsert(db, overlay)
            await db.commit()
        return overlay

    def _blank_thread_item(self, item_id: str) -> WorkItem:
        if not item_id.startswith("t") or not item_id[1:].isdigit():
            raise WorkItemError("unknown item")
        now = _now()
        return WorkItem(
            id=item_id,
            thread_id=int(item_id[1:]),
            title=None,
            note=None,
            parent_id=None,
            priority=DEFAULT_PRIORITY,
            due_at=None,
            snoozed_until=None,
            project=None,
            state=STATE_OPEN,
            created_at=now,
            updated_at=now,
        )

    async def _check_parent(
        self,
        item_id: str,
        parent_id: str | None,
        implicit_parents: dict[str, str] | None = None,
    ) -> None:
        """Refuse a parent that would make the tree a loop."""
        if parent_id is None:
            return
        if parent_id == item_id:
            raise WorkItemError("an item cannot be its own parent")
        items = {i.id: i for i in await self.list_all()}
        seen = {item_id}
        cursor: str | None = parent_id
        while cursor is not None:
            if cursor in seen:
                raise WorkItemError("that parent would create a cycle")
            seen.add(cursor)
            parent = items.get(cursor)
            explicit = parent.parent_id if parent else None
            cursor = explicit or (implicit_parents or {}).get(cursor)

    async def _write(self, item: WorkItem) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await self._upsert(db, item)
            await db.commit()

    @staticmethod
    async def _upsert(db: aiosqlite.Connection, item: WorkItem) -> None:
        await db.execute(
            "INSERT INTO work_items (id, thread_id, title, note, parent_id, priority, due_at,"
            " snoozed_until, project, state, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET title = excluded.title, note = excluded.note,"
            " parent_id = excluded.parent_id, priority = excluded.priority,"
            " due_at = excluded.due_at, snoozed_until = excluded.snoozed_until,"
            " project = excluded.project, state = excluded.state,"
            " updated_at = excluded.updated_at",
            (
                item.id,
                item.thread_id,
                item.title,
                item.note,
                item.parent_id,
                item.priority,
                item.due_at,
                item.snoozed_until,
                item.project,
                item.state,
                item.created_at,
                item.updated_at,
            ),
        )
