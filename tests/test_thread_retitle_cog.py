"""Cog-level wiring for re-titling: who gets re-titled, and how often."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from claude_discord.cogs.claude_chat import ClaudeChatCog
from claude_discord.discord_ui.thread_retitle import RetitlePolicy, RetitleTracker

BOT_USER_ID = 7


def _make_cog(auto_rename: bool = True, **policy_kwargs) -> ClaudeChatCog:
    bot = MagicMock()
    bot.channel_id = 111
    bot.user = MagicMock()
    bot.user.id = BOT_USER_ID
    runner = MagicMock()
    runner.command = "claude"
    runner.clone = MagicMock(return_value=MagicMock())
    repo = MagicMock()
    repo.get = AsyncMock(return_value=None)
    repo.save = AsyncMock()
    cog = ClaudeChatCog(
        bot=bot,
        repo=repo,
        runner=runner,
        channel_ids={111},
        auto_rename_threads=auto_rename,
    )
    if policy_kwargs:
        cog._retitle_tracker = RetitleTracker(policy=RetitlePolicy(**policy_kwargs))
    return cog


def _make_thread(owner_id: int = BOT_USER_ID, name: str = "Fix the auth bug") -> MagicMock:
    thread = MagicMock(spec=discord.Thread)
    thread.id = 555
    thread.owner_id = owner_id
    thread.name = name
    thread.edit = AsyncMock()
    return thread


class TestScheduleRetitle:
    def test_no_task_when_auto_rename_is_off(self) -> None:
        cog = _make_cog(auto_rename=False)
        cog._background_retitle_thread = MagicMock()
        cog._schedule_retitle(_make_thread(), "a wholly different subject")
        cog._background_retitle_thread.assert_not_called()

    def test_threads_the_bot_does_not_own_are_left_alone(self) -> None:
        """Renaming a human's thread out from under them is never ours to do."""
        cog = _make_cog(min_interval_seconds=0, messages_before_retitle=1)
        cog._background_retitle_thread = MagicMock()
        cog._schedule_retitle(_make_thread(owner_id=BOT_USER_ID + 1), "something else")
        cog._background_retitle_thread.assert_not_called()

    def test_empty_message_records_nothing(self) -> None:
        cog = _make_cog(min_interval_seconds=0, messages_before_retitle=1)
        cog._background_retitle_thread = MagicMock()
        cog._schedule_retitle(_make_thread(), "   ")
        cog._background_retitle_thread.assert_not_called()

    @pytest.mark.asyncio
    async def test_task_is_spawned_once_the_thresholds_are_met(self) -> None:
        cog = _make_cog(min_interval_seconds=0, messages_before_retitle=2)
        seen: list[tuple] = []

        async def _capture(thread, messages):
            seen.append((thread, messages))

        cog._background_retitle_thread = _capture  # type: ignore[method-assign]
        thread = _make_thread()

        cog._schedule_retitle(thread, "first request")
        await asyncio.sleep(0)
        assert seen == []

        cog._schedule_retitle(thread, "now about something else")
        await asyncio.sleep(0)

        assert len(seen) == 1
        assert seen[0][0] is thread
        assert seen[0][1] == ("first request", "now about something else")

    @pytest.mark.asyncio
    async def test_cooldown_holds_off_a_second_rename(self) -> None:
        cog = _make_cog(min_interval_seconds=900, messages_before_retitle=1)
        calls: list = []

        async def _capture(thread, messages):
            calls.append(messages)

        cog._background_retitle_thread = _capture  # type: ignore[method-assign]
        thread = _make_thread()
        for _ in range(5):
            cog._schedule_retitle(thread, "yet another message")
        await asyncio.sleep(0)
        assert calls == []


class TestBackgroundRetitle:
    @pytest.mark.asyncio
    async def test_applies_the_new_title(self) -> None:
        cog = _make_cog()
        thread = _make_thread()
        with patch(
            "claude_discord.cogs.claude_chat.suggest_retitle",
            new=AsyncMock(return_value="Migrate the billing database"),
        ):
            await cog._background_retitle_thread(thread, ("move billing to postgres",))
        thread.edit.assert_awaited_once_with(name="Migrate the billing database")

    @pytest.mark.asyncio
    async def test_keeps_the_lineage_tag(self) -> None:
        cog = _make_cog()
        thread = _make_thread(name="🤖K2 Fix the auth bug")
        with patch(
            "claude_discord.cogs.claude_chat.suggest_retitle",
            new=AsyncMock(return_value="Migrate the billing database"),
        ):
            await cog._background_retitle_thread(thread, ("move billing to postgres",))
        thread.edit.assert_awaited_once_with(name="🤖K2 Migrate the billing database")

    @pytest.mark.asyncio
    async def test_keep_verdict_leaves_the_title_alone(self) -> None:
        cog = _make_cog()
        thread = _make_thread()
        with patch(
            "claude_discord.cogs.claude_chat.suggest_retitle",
            new=AsyncMock(return_value=None),
        ):
            await cog._background_retitle_thread(thread, ("more of the same",))
        thread.edit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failing_edit_never_raises(self) -> None:
        cog = _make_cog()
        thread = _make_thread()
        thread.edit = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "rate limited"))
        with patch(
            "claude_discord.cogs.claude_chat.suggest_retitle",
            new=AsyncMock(return_value="Migrate the billing database"),
        ):
            await cog._background_retitle_thread(thread, ("move billing to postgres",))
        thread.edit.assert_awaited_once()


class TestThreadReplyWiring:
    @pytest.mark.asyncio
    async def test_thread_reply_feeds_the_retitler(self) -> None:
        cog = _make_cog()
        cog._run_claude = AsyncMock()
        cog._schedule_retitle = MagicMock()

        thread = _make_thread()
        thread.parent_id = 111
        msg = MagicMock(spec=discord.Message)
        msg.id = 1
        msg.author = MagicMock()
        msg.author.bot = False
        msg.content = "let's switch to the billing migration"
        msg.attachments = []
        msg.mentions = []
        msg.channel = thread
        msg.type = discord.MessageType.default

        await cog._handle_thread_reply(msg)

        cog._schedule_retitle.assert_called_once()
        assert cog._schedule_retitle.call_args.args[0] is thread
        assert cog._schedule_retitle.call_args.args[1] == "let's switch to the billing migration"


class TestClearForgetsDrift:
    @pytest.mark.asyncio
    async def test_clear_resets_the_drift_history(self) -> None:
        cog = _make_cog(min_interval_seconds=0, messages_before_retitle=2)
        thread = _make_thread()
        cog._schedule_retitle(thread, "first request")
        assert cog._retitle_tracker.tracked_thread_ids() == (thread.id,)

        interaction = MagicMock()
        interaction.channel = thread
        interaction.response.send_message = AsyncMock()
        cog._active_runners.pop(thread.id, None)
        cog.repo.delete = AsyncMock(return_value=True)

        await cog.clear_session.callback(cog, interaction)  # type: ignore[union-attr]

        assert cog._retitle_tracker.tracked_thread_ids() == ()
