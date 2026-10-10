"""Conversations the console owns: the transcript and title of each one.

A console conversation is the console's equivalent of a chat thread. It is
keyed by the same ``ThreadKey`` the session table, the slot queue and the
registry use (issued by the ``frontend_threads`` ledger under the frontend
name ``console``), so everything downstream of the frontend seam treats it
like any other thread. What Discord would keep for a thread — its name, with
the outcome marker in it, and its messages — is kept here instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import aiosqlite

#: The ``frontend`` name in the ledger and in ``sessions.origin``.
CONSOLE_FRONTEND = "console"

MAX_NAME_CHARS = 200
#: One stored message. The model's answer is never split for the console, so
#: the cap is a guard against a runaway, not a rendering limit.
MAX_MESSAGE_CHARS = 100_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS console_conversations (
    thread_key INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_activity_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS console_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_key INTEGER NOT NULL,
    author TEXT NOT NULL,
    is_bot INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_console_messages_thread ON console_messages(thread_key, id);
"""


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class Conversation:
    """One row of ``console_conversations``."""

    thread_key: int
    name: str
    created_at: str
    last_activity_at: str


@dataclass(frozen=True)
class ConsoleMessage:
    """One row of ``console_messages``."""

    id: int
    thread_key: int
    author: str
    is_bot: bool
    content: str
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        """The same shape the console returns for a chat thread's message."""
        return {
            "id": self.id,
            "author": self.author,
            "is_bot": self.is_bot,
            "content": self.content,
            "truncated": False,
            "created_at": self.created_at,
            "jump_url": None,
        }


class ConversationRepository:
    """CRUD for the console's conversations. One short-lived connection per call."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def init_db(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()

    async def create(self, thread_key: int, name: str) -> Conversation:
        """Record a conversation. Idempotent: an existing one is returned as is."""
        existing = await self.get(thread_key)
        if existing is not None:
            return existing
        now = _now()
        conversation = Conversation(
            thread_key=thread_key,
            name=name.strip()[:MAX_NAME_CHARS] or str(thread_key),
            created_at=now,
            last_activity_at=now,
        )
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO console_conversations"
                " (thread_key, name, created_at, last_activity_at) VALUES (?, ?, ?, ?)",
                (
                    conversation.thread_key,
                    conversation.name,
                    conversation.created_at,
                    conversation.last_activity_at,
                ),
            )
            await db.commit()
        return conversation

    async def get(self, thread_key: int) -> Conversation | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = list(
                await db.execute_fetchall(
                    "SELECT * FROM console_conversations WHERE thread_key = ?", (thread_key,)
                )
            )
        return _to_conversation(rows[0]) if rows else None

    async def list_all(self) -> list[Conversation]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall("SELECT * FROM console_conversations")
        return [_to_conversation(r) for r in rows]

    async def set_name(self, thread_key: int, name: str) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE console_conversations SET name = ? WHERE thread_key = ?",
                (name.strip()[:MAX_NAME_CHARS], thread_key),
            )
            await db.commit()

    async def append(self, thread_key: int, *, author: str, is_bot: bool, content: str) -> int:
        """Store a message and move the conversation's last activity to now."""
        now = _now()
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "INSERT INTO console_messages (thread_key, author, is_bot, content, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (thread_key, author, int(is_bot), content[:MAX_MESSAGE_CHARS], now),
            )
            await db.execute(
                "UPDATE console_conversations SET last_activity_at = ? WHERE thread_key = ?",
                (now, thread_key),
            )
            await db.commit()
            return int(cursor.lastrowid or 0)

    async def history(self, thread_key: int, limit: int) -> list[ConsoleMessage]:
        """The latest *limit* messages, oldest first."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                "SELECT * FROM console_messages WHERE thread_key = ? ORDER BY id DESC LIMIT ?",
                (thread_key, limit),
            )
        return [_to_message(r) for r in reversed(list(rows))]


def _to_conversation(row: aiosqlite.Row) -> Conversation:
    return Conversation(
        thread_key=int(row["thread_key"]),
        name=row["name"],
        created_at=row["created_at"],
        last_activity_at=row["last_activity_at"],
    )


def _to_message(row: aiosqlite.Row) -> ConsoleMessage:
    return ConsoleMessage(
        id=int(row["id"]),
        thread_key=int(row["thread_key"]),
        author=row["author"],
        is_bot=bool(row["is_bot"]),
        content=row["content"],
        created_at=row["created_at"],
    )
