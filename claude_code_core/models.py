"""SQLite database schema for Claude Code core.

Contains the tables needed by any Claude Code integration:
- sessions: thread/channel-to-session mapping
- usage_stats: rate limit tracking
- lounge_messages: AI Lounge cross-session coordination

Frontend-specific tables (pending_asks, pending_resumes, thread_inbox,
settings) remain in the frontend package (e.g. ccdb).
"""

from __future__ import annotations

import contextlib
import logging

import aiosqlite

from .account_pool_repo import ensure_account_schema

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    thread_id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    working_dir TEXT,
    model TEXT,
    origin TEXT NOT NULL DEFAULT 'discord',
    summary TEXT,
    context_window INTEGER,
    context_used INTEGER,
    backend TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    last_used_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_sessions_last_used ON sessions(last_used_at);
CREATE INDEX IF NOT EXISTS idx_sessions_session_id ON sessions(session_id);

CREATE TABLE IF NOT EXISTS usage_stats (
    profile TEXT NOT NULL DEFAULT 'default',
    rate_limit_type TEXT NOT NULL,
    status TEXT NOT NULL,
    utilization REAL NOT NULL,
    resets_at INTEGER NOT NULL,
    is_using_overage INTEGER NOT NULL DEFAULT 0,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (profile, rate_limit_type)
);

CREATE TABLE IF NOT EXISTS lounge_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL DEFAULT 'AI',
    message TEXT NOT NULL,
    thread_id INTEGER,
    posted_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_lounge_posted_at ON lounge_messages(posted_at);
"""

# Migrations for existing databases that lack new columns.
_MIGRATIONS = [
    "ALTER TABLE sessions ADD COLUMN origin TEXT NOT NULL DEFAULT 'discord'",
    "ALTER TABLE sessions ADD COLUMN summary TEXT",
    "ALTER TABLE sessions ADD COLUMN context_window INTEGER",
    "ALTER TABLE sessions ADD COLUMN context_used INTEGER",
    # Which CLI produced this session ID. Claude and Codex session stores are not
    # interoperable, so a stored ID is only resumable by the backend that made it.
    "ALTER TABLE sessions ADD COLUMN backend TEXT",
    # Drop UNIQUE constraint on session_id to allow fork (multiple threads, same source session)
    "DROP INDEX IF EXISTS idx_sessions_session_id",
    "CREATE INDEX IF NOT EXISTS idx_sessions_session_id ON sessions(session_id)",
    (
        "CREATE TABLE IF NOT EXISTS usage_stats ("
        "rate_limit_type TEXT PRIMARY KEY, "
        "status TEXT NOT NULL, "
        "utilization REAL NOT NULL, "
        "resets_at INTEGER NOT NULL, "
        "is_using_overage INTEGER NOT NULL DEFAULT 0, "
        "recorded_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')))"
    ),
    # lounge_messages table — cross-platform AI Lounge coordination
    (
        "CREATE TABLE IF NOT EXISTS lounge_messages ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "label TEXT NOT NULL DEFAULT 'AI', "
        "message TEXT NOT NULL, "
        "thread_id INTEGER, "
        "posted_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')))"
    ),
    "CREATE INDEX IF NOT EXISTS idx_lounge_posted_at ON lounge_messages(posted_at)",
]


async def init_db(db_path: str) -> None:
    """Initialize the database with the core schema.

    For fresh databases the full SCHEMA is applied. For existing databases
    the migration statements add any missing columns idempotently.
    """
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(SCHEMA)
        for stmt in _MIGRATIONS:
            with contextlib.suppress(Exception):
                await db.execute(stmt)
        await db.commit()
        # usage_stats keyed by (profile, rate_limit_type) + account-pool tables.
        await ensure_account_schema(db)
        await db.commit()
    logger.info("Core database initialized at %s", db_path)
