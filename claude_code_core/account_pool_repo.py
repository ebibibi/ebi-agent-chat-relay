"""Persistence for account pools: thread pins, pool cursors, rejection markers.

Also owns the one schema change pools need in a shared table: ``usage_stats``
was keyed by ``rate_limit_type`` alone and is now keyed by
``(profile, rate_limit_type)``. :func:`ensure_account_schema` rebuilds an old
table in place, assigning every existing row to :data:`DEFAULT_PROFILE`, and is
idempotent so both ``init_db`` implementations can call it on every start.
"""

from __future__ import annotations

import logging

import aiosqlite

from .account_pool import DEFAULT_PROFILE, PoolCursor

logger = logging.getLogger(__name__)

USAGE_STATS_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS usage_stats (
    profile TEXT NOT NULL DEFAULT '{DEFAULT_PROFILE}',
    rate_limit_type TEXT NOT NULL,
    status TEXT NOT NULL,
    utilization REAL NOT NULL,
    resets_at INTEGER NOT NULL,
    is_using_overage INTEGER NOT NULL DEFAULT 0,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (profile, rate_limit_type)
);
"""

ACCOUNT_SCHEMA = """
-- Which profile a thread's session currently lives in. The session transcript
-- is stored under that profile's directory, so this is where it is copied
-- *from* when the thread moves to another profile.
CREATE TABLE IF NOT EXISTS account_threads (
    thread_id INTEGER PRIMARY KEY,
    backend TEXT NOT NULL,
    profile TEXT NOT NULL,
    -- The profile's directory when pinned, so a profile later removed from the
    -- pool file can still be found as the source of the transcript.
    home TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- Pool-wide strategy state: the round-robin cursor and the sticky profile.
CREATE TABLE IF NOT EXISTS account_pool_state (
    backend TEXT PRIMARY KEY,
    rr_next INTEGER NOT NULL DEFAULT 0,
    sticky_profile TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

-- A profile that was rejected for quota, and until when (Unix seconds).
CREATE TABLE IF NOT EXISTS account_blocks (
    profile TEXT PRIMARY KEY,
    blocked_until INTEGER NOT NULL,
    reason TEXT,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
"""


async def ensure_account_schema(db: aiosqlite.Connection) -> None:
    """Create the pool tables and migrate ``usage_stats`` to per-profile keys."""
    await _migrate_usage_stats(db)
    await db.executescript(USAGE_STATS_SCHEMA + ACCOUNT_SCHEMA)


async def _migrate_usage_stats(db: aiosqlite.Connection) -> None:
    cursor = await db.execute("PRAGMA table_info(usage_stats)")
    columns = [row[1] for row in await cursor.fetchall()]
    if not columns or "profile" in columns:
        return
    # Rebuild: SQLite cannot change a primary key in place. One transaction,
    # so a crash midway leaves the old table intact.
    await db.commit()
    await db.execute("BEGIN IMMEDIATE")
    try:
        # Re-check under the write lock: another connection may have migrated
        # between the first look and the lock.
        cursor = await db.execute("PRAGMA table_info(usage_stats)")
        if "profile" in [row[1] for row in await cursor.fetchall()]:
            await db.rollback()
            return
        await db.execute("ALTER TABLE usage_stats RENAME TO usage_stats_pre_profile")
        await db.execute(USAGE_STATS_SCHEMA)
        await db.execute(
            "INSERT INTO usage_stats (profile, rate_limit_type, status, utilization, "
            "resets_at, is_using_overage, recorded_at) "
            "SELECT ?, rate_limit_type, status, utilization, resets_at, is_using_overage, "
            "recorded_at FROM usage_stats_pre_profile",
            (DEFAULT_PROFILE,),
        )
        await db.execute("DROP TABLE usage_stats_pre_profile")
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    logger.info("Migrated usage_stats to per-profile keys (existing rows → %r)", DEFAULT_PROFILE)


class AccountPoolRepository:
    """CRUD for thread pins, pool cursors and rejection markers."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        self._ready = False

    async def _connect(self) -> aiosqlite.Connection:
        db = await aiosqlite.connect(self.db_path)
        if not self._ready:
            await ensure_account_schema(db)
            await db.commit()
            self._ready = True
        return db

    async def get_pin(self, thread_id: int) -> tuple[str, str] | None:
        """Return ``(backend, profile)`` the thread's session lives in."""
        pin = await self.get_pin_home(thread_id)
        return (pin[0], pin[1]) if pin else None

    async def get_pin_home(self, thread_id: int) -> tuple[str, str, str | None] | None:
        """Return ``(backend, profile, home)``; ``home`` is ``None`` for the ambient login."""
        db = await self._connect()
        try:
            cursor = await db.execute(
                "SELECT backend, profile, home FROM account_threads WHERE thread_id = ?",
                (thread_id,),
            )
            row = await cursor.fetchone()
            return (row[0], row[1], row[2]) if row else None
        finally:
            await db.close()

    async def set_pin(
        self, thread_id: int, backend: str, profile: str, home: str | None = None
    ) -> None:
        db = await self._connect()
        try:
            await db.execute(
                """INSERT INTO account_threads (thread_id, backend, profile, home)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(thread_id) DO UPDATE SET
                     backend = excluded.backend,
                     profile = excluded.profile,
                     home = excluded.home,
                     updated_at = datetime('now', 'localtime')""",
                (thread_id, backend, profile, home),
            )
            await db.commit()
        finally:
            await db.close()

    async def get_cursor(self, backend: str) -> PoolCursor:
        db = await self._connect()
        try:
            cursor = await db.execute(
                "SELECT rr_next, sticky_profile FROM account_pool_state WHERE backend = ?",
                (backend,),
            )
            row = await cursor.fetchone()
            return PoolCursor(rr_next=row[0], sticky=row[1]) if row else PoolCursor()
        finally:
            await db.close()

    async def set_cursor(self, backend: str, state: PoolCursor) -> None:
        db = await self._connect()
        try:
            await db.execute(
                """INSERT INTO account_pool_state (backend, rr_next, sticky_profile)
                   VALUES (?, ?, ?)
                   ON CONFLICT(backend) DO UPDATE SET
                     rr_next = excluded.rr_next,
                     sticky_profile = excluded.sticky_profile,
                     updated_at = datetime('now', 'localtime')""",
                (backend, state.rr_next, state.sticky),
            )
            await db.commit()
        finally:
            await db.close()

    async def get_blocks(self) -> dict[str, int]:
        db = await self._connect()
        try:
            cursor = await db.execute("SELECT profile, blocked_until FROM account_blocks")
            return {row[0]: int(row[1]) for row in await cursor.fetchall()}
        finally:
            await db.close()

    async def set_block(self, profile: str, until: int, reason: str | None = None) -> None:
        db = await self._connect()
        try:
            await db.execute(
                """INSERT INTO account_blocks (profile, blocked_until, reason) VALUES (?, ?, ?)
                   ON CONFLICT(profile) DO UPDATE SET
                     blocked_until = excluded.blocked_until,
                     reason = excluded.reason,
                     recorded_at = datetime('now', 'localtime')""",
                (profile, int(until), reason),
            )
            await db.commit()
        finally:
            await db.close()
