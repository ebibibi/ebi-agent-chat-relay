"""SQLite database schema and initialization."""

from __future__ import annotations

import contextlib
import logging

import aiosqlite

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    thread_id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    working_dir TEXT,
    model TEXT,
    origin TEXT NOT NULL DEFAULT 'discord',
    summary TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    last_used_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_sessions_last_used ON sessions(last_used_at);
CREATE INDEX IF NOT EXISTS idx_sessions_session_id ON sessions(session_id);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_asks (
    thread_id INTEGER PRIMARY KEY,
    session_id TEXT NOT NULL,
    questions_json TEXT NOT NULL,
    question_idx INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS lounge_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    label TEXT NOT NULL DEFAULT 'AI',
    message TEXT NOT NULL,
    thread_id INTEGER,
    posted_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_lounge_posted_at ON lounge_messages(posted_at);

-- Sessions that should be resumed after a bot restart.
-- Rows expire automatically via TTL checks in PendingResumeRepository.
-- A Claude session that is about to restart the bot writes a row here first;
-- on_ready reads and deletes it to resume the session.
CREATE TABLE IF NOT EXISTS pending_resumes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    thread_id INTEGER NOT NULL UNIQUE,
    session_id TEXT,           -- optional: used for "claude --resume" continuity
    reason TEXT NOT NULL DEFAULT 'self_restart',
    resume_prompt TEXT,        -- message to post + send to Claude on resume
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Thread inbox: persistent status tracking across bot restarts.
-- Populated when a Claude session ends; cleared when the user replies.
-- status: 'waiting' (user's reply needed) | 'ambiguous' (unclear)
-- confidence: 'high' | 'low' (from claude -p classification)
CREATE TABLE IF NOT EXISTS thread_inbox (
    thread_id INTEGER PRIMARY KEY,
    status TEXT NOT NULL DEFAULT 'waiting',
    confidence TEXT NOT NULL DEFAULT 'high',
    last_message_url TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Rate limit events emitted by the Claude Code CLI (rate_limit_event stream-json type).
-- One row per rate_limit_type; upserted on every event so this holds the latest state.
CREATE TABLE IF NOT EXISTS usage_stats (
    rate_limit_type TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    utilization REAL NOT NULL,
    resets_at INTEGER NOT NULL,
    is_using_overage INTEGER NOT NULL DEFAULT 0,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Advisory resource claims: a session announces "I am working on X" so a
-- second session asking for the same X is told to step aside *before* it
-- starts. Advisory only — nothing enforces them. Every claim has a TTL so a
-- session that dies cannot pin a resource forever.
CREATE TABLE IF NOT EXISTS resource_claims (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    resource TEXT NOT NULL UNIQUE,
    thread_id INTEGER NOT NULL,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    expires_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_resource_claims_thread ON resource_claims(thread_id);

-- Where a ThreadKey actually lives. Every other table stores a conversation as
-- a bare integer; for Discord that integer is the thread's snowflake and needs
-- no translation, but a frontend whose ids are strings mints a surrogate here.
-- A surrogate is a hash, and a hash does not run backwards — without this row
-- a session could be looked up and still be unreplyable.
CREATE TABLE IF NOT EXISTS frontend_threads (
    thread_key INTEGER PRIMARY KEY,
    frontend TEXT NOT NULL,
    external_id TEXT NOT NULL,
    parent_external_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE(frontend, external_id)
);

CREATE INDEX IF NOT EXISTS idx_frontend_threads_frontend ON frontend_threads(frontend);

-- Which thread spawned which. Written at spawn time (before any session row
-- exists, so an auto_start=false thread is not an orphan) and read back to
-- answer "what did I start?" without parsing thread titles.
CREATE TABLE IF NOT EXISTS thread_lineage (
    thread_id INTEGER PRIMARY KEY,
    parent_thread_id INTEGER NOT NULL,
    family_code TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_thread_lineage_parent ON thread_lineage(parent_thread_id);
"""

# Migrations for existing databases that lack new columns.
_MIGRATIONS = [
    "ALTER TABLE sessions ADD COLUMN origin TEXT NOT NULL DEFAULT 'discord'",
    "ALTER TABLE sessions ADD COLUMN summary TEXT",
    # Which CLI produced this session ID (claude / codex) — see claude_code_core.models.
    "ALTER TABLE sessions ADD COLUMN backend TEXT",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_session_id ON sessions(session_id)",
    # Lounge table added in v1.x — safe to run on existing DBs
    (
        "CREATE TABLE IF NOT EXISTS lounge_messages ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "label TEXT NOT NULL DEFAULT 'AI', "
        "message TEXT NOT NULL, "
        "posted_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')))"
    ),
    "CREATE INDEX IF NOT EXISTS idx_lounge_posted_at ON lounge_messages(posted_at)",
    # pending_resumes added in v1.3 — safe to run on existing DBs
    (
        "CREATE TABLE IF NOT EXISTS pending_resumes ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "thread_id INTEGER NOT NULL UNIQUE, "
        "session_id TEXT, "
        "reason TEXT NOT NULL DEFAULT 'self_restart', "
        "resume_prompt TEXT, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')))"
    ),
    # thread_inbox added in v1.9 — safe to run on existing DBs
    (
        "CREATE TABLE IF NOT EXISTS thread_inbox ("
        "thread_id INTEGER PRIMARY KEY, "
        "status TEXT NOT NULL DEFAULT 'waiting', "
        "confidence TEXT NOT NULL DEFAULT 'high', "
        "last_message_url TEXT, "
        "updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')))"
    ),
    # Drop UNIQUE constraint on session_id to allow /fork (multiple threads, same source session)
    # SQLite cannot ALTER INDEX, so we drop and recreate as a non-unique index.
    "DROP INDEX IF EXISTS idx_sessions_session_id",
    "CREATE INDEX IF NOT EXISTS idx_sessions_session_id ON sessions(session_id)",
    # context stats columns added in v2.0
    "ALTER TABLE sessions ADD COLUMN context_window INTEGER",
    "ALTER TABLE sessions ADD COLUMN context_used INTEGER",
    # usage_stats table added in v2.0
    (
        "CREATE TABLE IF NOT EXISTS usage_stats ("
        "rate_limit_type TEXT PRIMARY KEY, "
        "status TEXT NOT NULL, "
        "utilization REAL NOT NULL, "
        "resets_at INTEGER NOT NULL, "
        "is_using_overage INTEGER NOT NULL DEFAULT 0, "
        "recorded_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')))"
    ),
    # thread_id column on lounge_messages — tracks which Discord thread posted the message
    "ALTER TABLE lounge_messages ADD COLUMN thread_id INTEGER",
    # resource_claims added in v3.2 — advisory cross-session locks
    (
        "CREATE TABLE IF NOT EXISTS resource_claims ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "resource TEXT NOT NULL UNIQUE, "
        "thread_id INTEGER NOT NULL, "
        "note TEXT, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')), "
        "expires_at TEXT NOT NULL)"
    ),
    "CREATE INDEX IF NOT EXISTS idx_resource_claims_thread ON resource_claims(thread_id)",
    # thread_lineage added in v4.1 — spawn parent/child links
    (
        "CREATE TABLE IF NOT EXISTS thread_lineage ("
        "thread_id INTEGER PRIMARY KEY, "
        "parent_thread_id INTEGER NOT NULL, "
        "family_code TEXT NOT NULL, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')))"
    ),
    "CREATE INDEX IF NOT EXISTS idx_thread_lineage_parent ON thread_lineage(parent_thread_id)",
]


async def init_db(db_path: str) -> None:
    """Initialize the database with the schema.

    For fresh databases the full SCHEMA is applied. For existing databases
    the migration statements add any missing columns idempotently.
    """
    async with aiosqlite.connect(db_path) as db:
        # WAL lets readers continue using the last committed snapshot while a
        # different connection holds a write transaction.
        await db.execute("PRAGMA journal_mode=WAL")
        await db.executescript(SCHEMA)
        for stmt in _MIGRATIONS:
            with contextlib.suppress(Exception):
                await db.execute(stmt)
        await db.commit()
    logger.info("Database initialized at %s", db_path)
