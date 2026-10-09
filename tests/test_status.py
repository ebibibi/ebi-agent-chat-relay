"""Unit tests for StatusManager stall notification feature."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_discord.claude.types import ToolCategory
from claude_discord.discord_ui.status import (
    STALL_HARD_SECONDS,
    StatusManager,
)


def _make_message() -> MagicMock:
    """Create a mock Discord message with guild.me for reactions."""
    msg = MagicMock()
    msg.add_reaction = AsyncMock()
    msg.remove_reaction = AsyncMock()
    msg.guild = MagicMock()
    msg.guild.me = MagicMock()
    return msg


class TestHardStallCallback:
    """Tests for the on_hard_stall callback feature."""

    @pytest.mark.asyncio
    async def test_callback_fires_on_hard_stall(self) -> None:
        callback = AsyncMock()
        msg = _make_message()
        sm = StatusManager(msg, on_hard_stall=callback)
        await sm.set_thinking()
        loop = asyncio.get_running_loop()
        sm._last_activity = loop.time() - STALL_HARD_SECONDS - 1
        await asyncio.sleep(2.5)
        callback.assert_awaited_once()
        await sm.cleanup()

    @pytest.mark.asyncio
    async def test_callback_fires_only_once_per_stall(self) -> None:
        callback = AsyncMock()
        msg = _make_message()
        sm = StatusManager(msg, on_hard_stall=callback)
        await sm.set_thinking()
        loop = asyncio.get_running_loop()
        sm._last_activity = loop.time() - STALL_HARD_SECONDS - 1
        await asyncio.sleep(5)
        callback.assert_awaited_once()
        await sm.cleanup()

    @pytest.mark.asyncio
    async def test_callback_resets_after_activity(self) -> None:
        callback = AsyncMock()
        msg = _make_message()
        sm = StatusManager(msg, on_hard_stall=callback)
        await sm.set_thinking()
        loop = asyncio.get_running_loop()
        sm._last_activity = loop.time() - STALL_HARD_SECONDS - 1
        await asyncio.sleep(2.5)
        assert callback.await_count == 1
        await sm.set_tool(ToolCategory.READ)
        sm._last_activity = loop.time() - STALL_HARD_SECONDS - 1
        await asyncio.sleep(2.5)
        assert callback.await_count == 2
        await sm.cleanup()

    @pytest.mark.asyncio
    async def test_no_callback_when_not_provided(self) -> None:
        msg = _make_message()
        sm = StatusManager(msg)
        await sm.set_thinking()
        loop = asyncio.get_running_loop()
        sm._last_activity = loop.time() - STALL_HARD_SECONDS - 1
        await asyncio.sleep(2.5)
        await sm.cleanup()

    @pytest.mark.asyncio
    async def test_callback_exception_does_not_crash_monitor(self) -> None:
        callback = AsyncMock(side_effect=Exception("Discord API error"))
        msg = _make_message()
        sm = StatusManager(msg, on_hard_stall=callback)
        await sm.set_thinking()
        loop = asyncio.get_running_loop()
        sm._last_activity = loop.time() - STALL_HARD_SECONDS - 1
        await asyncio.sleep(2.5)
        callback.assert_awaited_once()
        assert sm._stall_task is not None
        assert not sm._stall_task.done()
        await sm.cleanup()


class TestCompactStatus:
    """Tests for compact status emoji."""

    @pytest.mark.asyncio
    async def test_set_compact_changes_emoji(self) -> None:
        from claude_discord.discord_ui.status import EMOJI_COMPACT

        msg = _make_message()
        sm = StatusManager(msg)
        await sm.set_thinking()
        await sm.set_compact()
        # Allow debounce
        await asyncio.sleep(1)
        assert sm._target_emoji == EMOJI_COMPACT
        await sm.cleanup()

    @pytest.mark.asyncio
    async def test_set_compact_resets_stall_timer(self) -> None:
        """set_compact should reset stall timer so warning doesn't appear during compaction."""
        callback = AsyncMock()
        msg = _make_message()
        sm = StatusManager(msg, on_hard_stall=callback)
        await sm.set_thinking()
        # Simulate time passing
        loop = asyncio.get_running_loop()
        sm._last_activity = loop.time() - 25  # Almost at hard stall threshold
        # Compact resets the timer
        await sm.set_compact()
        # Wait past what would have been the stall threshold
        await asyncio.sleep(3)
        # Callback should NOT have fired because compact reset the timer
        callback.assert_not_awaited()
        await sm.cleanup()


@pytest.mark.asyncio
async def test_rename_of_deleted_thread_is_not_warned(caplog: pytest.LogCaptureFixture) -> None:
    import discord

    from claude_discord.thread_status import _rename

    thread = MagicMock(spec=discord.Thread)
    thread.id = 1
    thread.name = "title"
    thread.edit = AsyncMock(
        side_effect=discord.NotFound(MagicMock(status=404, reason="x"), "Unknown Channel")
    )
    with caplog.at_level("WARNING"):
        exc = await _rename(thread, "done")
    assert isinstance(exc, discord.NotFound)
    assert not caplog.records
