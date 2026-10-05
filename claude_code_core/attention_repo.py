"""Persistence for human activity — the raw input of the attention estimate.

One row per human message, keyed by ``(frontend, conversation_id, message_id)``
— Teams activity ids are only unique within their conversation — so recording
the same message twice adds nothing. Each row says where it came from
(``live``: it reached a session; ``backfill``: read back from history). No
message text is stored, ever: only where, when, who, and how much.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta

import aiosqlite

from .attention import (
    GROUP_BY_DAY,
    SOURCE_LIVE,
    AttentionConfig,
    AttentionParams,
    HumanActivity,
    build_report,
    local_day_bounds_utc,
)

logger = logging.getLogger(__name__)

__all__ = [
    "HUMAN_ACTIVITY_SCHEMA",
    "AttentionRecorder",
    "HumanActivityRepository",
    "load_report",
]

# Bursts at the edge of a range are formed from the messages just outside it;
# a day of context on each side covers any realistic chain of short gaps.
_EDGE_CONTEXT = timedelta(days=1)
# SQLite's default limit on bound parameters is 999; stay well under it.
_ID_CHUNK = 500

HUMAN_ACTIVITY_SCHEMA = """
CREATE TABLE IF NOT EXISTS human_activity (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    frontend         TEXT NOT NULL,
    conversation_id  TEXT NOT NULL,
    parent_id        TEXT,
    thread_title     TEXT,
    author_id        TEXT NOT NULL,
    occurred_at      TEXT NOT NULL,
    char_count       INTEGER NOT NULL DEFAULT 0,
    attachment_count INTEGER NOT NULL DEFAULT 0,
    message_id       TEXT NOT NULL,
    source           TEXT NOT NULL DEFAULT 'live'
);
"""

# Applied after the table exists, in order, on every start. Each step is
# idempotent, so a database created by any earlier version converges here.
_MIGRATIONS = (
    # The first key, (frontend, message_id), was too strict for Teams.
    "DROP INDEX IF EXISTS idx_human_activity_message",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_human_activity_key "
    "ON human_activity(frontend, conversation_id, message_id)",
    # Backfill de-duplicates by message id regardless of conversation.
    "CREATE INDEX IF NOT EXISTS idx_human_activity_message_id "
    "ON human_activity(frontend, message_id)",
    "CREATE INDEX IF NOT EXISTS idx_human_activity_author_time "
    "ON human_activity(author_id, occurred_at)",
    "CREATE INDEX IF NOT EXISTS idx_human_activity_time ON human_activity(occurred_at)",
)

_COLUMNS = (
    "frontend, conversation_id, parent_id, thread_title, author_id, "
    "occurred_at, char_count, attachment_count, message_id, source"
)


def _to_db_time(moment: datetime) -> str:
    # Fixed width, always UTC: lexicographic order is chronological order.
    return moment.astimezone(UTC).isoformat(timespec="milliseconds")


class HumanActivityRepository:
    """Async storage for :class:`HumanActivity` rows in the shared session DB."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init_db(self) -> None:
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(HUMAN_ACTIVITY_SCHEMA)
            cursor = await db.execute("PRAGMA table_info(human_activity)")
            columns = {row[1] for row in await cursor.fetchall()}
            if "source" not in columns:
                await db.execute(
                    "ALTER TABLE human_activity ADD COLUMN source TEXT NOT NULL DEFAULT 'live'"
                )
            for statement in _MIGRATIONS:
                await db.execute(statement)
            await db.commit()

    async def record(self, activity: HumanActivity) -> bool:
        """Insert one activity. Returns ``False`` when the message was already recorded."""
        return await self.record_many([activity]) == 1

    async def record_many(self, activities: list[HumanActivity]) -> int:
        """Insert many activities idempotently; returns how many were new."""
        if not activities:
            return 0
        rows = [
            (
                a.frontend,
                a.conversation_id,
                a.parent_id,
                a.thread_title,
                a.author_id,
                _to_db_time(a.occurred_at),
                a.char_count,
                a.attachment_count,
                a.message_id,
                a.source,
            )
            for a in activities
        ]
        async with aiosqlite.connect(self.db_path) as db:
            before = db.total_changes
            await db.executemany(
                f"INSERT OR IGNORE INTO human_activity ({_COLUMNS}) "  # noqa: S608
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )
            inserted = db.total_changes - before
            await db.commit()
        return inserted

    async def existing_message_ids(self, frontend: str, message_ids: Iterable[str]) -> set[str]:
        """Which of *message_ids* are already recorded on *frontend*, in any conversation."""
        wanted = list(dict.fromkeys(message_ids))
        found: set[str] = set()
        async with aiosqlite.connect(self.db_path) as db:
            for i in range(0, len(wanted), _ID_CHUNK):
                chunk = wanted[i : i + _ID_CHUNK]
                marks = ",".join("?" * len(chunk))
                cursor = await db.execute(
                    "SELECT message_id FROM human_activity "  # noqa: S608
                    f"WHERE frontend = ? AND message_id IN ({marks})",
                    [frontend, *chunk],
                )
                found.update(row[0] for row in await cursor.fetchall())
        return found

    async def count_between(
        self, start: datetime, end: datetime, *, source: str | None = None
    ) -> int:
        """How many rows fall in ``[start, end)``, optionally of one source."""
        query = "SELECT COUNT(*) FROM human_activity WHERE occurred_at >= ? AND occurred_at < ?"
        params: list[object] = [_to_db_time(start), _to_db_time(end)]
        if source is not None:
            query += " AND source = ?"
            params.append(source)
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(query, params)
            row = await cursor.fetchone()
        return int(row[0]) if row else 0

    async def list_between(
        self,
        start: datetime,
        end: datetime,
        *,
        author_id: str | None = None,
        include_backfill: bool = True,
    ) -> list[HumanActivity]:
        """Activities with ``start <= occurred_at < end``, oldest first."""
        query = f"SELECT {_COLUMNS} FROM human_activity WHERE occurred_at >= ? AND occurred_at < ?"  # noqa: S608
        params: list[object] = [_to_db_time(start), _to_db_time(end)]
        if author_id is not None:
            query += " AND author_id = ?"
            params.append(author_id)
        if not include_backfill:
            query += " AND source = ?"
            params.append(SOURCE_LIVE)
        query += " ORDER BY occurred_at, id"
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
        return [
            HumanActivity(
                frontend=row[0],
                conversation_id=row[1],
                parent_id=row[2],
                thread_title=row[3],
                author_id=row[4],
                occurred_at=datetime.fromisoformat(row[5]),
                char_count=row[6],
                attachment_count=row[7],
                message_id=row[8],
                source=row[9],
            )
            for row in rows
        ]

    async def count(self) -> int:
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM human_activity")
            row = await cursor.fetchone()
        return int(row[0]) if row else 0


class AttentionRecorder:
    """The frontend-neutral hook every inbound human message passes through.

    Frontends decide *whether* a message is a genuine human turn; this decides
    whether recording is on, and makes sure a storage failure can never stop
    the message from reaching its session.
    """

    def __init__(self, repo: HumanActivityRepository, config: AttentionConfig) -> None:
        self.repo = repo
        self.config = config

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    async def record(self, activity: HumanActivity) -> None:
        if not self.config.enabled:
            return
        try:
            await self.repo.record(activity)
        except Exception:
            # Metering is a side channel; losing one row must not lose the turn.
            logger.exception(
                "Could not record human activity for %s:%s",
                activity.frontend,
                activity.conversation_id,
            )


async def load_report(
    repo: HumanActivityRepository,
    params: AttentionParams,
    *,
    start: date,
    end: date,
    group_by: str = GROUP_BY_DAY,
    author_id: str | None = None,
    include_backfill: bool = True,
) -> dict[str, object]:
    """Read the rows a report for local days ``start``..``end`` needs and estimate it."""
    first, after = local_day_bounds_utc(start, end, params)
    activities = await repo.list_between(
        first - _EDGE_CONTEXT,
        after + _EDGE_CONTEXT,
        author_id=author_id,
        include_backfill=include_backfill,
    )
    return build_report(
        activities,
        params,
        start=start,
        end=end,
        group_by=group_by,
        author_id=author_id,
        include_backfill=include_backfill,
    )
