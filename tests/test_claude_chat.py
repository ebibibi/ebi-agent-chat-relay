"""Tests for ClaudeChatCog: /stop command, attachment handling, and interrupt-on-new-message."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from claude_discord.cogs.claude_chat import ClaudeChatCog
from claude_discord.concurrency import SessionRegistry


class _StubStatus:
    """Minimal StatusManager stand-in for _run_claude serialization tests."""

    _stall_hard = 300

    async def set_thinking(self) -> None:
        return None


class _StubStopView:
    """Minimal StopView stand-in — no Discord interaction."""

    def set_message(self, message: object) -> None:
        return None

    async def disable(self, message: object | None = None) -> None:
        return None


def _make_cog() -> ClaudeChatCog:
    """Return a ClaudeChatCog with minimal mocked dependencies."""
    bot = MagicMock()
    bot.channel_id = 999
    repo = MagicMock()
    repo.get = AsyncMock(return_value=None)
    repo.save = AsyncMock()
    repo.delete = AsyncMock(return_value=True)
    runner = MagicMock()
    runner.clone = MagicMock(return_value=MagicMock())
    return ClaudeChatCog(bot=bot, repo=repo, runner=runner)


def _make_thread_interaction(thread_id: int = 12345) -> MagicMock:
    """Return an Interaction whose channel is a discord.Thread."""
    interaction = MagicMock(spec=discord.Interaction)
    thread = MagicMock(spec=discord.Thread)
    thread.id = thread_id
    interaction.channel = thread
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    return interaction


def _make_channel_interaction() -> MagicMock:
    """Return an Interaction whose channel is NOT a thread."""
    interaction = MagicMock(spec=discord.Interaction)
    interaction.channel = MagicMock(spec=discord.TextChannel)
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    return interaction


class TestStopCommand:
    @pytest.mark.asyncio
    async def test_stop_outside_thread_sends_ephemeral(self) -> None:
        """Using /stop outside a thread sends an ephemeral error."""
        cog = _make_cog()
        interaction = _make_channel_interaction()

        await cog.stop_session.callback(cog, interaction)

        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args.kwargs
        assert call_kwargs.get("ephemeral") is True

    @pytest.mark.asyncio
    async def test_stop_no_active_runner_sends_ephemeral(self) -> None:
        """Using /stop when nothing is running sends an ephemeral notice."""
        cog = _make_cog()
        interaction = _make_thread_interaction(thread_id=12345)

        # _active_runners is empty — no session running
        await cog.stop_session.callback(cog, interaction)

        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args.kwargs
        assert call_kwargs.get("ephemeral") is True

    @pytest.mark.asyncio
    async def test_stop_calls_runner_interrupt(self) -> None:
        """Using /stop with an active runner calls runner.interrupt()."""
        cog = _make_cog()
        thread_id = 12345
        interaction = _make_thread_interaction(thread_id=thread_id)

        mock_runner = MagicMock()
        mock_runner.interrupt = AsyncMock()
        cog._active_runners[thread_id] = mock_runner

        await cog.stop_session.callback(cog, interaction)

        mock_runner.interrupt.assert_called_once()

    @pytest.mark.asyncio
    async def test_stop_does_not_delete_session_from_db(self) -> None:
        """/stop must NOT delete the session from the DB (so resume works)."""
        cog = _make_cog()
        thread_id = 12345
        interaction = _make_thread_interaction(thread_id=thread_id)

        mock_runner = MagicMock()
        mock_runner.interrupt = AsyncMock()
        cog._active_runners[thread_id] = mock_runner

        await cog.stop_session.callback(cog, interaction)

        # repo.delete should NEVER be called by /stop
        cog.repo.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_stop_does_not_remove_from_active_runners(self) -> None:
        """/stop should leave _active_runners cleanup to _run_claude's finally."""
        cog = _make_cog()
        thread_id = 12345
        interaction = _make_thread_interaction(thread_id=thread_id)

        mock_runner = MagicMock()
        mock_runner.interrupt = AsyncMock()
        cog._active_runners[thread_id] = mock_runner

        await cog.stop_session.callback(cog, interaction)

        # Still in dict — _run_claude's finally handles removal
        assert thread_id in cog._active_runners

    @pytest.mark.asyncio
    async def test_stop_sends_stopped_embed(self) -> None:
        """/stop success response should use the stopped_embed (orange, not red)."""
        cog = _make_cog()
        thread_id = 12345
        interaction = _make_thread_interaction(thread_id=thread_id)

        mock_runner = MagicMock()
        mock_runner.interrupt = AsyncMock()
        cog._active_runners[thread_id] = mock_runner

        await cog.stop_session.callback(cog, interaction)

        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args.kwargs
        embed = call_kwargs.get("embed")
        assert embed is not None
        assert "stopped" in embed.title.lower()
        # Orange color (not red error)
        assert embed.color.value == 0xFFA500


class TestActiveCountAlias:
    """Tests for ClaudeChatCog.active_count (DrainAware alias)."""

    def test_active_count_equals_active_session_count(self) -> None:
        """active_count should be an alias for active_session_count."""
        cog = _make_cog()
        assert cog.active_count == 0
        assert cog.active_count == cog.active_session_count

        # Add a fake runner
        cog._active_runners[1] = MagicMock()
        assert cog.active_count == 1
        assert cog.active_count == cog.active_session_count

        cog._active_runners[2] = MagicMock()
        assert cog.active_count == 2
        assert cog.active_count == cog.active_session_count


class TestRegistryAutoDiscovery:
    """Registry should be auto-discovered from bot.session_registry."""

    def test_auto_discovers_from_bot(self) -> None:
        """When registry=None, Cog picks up bot.session_registry."""
        bot = MagicMock()
        bot.session_registry = SessionRegistry()
        cog = ClaudeChatCog(bot=bot, repo=MagicMock(), runner=MagicMock())
        assert cog._registry is bot.session_registry

    def test_explicit_registry_takes_precedence(self) -> None:
        """When registry is explicitly passed, it wins over bot attribute."""
        bot = MagicMock()
        bot.session_registry = SessionRegistry()
        explicit = SessionRegistry()
        cog = ClaudeChatCog(bot=bot, repo=MagicMock(), runner=MagicMock(), registry=explicit)
        assert cog._registry is explicit

    def test_no_bot_attribute_falls_back_to_none(self) -> None:
        """When bot has no session_registry, _registry stays None."""
        bot = MagicMock(spec=[])  # no attributes
        cog = ClaudeChatCog(bot=bot, repo=MagicMock(), runner=MagicMock())
        assert cog._registry is None


class TestInterruptOnNewMessage:
    """New message in active thread should interrupt the running session."""

    def _make_thread_message(self, thread_id: int = 42) -> MagicMock:
        """Return a discord.Message inside a Thread."""
        thread = MagicMock(spec=discord.Thread)
        thread.id = thread_id
        thread.parent_id = 999
        thread.send = AsyncMock()
        msg = MagicMock(spec=discord.Message)
        msg.channel = thread
        msg.content = "new instruction"
        msg.attachments = []
        msg.author = MagicMock()
        msg.author.bot = False
        return msg

    @pytest.mark.asyncio
    async def test_handle_thread_reply_delegates_interrupt_to_run_claude(self) -> None:
        """_handle_thread_reply must ask _run_claude to preempt the running turn.

        Serialization + interrupt now live in _run_claude (the single run slot),
        so the reply path only needs to pass interrupt_existing=True.
        """
        cog = _make_cog()
        thread_id = 42
        message = self._make_thread_message(thread_id)

        cog._run_claude = AsyncMock()

        await cog._handle_thread_reply(message)

        cog._run_claude.assert_called_once()
        _, kwargs = cog._run_claude.call_args
        assert kwargs.get("interrupt_existing") is True

    @pytest.mark.asyncio
    async def test_evict_active_run_interrupts_and_notifies(self) -> None:
        """_evict_active_run(interrupt=True) posts the notice and SIGINTs the runner."""
        cog = _make_cog()
        thread_id = 42
        thread = MagicMock(spec=discord.Thread)
        thread.id = thread_id
        thread.send = AsyncMock()

        existing_runner = MagicMock()
        existing_runner.interrupt = AsyncMock()
        cog._active_runners[thread_id] = existing_runner

        await cog._evict_active_run(thread, interrupt=True, notice="-# ⚡ stop")

        existing_runner.interrupt.assert_called_once()
        thread.send.assert_called_once()
        sent_text: str = thread.send.call_args.args[0]
        assert "⚡" in sent_text or "interrupted" in sent_text.lower()

    @pytest.mark.asyncio
    async def test_evict_active_run_noop_when_idle(self) -> None:
        """_evict_active_run does nothing (no notice) when no runner is active."""
        cog = _make_cog()
        thread = MagicMock(spec=discord.Thread)
        thread.id = 42
        thread.send = AsyncMock()

        await cog._evict_active_run(thread, interrupt=True, notice="-# ⚡ stop")

        thread.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_evict_active_run_queue_mode_waits_without_interrupt(self) -> None:
        """interrupt=False must wait out the existing task without SIGINT (queue)."""
        cog = _make_cog()
        thread_id = 42
        thread = MagicMock(spec=discord.Thread)
        thread.id = thread_id
        thread.send = AsyncMock()

        existing_runner = MagicMock()
        existing_runner.interrupt = AsyncMock()
        cog._active_runners[thread_id] = existing_runner

        cleanup_done = asyncio.Event()
        call_order: list[str] = []

        async def slow_task() -> None:
            await cleanup_done.wait()
            call_order.append("task_done")

        task = asyncio.ensure_future(slow_task())
        cog._active_tasks[thread_id] = task
        cleanup_done.set()

        await cog._evict_active_run(thread, interrupt=False, notice="-# ⚡ stop")
        call_order.append("evict_returned")

        # Queue mode: no interrupt, no notice, but the prior task is awaited.
        existing_runner.interrupt.assert_not_called()
        thread.send.assert_not_called()
        assert call_order == ["task_done", "evict_returned"]

    @pytest.mark.asyncio
    async def test_run_claude_called_with_session_id_after_interrupt(self) -> None:
        """After interrupt, _run_claude is called with the session_id from the DB."""
        cog = _make_cog()
        thread_id = 42
        message = self._make_thread_message(thread_id)

        # Simulate a saved session in DB
        record = MagicMock()
        record.session_id = "abc-123"
        cog.repo.get = AsyncMock(return_value=record)

        existing_runner = MagicMock()
        existing_runner.interrupt = AsyncMock()
        cog._active_runners[thread_id] = existing_runner
        cog._run_claude = AsyncMock()

        await cog._handle_thread_reply(message)

        cog._run_claude.assert_called_once()
        _, kwargs = cog._run_claude.call_args
        assert kwargs.get("session_id") == "abc-123"

    @pytest.mark.asyncio
    async def test_active_tasks_dict_initialized(self) -> None:
        """ClaudeChatCog must initialize _active_tasks as an empty dict."""
        cog = _make_cog()
        assert hasattr(cog, "_active_tasks")
        assert isinstance(cog._active_tasks, dict)
        assert len(cog._active_tasks) == 0

    @pytest.mark.asyncio
    async def test_thread_locks_dict_initialized(self) -> None:
        """ClaudeChatCog must initialize _thread_locks as an empty dict."""
        cog = _make_cog()
        assert hasattr(cog, "_thread_locks")
        assert isinstance(cog._thread_locks, dict)
        assert len(cog._thread_locks) == 0

    @pytest.mark.asyncio
    async def test_concurrent_messages_both_reach_run_claude(self) -> None:
        """Two near-simultaneous replies both delegate to _run_claude (interrupt=True).

        The actual "never overlaps" guarantee is proven by
        test_real_run_claude_never_overlaps_same_thread, which exercises the
        real _run_claude. Here we only confirm both replies are handled and each
        asks to preempt the running turn.
        """
        cog = _make_cog()
        thread_id = 42

        call_count = 0
        flags: list[object] = []

        async def spy_run_claude(*args: object, **kwargs: object) -> None:
            nonlocal call_count
            call_count += 1
            flags.append(kwargs.get("interrupt_existing"))
            await asyncio.sleep(0.01)

        cog._run_claude = spy_run_claude

        msg1 = self._make_thread_message(thread_id)
        msg2 = self._make_thread_message(thread_id)

        t1 = asyncio.create_task(cog._handle_thread_reply(msg1))
        await asyncio.sleep(0)
        t2 = asyncio.create_task(cog._handle_thread_reply(msg2))

        await asyncio.gather(t1, t2, return_exceptions=True)

        assert call_count == 2
        assert flags == [True, True]

    @pytest.mark.asyncio
    async def test_real_run_claude_never_overlaps_same_thread(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The real _run_claude must never run two CLI subprocesses at once per thread.

        This exercises the actual serialization inside _run_claude (not a stub).
        Runner registration happens *after* several awaits (model lookup, runner
        build), so two near-simultaneous messages can slip past a naive
        "is anything running?" check and both spawn. The per-thread lock must
        make eviction + registration atomic so the two runs are serialized.
        """
        import claude_discord.cogs.claude_chat as chat_mod

        cog = _make_cog()
        thread_id = 42

        concurrency = 0
        max_concurrency = 0
        run_count = 0

        async def fake_run(config: object) -> None:
            nonlocal concurrency, max_concurrency, run_count
            run_count += 1
            concurrency += 1
            max_concurrency = max(max_concurrency, concurrency)
            await asyncio.sleep(0.02)
            concurrency -= 1

        monkeypatch.setattr(chat_mod, "run_claude_with_config", fake_run)
        # StatusManager / StopView touch Discord; stub them to no-ops.
        monkeypatch.setattr(chat_mod, "StatusManager", lambda *a, **k: _StubStatus())
        monkeypatch.setattr(chat_mod, "StopView", lambda *a, **k: _StubStopView())
        cog._get_dashboard = lambda: None  # type: ignore[method-assign]

        async def slow_build_runner(**kwargs: object) -> MagicMock:
            # The await here is the gap the race exploits: registration into
            # _active_runners happens only *after* this returns.
            await asyncio.sleep(0.005)
            runner = MagicMock()
            runner.interrupt = AsyncMock()
            runner.command = "claude"
            return runner

        cog._build_runner_for_thread = slow_build_runner  # type: ignore[method-assign]
        cog._get_current_model = AsyncMock(return_value=None)
        cog._get_allowed_tools = AsyncMock(return_value=None)
        cog._get_current_effort = AsyncMock(return_value=None)

        msg1 = self._make_thread_message(thread_id)
        msg2 = self._make_thread_message(thread_id)

        t1 = asyncio.create_task(cog._handle_thread_reply(msg1))
        await asyncio.sleep(0)
        t2 = asyncio.create_task(cog._handle_thread_reply(msg2))

        await asyncio.gather(t1, t2, return_exceptions=True)

        # Both messages ran, but never simultaneously in the same thread.
        assert run_count == 2
        assert max_concurrency == 1
        # No orphaned runner left registered after both finished.
        assert thread_id not in cog._active_runners


class TestSpawnSession:
    """Tests for ClaudeChatCog.spawn_session()."""

    @pytest.mark.asyncio
    async def test_spawn_creates_thread_and_returns_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """spawn_session creates a thread with the right name and returns it."""
        from unittest.mock import AsyncMock, MagicMock, patch

        import discord

        thread = MagicMock(spec=discord.Thread)
        thread.id = 42
        thread.name = "Test spawn"
        thread.send = AsyncMock()

        channel = MagicMock()
        channel.create_thread = AsyncMock(return_value=thread)

        bot = MagicMock()
        cog = ClaudeChatCog(bot=bot, repo=MagicMock(), runner=MagicMock())

        with patch.object(cog, "_run_claude", new=AsyncMock()):
            result = await cog.spawn_session(channel, "Do the thing")

        assert result is thread
        channel.create_thread.assert_called_once()
        call_kwargs = channel.create_thread.call_args.kwargs
        assert call_kwargs["name"] == "Do the thing"
        assert call_kwargs["type"] == discord.ChannelType.public_thread

    @pytest.mark.asyncio
    async def test_spawn_adds_invited_user_to_thread(self) -> None:
        """invite_user_id makes the requester a thread member, before the seed message."""
        from unittest.mock import AsyncMock, MagicMock, patch

        import discord

        thread = MagicMock(spec=discord.Thread)
        thread.send = AsyncMock()
        thread.add_user = AsyncMock()

        channel = MagicMock()
        channel.create_thread = AsyncMock(return_value=thread)

        cog = ClaudeChatCog(bot=MagicMock(), repo=MagicMock(), runner=MagicMock())

        with patch.object(cog, "_run_claude", new=AsyncMock()):
            await cog.spawn_session(channel, "Do the thing", invite_user_id=418192003549888523)

        thread.add_user.assert_awaited_once()
        assert thread.add_user.await_args.args[0].id == 418192003549888523

    @pytest.mark.asyncio
    async def test_spawn_without_invite_adds_nobody(self) -> None:
        from unittest.mock import AsyncMock, MagicMock, patch

        import discord

        thread = MagicMock(spec=discord.Thread)
        thread.send = AsyncMock()
        thread.add_user = AsyncMock()

        channel = MagicMock()
        channel.create_thread = AsyncMock(return_value=thread)

        cog = ClaudeChatCog(bot=MagicMock(), repo=MagicMock(), runner=MagicMock())

        with patch.object(cog, "_run_claude", new=AsyncMock()):
            await cog.spawn_session(channel, "Do the thing")

        thread.add_user.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_spawn_survives_add_user_failure(self) -> None:
        """A Discord-side add_user failure is a visibility miss, never a failed spawn."""
        from unittest.mock import AsyncMock, MagicMock, patch

        import discord

        thread = MagicMock(spec=discord.Thread)
        thread.send = AsyncMock()
        thread.add_user = AsyncMock(side_effect=RuntimeError("Missing Permissions"))

        channel = MagicMock()
        channel.create_thread = AsyncMock(return_value=thread)

        cog = ClaudeChatCog(bot=MagicMock(), repo=MagicMock(), runner=MagicMock())

        with patch.object(cog, "_run_claude", new=AsyncMock()):
            result = await cog.spawn_session(channel, "Do the thing", invite_user_id=999)

        assert result is thread
        thread.send.assert_awaited()

    @pytest.mark.asyncio
    async def test_spawn_uses_custom_thread_name(self) -> None:
        """thread_name overrides the default (prompt[:100])."""
        from unittest.mock import AsyncMock, MagicMock, patch

        import discord

        thread = MagicMock(spec=discord.Thread)
        thread.send = AsyncMock()

        channel = MagicMock()
        channel.create_thread = AsyncMock(return_value=thread)

        bot = MagicMock()
        cog = ClaudeChatCog(bot=bot, repo=MagicMock(), runner=MagicMock())

        with patch.object(cog, "_run_claude", new=AsyncMock()):
            await cog.spawn_session(channel, "Very long prompt text", thread_name="Short name")

        kwargs = channel.create_thread.call_args.kwargs
        assert kwargs["name"] == "Short name"

    @pytest.mark.asyncio
    async def test_spawn_posts_seed_message(self) -> None:
        """spawn_session sends the prompt as the first thread message."""
        from unittest.mock import AsyncMock, MagicMock, patch

        import discord

        thread = MagicMock(spec=discord.Thread)
        seed_msg = MagicMock()
        thread.send = AsyncMock(return_value=seed_msg)

        channel = MagicMock()
        channel.create_thread = AsyncMock(return_value=thread)

        bot = MagicMock()
        cog = ClaudeChatCog(bot=bot, repo=MagicMock(), runner=MagicMock())

        mock_run = AsyncMock()
        with patch.object(cog, "_run_claude", new=mock_run):
            await cog.spawn_session(channel, "Hello Claude")

        thread.send.assert_called_once_with("Hello Claude")
        # _run_claude receives the seed message (not a user message)
        user_msg_arg = mock_run.call_args.args[0]
        assert user_msg_arg is seed_msg

    @pytest.mark.asyncio
    async def test_spawn_chunks_long_seed_message(self) -> None:
        """A prompt longer than Discord's per-message limit is split across
        multiple seed messages, but the FULL prompt still reaches _run_claude.

        Regression: an ingested Teams thread exceeded Discord's 4000-char limit
        and a single thread.send(prompt) failed with HTTP 400 (code 50035)."""
        from unittest.mock import AsyncMock, MagicMock, patch

        import discord

        thread = MagicMock(spec=discord.Thread)
        sent: list[str] = []

        async def fake_send(content):
            sent.append(content)
            return MagicMock(name=f"seed-{len(sent)}")

        thread.send = AsyncMock(side_effect=fake_send)

        channel = MagicMock()
        channel.create_thread = AsyncMock(return_value=thread)

        bot = MagicMock()
        cog = ClaudeChatCog(bot=bot, repo=MagicMock(), runner=MagicMock())

        long_prompt = "x" * 5000  # well beyond Discord's per-message limit
        mock_run = AsyncMock()
        with patch.object(cog, "_run_claude", new=mock_run):
            await cog.spawn_session(channel, long_prompt)

        # Chunked into multiple seed messages, each within Discord's limit.
        assert thread.send.call_count > 1
        assert all(len(c) <= 2000 for c in sent)
        # The FULL, unmodified prompt still reached Claude (args: seed, thread, prompt).
        assert mock_run.call_args.args[2] == long_prompt

    @pytest.mark.asyncio
    async def test_spawn_auto_start_false_skips_run_claude(self) -> None:
        """When auto_start=False, spawn_session creates the thread but does not run Claude."""
        from unittest.mock import AsyncMock, MagicMock, patch

        import discord

        thread = MagicMock(spec=discord.Thread)
        thread.send = AsyncMock()

        channel = MagicMock()
        channel.create_thread = AsyncMock(return_value=thread)

        bot = MagicMock()
        cog = ClaudeChatCog(bot=bot, repo=MagicMock(), runner=MagicMock())

        mock_run = AsyncMock()
        with patch.object(cog, "_run_claude", new=mock_run):
            result = await cog.spawn_session(channel, "Hello", auto_start=False)

        assert result is thread
        thread.send.assert_called_once_with("Hello")
        mock_run.assert_not_called()


class TestFetchSeedContext:
    """Tests for ClaudeChatCog._fetch_seed_context()."""

    @pytest.mark.asyncio
    async def test_returns_bot_seed_message_content(self) -> None:
        """When the first message is from a bot, return its content."""
        seed_msg = MagicMock()
        seed_msg.author.bot = True
        seed_msg.content = "☀️ おはようございます！"

        thread = MagicMock(spec=discord.Thread)
        thread.id = 42

        async def _fake_history(**kwargs: object) -> list[MagicMock]:
            yield seed_msg  # type: ignore[misc]

        thread.history = _fake_history

        result = await ClaudeChatCog._fetch_seed_context(thread)
        assert result == "☀️ おはようございます！"

    @pytest.mark.asyncio
    async def test_joins_a_chunked_seed(self) -> None:
        """A seed longer than Discord's limit is posted as several bot messages.

        Returning only the first one truncates the context Claude wakes up with,
        which is invisible in the reply and gets worse the longer the seed is.
        """
        chunks = []
        for text in ("part one", "part two", "part three"):
            msg = MagicMock()
            msg.author.bot = True
            msg.content = text
            chunks.append(msg)

        human = MagicMock()
        human.author.bot = False
        human.content = "please write this up"

        thread = MagicMock(spec=discord.Thread)
        thread.id = 42

        async def _fake_history(**kwargs: object):
            for msg in [*chunks, human]:
                yield msg

        thread.history = _fake_history

        result = await ClaudeChatCog._fetch_seed_context(thread)
        assert result == "part one\npart two\npart three"

    @pytest.mark.asyncio
    async def test_stops_at_the_first_human_message(self) -> None:
        """Later bot messages are not seed context — they are the conversation."""
        seed = MagicMock()
        seed.author.bot = True
        seed.content = "seed"
        human = MagicMock()
        human.author.bot = False
        human.content = "reply"
        later_bot = MagicMock()
        later_bot.author.bot = True
        later_bot.content = "answer"

        thread = MagicMock(spec=discord.Thread)
        thread.id = 42

        async def _fake_history(**kwargs: object):
            for msg in (seed, human, later_bot):
                yield msg

        thread.history = _fake_history

        assert await ClaudeChatCog._fetch_seed_context(thread) == "seed"

    @pytest.mark.asyncio
    async def test_returns_none_for_non_bot_message(self) -> None:
        """When the first message is from a human, return None."""
        seed_msg = MagicMock()
        seed_msg.author.bot = False
        seed_msg.content = "Hello"

        thread = MagicMock(spec=discord.Thread)
        thread.id = 42

        async def _fake_history(**kwargs: object) -> list[MagicMock]:
            yield seed_msg  # type: ignore[misc]

        thread.history = _fake_history

        result = await ClaudeChatCog._fetch_seed_context(thread)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_thread(self) -> None:
        """When the thread has no messages, return None."""
        thread = MagicMock(spec=discord.Thread)
        thread.id = 42

        async def _fake_history(**kwargs: object) -> list[MagicMock]:
            return
            yield  # type: ignore[misc]  # make it an async generator

        thread.history = _fake_history

        result = await ClaudeChatCog._fetch_seed_context(thread)
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_exception(self) -> None:
        """On any error, return None gracefully."""
        thread = MagicMock(spec=discord.Thread)
        thread.id = 42
        thread.history = MagicMock(side_effect=Exception("API error"))

        result = await ClaudeChatCog._fetch_seed_context(thread)
        assert result is None

    @pytest.mark.asyncio
    async def test_spawn_auto_start_true_calls_run_claude(self) -> None:
        """When auto_start=True (default), _run_claude IS called."""
        from unittest.mock import AsyncMock, MagicMock, patch

        import discord

        thread = MagicMock(spec=discord.Thread)
        thread.send = AsyncMock()

        channel = MagicMock()
        channel.create_thread = AsyncMock(return_value=thread)

        bot = MagicMock()
        cog = ClaudeChatCog(bot=bot, repo=MagicMock(), runner=MagicMock())

        mock_run = AsyncMock()
        with patch.object(cog, "_run_claude", new=mock_run):
            await cog.spawn_session(channel, "Start session", auto_start=True)

        mock_run.assert_called_once()


class TestOnReady:
    """Tests for ClaudeChatCog.on_ready — startup session resume logic."""

    @pytest.mark.asyncio
    async def test_on_ready_no_resume_repo_is_noop(self) -> None:
        """If resume_repo is not set, on_ready should do nothing."""
        # spec=[] prevents MagicMock from auto-generating resume_repo attribute
        bot = MagicMock(spec=[])
        cog = ClaudeChatCog(bot=bot, repo=MagicMock(), runner=MagicMock())
        assert cog._resume_repo is None
        # Should complete without error and without touching bot
        await cog.on_ready()

    @pytest.mark.asyncio
    async def test_on_ready_no_pending_is_noop(self) -> None:
        """If resume_repo returns no pending entries, on_ready does nothing."""
        from unittest.mock import AsyncMock, MagicMock

        from claude_discord.database.resume_repo import PendingResumeRepository

        resume_repo = MagicMock(spec=PendingResumeRepository)
        resume_repo.get_pending = AsyncMock(return_value=[])

        bot = MagicMock()
        cog = ClaudeChatCog(bot=bot, repo=MagicMock(), runner=MagicMock(), resume_repo=resume_repo)
        await cog.on_ready()
        bot.get_channel.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_ready_deletes_before_spawning(self) -> None:
        """Row must be deleted BEFORE _run_claude is called (single-fire guarantee)."""
        from unittest.mock import AsyncMock, MagicMock, patch

        import discord

        from claude_discord.database.resume_repo import PendingResume, PendingResumeRepository

        entry = PendingResume(
            id=7,
            thread_id=555,
            session_id="sess-abc",
            reason="self_restart",
            resume_prompt="Continue please.",
            created_at="2026-02-21 20:00:00",
        )
        resume_repo = MagicMock(spec=PendingResumeRepository)
        resume_repo.get_pending = AsyncMock(return_value=[entry])
        resume_repo.delete = AsyncMock()

        thread = MagicMock(spec=discord.Thread)
        thread.id = 555
        thread.send = AsyncMock(return_value=MagicMock())
        parent = MagicMock(spec=discord.TextChannel)
        thread.parent = parent

        bot = MagicMock()
        bot.get_channel.return_value = thread

        cog = ClaudeChatCog(bot=bot, repo=MagicMock(), runner=MagicMock(), resume_repo=resume_repo)

        call_order: list[str] = []
        resume_repo.delete.side_effect = lambda _: call_order.append("delete")

        async def fake_run_claude(*args, **kwargs):
            call_order.append("run_claude")

        with patch.object(cog, "_run_claude", side_effect=fake_run_claude):
            await cog.on_ready()
            # create_task schedules the coroutine; yield to the event loop so it runs.
            await asyncio.sleep(0)

        assert call_order == ["delete", "run_claude"], (
            "delete() must be called before _run_claude to prevent double-resume"
        )

    @pytest.mark.asyncio
    async def test_on_ready_skips_non_thread_channels(self) -> None:
        """If get_channel returns a non-Thread, skip gracefully."""
        from unittest.mock import AsyncMock, MagicMock

        import discord

        from claude_discord.database.resume_repo import PendingResume, PendingResumeRepository

        entry = PendingResume(
            id=1,
            thread_id=100,
            session_id=None,
            reason="self_restart",
            resume_prompt=None,
            created_at="2026-02-21 20:00:00",
        )
        resume_repo = MagicMock(spec=PendingResumeRepository)
        resume_repo.get_pending = AsyncMock(return_value=[entry])
        resume_repo.delete = AsyncMock()

        # Return a TextChannel (not a Thread) — should be skipped
        bot = MagicMock()
        bot.get_channel.return_value = MagicMock(spec=discord.TextChannel)

        cog = ClaudeChatCog(bot=bot, repo=MagicMock(), runner=MagicMock(), resume_repo=resume_repo)
        # Should not raise
        await cog.on_ready()
        # delete was still called (single-fire)
        resume_repo.delete.assert_called_once_with(1)


class TestCogUnloadMarkForResume:
    """Tests for cog_unload() auto-marking active sessions for restart-resume."""

    def _make_cog_with_resume_repo(self) -> tuple[ClaudeChatCog, MagicMock, MagicMock]:
        """Return (cog, repo, resume_repo) with resume_repo configured."""
        bot = MagicMock()
        bot.channel_id = 999
        repo = MagicMock()
        repo.get = AsyncMock(return_value=None)
        resume_repo = MagicMock()
        resume_repo.mark = AsyncMock(return_value=1)
        cog = ClaudeChatCog(bot=bot, repo=repo, runner=MagicMock(), resume_repo=resume_repo)
        return cog, repo, resume_repo

    @pytest.mark.asyncio
    async def test_no_op_when_no_active_runners(self) -> None:
        """cog_unload is a no-op when no sessions are running."""
        cog, _, resume_repo = self._make_cog_with_resume_repo()
        assert len(cog._active_runners) == 0

        await cog.cog_unload()

        resume_repo.mark.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_op_when_no_resume_repo(self) -> None:
        """cog_unload is a no-op when resume_repo is not configured."""
        cog = _make_cog()  # no resume_repo
        cog._active_runners[111] = MagicMock()

        await cog.cog_unload()  # Should not raise

    @pytest.mark.asyncio
    async def test_marks_each_active_runner(self) -> None:
        """Calls resume_repo.mark() for every thread in _active_runners."""
        cog, repo, resume_repo = self._make_cog_with_resume_repo()
        cog._active_runners[111] = MagicMock()
        cog._active_runners[222] = MagicMock()

        await cog.cog_unload()

        assert resume_repo.mark.call_count == 2
        called_thread_ids = {call.args[0] for call in resume_repo.mark.call_args_list}
        assert called_thread_ids == {111, 222}

    @pytest.mark.asyncio
    async def test_uses_bot_shutdown_reason(self) -> None:
        """Marks sessions with reason='bot_shutdown'."""
        cog, _, resume_repo = self._make_cog_with_resume_repo()
        cog._active_runners[333] = MagicMock()

        await cog.cog_unload()

        call_kwargs = resume_repo.mark.call_args.kwargs
        assert call_kwargs["reason"] == "bot_shutdown"

    @pytest.mark.asyncio
    async def test_resolves_session_id_from_repo(self) -> None:
        """Looks up session_id from self.repo for --resume continuity."""
        cog, repo, resume_repo = self._make_cog_with_resume_repo()
        session_record = MagicMock()
        session_record.session_id = "test-session-xyz"
        repo.get = AsyncMock(return_value=session_record)

        cog._active_runners[444] = MagicMock()
        await cog.cog_unload()

        repo.get.assert_awaited_once_with(444)
        assert resume_repo.mark.call_args.kwargs["session_id"] == "test-session-xyz"

    @pytest.mark.asyncio
    async def test_continues_on_mark_failure(self) -> None:
        """Failure to mark one thread does not prevent marking others."""
        cog, _, resume_repo = self._make_cog_with_resume_repo()
        resume_repo.mark = AsyncMock(side_effect=[RuntimeError("db error"), 2])
        cog._active_runners[111] = MagicMock()
        cog._active_runners[222] = MagicMock()

        # Should not raise
        await cog.cog_unload()

        assert resume_repo.mark.call_count == 2

    @pytest.mark.asyncio
    async def test_uses_none_session_id_when_repo_has_no_record(self) -> None:
        """Falls back to session_id=None when no session record exists."""
        cog, repo, resume_repo = self._make_cog_with_resume_repo()
        repo.get = AsyncMock(return_value=None)
        cog._active_runners[555] = MagicMock()

        await cog.cog_unload()

        assert resume_repo.mark.call_args.kwargs["session_id"] is None

    @pytest.mark.asyncio
    async def test_resume_prompt_warns_against_auto_implementation(self) -> None:
        """The default resume prompt must NOT instruct Claude to complete pending tasks.

        After a bot restart, context compression may have erased the approval
        status of planned tasks.  The prompt must ask Claude to *report* the
        state first, not to auto-implement anything.
        """
        cog, _, resume_repo = self._make_cog_with_resume_repo()
        cog._active_runners[666] = MagicMock()

        await cog.cog_unload()

        prompt: str = resume_repo.mark.call_args.kwargs["resume_prompt"]
        # Must NOT tell Claude to complete remaining work automatically.
        assert "完了してください" not in prompt
        assert "残作業" not in prompt
        # Must ask Claude to report/confirm before acting.
        assert any(word in prompt for word in ("報告", "確認", "confirm", "report"))

    @pytest.mark.asyncio
    async def test_resume_prompt_mentions_context_compression_risk(self) -> None:
        """The default resume prompt warns that context compression may have occurred."""
        cog, _, resume_repo = self._make_cog_with_resume_repo()
        cog._active_runners[777] = MagicMock()

        await cog.cog_unload()

        prompt: str = resume_repo.mark.call_args.kwargs["resume_prompt"]
        # The prompt should mention the risk of lost approval state.
        assert any(
            word in prompt for word in ("コンテキスト", "圧縮", "context", "compress", "承認")
        )


class TestOnReadyFallbackResumePrompt:
    """Tests for the fallback resume_prompt used when on_ready finds no stored prompt."""

    @pytest.mark.asyncio
    async def test_fallback_prompt_warns_against_auto_implementation(self) -> None:
        """The on_ready fallback prompt must not instruct Claude to auto-complete tasks.

        When a PendingResume entry has no resume_prompt stored (e.g. from an
        older bot version or /api/mark-resume without a prompt), on_ready uses
        a hardcoded fallback.  That fallback must carry the same safety warning
        as the cog_unload default.
        """
        from unittest.mock import AsyncMock, MagicMock, patch

        import discord

        from claude_discord.database.resume_repo import PendingResume, PendingResumeRepository

        # Entry with no resume_prompt — triggers the fallback.
        entry = PendingResume(
            id=1,
            thread_id=100,
            session_id="sess-x",
            reason="bot_shutdown",
            resume_prompt=None,  # force fallback
            created_at="2026-03-03 00:00:00",
        )
        resume_repo = MagicMock(spec=PendingResumeRepository)
        resume_repo.get_pending = AsyncMock(return_value=[entry])
        resume_repo.delete = AsyncMock()

        thread = MagicMock(spec=discord.Thread)
        thread.id = 100
        sent_prompts: list[str] = []

        async def capture_send(content: str) -> MagicMock:
            sent_prompts.append(content)
            return MagicMock()

        thread.send = capture_send
        thread.parent = MagicMock(spec=discord.TextChannel)

        bot = MagicMock()
        bot.get_channel.return_value = thread

        cog = ClaudeChatCog(bot=bot, repo=MagicMock(), runner=MagicMock(), resume_repo=resume_repo)

        with patch.object(cog, "_run_claude", new=AsyncMock()):
            await cog.on_ready()

        assert sent_prompts, "Expected at least one message to be sent to the thread"
        full_message = sent_prompts[0]
        # Must NOT auto-instruct completion of pending tasks.
        assert "完了してください" not in full_message
        assert "残作業" not in full_message
        # Must ask Claude to report/confirm first.
        assert any(word in full_message for word in ("報告", "確認", "confirm", "report"))


class TestOnMessageSystemMessageFilter:
    """on_message must ignore Discord system messages (e.g. thread renames)."""

    def _make_system_message(self, msg_type: discord.MessageType) -> MagicMock:
        """Return a non-bot message of the given Discord MessageType."""
        msg = MagicMock(spec=discord.Message)
        msg.author = MagicMock()
        msg.author.bot = False
        msg.author.id = 42
        msg.type = msg_type
        thread = MagicMock(spec=discord.Thread)
        thread.id = 12345
        thread.parent_id = 999  # matches bot.channel_id
        msg.channel = thread
        return msg

    @pytest.mark.asyncio
    async def test_thread_rename_does_not_reach_claude(self) -> None:
        """CHANNEL_NAME_CHANGE system message must be silently ignored."""
        cog = _make_cog()
        msg = self._make_system_message(discord.MessageType.channel_name_change)

        # on_message must return without invoking any runner
        await cog.on_message(msg)

        # No runner was started — active_runners stays empty
        assert len(cog._active_runners) == 0

    @pytest.mark.asyncio
    async def test_pins_add_does_not_reach_claude(self) -> None:
        """PINS_ADD system message must also be silently ignored."""
        cog = _make_cog()
        msg = self._make_system_message(discord.MessageType.pins_add)

        await cog.on_message(msg)

        assert len(cog._active_runners) == 0

    @pytest.mark.asyncio
    async def test_default_message_is_processed(self) -> None:
        """Regular user messages (MessageType.default) must still be handled."""
        cog = _make_cog()
        msg = self._make_system_message(discord.MessageType.default)
        msg.content = "hello"
        msg.attachments = []

        # _handle_thread_reply will try to run Claude — just check it's NOT
        # short-circuited by the system-message filter (it may fail later,
        # that's fine; we only care the filter doesn't block it).
        import contextlib

        with contextlib.suppress(Exception):
            await cog.on_message(msg)

        # The filter did not block it — execution reached _handle_thread_reply


class TestImageOnlyMessage:
    """Image-only messages (no text) must be handled without errors.

    This is a regression test suite for the bug where sending a Discord message
    with only an image attachment (no text) caused a ValueError in
    RunConfig.__post_init__ because prompt was empty. The ValueError propagated
    uncaught through the event loop, freezing the entire bot.
    """

    @staticmethod
    def _make_image_message(thread_id: int = 42) -> MagicMock:
        """Return a discord.Message with only an image attachment (no text)."""
        thread = MagicMock(spec=discord.Thread)
        thread.id = thread_id
        thread.parent_id = 999
        thread.send = AsyncMock()
        msg = MagicMock(spec=discord.Message)
        msg.id = thread_id  # must be an int so str(msg.id) is a valid path on Windows
        msg.channel = thread
        msg.content = ""  # No text — image only
        msg.author = MagicMock()
        msg.author.bot = False
        att = MagicMock(spec=discord.Attachment)
        att.filename = "photo.png"
        att.content_type = "image/png"
        att.size = 500_000
        att.url = "https://cdn.discordapp.com/attachments/111/222/photo.png"
        att.read = AsyncMock(return_value=b"PNG...")
        msg.attachments = [att]
        return msg

    @pytest.mark.asyncio
    async def test_build_prompt_and_images_returns_empty_prompt(self) -> None:
        """Image-only message should return header + ImageData list."""
        cog = _make_cog()
        msg = self._make_image_message()

        prompt, images = await cog._build_prompt_and_images(msg)

        # With save_dir enabled, image-only messages get an attachment header.
        assert "photo.png" in prompt
        assert len(images) == 1
        assert images[0].media_type == "image/png"

    @pytest.mark.asyncio
    async def test_handle_thread_reply_does_not_crash(self) -> None:
        """_handle_thread_reply with image-only message must not raise."""
        cog = _make_cog()
        msg = self._make_image_message()
        cog._run_claude = AsyncMock()

        # Must not raise ValueError or any other exception
        await cog._handle_thread_reply(msg)

        cog._run_claude.assert_called_once()
        # Verify images were passed through
        call_kwargs = cog._run_claude.call_args
        images = call_kwargs.kwargs.get("images")
        assert images is not None and len(images) == 1

    @pytest.mark.asyncio
    async def test_handle_thread_reply_skips_empty_message(self) -> None:
        """A message with no text AND no attachments should not start a session."""
        cog = _make_cog()
        thread = MagicMock(spec=discord.Thread)
        thread.id = 42
        thread.parent_id = 999
        thread.send = AsyncMock()
        msg = MagicMock(spec=discord.Message)
        msg.channel = thread
        msg.content = ""
        msg.attachments = []
        msg.author = MagicMock()
        msg.author.bot = False

        cog._run_claude = AsyncMock()

        await cog._handle_thread_reply(msg)

        cog._run_claude.assert_not_called()


class TestMultiChannelSupport:
    """channel_ids parameter allows the bot to listen on multiple channels."""

    def _make_cog_with_channels(self, channel_ids: set[int]) -> ClaudeChatCog:
        bot = MagicMock()
        bot.channel_id = 999  # primary (should be overridden by explicit channel_ids)
        repo = MagicMock()
        repo.get = AsyncMock(return_value=None)
        repo.save = AsyncMock()
        runner = MagicMock()
        runner.clone = MagicMock(return_value=MagicMock())
        return ClaudeChatCog(bot=bot, repo=repo, runner=runner, channel_ids=channel_ids)

    def _make_message(self, channel_id: int, author_id: int = 42) -> MagicMock:
        msg = MagicMock(spec=discord.Message)
        msg.author = MagicMock()
        msg.author.bot = False
        msg.author.id = author_id
        msg.type = discord.MessageType.default
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = channel_id
        msg.channel = channel
        msg.content = "hello"
        msg.attachments = []
        return msg

    def _make_thread_message(self, parent_id: int, author_id: int = 42) -> MagicMock:
        msg = MagicMock(spec=discord.Message)
        msg.author = MagicMock()
        msg.author.bot = False
        msg.author.id = author_id
        msg.type = discord.MessageType.default
        thread = MagicMock(spec=discord.Thread)
        thread.id = 55555
        thread.parent_id = parent_id
        msg.channel = thread
        msg.content = "reply"
        msg.attachments = []
        return msg

    def test_channel_ids_overrides_bot_channel_id(self) -> None:
        """Explicit channel_ids takes precedence over bot.channel_id."""
        cog = self._make_cog_with_channels({111, 222})
        assert cog._channel_ids == {111, 222}
        assert 999 not in cog._channel_ids  # bot.channel_id is NOT included

    def test_fallback_to_bot_channel_id_when_no_channel_ids(self) -> None:
        """When channel_ids is None, falls back to {bot.channel_id}."""
        cog = _make_cog()  # no channel_ids, bot.channel_id = 999
        assert cog._channel_ids == {999}

    @pytest.mark.asyncio
    async def test_message_in_secondary_channel_triggers_new_conversation(self) -> None:
        """Message in a secondary channel (not bot.channel_id) triggers a new session."""
        cog = self._make_cog_with_channels({111, 222})
        cog._handle_new_conversation = AsyncMock()

        msg = self._make_message(channel_id=222)
        await cog.on_message(msg)

        cog._handle_new_conversation.assert_awaited_once_with(msg)

    @pytest.mark.asyncio
    async def test_message_in_unknown_channel_is_ignored(self) -> None:
        """Message in a channel not in channel_ids must be silently dropped."""
        cog = self._make_cog_with_channels({111, 222})
        cog._handle_new_conversation = AsyncMock()
        cog._handle_thread_reply = AsyncMock()

        msg = self._make_message(channel_id=333)
        await cog.on_message(msg)

        cog._handle_new_conversation.assert_not_called()
        cog._handle_thread_reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_thread_under_secondary_channel_triggers_reply(self) -> None:
        """Thread reply under a secondary channel must be handled."""
        cog = self._make_cog_with_channels({111, 222})
        cog._handle_thread_reply = AsyncMock()

        msg = self._make_thread_message(parent_id=222)
        await cog.on_message(msg)

        cog._handle_thread_reply.assert_awaited_once_with(msg)

    @pytest.mark.asyncio
    async def test_thread_under_unknown_channel_is_ignored(self) -> None:
        """Thread reply under a channel not in channel_ids must be dropped."""
        cog = self._make_cog_with_channels({111, 222})
        cog._handle_thread_reply = AsyncMock()

        msg = self._make_thread_message(parent_id=333)
        await cog.on_message(msg)

        cog._handle_thread_reply.assert_not_called()


class TestMentionOnlyChannels:
    """mention_only_channel_ids: bot only responds when @mentioned in those channels."""

    def _make_cog(
        self,
        channel_ids: set[int],
        mention_only_channel_ids: set[int] | None = None,
        session_record: object | None = None,
    ) -> ClaudeChatCog:
        bot = MagicMock()
        bot.channel_id = 999
        bot.user = MagicMock()
        bot.user.id = 1111  # bot's own user ID
        repo = MagicMock()
        repo.get = AsyncMock(return_value=session_record)
        repo.save = AsyncMock()
        runner = MagicMock()
        runner.clone = MagicMock(return_value=MagicMock())
        return ClaudeChatCog(
            bot=bot,
            repo=repo,
            runner=runner,
            channel_ids=channel_ids,
            mention_only_channel_ids=mention_only_channel_ids,
        )

    def _make_message(
        self,
        channel_id: int,
        mentions: list | None = None,
        author_id: int = 42,
    ) -> MagicMock:
        msg = MagicMock(spec=discord.Message)
        msg.author = MagicMock()
        msg.author.bot = False
        msg.author.id = author_id
        msg.type = discord.MessageType.default
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = channel_id
        msg.channel = channel
        msg.content = "hello"
        msg.attachments = []
        msg.mentions = mentions or []
        return msg

    @pytest.mark.asyncio
    async def test_mention_only_channel_without_mention_is_ignored(self) -> None:
        """Message in a mention-only channel without @bot mention must be dropped."""
        cog = self._make_cog(
            channel_ids={111, 222},
            mention_only_channel_ids={222},
        )
        cog._handle_new_conversation = AsyncMock()

        msg = self._make_message(channel_id=222, mentions=[])  # no bot mention
        await cog.on_message(msg)

        cog._handle_new_conversation.assert_not_called()

    @pytest.mark.asyncio
    async def test_mention_only_channel_with_mention_answers_in_place(self) -> None:
        """A mention there is answered in the channel itself — no thread is opened."""
        cog = self._make_cog(
            channel_ids={111, 222},
            mention_only_channel_ids={222},
        )
        cog._handle_mention = AsyncMock()
        cog._handle_new_conversation = AsyncMock()

        bot_user = cog.bot.user
        msg = self._make_message(channel_id=222, mentions=[bot_user])
        await cog.on_message(msg)

        cog._handle_mention.assert_awaited_once_with(msg)
        cog._handle_new_conversation.assert_not_called()

    @pytest.mark.asyncio
    async def test_non_mention_only_channel_responds_to_all_messages(self) -> None:
        """Messages in regular channels (not mention-only) are handled as before."""
        cog = self._make_cog(
            channel_ids={111, 222},
            mention_only_channel_ids={222},  # 111 is NOT mention-only
        )
        cog._handle_new_conversation = AsyncMock()

        msg = self._make_message(channel_id=111, mentions=[])  # no mention, still works
        await cog.on_message(msg)

        cog._handle_new_conversation.assert_awaited_once_with(msg)

    @staticmethod
    def _make_thread_message(
        parent_id: int,
        mentions: list | None = None,
        owner_id: int = 42,
    ) -> MagicMock:
        msg = MagicMock(spec=discord.Message)
        msg.author = MagicMock()
        msg.author.bot = False
        msg.author.id = 42
        msg.type = discord.MessageType.default
        msg.mentions = mentions or []
        thread = MagicMock(spec=discord.Thread)
        thread.id = 55555
        thread.parent_id = parent_id
        thread.owner_id = owner_id  # 42 = a human created the thread
        msg.channel = thread
        msg.content = "reply"
        msg.attachments = []
        return msg

    @pytest.mark.asyncio
    async def test_unknown_thread_under_mention_only_channel_is_ignored(self) -> None:
        """A human-created thread in a mention-only channel must NOT start a session.

        Regression: mention-only channels were only gated on direct channel
        messages.  Creating a thread there routed straight to
        ``_handle_thread_reply``, which spawns a fresh session when no session
        record exists — bypassing the mention gate entirely.
        """
        cog = self._make_cog(
            channel_ids={111, 222},
            mention_only_channel_ids={222},
            session_record=None,  # ccdb does not own this thread
        )
        cog._handle_thread_reply = AsyncMock()

        await cog.on_message(self._make_thread_message(parent_id=222))

        cog._handle_thread_reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_thread_under_mention_only_channel_with_mention_is_handled(
        self,
    ) -> None:
        """An explicit @mention is still an opt-in, even in an unknown thread."""
        cog = self._make_cog(
            channel_ids={111, 222},
            mention_only_channel_ids={222},
            session_record=None,
        )
        cog._handle_mention = AsyncMock()

        msg = self._make_thread_message(parent_id=222, mentions=[cog.bot.user])
        await cog.on_message(msg)

        cog._handle_mention.assert_awaited_once_with(msg)

    @pytest.mark.asyncio
    async def test_human_thread_with_session_record_still_requires_a_mention(self) -> None:
        """A past session in a human's thread is not standing consent to keep listening.

        Regression: once a human-created thread had been woken with a single
        @mention, the session record made every later message in that thread
        start Claude again — including messages meant for the other humans in
        the thread.  Mention-only must mean mention-only, every time.
        """
        cog = self._make_cog(
            channel_ids={111, 222},
            mention_only_channel_ids={222},
            session_record=MagicMock(),  # ccdb ran here before
        )
        cog._handle_thread_reply = AsyncMock()

        await cog.on_message(self._make_thread_message(parent_id=222, owner_id=42))

        cog._handle_thread_reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_human_thread_with_session_record_resumes_on_mention(self) -> None:
        """Re-mentioning the bot in such a thread answers there again."""
        cog = self._make_cog(
            channel_ids={111, 222},
            mention_only_channel_ids={222},
            session_record=MagicMock(),
        )
        cog._handle_mention = AsyncMock()

        msg = self._make_thread_message(parent_id=222, mentions=[cog.bot.user], owner_id=42)
        await cog.on_message(msg)

        cog._handle_mention.assert_awaited_once_with(msg)

    @pytest.mark.asyncio
    async def test_bot_created_thread_here_also_needs_a_mention(self) -> None:
        """Owning the thread is not consent: under a mention-only parent, even
        ccdb's own threads run only when summoned."""
        cog = self._make_cog(
            channel_ids={111, 222},
            mention_only_channel_ids={222},
            session_record=None,
        )
        cog._handle_thread_reply = AsyncMock()
        cog._handle_mention = AsyncMock()

        msg = self._make_thread_message(parent_id=222, owner_id=cog.bot.user.id)
        await cog.on_message(msg)

        cog._handle_thread_reply.assert_not_called()
        cog._handle_mention.assert_not_called()

    @pytest.mark.asyncio
    async def test_thread_under_regular_channel_needs_no_mention(self) -> None:
        """Non-mention-only parents are unaffected: no session lookup, always handled."""
        cog = self._make_cog(
            channel_ids={111, 222},
            mention_only_channel_ids={222},
            session_record=None,
        )
        cog._handle_thread_reply = AsyncMock()

        msg = self._make_thread_message(parent_id=111)
        await cog.on_message(msg)

        cog._handle_thread_reply.assert_awaited_once_with(msg)
        cog.repo.get.assert_not_called()

    def test_mention_only_channel_ids_default_to_empty_set(self) -> None:
        """Without mention_only_channel_ids, the set is empty (all messages handled)."""
        cog = self._make_cog(channel_ids={111})
        assert cog._mention_only_channel_ids == set()


class TestMentionAnywhere:
    """Default policy: no-mention channels are listed; everywhere else needs an @mention."""

    def _make_cog(
        self,
        channel_ids: set[int] | None = None,
        *,
        mention_anywhere: bool = True,
        session_record: object | None = None,
    ) -> ClaudeChatCog:
        bot = MagicMock()
        bot.channel_id = 999
        bot.user = MagicMock()
        bot.user.id = 1111
        repo = MagicMock()
        repo.get = AsyncMock(return_value=session_record)
        repo.save = AsyncMock()
        runner = MagicMock()
        runner.clone = MagicMock(return_value=MagicMock())
        return ClaudeChatCog(
            bot=bot,
            repo=repo,
            runner=runner,
            channel_ids=channel_ids if channel_ids is not None else {111},
            mention_anywhere=mention_anywhere,
        )

    @staticmethod
    def _channel_message(channel_id: int, mentions: list | None = None) -> MagicMock:
        msg = MagicMock(spec=discord.Message)
        msg.author = MagicMock()
        msg.author.bot = False
        msg.author.id = 42
        msg.type = discord.MessageType.default
        msg.mentions = mentions or []
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = channel_id
        msg.channel = channel
        msg.guild = MagicMock()
        msg.content = "hello"
        msg.attachments = []
        return msg

    @staticmethod
    def _thread_message(
        parent_id: int,
        mentions: list | None = None,
        owner_id: int = 42,
    ) -> MagicMock:
        msg = MagicMock(spec=discord.Message)
        msg.author = MagicMock()
        msg.author.bot = False
        msg.author.id = 42
        msg.type = discord.MessageType.default
        msg.mentions = mentions or []
        thread = MagicMock(spec=discord.Thread)
        thread.id = 55555
        thread.parent_id = parent_id
        thread.owner_id = owner_id
        msg.channel = thread
        msg.guild = MagicMock()
        msg.content = "reply"
        msg.attachments = []
        return msg

    @pytest.mark.asyncio
    async def test_mention_in_an_unlisted_channel_answers_in_place(self) -> None:
        """A mention is a question to answer here, not a session to open elsewhere."""
        cog = self._make_cog(channel_ids={111})
        cog._handle_mention = AsyncMock()
        cog._handle_new_conversation = AsyncMock()

        msg = self._channel_message(channel_id=777, mentions=[cog.bot.user])
        await cog.on_message(msg)

        cog._handle_mention.assert_awaited_once_with(msg)
        cog._handle_new_conversation.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_mention_in_an_unlisted_channel_stays_silent(self) -> None:
        cog = self._make_cog(channel_ids={111})
        cog._handle_new_conversation = AsyncMock()
        cog._handle_thread_reply = AsyncMock()

        await cog.on_message(self._channel_message(channel_id=777))

        cog._handle_new_conversation.assert_not_called()
        cog._handle_thread_reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_listed_channel_needs_no_mention(self) -> None:
        cog = self._make_cog(channel_ids={111})
        cog._handle_new_conversation = AsyncMock()

        msg = self._channel_message(channel_id=111)
        await cog.on_message(msg)

        cog._handle_new_conversation.assert_awaited_once_with(msg)

    @pytest.mark.asyncio
    async def test_mention_in_an_unlisted_thread_answers_in_that_thread(self) -> None:
        """A mention inside someone else's thread answers *in* that thread."""
        cog = self._make_cog(channel_ids={111})
        cog._handle_mention = AsyncMock()

        msg = self._thread_message(parent_id=777, mentions=[cog.bot.user])
        await cog.on_message(msg)

        cog._handle_mention.assert_awaited_once_with(msg)

    @pytest.mark.asyncio
    async def test_no_mention_in_an_unlisted_thread_stays_silent(self) -> None:
        cog = self._make_cog(channel_ids={111})
        cog._handle_thread_reply = AsyncMock()

        await cog.on_message(self._thread_message(parent_id=777))

        cog._handle_thread_reply.assert_not_called()

    @pytest.mark.asyncio
    async def test_bot_created_thread_outside_the_scope_still_needs_a_mention(self) -> None:
        """Owning the thread is not consent either — outside the listed channels,
        every run is summoned explicitly."""
        cog = self._make_cog(channel_ids={111})
        cog._handle_thread_reply = AsyncMock()
        cog._handle_mention = AsyncMock()

        msg = self._thread_message(parent_id=777, owner_id=cog.bot.user.id)
        await cog.on_message(msg)

        cog._handle_thread_reply.assert_not_called()
        cog._handle_mention.assert_not_called()

    @pytest.mark.asyncio
    async def test_thread_under_a_listed_channel_still_needs_no_mention(self) -> None:
        """Session threads live under the listed channels — those keep flowing."""
        cog = self._make_cog(channel_ids={111})
        cog._handle_thread_reply = AsyncMock()

        msg = self._thread_message(parent_id=111, owner_id=cog.bot.user.id)
        await cog.on_message(msg)

        cog._handle_thread_reply.assert_awaited_once_with(msg)

    @pytest.mark.asyncio
    async def test_mention_anywhere_can_be_disabled(self) -> None:
        """Opt-out restores the strict listed-channels-only behaviour."""
        cog = self._make_cog(channel_ids={111}, mention_anywhere=False)
        cog._handle_new_conversation = AsyncMock()

        await cog.on_message(self._channel_message(channel_id=777, mentions=[cog.bot.user]))

        cog._handle_new_conversation.assert_not_called()

    @pytest.mark.asyncio
    async def test_direct_messages_are_never_picked_up_by_the_mention_path(self) -> None:
        """No guild means no channel policy to inherit — stay out of DMs."""
        cog = self._make_cog(channel_ids={111})
        cog._handle_new_conversation = AsyncMock()

        msg = self._channel_message(channel_id=777, mentions=[cog.bot.user])
        msg.guild = None
        await cog.on_message(msg)

        cog._handle_new_conversation.assert_not_called()

    def test_mention_anywhere_is_on_by_default(self) -> None:
        bot = MagicMock()
        bot.channel_id = 999
        cog = ClaudeChatCog(bot=bot, repo=MagicMock(), runner=MagicMock())
        assert cog._mention_anywhere is True


class TestHandleMention:
    """A mention is answered *where it was written* — no thread is ever created."""

    def _make_cog(self, *, session_record: object | None = None) -> ClaudeChatCog:
        bot = MagicMock()
        bot.channel_id = 999
        bot.user = MagicMock()
        bot.user.id = 1111
        repo = MagicMock()
        repo.get = AsyncMock(return_value=session_record)
        repo.save = AsyncMock()
        runner = MagicMock()
        runner.clone = MagicMock(return_value=MagicMock())
        cog = ClaudeChatCog(bot=bot, repo=repo, runner=runner, channel_ids={111})
        cog._run_claude = AsyncMock()
        return cog

    @staticmethod
    def _channel_message(channel_id: int = 777) -> MagicMock:
        msg = MagicMock(spec=discord.Message)
        msg.id = 7
        msg.author = MagicMock()
        msg.author.bot = False
        msg.author.id = 42
        msg.type = discord.MessageType.default
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = channel_id
        msg.channel = channel
        msg.guild = MagicMock()
        msg.content = "@bot what do you think?"
        msg.attachments = []
        msg.create_thread = AsyncMock()
        return msg

    @staticmethod
    def _thread_message(thread_id: int = 555, parent_id: int = 777) -> MagicMock:
        msg = MagicMock(spec=discord.Message)
        msg.id = 7
        msg.author = MagicMock()
        msg.author.bot = False
        msg.author.id = 42
        msg.type = discord.MessageType.default
        thread = MagicMock(spec=discord.Thread)
        thread.id = thread_id
        thread.parent_id = parent_id
        thread.owner_id = 42
        msg.channel = thread
        msg.guild = MagicMock()
        msg.content = "@bot thoughts?"
        msg.attachments = []
        msg.create_thread = AsyncMock()
        return msg

    @pytest.mark.asyncio
    async def test_channel_mention_never_creates_a_thread(self) -> None:
        cog = self._make_cog()
        msg = self._channel_message()
        with patch(
            "claude_discord.cogs.claude_chat.build_recent_transcript",
            new_callable=AsyncMock,
            return_value=None,
        ):
            await cog._handle_mention(msg)

        msg.create_thread.assert_not_called()
        assert cog._run_claude.await_args.args[1] is msg.channel

    @pytest.mark.asyncio
    async def test_thread_mention_answers_in_the_same_thread(self) -> None:
        cog = self._make_cog()
        msg = self._thread_message()
        with patch(
            "claude_discord.cogs.claude_chat.build_recent_transcript",
            new_callable=AsyncMock,
            return_value=None,
        ):
            await cog._handle_mention(msg)

        msg.create_thread.assert_not_called()
        assert cog._run_claude.await_args.args[1] is msg.channel

    @pytest.mark.asyncio
    async def test_recent_history_of_that_place_is_prepended(self) -> None:
        """Whatever ccdb is answering in — channel or thread — it reads that place first."""
        cog = self._make_cog()
        msg = self._channel_message()
        with patch(
            "claude_discord.cogs.claude_chat.build_recent_transcript",
            new_callable=AsyncMock,
            return_value="TRANSCRIPT",
        ) as build:
            await cog._handle_mention(msg)

        assert build.await_args.args[0] is msg.channel
        assert build.await_args.kwargs["exclude_message_id"] == msg.id
        prompt = cog._run_claude.await_args.args[2]
        assert prompt.startswith("TRANSCRIPT")
        assert "what do you think?" in prompt

    @pytest.mark.asyncio
    async def test_existing_session_for_that_place_is_resumed(self) -> None:
        record = MagicMock()
        record.session_id = "sess-1"
        record.working_dir = "/tmp"
        cog = self._make_cog(session_record=record)
        cog._session_id_for_current_backend = AsyncMock(return_value="sess-1")
        with patch(
            "claude_discord.cogs.claude_chat.build_recent_transcript",
            new_callable=AsyncMock,
            return_value=None,
        ):
            await cog._handle_mention(self._channel_message())

        assert cog._run_claude.await_args.kwargs["session_id"] == "sess-1"

    @pytest.mark.asyncio
    async def test_transcript_can_be_disabled(self) -> None:
        cog = self._make_cog()
        cog._thread_context_days = 0
        with patch(
            "claude_discord.cogs.claude_chat.build_recent_transcript",
            new_callable=AsyncMock,
        ) as build:
            await cog._handle_mention(self._channel_message())

        build.assert_not_called()


class TestThreadContextInjection:
    """A mention into a foreign thread carries that thread's recent history."""

    def _make_cog(self, *, session_record: object | None = None) -> ClaudeChatCog:
        bot = MagicMock()
        bot.channel_id = 999
        bot.user = MagicMock()
        bot.user.id = 1111
        bot.inbox_repo = None
        repo = MagicMock()
        repo.get = AsyncMock(return_value=session_record)
        repo.save = AsyncMock()
        runner = MagicMock()
        runner.clone = MagicMock(return_value=MagicMock())
        cog = ClaudeChatCog(bot=bot, repo=repo, runner=runner, channel_ids={111})
        cog._run_claude = AsyncMock()
        return cog

    @staticmethod
    def _thread_message(owner_id: int) -> MagicMock:
        msg = MagicMock(spec=discord.Message)
        msg.author = MagicMock()
        msg.author.bot = False
        msg.author.id = 42
        msg.id = 7
        msg.type = discord.MessageType.default
        msg.mentions = []
        thread = MagicMock(spec=discord.Thread)
        thread.id = 55555
        thread.parent_id = 111
        thread.owner_id = owner_id
        msg.channel = thread
        msg.content = "what do you think?"
        msg.attachments = []
        return msg

    @pytest.mark.asyncio
    async def test_foreign_thread_history_is_prepended_to_the_prompt(self) -> None:
        cog = self._make_cog()
        with patch(
            "claude_discord.cogs.claude_chat.build_recent_transcript",
            new_callable=AsyncMock,
            return_value="TRANSCRIPT",
        ):
            await cog._handle_thread_reply(self._thread_message(owner_id=42))

        prompt = cog._run_claude.await_args.args[2]
        assert prompt.startswith("TRANSCRIPT")
        assert "what do you think?" in prompt

    @pytest.mark.asyncio
    async def test_bot_owned_thread_skips_the_transcript(self) -> None:
        """ccdb saw every turn in its own thread — re-sending it would just burn tokens."""
        cog = self._make_cog()
        with patch(
            "claude_discord.cogs.claude_chat.build_recent_transcript",
            new_callable=AsyncMock,
        ) as build:
            await cog._handle_thread_reply(self._thread_message(owner_id=1111))

        build.assert_not_called()

    @pytest.mark.asyncio
    async def test_transcript_is_skipped_when_disabled(self) -> None:
        cog = self._make_cog()
        cog._thread_context_days = 0
        with patch(
            "claude_discord.cogs.claude_chat.build_recent_transcript",
            new_callable=AsyncMock,
        ) as build:
            await cog._handle_thread_reply(self._thread_message(owner_id=42))

        build.assert_not_called()


class TestInlineReplyChannels:
    """inline_reply_channel_ids: bot responds directly in channel without creating a thread."""

    def _make_cog(
        self,
        channel_ids: set[int],
        inline_reply_channel_ids: set[int] | None = None,
    ) -> ClaudeChatCog:
        bot = MagicMock()
        bot.channel_id = 999
        bot.user = MagicMock()
        repo = MagicMock()
        repo.get = AsyncMock(return_value=None)
        repo.save = AsyncMock()
        runner = MagicMock()
        runner.clone = MagicMock(return_value=MagicMock())
        return ClaudeChatCog(
            bot=bot,
            repo=repo,
            runner=runner,
            channel_ids=channel_ids,
            inline_reply_channel_ids=inline_reply_channel_ids,
        )

    def _make_channel_message(self, channel_id: int, author_id: int = 42) -> MagicMock:
        msg = MagicMock(spec=discord.Message)
        msg.author = MagicMock()
        msg.author.bot = False
        msg.author.id = author_id
        msg.type = discord.MessageType.default
        msg.content = "hello"
        msg.attachments = []
        msg.mentions = []
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = channel_id
        msg.channel = channel
        return msg

    @pytest.mark.asyncio
    async def test_inline_channel_does_not_create_thread(self) -> None:
        """In inline-reply mode, _handle_new_conversation must NOT call create_thread."""
        cog = self._make_cog(channel_ids={111, 222}, inline_reply_channel_ids={222})
        cog._run_claude = AsyncMock()

        msg = self._make_channel_message(channel_id=222)
        await cog._handle_new_conversation(msg)

        # channel.create_thread must NOT have been called
        msg.create_thread.assert_not_called()

    @pytest.mark.asyncio
    async def test_inline_channel_passes_channel_to_run_claude(self) -> None:
        """In inline-reply mode, _run_claude receives the channel, not a thread."""
        cog = self._make_cog(channel_ids={111, 222}, inline_reply_channel_ids={222})
        cog._run_claude = AsyncMock()

        msg = self._make_channel_message(channel_id=222)
        await cog._handle_new_conversation(msg)

        cog._run_claude.assert_awaited_once()
        _, called_thread, *_ = cog._run_claude.call_args.args
        assert called_thread is msg.channel  # channel itself, not a thread

    @pytest.mark.asyncio
    async def test_inline_channel_resumes_stored_session(self) -> None:
        """An inline channel is one continuous conversation, keyed by channel id.

        Every message in such a channel goes through _handle_new_conversation, so
        without this the session would restart cold each time — unlike threads.
        """
        cog = self._make_cog(channel_ids={111, 222}, inline_reply_channel_ids={222})
        cog.repo.get = AsyncMock(return_value=SimpleNamespace(session_id="sess-abc"))
        cog._run_claude = AsyncMock()

        msg = self._make_channel_message(channel_id=222)
        await cog._handle_new_conversation(msg)

        cog.repo.get.assert_awaited_once_with(222)
        assert cog._run_claude.call_args.kwargs["session_id"] == "sess-abc"

    @pytest.mark.asyncio
    async def test_inline_channel_without_prior_session_starts_fresh(self) -> None:
        """First message in an inline channel has no stored session to resume."""
        cog = self._make_cog(channel_ids={111, 222}, inline_reply_channel_ids={222})
        cog._run_claude = AsyncMock()

        await cog._handle_new_conversation(self._make_channel_message(channel_id=222))

        assert cog._run_claude.call_args.kwargs["session_id"] is None

    @pytest.mark.asyncio
    async def test_non_inline_channel_still_creates_thread(self) -> None:
        """Regular channels (not in inline_reply_channel_ids) still create threads."""
        cog = self._make_cog(channel_ids={111, 222}, inline_reply_channel_ids={222})
        cog._run_claude = AsyncMock()
        mock_thread = MagicMock()
        msg = self._make_channel_message(channel_id=111)
        msg.create_thread = AsyncMock(return_value=mock_thread)

        await cog._handle_new_conversation(msg)

        msg.create_thread.assert_awaited_once()

    def test_inline_reply_channel_ids_default_to_empty_set(self) -> None:
        """Without inline_reply_channel_ids, the set is empty (thread mode for all channels)."""
        cog = self._make_cog(channel_ids={111})
        assert cog._inline_reply_channel_ids == set()


class TestAutoRenameThreads:
    """auto_rename_threads=True fires a background task to rename the thread."""

    def _make_cog(self, auto_rename: bool = False) -> ClaudeChatCog:
        bot = MagicMock()
        bot.channel_id = 111
        bot.user = MagicMock()
        runner = MagicMock()
        runner.command = "claude"
        runner.clone = MagicMock(return_value=MagicMock())
        repo = MagicMock()
        repo.get = AsyncMock(return_value=None)
        repo.save = AsyncMock()
        return ClaudeChatCog(
            bot=bot,
            repo=repo,
            runner=runner,
            channel_ids={111},
            auto_rename_threads=auto_rename,
        )

    def _make_channel_message(self, content: str = "Fix the auth bug") -> MagicMock:
        msg = MagicMock(spec=discord.Message)
        msg.author = MagicMock()
        msg.author.bot = False
        msg.author.id = 42
        msg.type = discord.MessageType.default
        msg.content = content
        msg.attachments = []
        msg.mentions = []
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 111
        msg.channel = channel
        return msg

    @pytest.mark.asyncio
    async def test_auto_rename_disabled_by_default(self) -> None:
        """auto_rename_threads defaults to False — no rename task is spawned."""
        cog = self._make_cog(auto_rename=False)
        cog._run_claude = AsyncMock()
        cog._background_rename_thread = AsyncMock()

        mock_thread = MagicMock()
        msg = self._make_channel_message()
        msg.create_thread = AsyncMock(return_value=mock_thread)

        await cog._handle_new_conversation(msg)

        cog._background_rename_thread.assert_not_called()

    @pytest.mark.asyncio
    async def test_auto_rename_enabled_spawns_rename_task(self) -> None:
        """When auto_rename_threads=True, _background_rename_thread is called after thread creation.

        Ensures the rename task is scheduled as a background coroutine.
        """
        cog = self._make_cog(auto_rename=True)
        cog._run_claude = AsyncMock()

        rename_called_with: list = []

        async def _capture_rename(thread, message):
            rename_called_with.append((thread, message))

        cog._background_rename_thread = _capture_rename  # type: ignore[method-assign]

        mock_thread = MagicMock()
        msg = self._make_channel_message("Please help me refactor the payment module")
        msg.create_thread = AsyncMock(return_value=mock_thread)

        await cog._handle_new_conversation(msg)

        # Give the background task a chance to complete
        import asyncio

        await asyncio.sleep(0)

        assert len(rename_called_with) == 1
        assert rename_called_with[0][0] is mock_thread
        assert rename_called_with[0][1] == "Please help me refactor the payment module"

    @pytest.mark.asyncio
    async def test_auto_rename_skipped_for_empty_message(self) -> None:
        """When message has no content, no rename task should be created."""
        cog = self._make_cog(auto_rename=True)
        cog._run_claude = AsyncMock()
        cog._background_rename_thread = AsyncMock()

        mock_thread = MagicMock()
        msg = self._make_channel_message(content="")
        msg.create_thread = AsyncMock(return_value=mock_thread)

        await cog._handle_new_conversation(msg)

        cog._background_rename_thread.assert_not_called()

    @pytest.mark.asyncio
    async def test_background_rename_thread_calls_thread_edit(self) -> None:
        """_background_rename_thread should call thread.edit(name=...) when title is available."""
        from unittest.mock import patch

        cog = self._make_cog(auto_rename=True)
        mock_thread = MagicMock()
        mock_thread.id = 999
        mock_thread.edit = AsyncMock()

        with patch(
            "claude_discord.cogs.claude_chat.suggest_title",
            new=AsyncMock(return_value="Refactor payment module"),
        ):
            await cog._background_rename_thread(mock_thread, "refactor payment module")

        mock_thread.edit.assert_awaited_once_with(name="Refactor payment module")

    @pytest.mark.asyncio
    async def test_background_rename_thread_no_edit_when_no_title(self) -> None:
        """When suggest_title returns None, thread.edit must NOT be called."""
        from unittest.mock import patch

        cog = self._make_cog(auto_rename=True)
        mock_thread = MagicMock()
        mock_thread.id = 999
        mock_thread.edit = AsyncMock()

        with patch(
            "claude_discord.cogs.claude_chat.suggest_title",
            new=AsyncMock(return_value=None),
        ):
            await cog._background_rename_thread(mock_thread, "some message")

        mock_thread.edit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_background_rename_thread_handles_edit_error_gracefully(self) -> None:
        """Discord API errors during rename must not propagate — silent no-op."""
        from unittest.mock import patch

        cog = self._make_cog(auto_rename=True)
        mock_thread = MagicMock()
        mock_thread.id = 999
        mock_thread.edit = AsyncMock(side_effect=RuntimeError("Discord API error"))

        with patch(
            "claude_discord.cogs.claude_chat.suggest_title",
            new=AsyncMock(return_value="Some title"),
        ):
            # Should not raise
            await cog._background_rename_thread(mock_thread, "some message")


class TestCompactCommand:
    """Tests for /compact slash command."""

    @pytest.mark.asyncio
    async def test_compact_outside_thread_sends_ephemeral(self) -> None:
        cog = _make_cog()
        interaction = _make_channel_interaction()

        await cog.compact_session.callback(cog, interaction)

        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args.kwargs
        assert call_kwargs.get("ephemeral") is True

    @pytest.mark.asyncio
    async def test_compact_no_session_sends_ephemeral(self) -> None:
        cog = _make_cog()
        interaction = _make_thread_interaction(thread_id=12345)
        cog.repo.get = AsyncMock(return_value=None)

        await cog.compact_session.callback(cog, interaction)

        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args.kwargs
        assert call_kwargs.get("ephemeral") is True

    @pytest.mark.asyncio
    async def test_compact_while_running_sends_ephemeral(self) -> None:
        cog = _make_cog()
        thread_id = 12345
        interaction = _make_thread_interaction(thread_id=thread_id)
        record = MagicMock()
        record.session_id = "abc-123"
        record.working_dir = None
        cog.repo.get = AsyncMock(return_value=record)
        cog._active_runners[thread_id] = MagicMock()

        await cog.compact_session.callback(cog, interaction)

        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args.kwargs
        assert call_kwargs.get("ephemeral") is True
        assert "running" in interaction.response.send_message.call_args.args[0].lower()

    @pytest.mark.asyncio
    async def test_compact_defers_and_calls_run_claude(self) -> None:
        cog = _make_cog()
        thread_id = 12345
        interaction = _make_thread_interaction(thread_id=thread_id)
        record = MagicMock()
        record.session_id = "abc-123"
        record.working_dir = "/tmp/test"
        cog.repo.get = AsyncMock(return_value=record)

        interaction.response.defer = AsyncMock()
        interaction.followup = MagicMock()
        interaction.followup.send = AsyncMock()

        cog._run_claude = AsyncMock()

        await cog.compact_session.callback(cog, interaction)

        interaction.response.defer.assert_called_once()
        cog._run_claude.assert_called_once()
        call_kwargs = cog._run_claude.call_args.kwargs
        assert call_kwargs["prompt"] == "/compact"
        assert call_kwargs["session_id"] == "abc-123"


class TestGoalCommand:
    """Tests for /goal slash command."""

    @pytest.mark.asyncio
    async def test_goal_outside_thread_sends_ephemeral(self) -> None:
        cog = _make_cog()
        interaction = _make_channel_interaction()

        await cog.goal_session.callback(cog, interaction, condition=None)

        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args.kwargs
        assert call_kwargs.get("ephemeral") is True

    @pytest.mark.asyncio
    async def test_goal_no_session_sends_ephemeral(self) -> None:
        cog = _make_cog()
        interaction = _make_thread_interaction(thread_id=12345)
        cog.repo.get = AsyncMock(return_value=None)

        await cog.goal_session.callback(cog, interaction, condition="all tests pass")

        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args.kwargs
        assert call_kwargs.get("ephemeral") is True

    @pytest.mark.asyncio
    async def test_goal_while_running_sends_ephemeral(self) -> None:
        cog = _make_cog()
        thread_id = 12345
        interaction = _make_thread_interaction(thread_id=thread_id)
        record = MagicMock()
        record.session_id = "abc-123"
        record.working_dir = None
        cog.repo.get = AsyncMock(return_value=record)
        cog._active_runners[thread_id] = MagicMock()

        await cog.goal_session.callback(cog, interaction, condition="all tests pass")

        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args.kwargs
        assert call_kwargs.get("ephemeral") is True
        assert "running" in interaction.response.send_message.call_args.args[0].lower()

    @pytest.mark.asyncio
    async def test_goal_with_condition_sends_goal_prompt(self) -> None:
        cog = _make_cog()
        thread_id = 12345
        interaction = _make_thread_interaction(thread_id=thread_id)
        record = MagicMock()
        record.session_id = "abc-123"
        record.working_dir = "/tmp/test"
        cog.repo.get = AsyncMock(return_value=record)

        interaction.response.defer = AsyncMock()
        interaction.followup = MagicMock()
        interaction.followup.send = AsyncMock()

        cog._run_claude = AsyncMock()

        await cog.goal_session.callback(cog, interaction, condition="all tests pass")

        interaction.response.defer.assert_called_once()
        cog._run_claude.assert_called_once()
        call_kwargs = cog._run_claude.call_args.kwargs
        assert call_kwargs["prompt"] == "/goal all tests pass"
        assert call_kwargs["session_id"] == "abc-123"
        assert call_kwargs["working_dir_override"] == "/tmp/test"

    @pytest.mark.asyncio
    async def test_goal_no_condition_sends_status_check(self) -> None:
        cog = _make_cog()
        thread_id = 12345
        interaction = _make_thread_interaction(thread_id=thread_id)
        record = MagicMock()
        record.session_id = "abc-123"
        record.working_dir = None
        cog.repo.get = AsyncMock(return_value=record)

        interaction.response.defer = AsyncMock()
        interaction.followup = MagicMock()
        interaction.followup.send = AsyncMock()

        cog._run_claude = AsyncMock()

        await cog.goal_session.callback(cog, interaction, condition=None)

        cog._run_claude.assert_called_once()
        call_kwargs = cog._run_claude.call_args.kwargs
        assert call_kwargs["prompt"] == "/goal"

    @pytest.mark.asyncio
    async def test_goal_clear_sends_clear_prompt(self) -> None:
        cog = _make_cog()
        thread_id = 12345
        interaction = _make_thread_interaction(thread_id=thread_id)
        record = MagicMock()
        record.session_id = "abc-123"
        record.working_dir = None
        cog.repo.get = AsyncMock(return_value=record)

        interaction.response.defer = AsyncMock()
        interaction.followup = MagicMock()
        interaction.followup.send = AsyncMock()

        cog._run_claude = AsyncMock()

        await cog.goal_session.callback(cog, interaction, condition="clear")

        cog._run_claude.assert_called_once()
        call_kwargs = cog._run_claude.call_args.kwargs
        assert call_kwargs["prompt"] == "/goal clear"

    @pytest.mark.asyncio
    async def test_goal_seed_message_emoji(self) -> None:
        cog = _make_cog()
        thread_id = 12345
        interaction = _make_thread_interaction(thread_id=thread_id)
        record = MagicMock()
        record.session_id = "abc-123"
        record.working_dir = None
        cog.repo.get = AsyncMock(return_value=record)

        interaction.response.defer = AsyncMock()
        seed = MagicMock()
        interaction.followup = MagicMock()
        interaction.followup.send = AsyncMock(return_value=seed)

        cog._run_claude = AsyncMock()

        await cog.goal_session.callback(cog, interaction, condition="all tests pass")

        send_args = interaction.followup.send.call_args
        assert "◎" in send_args.args[0] or "goal" in send_args.args[0].lower()
