"""Meter what a person types into Ebi AI Console (docs/attention.md).

The chat frontends decide "a human typed this" by the author: the relay posts
everything it injects as the bot. The Console cannot: a reply sent from it is
posted into a chat thread *by the bot*, and its own conversations store every
turn under a name — the signed-in identity for a person, but also ``wait``,
``restart`` or ``thread <id>`` for turns the relay starts itself.

So the Console meters at the entry point instead. Only the endpoints a person
drives (start an item, reply) record a row; the relay's own turns (waits,
restarts, relays from other sessions, the Resume button's canned prompt, the
Workspace brief) never do. Rows carry metadata only, never the text.

History is recoverable: ``console_messages`` keeps every turn, so
:func:`backfill_console` re-derives the same rows (same message ids) for the
time before this was recorded live.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

import aiosqlite

from claude_code_core.attention import SOURCE_BACKFILL, SOURCE_LIVE, HumanActivity
from claude_code_core.attention_repo import HumanActivityRepository

from .conversations import CONSOLE_FRONTEND

__all__ = [
    "BackfillStats",
    "backfill_console",
    "console_activity",
    "is_human_author",
    "measure",
]

logger = logging.getLogger(__name__)

#: Names the relay starts console turns under (wait watcher, restart resume).
MACHINE_AUTHORS = frozenset({"wait", "restart"})
#: A relay from another session (``POST /api/threads/<id>/message``).
_RELAY_AUTHOR = re.compile(r"^thread \d+$")
#: Prefixes of rows a person caused without typing them (the Stop button).
_NOT_TYPED_PREFIXES = ("⏹ Stopped by ",)
#: How ``uploads.with_attachments`` introduces the attached files.
_ATTACHMENT_NOTE = "\n\n📎 Attached files (open the local path with your file-reading tool):"
_ATTACHMENT_ONLY = _ATTACHMENT_NOTE.lstrip("\n")


def is_human_author(author: str) -> bool:
    """Whether a ``console_messages`` author is a signed-in person, not the relay."""
    author = (author or "").strip()
    return bool(author) and author not in MACHINE_AUTHORS and not _RELAY_AUTHOR.match(author)


def measure(content: str) -> tuple[int, int]:
    """Characters typed and files attached in a stored console message.

    The attachment list ``with_attachments`` appends is not typing; it counts
    as attachments (weighted by ``CCDB_ATTENTION_ATTACHMENT_WEIGHT``).
    """
    content = content or ""
    if content.startswith(_ATTACHMENT_ONLY):
        text, files = "", content[len(_ATTACHMENT_ONLY) :]
    elif _ATTACHMENT_NOTE in content:
        text, _, files = content.partition(_ATTACHMENT_NOTE)
    else:
        return len(content), 0
    attachments = sum(1 for line in files.splitlines() if line.startswith("- ["))
    return len(text), attachments


def console_activity(
    *,
    thread_key: int,
    message_id: int | str,
    author_id: str,
    content: str,
    occurred_at: datetime,
    title: str | None = None,
    frontend: str = CONSOLE_FRONTEND,
    source: str = SOURCE_LIVE,
) -> HumanActivity:
    """The metadata row for one human console message. *content* is only measured."""
    chars, attachments = measure(content)
    return HumanActivity(
        frontend=frontend,
        conversation_id=str(thread_key),
        author_id=author_id,
        occurred_at=occurred_at,
        message_id=str(message_id),
        char_count=chars,
        attachment_count=attachments,
        thread_title=title,
        source=source,
    )


@dataclass
class BackfillStats:
    scanned: int = 0
    human_messages: int = 0
    already_recorded: int = 0
    inserted: int = 0


def _parse_time(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(UTC)


def _is_typed(content: str, skip_contents: Iterable[str]) -> bool:
    return not content.startswith(_NOT_TYPED_PREFIXES) and content not in skip_contents


async def backfill_console(
    conversations_db: str,
    repo: HumanActivityRepository,
    *,
    since: datetime,
    until: datetime,
    author_id: str | None = None,
    skip_contents: Iterable[str] = (),
) -> BackfillStats:
    """Record past human console messages between *since* and *until* (UTC).

    Rows are keyed by the ``console_messages`` id, the same key the live path
    uses, so running this again — or over days already recorded live — adds
    nothing twice. *author_id* overrides the stored identity (see
    ``CCDB_ATTENTION_CONSOLE_AUTHOR``); *skip_contents* are canned prompts a
    button sends, which a person did not type.
    """
    skip = frozenset(skip_contents)
    stats = BackfillStats()
    async with aiosqlite.connect(conversations_db) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT m.id, m.thread_key, m.author, m.content, m.created_at, c.name"
            " FROM console_messages m"
            " LEFT JOIN console_conversations c ON c.thread_key = m.thread_key"
            " WHERE m.is_bot = 0 AND m.created_at >= ? AND m.created_at < ?"
            " ORDER BY m.id",
            (_db_time(since), _db_time(until)),
        )
        rows = list(await cursor.fetchall())
    stats.scanned = len(rows)
    activities = [
        console_activity(
            thread_key=row["thread_key"],
            message_id=row["id"],
            author_id=author_id or row["author"],
            content=row["content"],
            occurred_at=_parse_time(row["created_at"]),
            title=row["name"],
            source=SOURCE_BACKFILL,
        )
        for row in rows
        if is_human_author(row["author"]) and _is_typed(row["content"], skip)
    ]
    stats.human_messages = len(activities)
    known = await repo.existing_message_ids(CONSOLE_FRONTEND, [a.message_id for a in activities])
    fresh = [a for a in activities if a.message_id not in known]
    stats.already_recorded = len(activities) - len(fresh)
    stats.inserted = await repo.record_many(fresh) if fresh else 0
    return stats


def _db_time(moment: datetime) -> str:
    """``console_messages.created_at`` form, so the range compares as text."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
