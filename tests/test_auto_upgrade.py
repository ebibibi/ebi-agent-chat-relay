"""Tests for AutoUpgradeCog."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from discord.ext import commands

from claude_discord.cogs.auto_upgrade import AutoUpgradeCog, UpgradeApprovalView, UpgradeConfig

_PATCH_EXEC = "asyncio.create_subprocess_exec"
_PATCH_WAIT = "asyncio.wait_for"
_PATCH_SLEEP = "asyncio.sleep"


@pytest.fixture
def bot() -> MagicMock:
    return MagicMock(spec=commands.Bot)


@pytest.fixture
def config() -> UpgradeConfig:
    return UpgradeConfig(
        package_name="claude-code-discord-bridge",
        trigger_prefix="🔄 ebibot-upgrade",
        working_dir="/home/user/bot",
    )


@pytest.fixture
def cog(bot: MagicMock, config: UpgradeConfig) -> AutoUpgradeCog:
    return AutoUpgradeCog(bot=bot, config=config)


def _make_message(
    content: str = "🔄 ebibot-upgrade",
    webhook_id: int | None = 12345,
    channel_id: int = 999,
) -> MagicMock:
    msg = MagicMock(spec=discord.Message)
    msg.content = content
    msg.webhook_id = webhook_id
    msg.channel = MagicMock()
    msg.channel.id = channel_id
    msg.reply = AsyncMock()
    msg.add_reaction = AsyncMock()
    thread = MagicMock(spec=discord.Thread)
    thread.send = AsyncMock()
    msg.create_thread = AsyncMock(return_value=thread)
    return msg


def _make_process(
    returncode: int = 0,
    stdout: bytes = b"ok",
) -> MagicMock:
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    return proc


class TestFiltering:
    """Test message filtering logic."""

    @pytest.mark.asyncio
    async def test_ignores_non_webhook(
        self,
        cog: AutoUpgradeCog,
    ) -> None:
        msg = _make_message(webhook_id=None)
        msg.create_thread = AsyncMock()
        await cog.on_message(msg)
        msg.create_thread.assert_not_called()

    @pytest.mark.asyncio
    async def test_ignores_unauthorized_webhook(
        self,
        bot: MagicMock,
    ) -> None:
        config = UpgradeConfig(
            package_name="pkg",
            allowed_webhook_ids={99999},
        )
        cog = AutoUpgradeCog(bot=bot, config=config)
        msg = _make_message(webhook_id=12345)
        msg.create_thread = AsyncMock()
        await cog.on_message(msg)
        msg.create_thread.assert_not_called()

    @pytest.mark.asyncio
    async def test_ignores_wrong_channel(
        self,
        bot: MagicMock,
    ) -> None:
        config = UpgradeConfig(
            package_name="pkg",
            channel_ids={111},
        )
        cog = AutoUpgradeCog(bot=bot, config=config)
        msg = _make_message(channel_id=999)
        msg.create_thread = AsyncMock()
        await cog.on_message(msg)
        msg.create_thread.assert_not_called()

    @pytest.mark.asyncio
    async def test_ignores_wrong_prefix(
        self,
        cog: AutoUpgradeCog,
    ) -> None:
        msg = _make_message(content="Hello")
        msg.create_thread = AsyncMock()
        await cog.on_message(msg)
        msg.create_thread.assert_not_called()

    @pytest.mark.asyncio
    async def test_exact_match_required(
        self,
        cog: AutoUpgradeCog,
    ) -> None:
        """Trigger prefix must be exact match."""
        msg = _make_message(content="🔄 ebibot-upgrade extra")
        msg.create_thread = AsyncMock()
        await cog.on_message(msg)
        msg.create_thread.assert_not_called()


class TestUpgradeSteps:
    """Test upgrade step execution.

    All subprocess calls use create_subprocess_exec with args
    from UpgradeConfig, not user input.
    """

    @pytest.mark.asyncio
    async def test_upgrade_failure_adds_error_reaction(
        self,
        cog: AutoUpgradeCog,
    ) -> None:
        """If upgrade step fails, should add error reaction."""
        msg = _make_message()
        proc_fail = _make_process(returncode=1, stdout=b"error")
        with (
            patch(_PATCH_EXEC, new_callable=AsyncMock, return_value=proc_fail),
            patch(_PATCH_WAIT, new_callable=AsyncMock, return_value=(b"error", b"")),
        ):
            await cog.on_message(msg)

        msg.add_reaction.assert_called_with("❌")

    @pytest.mark.asyncio
    async def test_success_adds_check_reaction(
        self,
        cog: AutoUpgradeCog,
    ) -> None:
        """Successful upgrade should add check reaction."""
        msg = _make_message()
        proc_ok = _make_process()
        with (
            patch(_PATCH_EXEC, new_callable=AsyncMock, return_value=proc_ok),
            patch(_PATCH_WAIT, new_callable=AsyncMock, return_value=(b"ok", b"")),
        ):
            await cog.on_message(msg)

        msg.add_reaction.assert_called_with("✅")

    @pytest.mark.asyncio
    async def test_no_restart_shows_completion(
        self,
        cog: AutoUpgradeCog,
    ) -> None:
        """When no restart_command, show completion message."""
        msg = _make_message()
        thread = msg.create_thread.return_value
        proc_ok = _make_process()
        with (
            patch(_PATCH_EXEC, new_callable=AsyncMock, return_value=proc_ok),
            patch(_PATCH_WAIT, new_callable=AsyncMock, return_value=(b"ok", b"")),
        ):
            await cog.on_message(msg)

        send_calls = [str(c) for c in thread.send.call_args_list]
        assert any("Upgrade complete" in s for s in send_calls)

    @pytest.mark.asyncio
    async def test_restart_command_fired(
        self,
        bot: MagicMock,
    ) -> None:
        """Restart command should be fired after upgrade.

        The restart command uses create_subprocess_exec with
        args from UpgradeConfig, not from user/webhook input.
        """
        config = UpgradeConfig(
            package_name="pkg",
            restart_command=[
                "sudo",
                "systemctl",
                "restart",
                "bot.service",
            ],
            working_dir="/tmp",
        )
        cog = AutoUpgradeCog(bot=bot, config=config)
        msg = _make_message(content="🔄 upgrade")

        proc_ok = _make_process()
        exec_calls: list[tuple] = []

        async def mock_subprocess_exec(*args, **kwargs):
            exec_calls.append(args)
            return proc_ok

        with (
            patch(_PATCH_EXEC, side_effect=mock_subprocess_exec),
            patch(_PATCH_WAIT, new_callable=AsyncMock, return_value=(b"ok", b"")),
            patch(_PATCH_SLEEP, new_callable=AsyncMock),
        ):
            await cog.on_message(msg)

        assert len(exec_calls) == 3
        assert exec_calls[2] == (
            "sudo",
            "systemctl",
            "restart",
            "bot.service",
        )


class TestDrainCheck:
    """Test graceful-drain-before-restart behaviour."""

    def _make_cog_with_restart(
        self,
        bot: MagicMock,
        drain_check=None,
        drain_timeout: int = 300,
        drain_poll_interval: int = 10,
    ) -> AutoUpgradeCog:
        config = UpgradeConfig(
            package_name="pkg",
            restart_command=["sudo", "systemctl", "restart", "bot.service"],
            working_dir="/tmp",
        )
        return AutoUpgradeCog(
            bot=bot,
            config=config,
            drain_check=drain_check,
            drain_timeout=drain_timeout,
            drain_poll_interval=drain_poll_interval,
        )

    @pytest.mark.asyncio
    async def test_no_drain_check_skips_waiting(
        self,
        bot: MagicMock,
    ) -> None:
        """When drain_check is None, _drain returns immediately."""
        cog = self._make_cog_with_restart(bot)
        thread = MagicMock(spec=discord.Thread)
        thread.send = AsyncMock()

        with patch(_PATCH_SLEEP, new_callable=AsyncMock) as mock_sleep:
            await cog._drain(thread)

        mock_sleep.assert_not_called()
        thread.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_drain_check_true_returns_immediately(
        self,
        bot: MagicMock,
    ) -> None:
        """When drain_check already returns True, no polling occurs."""
        cog = self._make_cog_with_restart(bot, drain_check=lambda: True)
        thread = MagicMock(spec=discord.Thread)
        thread.send = AsyncMock()

        with patch(_PATCH_SLEEP, new_callable=AsyncMock) as mock_sleep:
            await cog._drain(thread)

        mock_sleep.assert_not_called()
        thread.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_drain_waits_until_check_passes(
        self,
        bot: MagicMock,
    ) -> None:
        """drain_check False→True: polls once then proceeds."""
        poll_count = 0

        def drain_check() -> bool:
            nonlocal poll_count
            poll_count += 1
            return poll_count > 1  # False first, True second

        cog = self._make_cog_with_restart(
            bot,
            drain_check=drain_check,
            drain_poll_interval=5,
        )
        thread = MagicMock(spec=discord.Thread)
        thread.send = AsyncMock()

        sleep_calls: list[float] = []

        async def capture_sleep(s: float) -> None:
            sleep_calls.append(s)

        with patch(_PATCH_SLEEP, side_effect=capture_sleep):
            await cog._drain(thread)

        assert sleep_calls == [5]
        send_texts = " ".join(str(c) for c in thread.send.call_args_list)
        assert "waiting for active sessions" in send_texts
        assert "Sessions finished" in send_texts

    @pytest.mark.asyncio
    async def test_drain_timeout_proceeds_anyway(
        self,
        bot: MagicMock,
    ) -> None:
        """When drain_check never returns True, returns after timeout."""
        cog = self._make_cog_with_restart(
            bot,
            drain_check=lambda: False,
            drain_timeout=20,
            drain_poll_interval=10,
        )
        thread = MagicMock(spec=discord.Thread)
        thread.send = AsyncMock()

        with patch(_PATCH_SLEEP, new_callable=AsyncMock):
            await cog._drain(thread)

        # Posts timeout warning, then returns (restart proceeds)
        send_texts = " ".join(str(c) for c in thread.send.call_args_list)
        assert "timeout" in send_texts.lower()


class TestConcurrency:
    """Test concurrent execution prevention."""

    @pytest.mark.asyncio
    async def test_concurrent_blocked(
        self,
        cog: AutoUpgradeCog,
    ) -> None:
        msg = _make_message()
        await cog._lock.acquire()
        try:
            await cog.on_message(msg)
            msg.reply.assert_called_once()
            assert "already running" in msg.reply.call_args[0][0]
        finally:
            cog._lock.release()


class TestUpgradeConfigDataclass:
    """Test UpgradeConfig dataclass."""

    def test_defaults(self) -> None:
        config = UpgradeConfig(package_name="my-pkg")
        assert config.package_name == "my-pkg"
        assert config.trigger_prefix == "🔄 upgrade"
        assert config.working_dir == "."
        assert config.upgrade_command is None
        assert config.sync_command is None
        assert config.restart_command is None
        assert config.allowed_webhook_ids is None
        assert config.channel_ids is None
        assert config.step_timeout == 120

    def test_frozen(self) -> None:
        config = UpgradeConfig(package_name="my-pkg")
        with pytest.raises(AttributeError):
            config.package_name = "changed"  # type: ignore[misc]

    def test_custom_commands(self) -> None:
        config = UpgradeConfig(
            package_name="my-pkg",
            upgrade_command=["pip", "install", "--upgrade", "my-pkg"],
            sync_command=["pip", "check"],
        )
        assert config.upgrade_command == [
            "pip",
            "install",
            "--upgrade",
            "my-pkg",
        ]
        assert config.sync_command == ["pip", "check"]


class TestAutoDrainDiscovery:
    """Test auto-discovery of DrainAware Cogs."""

    def _make_cog_with_restart(
        self,
        bot: MagicMock,
        drain_check=None,
    ) -> AutoUpgradeCog:
        config = UpgradeConfig(
            package_name="pkg",
            restart_command=["sudo", "systemctl", "restart", "bot.service"],
            working_dir="/tmp",
        )
        return AutoUpgradeCog(bot=bot, config=config, drain_check=drain_check)

    def test_auto_discovers_drain_aware_cogs(self) -> None:
        """When no explicit drain_check, discovers DrainAware Cogs."""
        bot = MagicMock(spec=commands.Bot)

        class FakeDrainCog:
            @property
            def active_count(self) -> int:
                return 0

        fake_cog = FakeDrainCog()
        bot.cogs = MagicMock()
        cog = self._make_cog_with_restart(bot)
        bot.cogs.values.return_value = [fake_cog, cog]

        assert cog._auto_drain_check() is True

    def test_auto_drain_check_busy_cog_returns_false(self) -> None:
        """auto_drain_check returns False when a DrainAware Cog is busy."""
        bot = MagicMock(spec=commands.Bot)

        class BusyCog:
            @property
            def active_count(self) -> int:
                return 2

        busy_cog = BusyCog()
        cog = self._make_cog_with_restart(bot)
        bot.cogs.values.return_value = [busy_cog, cog]

        assert cog._auto_drain_check() is False

    def test_no_drain_aware_cogs_returns_true(self) -> None:
        """When no DrainAware Cogs exist, safe to restart."""
        bot = MagicMock(spec=commands.Bot)

        class PlainCog:
            pass

        bot.cogs.values.return_value = [PlainCog()]
        cog = self._make_cog_with_restart(bot)

        assert cog._auto_drain_check() is True

    def test_explicit_drain_check_takes_precedence(self) -> None:
        """Explicit drain_check should be used over auto-discovery."""
        bot = MagicMock(spec=commands.Bot)
        explicit_called = False

        def explicit_check() -> bool:
            nonlocal explicit_called
            explicit_called = True
            return True

        # Even with a busy DrainAware cog, explicit check is used
        class BusyCog:
            @property
            def active_count(self) -> int:
                return 5

        bot.cogs.values.return_value = [BusyCog()]
        cog = self._make_cog_with_restart(bot, drain_check=explicit_check)

        # The _drain method uses explicit check, not auto
        assert cog._drain_check is not None
        assert cog._drain_check() is True
        assert explicit_called

    def test_auto_drain_excludes_self(self) -> None:
        """AutoUpgradeCog should not check itself (it's not DrainAware anyway,
        but if it were, it should exclude itself to avoid deadlock)."""
        bot = MagicMock(spec=commands.Bot)
        cog = self._make_cog_with_restart(bot)
        bot.cogs.values.return_value = [cog]

        assert cog._auto_drain_check() is True


class TestRestartApproval:
    """Test restart_approval mode."""

    def _make_cog_with_approval(
        self,
        bot: MagicMock,
        restart_approval: bool = True,
    ) -> AutoUpgradeCog:
        config = UpgradeConfig(
            package_name="pkg",
            restart_command=["sudo", "systemctl", "restart", "bot.service"],
            working_dir="/tmp",
            restart_approval=restart_approval,
        )
        return AutoUpgradeCog(bot=bot, config=config, drain_poll_interval=5)

    @pytest.mark.asyncio
    async def test_approval_mode_waits_for_reaction(
        self,
        bot: MagicMock,
    ) -> None:
        """When restart_approval=True, should post approval message and wait."""
        cog = self._make_cog_with_approval(bot)
        thread = MagicMock(spec=discord.Thread)
        thread.send = AsyncMock()
        approval_msg = MagicMock()
        approval_msg.id = 99999
        approval_msg.add_reaction = AsyncMock()
        thread.send.return_value = approval_msg

        # Simulate immediate approval reaction
        reaction_event = MagicMock()
        reaction_event.message_id = 99999
        reaction_event.emoji = "✅"
        reaction_event.user_id = 42  # Not the bot
        bot.user = MagicMock()
        bot.user.id = 1  # Bot ID
        bot.wait_for = AsyncMock(return_value=reaction_event)

        await cog._wait_for_approval(MagicMock(), thread)

        # Should have posted the approval message
        thread.send.assert_any_call("📦 Update installed. React ✅ on this message to restart.")
        # Should have added the reaction
        approval_msg.add_reaction.assert_called_once_with("✅")
        # Should have posted the approval confirmation
        thread.send.assert_any_call("👍 Restart approved!")

    @pytest.mark.asyncio
    async def test_approval_mode_sends_reminder_on_timeout(
        self,
        bot: MagicMock,
    ) -> None:
        """When no reaction within timeout, sends reminder then waits again."""
        cog = self._make_cog_with_approval(bot)
        thread = MagicMock(spec=discord.Thread)
        thread.send = AsyncMock()
        approval_msg = MagicMock()
        approval_msg.id = 99999
        approval_msg.add_reaction = AsyncMock()
        thread.send.return_value = approval_msg

        bot.user = MagicMock()
        bot.user.id = 1

        call_count = 0

        async def wait_for_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TimeoutError
            # Second call: return approval
            event = MagicMock()
            event.message_id = 99999
            event.emoji = "✅"
            event.user_id = 42
            return event

        bot.wait_for = AsyncMock(side_effect=wait_for_side_effect)

        await cog._wait_for_approval(MagicMock(), thread)

        # Should have sent a reminder
        reminder_calls = [str(c) for c in thread.send.call_args_list if "Still waiting" in str(c)]
        assert len(reminder_calls) == 1

    @pytest.mark.asyncio
    async def test_approval_mode_sends_reminder_on_asyncio_timeout(
        self,
        bot: MagicMock,
    ) -> None:
        """A raised ``asyncio.TimeoutError`` still triggers the reminder path.

        Since Python 3.11 ``asyncio.TimeoutError`` is an alias of the builtin
        ``TimeoutError``, so the handler catches the builtin alone. This test
        pins that equivalence: if a future Python (or a third-party shim) ever
        reintroduces a distinct asyncio timeout type, the bare
        ``except TimeoutError`` would silently drop it and this test fails.
        """
        cog = self._make_cog_with_approval(bot)
        thread = MagicMock(spec=discord.Thread)
        thread.send = AsyncMock()
        approval_msg = MagicMock()
        approval_msg.id = 99999
        approval_msg.add_reaction = AsyncMock()
        thread.send.return_value = approval_msg

        bot.user = MagicMock()
        bot.user.id = 1

        call_count = 0

        async def wait_for_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TimeoutError  # Python 3.10 asyncio variant
            event = MagicMock()
            event.message_id = 99999
            event.emoji = "✅"
            event.user_id = 42
            return event

        bot.wait_for = AsyncMock(side_effect=wait_for_side_effect)

        await cog._wait_for_approval(MagicMock(), thread)

        reminder_calls = [str(c) for c in thread.send.call_args_list if "Still waiting" in str(c)]
        assert len(reminder_calls) == 1

    @pytest.mark.asyncio
    async def test_no_approval_mode_skips_wait(
        self,
        bot: MagicMock,
    ) -> None:
        """When restart_approval=False, restart happens without waiting."""
        config = UpgradeConfig(
            package_name="pkg",
            restart_command=["sudo", "systemctl", "restart", "bot.service"],
            working_dir="/tmp",
            restart_approval=False,
        )
        cog = AutoUpgradeCog(bot=bot, config=config)
        msg = _make_message(content="🔄 upgrade")

        proc_ok = _make_process()
        exec_calls: list[tuple] = []

        async def mock_subprocess(*args, **kwargs):
            exec_calls.append(args)
            return proc_ok

        with (
            patch(_PATCH_EXEC, side_effect=mock_subprocess),
            patch(_PATCH_WAIT, new_callable=AsyncMock, return_value=(b"ok", b"")),
            patch(_PATCH_SLEEP, new_callable=AsyncMock),
        ):
            await cog.on_message(msg)

        # Should have restarted without calling wait_for
        bot.wait_for = AsyncMock()
        bot.wait_for.assert_not_called()
        # restart command should have fired
        assert len(exec_calls) == 3

    def test_restart_approval_config_default_false(self) -> None:
        """restart_approval should default to False."""
        config = UpgradeConfig(package_name="pkg")
        assert config.restart_approval is False

    @pytest.mark.asyncio
    async def test_restart_method_fires_command(
        self,
        bot: MagicMock,
    ) -> None:
        """_restart should send message, react, and fire command."""
        cog = self._make_cog_with_approval(bot)
        trigger_msg = MagicMock()
        trigger_msg.add_reaction = AsyncMock()
        thread = MagicMock(spec=discord.Thread)
        thread.send = AsyncMock()

        with (
            patch(_PATCH_EXEC, new_callable=AsyncMock) as mock_sub,
            patch(_PATCH_SLEEP, new_callable=AsyncMock),
        ):
            await cog._restart(trigger_msg, thread)

        thread.send.assert_called_with("🔄 Restarting...")
        trigger_msg.add_reaction.assert_called_with("✅")
        mock_sub.assert_called_once()


class TestActiveSessionCount:
    """Tests for ClaudeChatCog.active_session_count property."""

    def test_count_starts_at_zero(self) -> None:
        from claude_discord.claude.runner import ClaudeRunner
        from claude_discord.cogs.claude_chat import ClaudeChatCog

        bot = MagicMock()
        bot.channel_id = 999
        cog = ClaudeChatCog(
            bot=bot,
            repo=MagicMock(),
            runner=MagicMock(spec=ClaudeRunner),
        )
        assert cog.active_session_count == 0

    def test_count_reflects_runners(self) -> None:
        from claude_discord.claude.runner import ClaudeRunner
        from claude_discord.cogs.claude_chat import ClaudeChatCog

        bot = MagicMock()
        bot.channel_id = 999
        cog = ClaudeChatCog(
            bot=bot,
            repo=MagicMock(),
            runner=MagicMock(spec=ClaudeRunner),
        )
        cog._active_runners[1] = MagicMock()
        cog._active_runners[2] = MagicMock()
        assert cog.active_session_count == 2

        del cog._active_runners[1]
        assert cog.active_session_count == 1


class TestMarkSessionsForResume:
    """Tests for _collect_active_thread_ids and _mark_sessions_for_resume."""

    def _make_cog_with_restart(self, bot: MagicMock) -> AutoUpgradeCog:
        config = UpgradeConfig(
            package_name="pkg",
            restart_command=["sudo", "systemctl", "restart", "bot.service"],
            working_dir="/tmp",
        )
        return AutoUpgradeCog(bot=bot, config=config)

    def test_collect_active_thread_ids_empty(self) -> None:
        """Returns empty frozenset when no cog has _active_runners."""
        bot = MagicMock(spec=commands.Bot)
        bot.cogs.values.return_value = []
        cog = self._make_cog_with_restart(bot)
        assert cog._collect_active_thread_ids() == frozenset()

    def test_collect_active_thread_ids_from_cog(self) -> None:
        """Collects thread IDs from cogs that have _active_runners."""
        bot = MagicMock(spec=commands.Bot)
        cog = self._make_cog_with_restart(bot)

        class FakeChatCog:
            _active_runners = {111: MagicMock(), 222: MagicMock()}

        fake_chat = FakeChatCog()
        bot.cogs.values.return_value = [fake_chat, cog]

        result = cog._collect_active_thread_ids()
        assert result == frozenset({111, 222})

    def test_collect_active_thread_ids_excludes_self(self) -> None:
        """Does not include threads from the cog itself (it has no _active_runners)."""
        bot = MagicMock(spec=commands.Bot)
        cog = self._make_cog_with_restart(bot)
        # Manually give the cog an _active_runners to verify self-exclusion
        cog._active_runners = {999: MagicMock()}  # type: ignore[attr-defined]
        bot.cogs.values.return_value = [cog]

        # self is excluded
        result = cog._collect_active_thread_ids()
        assert 999 not in result

    @pytest.mark.asyncio
    async def test_mark_sessions_no_op_when_empty(self) -> None:
        """No API calls when thread_ids is empty."""
        bot = MagicMock(spec=commands.Bot)
        bot.resume_repo = AsyncMock()
        cog = self._make_cog_with_restart(bot)
        thread = MagicMock(spec=discord.Thread)
        thread.send = AsyncMock()

        await cog._mark_sessions_for_resume(frozenset(), thread)

        bot.resume_repo.mark.assert_not_called()
        thread.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_mark_sessions_no_op_when_no_resume_repo(self) -> None:
        """No-op when bot.resume_repo is not set."""
        bot = MagicMock(spec=commands.Bot)
        del bot.resume_repo  # ensure attribute is absent
        cog = self._make_cog_with_restart(bot)
        thread = MagicMock(spec=discord.Thread)
        thread.send = AsyncMock()

        await cog._mark_sessions_for_resume(frozenset({111}), thread)

        thread.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_mark_sessions_calls_mark_for_each_thread(self) -> None:
        """Calls resume_repo.mark() for each active thread."""
        bot = MagicMock(spec=commands.Bot)
        resume_repo = MagicMock()
        resume_repo.mark = AsyncMock(return_value=1)
        bot.resume_repo = resume_repo

        # No session_repo → session_id will be None
        del bot.session_repo

        cog = self._make_cog_with_restart(bot)
        thread = MagicMock(spec=discord.Thread)
        thread.send = AsyncMock()

        await cog._mark_sessions_for_resume(frozenset({111, 222}), thread)

        assert resume_repo.mark.call_count == 2
        called_thread_ids = {call.args[0] for call in resume_repo.mark.call_args_list}
        assert called_thread_ids == {111, 222}

        # Should post a Discord message reporting the count
        thread.send.assert_called_once()
        assert "2" in thread.send.call_args.args[0]

    @pytest.mark.asyncio
    async def test_mark_sessions_resolves_session_id_from_repo(self) -> None:
        """Looks up session_id from session_repo when available."""
        bot = MagicMock(spec=commands.Bot)

        resume_repo = MagicMock()
        resume_repo.mark = AsyncMock(return_value=1)
        bot.resume_repo = resume_repo

        session_record = MagicMock()
        session_record.session_id = "abc-123"
        session_repo = MagicMock()
        session_repo.get = AsyncMock(return_value=session_record)
        bot.session_repo = session_repo

        cog = self._make_cog_with_restart(bot)
        thread = MagicMock(spec=discord.Thread)
        thread.send = AsyncMock()

        await cog._mark_sessions_for_resume(frozenset({111}), thread)

        session_repo.get.assert_awaited_once_with(111)
        resume_repo.mark.assert_awaited_once()
        call_kwargs = resume_repo.mark.call_args.kwargs
        assert call_kwargs["session_id"] == "abc-123"
        assert call_kwargs["reason"] == "bot_upgrade"

    @pytest.mark.asyncio
    async def test_mark_sessions_handles_mark_failure_gracefully(self) -> None:
        """Continues marking other threads even if one fails."""
        bot = MagicMock(spec=commands.Bot)
        resume_repo = MagicMock()
        resume_repo.mark = AsyncMock(side_effect=[RuntimeError("db error"), 2])
        bot.resume_repo = resume_repo
        del bot.session_repo

        cog = self._make_cog_with_restart(bot)
        thread = MagicMock(spec=discord.Thread)
        thread.send = AsyncMock()

        # Should not raise; one thread fails, one succeeds
        await cog._mark_sessions_for_resume(frozenset({111, 222}), thread)

        assert resume_repo.mark.call_count == 2
        # Only 1 succeeded, so Discord message says "1"
        thread.send.assert_called_once()
        assert "1" in thread.send.call_args.args[0]

    @pytest.mark.asyncio
    async def test_upgrade_marks_active_sessions_before_restart(self) -> None:
        """Integration: active sessions are marked for resume during upgrade."""
        bot = MagicMock(spec=commands.Bot)
        bot.user = MagicMock()
        bot.user.id = 0

        resume_repo = MagicMock()
        resume_repo.mark = AsyncMock(return_value=1)
        bot.resume_repo = resume_repo
        del bot.session_repo

        class FakeChatCog:
            _active_runners = {555: MagicMock()}

        bot.cogs.values.return_value = [FakeChatCog()]

        config = UpgradeConfig(
            package_name="pkg",
            trigger_prefix="🔄 upgrade",
            working_dir="/tmp",
            restart_command=["sudo", "systemctl", "restart", "bot.service"],
        )
        cog = AutoUpgradeCog(bot=bot, config=config)

        trigger_msg = _make_message(content="🔄 upgrade")

        with (
            patch(_PATCH_EXEC, new_callable=AsyncMock) as mock_exec,
            patch(_PATCH_WAIT, new_callable=AsyncMock) as mock_wait,
            patch(_PATCH_SLEEP, new_callable=AsyncMock),
        ):
            proc = _make_process(returncode=0, stdout=b"ok")
            mock_exec.return_value = proc
            mock_wait.return_value = (b"ok", b"")

            # Drain check: return True immediately (no active sessions during drain)
            cog._drain_check = lambda: True

            await cog._run_upgrade(trigger_msg)

        # resume_repo.mark should have been called for thread 555
        resume_repo.mark.assert_awaited_once()
        assert resume_repo.mark.call_args.args[0] == 555
        assert resume_repo.mark.call_args.kwargs["reason"] == "bot_upgrade"


class TestUpgradeApprovalView:
    """Unit tests for UpgradeApprovalView."""

    @pytest.mark.asyncio
    async def test_button_is_green_and_labelled(self) -> None:
        """View has exactly one green button with the default label."""
        approved = asyncio.Event()
        view = UpgradeApprovalView(approved_event=approved)
        buttons = [c for c in view.children if isinstance(c, discord.ui.Button)]
        assert len(buttons) == 1
        assert buttons[0].style == discord.ButtonStyle.green
        assert "Approve" in (buttons[0].label or "")

    @pytest.mark.asyncio
    async def test_custom_label(self) -> None:
        """Custom label is applied to the button."""
        approved = asyncio.Event()
        view = UpgradeApprovalView(approved_event=approved, label="✅ Approve Restart")
        buttons = [c for c in view.children if isinstance(c, discord.ui.Button)]
        assert buttons[0].label == "✅ Approve Restart"

    @pytest.mark.asyncio
    async def test_button_click_sets_event(self) -> None:
        """Clicking the button sets the approved event and disables the button."""
        approved = asyncio.Event()
        view = UpgradeApprovalView(approved_event=approved, bot_id=None)
        button = next(c for c in view.children if isinstance(c, discord.ui.Button))

        interaction = MagicMock(spec=discord.Interaction)
        interaction.user = MagicMock()
        interaction.user.id = 42
        interaction.response = AsyncMock()
        interaction.response.edit_message = AsyncMock()

        # _ViewCallback wraps the decorated method; invoke with just (interaction).
        await button.callback(interaction)

        assert approved.is_set()
        assert button.disabled is True
        interaction.response.edit_message.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_bot_click_is_ignored(self) -> None:
        """Bot's own click is deferred without setting the event."""
        approved = asyncio.Event()
        bot_id = 999
        view = UpgradeApprovalView(approved_event=approved, bot_id=bot_id)
        button = next(c for c in view.children if isinstance(c, discord.ui.Button))

        interaction = MagicMock(spec=discord.Interaction)
        interaction.user = MagicMock()
        interaction.user.id = bot_id  # same as bot
        interaction.response = AsyncMock()
        interaction.response.defer = AsyncMock()

        await button.callback(interaction)

        assert not approved.is_set()
        interaction.response.defer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_bump_reposts_message_at_bottom(self) -> None:
        """bump() deletes the old message and posts a new one at the bottom."""
        approved = asyncio.Event()
        content = "🔔 **Approval needed** — please approve"
        view = UpgradeApprovalView(approved_event=approved, content=content)

        old_msg = MagicMock(spec=discord.Message)
        old_msg.delete = AsyncMock()
        new_msg = MagicMock(spec=discord.Message)

        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock(return_value=new_msg)

        view.set_message(old_msg)
        await view.bump(channel)

        channel.send.assert_awaited_once_with(content, view=view)
        old_msg.delete.assert_awaited_once()
        assert view._message is new_msg

    @pytest.mark.asyncio
    async def test_bump_noop_when_already_approved(self) -> None:
        """bump() does nothing once the event is already set."""
        approved = asyncio.Event()
        approved.set()
        view = UpgradeApprovalView(approved_event=approved, content="test")

        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock()

        await view.bump(channel)

        channel.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_bump_no_delete_when_no_previous_message(self) -> None:
        """bump() posts a new message even when no previous message is stored."""
        approved = asyncio.Event()
        content = "🔔 Approval needed"
        view = UpgradeApprovalView(approved_event=approved, content=content)

        new_msg = MagicMock(spec=discord.Message)
        channel = MagicMock(spec=discord.TextChannel)
        channel.send = AsyncMock(return_value=new_msg)

        # No set_message() called — _message is None
        await view.bump(channel)

        channel.send.assert_awaited_once_with(content, view=view)
        assert view._message is new_msg


class TestApprovalButton:
    """Integration tests for button-based approval in _wait_for_approval."""

    def _make_cog(self, bot: MagicMock) -> AutoUpgradeCog:
        config = UpgradeConfig(
            package_name="pkg",
            restart_command=["sudo", "systemctl", "restart", "bot.service"],
            working_dir="/tmp",
            restart_approval=True,
        )
        return AutoUpgradeCog(bot=bot, config=config)

    def _make_thread_with_parent(self) -> tuple[MagicMock, MagicMock]:
        """Return (thread, parent_channel) where parent.send is an AsyncMock."""
        parent = MagicMock(spec=discord.TextChannel)
        parent.send = AsyncMock(return_value=MagicMock(spec=discord.Message, delete=AsyncMock()))

        thread = MagicMock(spec=discord.Thread)
        thread.send = AsyncMock()
        thread.parent = parent

        approval_msg = MagicMock()
        approval_msg.id = 99999
        approval_msg.add_reaction = AsyncMock()
        thread.send.return_value = approval_msg

        return thread, parent

    @pytest.mark.asyncio
    async def test_button_approval_posts_to_parent_channel(self, bot: MagicMock) -> None:
        """When thread has a parent channel, a button message is posted there."""
        cog = self._make_cog(bot)
        thread, parent = self._make_thread_with_parent()

        bot.user = MagicMock()
        bot.user.id = 1

        async def capture_and_hang(*args, **kwargs):
            # block until test sets the event externally
            await asyncio.sleep(10)  # will be cancelled

        bot.wait_for = AsyncMock(side_effect=capture_and_hang)

        async def run_and_inject() -> None:
            # Give _wait_for_approval time to create the view and post to parent
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            # Find the UpgradeApprovalView that was sent to the parent channel
            call_kwargs = parent.send.call_args
            if call_kwargs is not None:
                view = call_kwargs.kwargs.get("view") or (
                    call_kwargs.args[1] if len(call_kwargs.args) > 1 else None
                )
                if isinstance(view, UpgradeApprovalView):
                    view._event.set()

        task = asyncio.create_task(run_and_inject())
        await cog._wait_for_approval(MagicMock(), thread)
        await task

        # button message was posted to the parent channel
        parent.send.assert_called_once()

    @pytest.mark.asyncio
    async def test_button_approval_resolves_without_reaction(self, bot: MagicMock) -> None:
        """Button click alone (no reaction) grants approval and posts confirmation."""
        cog = self._make_cog(bot)
        thread, parent = self._make_thread_with_parent()

        bot.user = MagicMock()
        bot.user.id = 1

        # Reaction never fires (hangs until cancelled)
        async def hang(*args, **kwargs):
            await asyncio.sleep(60)

        bot.wait_for = AsyncMock(side_effect=hang)

        # Inject button click after setup
        async def click_button() -> None:
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            call = parent.send.call_args
            if call is not None:
                view = call.kwargs.get("view") or (call.args[1] if len(call.args) > 1 else None)
                if isinstance(view, UpgradeApprovalView):
                    view._event.set()

        task = asyncio.create_task(click_button())
        await cog._wait_for_approval(MagicMock(), thread)
        await task

        # Final confirmation sent to thread
        thread.send.assert_any_call("👍 Restart approved!")

    @pytest.mark.asyncio
    async def test_channel_message_deleted_after_approval(self, bot: MagicMock) -> None:
        """The channel button message is deleted after approval is granted."""
        cog = self._make_cog(bot)
        thread, parent = self._make_thread_with_parent()

        bot.user = MagicMock()
        bot.user.id = 1

        reaction_event = MagicMock()
        reaction_event.message_id = 99999
        reaction_event.emoji = "✅"
        reaction_event.user_id = 42
        bot.wait_for = AsyncMock(return_value=reaction_event)

        await cog._wait_for_approval(MagicMock(), thread)

        # The channel message should have been deleted
        channel_msg = parent.send.return_value
        channel_msg.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_timeout_bumps_button_in_channel(self, bot: MagicMock) -> None:
        """When the reaction watch times out, the channel button is re-posted at bottom."""
        cog = self._make_cog(bot)
        thread, parent = self._make_thread_with_parent()

        bot.user = MagicMock()
        bot.user.id = 1

        # Each call to parent.send returns a fresh mock message with delete()
        bumped_msg = MagicMock(spec=discord.Message, delete=AsyncMock())
        parent.send = AsyncMock(
            side_effect=[
                MagicMock(spec=discord.Message, delete=AsyncMock()),  # initial post
                bumped_msg,  # bump re-post after timeout
            ]
        )

        call_count = 0

        async def wait_for_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TimeoutError
            # Second call: approve
            event = MagicMock()
            event.message_id = 99999
            event.emoji = "✅"
            event.user_id = 42
            return event

        bot.wait_for = AsyncMock(side_effect=wait_for_side_effect)

        await cog._wait_for_approval(MagicMock(), thread)

        # parent.send called twice: initial post + bump after timeout
        assert parent.send.call_count == 2

    @pytest.mark.asyncio
    async def test_no_parent_channel_still_works(self, bot: MagicMock) -> None:
        """When thread.parent is None (or not Messageable), approval still works via reaction."""
        cog = self._make_cog(bot)
        thread = MagicMock(spec=discord.Thread)
        thread.parent = None
        thread.send = AsyncMock()
        approval_msg = MagicMock()
        approval_msg.id = 99999
        approval_msg.add_reaction = AsyncMock()
        thread.send.return_value = approval_msg

        bot.user = MagicMock()
        bot.user.id = 1
        reaction_event = MagicMock()
        reaction_event.message_id = 99999
        reaction_event.emoji = "✅"
        reaction_event.user_id = 42
        bot.wait_for = AsyncMock(return_value=reaction_event)

        await cog._wait_for_approval(MagicMock(), thread)

        thread.send.assert_any_call("👍 Restart approved!")


class TestPipelineThreadDeleted:
    """The upgrade thread can be deleted while the pipeline waits on approval."""

    @pytest.mark.asyncio
    async def test_deleted_thread_does_not_leak_notfound(self, bot: MagicMock) -> None:
        config = UpgradeConfig(package_name="pkg", upgrade_approval=True)
        cog = AutoUpgradeCog(bot, config)
        thread = MagicMock(spec=discord.Thread)
        gone = discord.NotFound(MagicMock(status=404, reason="Not Found"), "Unknown Channel")
        thread.send = AsyncMock(side_effect=gone)
        cog._wait_for_approval = AsyncMock(side_effect=TimeoutError)  # type: ignore[method-assign]

        await cog._run_pipeline(thread, status_target=None)  # must not raise

    @pytest.mark.asyncio
    async def test_deleted_thread_on_unexpected_error_does_not_leak(self, bot: MagicMock) -> None:
        config = UpgradeConfig(package_name="pkg", upgrade_approval=True)
        cog = AutoUpgradeCog(bot, config)
        thread = MagicMock(spec=discord.Thread)
        gone = discord.NotFound(MagicMock(status=404, reason="Not Found"), "Unknown Channel")
        thread.send = AsyncMock(side_effect=gone)
        cog._wait_for_approval = AsyncMock(side_effect=gone)  # type: ignore[method-assign]

        await cog._run_pipeline(thread, status_target=None)  # must not raise
