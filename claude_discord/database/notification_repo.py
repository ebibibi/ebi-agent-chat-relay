"""Notification repository for scheduled notifications (aiosqlite).

Provides async CRUD for the scheduled_notifications table.
Used by the REST API extension for push notifications to Discord.
"""

from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

logger = logging.getLogger(__name__)

NOTIFICATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS scheduled_notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    message TEXT NOT NULL,
    title TEXT,
    color INTEGER DEFAULT 49151,
    scheduled_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'api',
    channel_id INTEGER,
    status TEXT NOT NULL DEFAULT 'pending',
    sent_at TEXT,
    error_message TEXT,
    created_at TEXT DEFAULT (datetime('now', 'localtime'))
);

CREATE INDEX IF NOT EXISTS idx_notif_status_scheduled
    ON scheduled_notifications(status, scheduled_at);
"""


class NotificationRepository:
    """Async CRUD for scheduled_notifications table."""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path

    async def init_db(self) -> None:
        """Initialize the notification schema, including a fresh deployment directory."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(NOTIFICATION_SCHEMA)
            await db.commit()
        logger.info("Notification DB initialized at %s", self.db_path)

    async def create(
        self,
        message: str,
        scheduled_at: str,
        *,
        title: str | None = None,
        color: int = 0x00BFFF,
        source: str = "api",
        channel_id: int | None = None,
    ) -> int:
        """Schedule a notification. Returns the created ID."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """INSERT INTO scheduled_notifications
                    (message, title, color, scheduled_at, source, channel_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (message, title, color, scheduled_at, source, channel_id),
            )
            await db.commit()
            row_id = cursor.lastrowid
        assert row_id is not None, "INSERT should always return a lastrowid"
        logger.info("Notification scheduled: id=%d, at=%s", row_id, scheduled_at)
        return row_id

    async def get_pending(self, before: str | None = None) -> list[dict]:
        """Get pending notifications, optionally filtered by time."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            if before:
                cursor = await db.execute(
                    """SELECT * FROM scheduled_notifications
                       WHERE status = 'pending' AND scheduled_at <= ?
                       ORDER BY scheduled_at""",
                    (before,),
                )
            else:
                cursor = await db.execute(
                    """SELECT * FROM scheduled_notifications
                       WHERE status = 'pending'
                       ORDER BY scheduled_at""",
                )
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    async def mark_sent(self, notification_id: int) -> None:
        """Mark a notification as sent."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """UPDATE scheduled_notifications
                   SET status = 'sent', sent_at = datetime('now', 'localtime')
                   WHERE id = ?""",
                (notification_id,),
            )
            await db.commit()

    async def mark_failed(self, notification_id: int, error: str) -> None:
        """Mark a notification as failed."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """UPDATE scheduled_notifications
                   SET status = 'failed', error_message = ?
                   WHERE id = ?""",
                (error, notification_id),
            )
            await db.commit()

    async def cancel(self, notification_id: int) -> bool:
        """Cancel a pending notification. Returns True if cancelled."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """UPDATE scheduled_notifications
                   SET status = 'cancelled'
                   WHERE id = ? AND status = 'pending'""",
                (notification_id,),
            )
            await db.commit()
            return cursor.rowcount > 0
