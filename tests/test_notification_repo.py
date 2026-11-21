"""Tests for NotificationRepository."""

from __future__ import annotations

import os
import tempfile

import pytest

from claude_discord.database.notification_repo import NotificationRepository


@pytest.fixture
async def repo() -> NotificationRepository:
    """Create a temp database with notification schema."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    repo = NotificationRepository(path)
    await repo.init_db()
    yield repo
    os.unlink(path)


class TestCreate:
    @pytest.mark.asyncio
    async def test_create_returns_id(self, repo: NotificationRepository) -> None:
        nid = await repo.create(message="hello", scheduled_at="2026-01-01T09:00:00")
        assert isinstance(nid, int)
        assert nid > 0

    @pytest.mark.asyncio
    async def test_create_with_all_fields(self, repo: NotificationRepository) -> None:
        nid = await repo.create(
            message="test",
            scheduled_at="2026-01-01T09:00:00",
            title="Title",
            color=0xFF0000,
            source="webhook",
            channel_id=12345,
        )
        assert nid > 0


class TestGetPending:
    @pytest.mark.asyncio
    async def test_empty_db(self, repo: NotificationRepository) -> None:
        result = await repo.get_pending()
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_pending(self, repo: NotificationRepository) -> None:
        await repo.create(message="a", scheduled_at="2026-01-01T09:00:00")
        await repo.create(message="b", scheduled_at="2026-01-01T10:00:00")
        result = await repo.get_pending()
        assert len(result) == 2
        assert result[0]["message"] == "a"
        assert result[1]["message"] == "b"

    @pytest.mark.asyncio
    async def test_filter_by_before(self, repo: NotificationRepository) -> None:
        await repo.create(message="early", scheduled_at="2026-01-01T08:00:00")
        await repo.create(message="late", scheduled_at="2026-01-01T12:00:00")
        result = await repo.get_pending(before="2026-01-01T10:00:00")
        assert len(result) == 1
        assert result[0]["message"] == "early"


class TestMarkSent:
    @pytest.mark.asyncio
    async def test_mark_sent_removes_from_pending(self, repo: NotificationRepository) -> None:
        nid = await repo.create(message="test", scheduled_at="2026-01-01T09:00:00")
        await repo.mark_sent(nid)
        result = await repo.get_pending()
        assert len(result) == 0


class TestMarkFailed:
    @pytest.mark.asyncio
    async def test_mark_failed_removes_from_pending(self, repo: NotificationRepository) -> None:
        nid = await repo.create(message="test", scheduled_at="2026-01-01T09:00:00")
        await repo.mark_failed(nid, "connection error")
        result = await repo.get_pending()
        assert len(result) == 0


class TestCancel:
    @pytest.mark.asyncio
    async def test_cancel_pending(self, repo: NotificationRepository) -> None:
        nid = await repo.create(message="test", scheduled_at="2026-01-01T09:00:00")
        assert await repo.cancel(nid) is True
        result = await repo.get_pending()
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_cancel_nonexistent(self, repo: NotificationRepository) -> None:
        assert await repo.cancel(99999) is False

    @pytest.mark.asyncio
    async def test_cancel_already_sent(self, repo: NotificationRepository) -> None:
        nid = await repo.create(message="test", scheduled_at="2026-01-01T09:00:00")
        await repo.mark_sent(nid)
        assert await repo.cancel(nid) is False


async def test_first_start_creates_notification_database_parent(tmp_path):
    repo = NotificationRepository(str(tmp_path / "fresh" / "nested" / "notifications.db"))
    await repo.init_db()
    assert (tmp_path / "fresh" / "nested" / "notifications.db").is_file()
