"""What a parent thread says about where its work stands.

The Console shows a panel beside a thread family's conversation. Most of it is measured
(which child is running, which one waits for the person), but only the parent knows what it
needs from the person once it has weighed its children's reports, and what has been settled.
That part is stored here, per thread, because one project can have several families at once
and a family can exist with no project at all.

A write replaces only the fields it names, so a parent can drop an answered question
without resending every decision.

``deliverables`` is what the thread produced that is not an attached file (a PR, a release,
a note), or a title and a "superseded" flag for a file it attached. Files themselves are
collected from the conversation (``console/deliverables.py``), never written here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import aiosqlite

SITUATION_FIELDS = ("asks", "doing", "decided", "deliverables")
TEXT_FIELDS = ("asks", "doing", "decided")
MAX_ENTRIES = 40
MAX_ENTRY_CHARS = 600
#: What a deliverable entry may say about itself; ``kind`` only picks the icon.
DELIVERABLE_KINDS = ("pr", "release", "note", "doc", "video", "image", "link", "file")
#: ``at`` is accepted so a record read back can be resent as is, but the server sets it.
DELIVERABLE_KEYS = frozenset({"title", "url", "file", "kind", "note", "superseded", "at"})
MAX_TITLE_CHARS = 200
MAX_NOTE_CHARS = 300
MAX_URL_CHARS = 2000
MAX_FILE_CHARS = 300


class SituationError(ValueError):
    """A situation update that cannot be stored. The message is written for the agent."""


def parse_situation(body: Any) -> dict[str, list[Any]]:
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
    return {
        name: _deliverables(value) if name == "deliverables" else _entries(name, value)
        for name, value in body.items()
    }


def _deliverables(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise SituationError("deliverables must be an array of objects")
    if len(value) > MAX_ENTRIES:
        raise SituationError(
            f"deliverables has more than {MAX_ENTRIES} entries; drop what nobody needs any more"
        )
    return [_deliverable(entry) for entry in value]


def _short(entry: dict[str, Any], key: str, limit: int) -> str:
    value = entry.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise SituationError(f"deliverables: {key} must be a string")
    value = value.strip()
    if len(value) > limit:
        raise SituationError(f"deliverables: {key} is over {limit} characters; shorten it")
    return value


def _safe_url(url: str) -> bool:
    """An address the panel may link to: the web, or a link inside the Console (``/#...``)."""
    if any(ch.isspace() or ord(ch) < 32 for ch in url):
        return False
    lowered = url.lower()
    return lowered.startswith(("https://", "http://")) or url.startswith("/#")


def _deliverable(entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise SituationError(
            'each deliverable is an object such as {"title": "PR #12", "url": "https://..."}'
        )
    unknown = sorted(set(entry) - DELIVERABLE_KEYS)
    if unknown:
        raise SituationError(
            f"deliverables: unknown key {unknown[0]!r}; use {', '.join(sorted(DELIVERABLE_KEYS))}"
        )
    title = _short(entry, "title", MAX_TITLE_CHARS)
    if not title:
        raise SituationError("deliverables: every entry needs a title")
    url = _short(entry, "url", MAX_URL_CHARS)
    file = _short(entry, "file", MAX_FILE_CHARS)
    if not url and not file:
        raise SituationError(
            f"deliverables: {title!r} needs a url, or file (the name of a file the thread attached)"
        )
    if url and not _safe_url(url):
        raise SituationError(
            f"deliverables: the url of {title!r} must start with https://, http:// or /# "
            "(a Console link such as /#note/...)"
        )
    kind = _short(entry, "kind", 20)
    if kind and kind not in DELIVERABLE_KINDS:
        raise SituationError(f"deliverables: kind must be one of {', '.join(DELIVERABLE_KINDS)}")
    superseded = entry.get("superseded", False)
    if not isinstance(superseded, bool):
        raise SituationError("deliverables: superseded must be true or false")
    clean: dict[str, Any] = {"title": title}
    for key, value in (("url", url), ("file", file), ("kind", kind)):
        if value:
            clean[key] = value
    note = _short(entry, "note", MAX_NOTE_CHARS)
    if note:
        clean["note"] = note
    if superseded:
        clean["superseded"] = True
    return clean


def _identity(entry: dict[str, Any]) -> str:
    """What makes two declarations the same deliverable: its address, else its file, else title."""
    if entry.get("url"):
        return str(entry["url"])
    if entry.get("file"):
        return f"file:{entry['file']}"
    return str(entry.get("title", ""))


def _entries(name: str, value: Any) -> list[str]:
    if name == "asks" and isinstance(value, list) and any(isinstance(e, dict) for e in value):
        raise SituationError(
            "asks takes plain strings only; ask the user with a decision request sent with the "
            "/waiting, /review or /action marker instead (the panel shows it as a card)"
        )
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
    deliverables: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            # Thread ids exceed 2**53: as a JSON number a browser would round them.
            "thread_id": str(self.thread_id),
            "asks": self.asks,
            "doing": self.doing,
            "decided": self.decided,
            "deliverables": self.deliverables,
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
            deliverables=json.loads(row["deliverables"] or "[]"),
        )

    async def get_many(self, thread_ids: list[int]) -> list[Situation]:
        """The records of several threads (a family), in no particular order."""
        found = []
        for thread_id in dict.fromkeys(int(t) for t in thread_ids):
            record = await self.get(thread_id)
            if record is not None:
                found.append(record)
        return found

    async def _stamped(self, thread_id: int, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Each entry with the time it was first declared, kept across rewrites of the list.

        The panel sorts by it: one shared record time would put a whole list above every file,
        in the order the agent happened to write it (#1548).
        """
        before = await self.get(thread_id)
        known = {_identity(e): e.get("at") for e in (before.deliverables if before else [])}
        now = datetime.now(UTC).isoformat()
        return [{**e, "at": known.get(_identity(e)) or now} for e in entries]

    async def update(self, thread_id: int, fields: dict[str, list[Any]]) -> Situation:
        if "deliverables" in fields:
            fields = {
                **fields,
                "deliverables": await self._stamped(thread_id, fields["deliverables"]),
            }
        """Replace the named fields in one statement, so two writers cannot lose a field."""
        values = [
            json.dumps(fields[name], ensure_ascii=False) if name in fields else None
            for name in SITUATION_FIELDS
        ]
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO thread_situation
                    (thread_id, asks, doing, decided, deliverables, updated_at)
                VALUES (?, COALESCE(?, '[]'), COALESCE(?, '[]'), COALESCE(?, '[]'),
                        COALESCE(?, '[]'), ?)
                ON CONFLICT(thread_id) DO UPDATE SET
                    asks = COALESCE(?, asks),
                    doing = COALESCE(?, doing),
                    decided = COALESCE(?, decided),
                    deliverables = COALESCE(?, deliverables),
                    updated_at = excluded.updated_at
                """,
                (int(thread_id), *values, datetime.now(UTC).isoformat(), *values),
            )
            await db.commit()
        stored = await self.get(thread_id)
        if stored is None:  # pragma: no cover - the row was written just above
            raise RuntimeError(f"situation of {thread_id} was not stored")
        return stored
