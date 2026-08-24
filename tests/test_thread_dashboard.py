"""Tests for the ThreadStatusDashboard — live session status embed.

Issue: https://github.com/ebibibi/ebi-agent-chat-relay/issues/67
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_discord.discord_ui.thread_dashboard import (
    _STALE_HOURS,
    ThreadState,
    ThreadStatusDashboard,
    _ThreadInfo,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_channel() -> MagicMock:
    """Return a mocked discord.TextChannel."""
    channel = MagicMock(spec=discord.TextChannel)
    msg = MagicMock(spec=discord.Message)
    msg.edit = AsyncMock()
    channel.send = AsyncMock(return_value=msg)
    return channel


def _make_thread(thread_id: int = 111) -> MagicMock:
    """Return a mocked discord.Thread."""
    thread = MagicMock(spec=discord.Thread)
    thread.id = thread_id
    thread.send = AsyncMock()
    return thread


def _make_dashboard(
    owner_id: int | None = None,
) -> tuple[ThreadStatusDashboard, MagicMock]:
    """Return a (dashboard, channel) pair ready for testing."""
    channel = _make_channel()
    dashboard = ThreadStatusDashboard(channel=channel, owner_id=owner_id)
    return dashboard, channel


# ---------------------------------------------------------------------------
# Initialisation
# ---------------------------------------------------------------------------


class TestInitialize:
    @pytest.mark.asyncio
    async def test_initialize_posts_embed(self) -> None:
        dashboard, channel = _make_dashboard()
        await dashboard.initialize()
        channel.send.assert_called_once()
        # Embed should be passed as keyword argument
        call_kwargs = channel.send.call_args.kwargs
        assert "embed" in call_kwargs


# ---------------------------------------------------------------------------
# State transitions
# ---------------------------------------------------------------------------


class TestSetState:
    @pytest.mark.asyncio
    async def test_set_state_processing_adds_thread(self) -> None:
        dashboard, channel = _make_dashboard()
        await dashboard.initialize()
        thread = _make_thread(111)

        await dashboard.set_state(111, ThreadState.PROCESSING, "doing stuff", thread=thread)

        assert 111 in dashboard._threads
        assert dashboard._threads[111].state == ThreadState.PROCESSING

    @pytest.mark.asyncio
    async def test_set_state_updates_dashboard_embed(self) -> None:
        dashboard, channel = _make_dashboard()
        await dashboard.initialize()
        msg = channel.send.return_value

        await dashboard.set_state(222, ThreadState.PROCESSING, "work", thread=_make_thread(222))

        # Edit should have been called once after the state change
        msg.edit.assert_called_once()

    @pytest.mark.asyncio
    async def test_set_state_without_initialize_does_not_crash(self) -> None:
        """set_state before initialize() skips dashboard edit (no message to edit)."""
        dashboard, _ = _make_dashboard()
        # No initialize() called — _dashboard_message is None
        # Should not raise
        await dashboard.set_state(1, ThreadState.PROCESSING, "test")

    @pytest.mark.asyncio
    async def test_multiple_state_updates_accumulate(self) -> None:
        dashboard, channel = _make_dashboard()
        await dashboard.initialize()

        await dashboard.set_state(1, ThreadState.PROCESSING, "task 1", thread=_make_thread(1))
        await dashboard.set_state(2, ThreadState.PROCESSING, "task 2", thread=_make_thread(2))

        assert len(dashboard._threads) == 2

    @pytest.mark.asyncio
    async def test_update_existing_thread_state(self) -> None:
        dashboard, _ = _make_dashboard()
        await dashboard.initialize()
        thread = _make_thread(5)

        await dashboard.set_state(5, ThreadState.PROCESSING, "start", thread=thread)
        await dashboard.set_state(5, ThreadState.WAITING_INPUT, "start", thread=thread)

        assert dashboard._threads[5].state == ThreadState.WAITING_INPUT


# ---------------------------------------------------------------------------
# Owner mention on WAITING_INPUT
# ---------------------------------------------------------------------------


class TestOwnerMention:
    @pytest.mark.asyncio
    async def test_mention_sent_on_waiting_input_transition(self) -> None:
        dashboard, channel = _make_dashboard(owner_id=42)
        await dashboard.initialize()
        thread = _make_thread(10)

        # First transition: PROCESSING → WAITING_INPUT
        await dashboard.set_state(10, ThreadState.PROCESSING, "working", thread=thread)
        await dashboard.set_state(10, ThreadState.WAITING_INPUT, "working", thread=thread)

        thread.send.assert_called_once()
        sent_text = thread.send.call_args.args[0]
        assert sent_text == "🟡 <@42> The agent has finished — your reply is needed here."

    @pytest.mark.asyncio
    async def test_mention_not_sent_if_already_waiting(self) -> None:
        """Repeated WAITING_INPUT transitions should NOT spam mentions."""
        dashboard, _ = _make_dashboard(owner_id=42)
        await dashboard.initialize()
        thread = _make_thread(10)

        await dashboard.set_state(10, ThreadState.WAITING_INPUT, "w", thread=thread)
        thread.send.reset_mock()

        # Second WAITING_INPUT — should not mention again
        await dashboard.set_state(10, ThreadState.WAITING_INPUT, "w", thread=thread)

        thread.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_mention_when_owner_id_not_set(self) -> None:
        dashboard, _ = _make_dashboard(owner_id=None)
        await dashboard.initialize()
        thread = _make_thread(10)

        await dashboard.set_state(10, ThreadState.PROCESSING, "w", thread=thread)
        await dashboard.set_state(10, ThreadState.WAITING_INPUT, "w", thread=thread)

        thread.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_mention_when_thread_not_provided(self) -> None:
        """Without a thread object, the mention is silently skipped."""
        dashboard, _ = _make_dashboard(owner_id=42)
        await dashboard.initialize()

        # No thread= argument — should not crash
        await dashboard.set_state(10, ThreadState.PROCESSING, "w")
        await dashboard.set_state(10, ThreadState.WAITING_INPUT, "w")

    @pytest.mark.asyncio
    async def test_mention_survives_http_error(self) -> None:
        """A failed HTTP call for the mention must not crash the dashboard."""
        dashboard, _ = _make_dashboard(owner_id=42)
        await dashboard.initialize()
        thread = _make_thread(10)
        thread.send.side_effect = discord.HTTPException(MagicMock(), "error")

        # Should not raise
        await dashboard.set_state(10, ThreadState.PROCESSING, "w", thread=thread)
        await dashboard.set_state(10, ThreadState.WAITING_INPUT, "w", thread=thread)


# ---------------------------------------------------------------------------
# Remove
# ---------------------------------------------------------------------------


class TestRemove:
    @pytest.mark.asyncio
    async def test_remove_existing_thread(self) -> None:
        dashboard, _ = _make_dashboard()
        await dashboard.initialize()
        await dashboard.set_state(77, ThreadState.PROCESSING, "task")

        await dashboard.remove(77)

        assert 77 not in dashboard._threads

    @pytest.mark.asyncio
    async def test_remove_nonexistent_is_noop(self) -> None:
        dashboard, _ = _make_dashboard()
        await dashboard.initialize()
        # Should not raise
        await dashboard.remove(9999)


# ---------------------------------------------------------------------------
# Shutdown resilience
# ---------------------------------------------------------------------------


class TestShutdownResilience:
    @pytest.mark.asyncio
    async def test_refresh_survives_session_closed_runtime_error(self) -> None:
        """RuntimeError('Session is closed') during bot shutdown must not propagate.

        When aiohttp closes its HTTP session during shutdown, message.edit() raises
        RuntimeError instead of discord.HTTPException. The dashboard must swallow it.
        """
        dashboard, channel = _make_dashboard()
        await dashboard.initialize()
        msg = channel.send.return_value
        msg.edit.side_effect = RuntimeError("Session is closed")

        # Should not raise
        await dashboard.set_state(1, ThreadState.PROCESSING, "work", thread=_make_thread(1))

    @pytest.mark.asyncio
    async def test_remove_survives_session_closed_runtime_error(self) -> None:
        """remove() must also survive RuntimeError during shutdown."""
        dashboard, channel = _make_dashboard()
        await dashboard.initialize()
        msg = channel.send.return_value
        msg.edit.side_effect = RuntimeError("Session is closed")
        await dashboard.set_state(1, ThreadState.PROCESSING, "work")

        # Should not raise
        await dashboard.remove(1)


# ---------------------------------------------------------------------------
# Embed building
# ---------------------------------------------------------------------------


class TestBuildEmbed:
    def test_empty_embed_shows_no_active_sessions(self) -> None:
        dashboard, _ = _make_dashboard()
        embed = dashboard._build_embed()
        assert embed.description is not None
        assert "No active sessions" in embed.description

    def test_embed_shows_thread_mention(self) -> None:
        dashboard, _ = _make_dashboard()
        dashboard._threads[123] = _thread_info(123, ThreadState.PROCESSING, "doing stuff")
        embed = dashboard._build_embed()
        field_names = [f.name for f in embed.fields]
        assert any("<#123>" in name for name in field_names)

    def test_embed_yellow_when_any_waiting(self) -> None:
        dashboard, _ = _make_dashboard()
        dashboard._threads[1] = _thread_info(1, ThreadState.PROCESSING, "p")
        dashboard._threads[2] = _thread_info(2, ThreadState.WAITING_INPUT, "w")
        embed = dashboard._build_embed()
        assert embed.color.value == 0xFEE75C  # Yellow

    def test_embed_blurple_when_all_processing(self) -> None:
        dashboard, _ = _make_dashboard()
        dashboard._threads[1] = _thread_info(1, ThreadState.PROCESSING, "p")
        embed = dashboard._build_embed()
        assert embed.color.value == 0x5865F2  # Blurple


# ---------------------------------------------------------------------------
# Stale entry pruning
# ---------------------------------------------------------------------------


class TestStalePruning:
    def test_stale_entries_pruned_on_refresh(self) -> None:
        dashboard, _ = _make_dashboard()
        info = _thread_info(55, ThreadState.WAITING_INPUT, "old")
        # Make the state_changed_at very old
        info.state_changed_at = time.monotonic() - (_STALE_HOURS * 3600 + 1)
        dashboard._threads[55] = info

        dashboard._prune_stale()

        assert 55 not in dashboard._threads

    def test_recent_entries_not_pruned(self) -> None:
        dashboard, _ = _make_dashboard()
        info = _thread_info(56, ThreadState.PROCESSING, "fresh")
        dashboard._threads[56] = info

        dashboard._prune_stale()

        assert 56 in dashboard._threads


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _thread_info(
    thread_id: int,
    state: ThreadState,
    description: str,
) -> _ThreadInfo:
    return _ThreadInfo(thread_id=thread_id, description=description, state=state)
