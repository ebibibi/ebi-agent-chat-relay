"""Unit tests for EventProcessor.

These tests exercise individual event handlers in isolation, unlike the
integration tests in test_run_helper.py which test the full pipeline.

The key benefit of extracting EventProcessor as a class is that each
event type can be tested independently without running the full
run_claude_with_config() pipeline.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_code_core.frontend import ActivitySpec
from claude_discord.claude.types import (
    AskOption,
    AskQuestion,
    MessageType,
    StreamEvent,
    TodoItem,
    ToolCategory,
    ToolUseEvent,
)
from claude_discord.cogs.event_processor import EventProcessor, _backend_name_from_runner
from claude_discord.cogs.run_config import RunConfig
from claude_discord.discord_ui.prompt_views import ChoiceView
from claude_discord.surface import DiscordActivity, FormLauncher


def _make_config(thread: MagicMock, runner: MagicMock, **kwargs) -> RunConfig:
    """Build a minimal RunConfig for tests."""
    return RunConfig(thread=thread, runner=runner, prompt="test prompt", **kwargs)


async def _wait_for_prompt_message(thread: MagicMock, timeout: float = 2.0) -> None:
    """Let a background prompt task get as far as posting its message.

    Approval prompts are dispatched off the event loop so a two-minute wait for
    a human cannot stall event processing. The posting itself still happens
    promptly, so a bounded poll is enough — and fails loudly rather than
    hanging if the task never runs at all.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        # Match the prompt's own view, not merely "a message with a view":
        # a session posts assistant text and a stop button first, and either
        # would otherwise satisfy the wait before the prompt has gone out.
        if any(
            isinstance(call.kwargs.get("view"), ChoiceView | FormLauncher)
            for call in thread.send.call_args_list
        ):
            return
        await asyncio.sleep(0)
    raise AssertionError("the prompt task never posted a message")


def _posted_mentions(thread: MagicMock) -> list[str]:
    """Every mention string the thread was asked to post.

    Checked across all calls rather than the last one: a prompt is posted from
    its own task, so it no longer has to be the most recent thing sent.
    """
    return [
        call.kwargs["content"] for call in thread.send.call_args_list if call.kwargs.get("content")
    ]


def _make_tool_event(tool_id: str = "t1") -> StreamEvent:
    return StreamEvent(
        message_type=MessageType.ASSISTANT,
        tool_use=ToolUseEvent(
            tool_id=tool_id,
            tool_name="Bash",
            tool_input={"command": "echo hi"},
            category=ToolCategory.COMMAND,
        ),
    )


def _make_result_event(**kwargs) -> StreamEvent:
    return StreamEvent(
        message_type=MessageType.RESULT,
        is_complete=True,
        cost_usd=0.01,
        duration_ms=500,
        **kwargs,
    )


def test_backend_name_does_not_unwrap_arbitrary_mock_attributes() -> None:
    """Only real backend wrappers should have their ``inner`` traversed."""
    assert _backend_name_from_runner(MagicMock()) == "claude"


class TestEventProcessorProperties:
    """Initial state and property behaviour."""

    def test_session_id_is_none_initially(self, thread: MagicMock, runner: MagicMock) -> None:
        config = _make_config(thread, runner)
        p = EventProcessor(config)
        assert p.session_id is None

    def test_session_id_inherits_from_config(self, thread: MagicMock, runner: MagicMock) -> None:
        config = _make_config(thread, runner, session_id="existing")
        p = EventProcessor(config)
        assert p.session_id == "existing"

    def test_pending_ask_none_initially(self, thread: MagicMock, runner: MagicMock) -> None:
        config = _make_config(thread, runner)
        p = EventProcessor(config)
        assert p.pending_ask is None

    def test_should_drain_false_initially(self, thread: MagicMock, runner: MagicMock) -> None:
        config = _make_config(thread, runner)
        p = EventProcessor(config)
        assert p.should_drain is False

    def test_assistant_text_sent_false_initially(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        config = _make_config(thread, runner)
        p = EventProcessor(config)
        assert p.assistant_text_sent is False


class TestOnSystem:
    """SYSTEM event handling."""

    @pytest.mark.asyncio
    async def test_captures_session_id(self, thread: MagicMock, runner: MagicMock) -> None:
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        await p.process(StreamEvent(message_type=MessageType.SYSTEM, session_id="sess-abc"))

        assert p.session_id == "sess-abc"

    @pytest.mark.asyncio
    async def test_saves_to_repo_with_summary_for_new_session(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        """New sessions save the prompt as summary."""
        repo = MagicMock()
        repo.save = AsyncMock()
        config = _make_config(thread, runner, repo=repo)
        p = EventProcessor(config)

        await p.process(StreamEvent(message_type=MessageType.SYSTEM, session_id="s1"))

        repo.save.assert_called_once_with(
            thread.id,
            "s1",
            working_dir=runner.working_dir,
            origin="discord",
            summary="test prompt",
            backend="claude",
        )

    @pytest.mark.asyncio
    async def test_saves_the_frontend_origin_for_a_teams_session(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        repo = MagicMock()
        repo.save = AsyncMock()
        config = _make_config(thread, runner, repo=repo, session_origin="teams")
        p = EventProcessor(config)

        await p.process(StreamEvent(message_type=MessageType.SYSTEM, session_id="s1"))

        assert repo.save.call_args.kwargs["origin"] == "teams"

    @pytest.mark.asyncio
    async def test_saves_to_repo_without_summary_for_resumed_session(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        """Resumed sessions do not overwrite the existing summary."""
        repo = MagicMock()
        repo.save = AsyncMock()
        config = _make_config(thread, runner, repo=repo, session_id="existing-sess")
        p = EventProcessor(config)

        await p.process(StreamEvent(message_type=MessageType.SYSTEM, session_id="existing-sess"))

        repo.save.assert_called_once_with(
            thread.id,
            "existing-sess",
            working_dir=runner.working_dir,
            origin="discord",
            backend="claude",
        )

    @pytest.mark.asyncio
    async def test_summary_truncated_to_100_chars(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        """Summary is truncated to 100 characters."""
        repo = MagicMock()
        repo.save = AsyncMock()
        long_prompt = "x" * 200
        config = RunConfig(thread=thread, runner=runner, prompt=long_prompt, repo=repo)
        p = EventProcessor(config)

        await p.process(StreamEvent(message_type=MessageType.SYSTEM, session_id="s1"))

        repo.save.assert_called_once_with(
            thread.id,
            "s1",
            working_dir=runner.working_dir,
            origin="discord",
            summary="x" * 100,
            backend="claude",
        )

    @pytest.mark.asyncio
    async def test_sends_start_embed_for_new_session(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        """Start embed is posted when no session_id was pre-set (new session)."""
        config = _make_config(thread, runner)  # no session_id
        p = EventProcessor(config)

        await p.process(StreamEvent(message_type=MessageType.SYSTEM, session_id="new-sess"))

        thread.send.assert_called_once()
        call_kwargs = thread.send.call_args.kwargs
        assert "embed" in call_kwargs

    @pytest.mark.asyncio
    async def test_no_start_embed_for_resumed_session(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        """Start embed is NOT posted when a session_id is pre-set (resume)."""
        config = _make_config(thread, runner, session_id="pre-existing")
        p = EventProcessor(config)

        await p.process(StreamEvent(message_type=MessageType.SYSTEM, session_id="pre-existing"))

        thread.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_start_embed_sent_only_once(self, thread: MagicMock, runner: MagicMock) -> None:
        """Multiple SYSTEM events (can happen with Claude Code) only produce one start embed."""
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        await p.process(StreamEvent(message_type=MessageType.SYSTEM, session_id="s1"))
        await p.process(StreamEvent(message_type=MessageType.SYSTEM, session_id="s1"))

        embed_sends = [c for c in thread.send.call_args_list if "embed" in c.kwargs]
        assert len(embed_sends) == 1


class TestOnAssistantText:
    """ASSISTANT text streaming handling."""

    @pytest.mark.asyncio
    async def test_complete_text_sends_message(self, thread: MagicMock, runner: MagicMock) -> None:
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        event = StreamEvent(message_type=MessageType.ASSISTANT, text="Hello!", is_partial=False)
        await p.process(event)

        text_sends = [
            c for c in thread.send.call_args_list if c.args and isinstance(c.args[0], str)
        ]
        assert any("Hello!" in c.args[0] for c in text_sends)

    @pytest.mark.asyncio
    async def test_complete_text_marks_assistant_text_sent(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        event = StreamEvent(message_type=MessageType.ASSISTANT, text="Hello!", is_partial=False)
        await p.process(event)

        assert p.assistant_text_sent is True

    @pytest.mark.asyncio
    async def test_partial_text_does_not_mark_sent(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        event = StreamEvent(message_type=MessageType.ASSISTANT, text="Hel", is_partial=True)
        await p.process(event)

        assert p.assistant_text_sent is False


class TestOnAssistantThinking:
    """ASSISTANT thinking event handling."""

    @pytest.mark.asyncio
    async def test_complete_thinking_sends_embed(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        event = StreamEvent(
            message_type=MessageType.ASSISTANT,
            thinking="I am thinking...",
            is_partial=False,
        )
        await p.process(event)

        embed_sends = [c for c in thread.send.call_args_list if "embed" in c.kwargs]
        assert len(embed_sends) == 1

    @pytest.mark.asyncio
    async def test_partial_thinking_does_not_send_embed(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        event = StreamEvent(message_type=MessageType.ASSISTANT, thinking="I am", is_partial=True)
        await p.process(event)

        thread.send.assert_not_called()


class TestOnToolUse:
    """ASSISTANT tool_use event handling."""

    @pytest.mark.asyncio
    async def test_tool_use_sends_embed(self, thread: MagicMock, runner: MagicMock) -> None:
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        await p.process(_make_tool_event("t1"))

        embed_sends = [c for c in thread.send.call_args_list if "embed" in c.kwargs]
        assert len(embed_sends) == 1

    @pytest.mark.asyncio
    async def test_tool_use_tracked_in_state(self, thread: MagicMock, runner: MagicMock) -> None:
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        await p.process(_make_tool_event("tool-xyz"))

        assert "tool-xyz" in p._state.active_tools

    @pytest.mark.asyncio
    async def test_tool_use_starts_timer(self, thread: MagicMock, runner: MagicMock) -> None:
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        await p.process(_make_tool_event("t-timer"))

        activity = p._state.active_tools["t-timer"]
        assert isinstance(activity, DiscordActivity)
        assert activity._timer is not None
        await p.finalize()


class TestOnToolResult:
    """USER (tool result) event handling."""

    @pytest.mark.asyncio
    async def test_activity_completed_on_result(self, thread: MagicMock, runner: MagicMock) -> None:
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        activity = MagicMock()
        activity.complete = AsyncMock()
        p._state.active_tools["t1"] = activity

        result_event = StreamEvent(message_type=MessageType.USER, tool_result_id="t1")
        await p.process(result_event)

        activity.complete.assert_awaited_once_with("")
        assert "t1" not in p._state.active_tools

    @pytest.mark.asyncio
    async def test_tool_embed_updated_with_result(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        # Plant a fake in-progress tool message
        fake_embed = MagicMock(spec=discord.Embed)
        fake_embed.title = "Running: echo hi"
        fake_msg = MagicMock(spec=discord.Message)
        fake_msg.embeds = [fake_embed]
        fake_msg.edit = AsyncMock()
        p._state.active_tools["t1"] = DiscordActivity(
            fake_msg, ActivitySpec(kind="tool", title="Running: echo hi")
        )

        result_event = StreamEvent(
            message_type=MessageType.USER,
            tool_result_id="t1",
            tool_result_content="output here",
        )
        await p.process(result_event)

        fake_msg.edit.assert_called_once()


class TestToolResultCollapse:
    """Tool results with >1 line are shown collapsed with an expand button."""

    def _plant_tool_msg(self, p: EventProcessor, tool_id: str) -> MagicMock:
        fake_embed = MagicMock(spec=discord.Embed)
        fake_embed.title = "🔧 Running: cat file..."
        fake_msg = MagicMock(spec=discord.Message)
        fake_msg.embeds = [fake_embed]
        fake_msg.edit = AsyncMock()
        p._state.active_tools[tool_id] = DiscordActivity(
            fake_msg, ActivitySpec(kind="tool", title="Running: cat file")
        )
        return fake_msg

    def _make_result_event(self, tool_id: str, content: str | None) -> StreamEvent:
        return StreamEvent(
            message_type=MessageType.USER,
            tool_result_id=tool_id,
            tool_result_content=content,
        )

    @pytest.mark.asyncio
    async def test_single_line_result_no_expand_button(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        """Single-line result → full embed shown inline, no expand button."""
        config = _make_config(thread, runner)
        p = EventProcessor(config)
        fake_msg = self._plant_tool_msg(p, "t1")

        await p.process(self._make_result_event("t1", "ok"))

        call_kwargs = fake_msg.edit.call_args.kwargs
        assert "view" not in call_kwargs

    @pytest.mark.asyncio
    async def test_two_line_result_adds_expand_button(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        """Two or more lines → ToolResultView attached to the message."""
        from claude_discord.discord_ui.views import ToolResultView

        config = _make_config(thread, runner)
        p = EventProcessor(config)
        fake_msg = self._plant_tool_msg(p, "t1")

        await p.process(self._make_result_event("t1", "line1\nline2"))

        call_kwargs = fake_msg.edit.call_args.kwargs
        assert "view" in call_kwargs
        assert isinstance(call_kwargs["view"], ToolResultView)

    @pytest.mark.asyncio
    async def test_long_result_adds_expand_button(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        """Many lines → ToolResultView attached to the message."""
        from claude_discord.discord_ui.views import ToolResultView

        config = _make_config(thread, runner)
        p = EventProcessor(config)
        fake_msg = self._plant_tool_msg(p, "t1")

        content = "\n".join(f"line{i}" for i in range(10))
        await p.process(self._make_result_event("t1", content))

        call_kwargs = fake_msg.edit.call_args.kwargs
        assert "view" in call_kwargs
        assert isinstance(call_kwargs["view"], ToolResultView)

    @pytest.mark.asyncio
    async def test_long_result_embed_shows_only_preview(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        """The collapsed embed description has first 3 lines but not line4+."""
        config = _make_config(thread, runner)
        p = EventProcessor(config)
        fake_msg = self._plant_tool_msg(p, "t1")

        content = "\n".join(f"line{i}" for i in range(10))
        await p.process(self._make_result_event("t1", content))

        embed = fake_msg.edit.call_args.kwargs["embed"]
        assert "line0" in embed.description
        assert "line2" in embed.description
        assert "line3" not in embed.description
        assert "+7" in embed.description  # 10 total - 3 shown = 7 hidden

    @pytest.mark.asyncio
    async def test_empty_result_still_updates_embed(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        """Tool with no output still clears the in-progress indicator."""
        config = _make_config(thread, runner)
        p = EventProcessor(config)
        fake_msg = self._plant_tool_msg(p, "t1")

        await p.process(self._make_result_event("t1", None))

        fake_msg.edit.assert_called_once()
        call_kwargs = fake_msg.edit.call_args.kwargs
        assert "view" not in call_kwargs
        # Embed title should strip the trailing "..."
        embed = call_kwargs["embed"]
        assert "..." not in embed.title


class TestAskUserQuestion:
    """AskUserQuestion detection."""

    @pytest.mark.asyncio
    async def test_pending_ask_set_on_detect(self, thread: MagicMock, runner: MagicMock) -> None:
        runner.interrupt = AsyncMock()
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        ask = AskQuestion(
            question="Which option?",
            options=[AskOption(label="A"), AskOption(label="B")],
        )
        event = StreamEvent(
            message_type=MessageType.ASSISTANT,
            ask_questions=[ask],
        )
        await p.process(event)

        assert p.pending_ask == [ask]
        assert p.should_drain is True

    @pytest.mark.asyncio
    async def test_runner_interrupted_on_ask(self, thread: MagicMock, runner: MagicMock) -> None:
        runner.interrupt = AsyncMock()
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        event = StreamEvent(
            message_type=MessageType.ASSISTANT,
            ask_questions=[AskQuestion(question="?", options=[AskOption(label="A")])],
        )
        await p.process(event)

        runner.interrupt.assert_called_once()


class TestOnComplete:
    """RESULT (is_complete) event handling."""

    @pytest.mark.asyncio
    async def test_complete_sends_session_complete_embed(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        await p.process(_make_result_event(session_id="s1"))

        embed_sends = [c for c in thread.send.call_args_list if "embed" in c.kwargs]
        assert len(embed_sends) >= 1

    @pytest.mark.asyncio
    async def test_complete_send_not_found_does_not_raise(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        """If the Discord thread disappears, completion delivery is non-fatal."""
        thread.send.side_effect = discord.NotFound(MagicMock(), "Unknown Channel")
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        await p.process(_make_result_event(session_id="s1"))

        assert p.session_id == "s1"

    @pytest.mark.asyncio
    async def test_complete_send_unexpected_error_still_raises(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        """Unexpected delivery failures must not be hidden as missing channels."""
        thread.send.side_effect = RuntimeError("unexpected delivery failure")
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        with pytest.raises(RuntimeError, match="unexpected delivery failure"):
            await p.process(_make_result_event(session_id="s1"))

    @pytest.mark.asyncio
    async def test_error_sends_error_embed(self, thread: MagicMock, runner: MagicMock) -> None:
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        event = StreamEvent(
            message_type=MessageType.RESULT,
            is_complete=True,
            error="Something went wrong",
        )
        await p.process(event)

        embed_sends = [c for c in thread.send.call_args_list if "embed" in c.kwargs]
        assert len(embed_sends) >= 1

    @pytest.mark.asyncio
    async def test_complete_result_text_not_repeated_if_already_sent(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        """If assistant text was streamed, RESULT text must not duplicate it."""
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        # Simulate assistant text having been sent already
        assistant_event = StreamEvent(
            message_type=MessageType.ASSISTANT, text="Answer.", is_partial=False
        )
        await p.process(assistant_event)

        # RESULT also has text — should NOT re-send it
        result_event = _make_result_event(text="Answer.", session_id="s1")
        await p.process(result_event)

        text_sends = [
            c for c in thread.send.call_args_list if c.args and isinstance(c.args[0], str)
        ]
        answer_sends = [c for c in text_sends if "Answer." in c.args[0]]
        assert len(answer_sends) == 1  # Sent exactly once, not twice


class TestConnectionErrorResilience:
    """Discord/aiohttp connection errors must not crash the session.

    ServerDisconnectedError (aiohttp.ClientError subclass) is raised when
    the Discord HTTP session closes — e.g. during bot shutdown. It is NOT a
    subclass of discord.HTTPException, so it was previously uncaught and
    would propagate up to kill the entire session.
    """

    @pytest.mark.asyncio
    async def test_tool_use_send_failure_does_not_raise(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        """If thread.send fails (connection closed), _handle_tool_use returns silently."""
        thread.send.side_effect = Exception("Server disconnected")
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        # Should not raise — failure is logged and the handler returns early.
        await p.process(_make_tool_event("t1"))

        # Tool was not tracked because send failed
        assert "t1" not in p._state.active_tools
        assert "t1" not in p._state.active_timers

    @pytest.mark.asyncio
    async def test_assistant_text_send_failure_does_not_raise(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        """If the thread disappears, assistant text delivery should not crash the session."""
        thread.send.side_effect = discord.NotFound(MagicMock(), "Unknown Channel")
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        await p.process(StreamEvent(message_type=MessageType.ASSISTANT, text="Hello"))

        assert p.assistant_text_sent is False

    @pytest.mark.asyncio
    async def test_tool_result_edit_failure_does_not_raise(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        """If tool embed edit raises a connection error, _on_tool_result suppresses it."""
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        fake_embed = MagicMock(spec=discord.Embed)
        fake_embed.title = "Running: echo hi"
        fake_msg = MagicMock(spec=discord.Message)
        fake_msg.embeds = [fake_embed]
        fake_msg.edit = AsyncMock(side_effect=Exception("Server disconnected"))
        p._state.active_tools["t1"] = DiscordActivity(
            fake_msg, ActivitySpec(kind="tool", title="Running: echo hi")
        )

        result_event = StreamEvent(
            message_type=MessageType.USER,
            tool_result_id="t1",
            tool_result_content="output",
        )
        # Should not raise — failure is suppressed.
        await p.process(result_event)


class TestFinalize:
    """finalize() cleanup."""

    @pytest.mark.asyncio
    async def test_finalize_cancels_active_timers(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        fake_task = MagicMock(spec=asyncio.Task)
        fake_task.done.return_value = False
        p._state.active_timers["t1"] = fake_task

        await p.finalize()

        fake_task.cancel.assert_called_once()
        assert len(p._state.active_timers) == 0

    @pytest.mark.asyncio
    async def test_finalize_skips_done_tasks(self, thread: MagicMock, runner: MagicMock) -> None:
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        fake_task = MagicMock(spec=asyncio.Task)
        fake_task.done.return_value = True
        p._state.active_timers["t1"] = fake_task

        await p.finalize()

        fake_task.cancel.assert_not_called()


class TestCompactHandling:
    """Tests for context compaction event handling."""

    @pytest.mark.asyncio
    async def test_compact_sends_notification(self) -> None:
        thread = MagicMock()
        thread.send = AsyncMock(return_value=MagicMock(embeds=[]))
        runner = MagicMock()
        runner.interrupt = AsyncMock()
        status = MagicMock()
        status.set_compact = AsyncMock()
        status.set_thinking = AsyncMock()
        status._reset_stall_timer = MagicMock()
        config = _make_config(thread, runner, status=status)
        processor = EventProcessor(config)

        event = StreamEvent(
            message_type=MessageType.SYSTEM,
            is_compact=True,
            compact_trigger="auto",
            compact_pre_tokens=167745,
        )
        await processor.process(event)

        status.set_compact.assert_awaited_once()
        # Check that a message was sent to the thread
        embeds = [c.kwargs["embed"] for c in thread.send.call_args_list if "embed" in c.kwargs]
        assert any("compact" in (embed.description or "").lower() for embed in embeds)

    def test_compact_occurred_false_initially(self, thread: MagicMock, runner: MagicMock) -> None:
        config = _make_config(thread, runner)
        p = EventProcessor(config)
        assert p.compact_occurred is False

    @pytest.mark.asyncio
    async def test_compact_sets_compact_occurred(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        runner.interrupt = AsyncMock()
        thread.send = AsyncMock(return_value=MagicMock(embeds=[]))
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        await p.process(StreamEvent(message_type=MessageType.SYSTEM, is_compact=True))

        assert p.compact_occurred is True

    @pytest.mark.asyncio
    async def test_compact_sets_should_drain(self, thread: MagicMock, runner: MagicMock) -> None:
        runner.interrupt = AsyncMock()
        thread.send = AsyncMock(return_value=MagicMock(embeds=[]))
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        await p.process(StreamEvent(message_type=MessageType.SYSTEM, is_compact=True))

        assert p.should_drain is True

    @pytest.mark.asyncio
    async def test_compact_calls_runner_interrupt(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        runner.interrupt = AsyncMock()
        thread.send = AsyncMock(return_value=MagicMock(embeds=[]))
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        await p.process(StreamEvent(message_type=MessageType.SYSTEM, is_compact=True))

        runner.interrupt.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_compact_does_not_interrupt_on_post_compact_rerun(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        """When post_compact_rerun=True, we already added the guardrail — don't interrupt again."""
        runner.interrupt = AsyncMock()
        thread.send = AsyncMock(return_value=MagicMock(embeds=[]))
        config = _make_config(thread, runner, post_compact_rerun=True)
        p = EventProcessor(config)

        await p.process(StreamEvent(message_type=MessageType.SYSTEM, is_compact=True))

        runner.interrupt.assert_not_awaited()
        assert p.compact_occurred is False

    @pytest.mark.asyncio
    async def test_progress_resets_stall_timer(self) -> None:
        thread = MagicMock()
        thread.send = AsyncMock(return_value=MagicMock(embeds=[]))
        runner = MagicMock()
        status = MagicMock()
        status._reset_stall_timer = MagicMock()
        status.set_thinking = AsyncMock()
        config = _make_config(thread, runner, status=status)
        processor = EventProcessor(config)

        event = StreamEvent(message_type=MessageType.PROGRESS)
        await processor.process(event)

        status.set_thinking.assert_awaited_once()


class TestTodoWrite:
    """TodoWrite event handling.

    Each update deletes the previous embed and reposts at the bottom of the
    thread so the task list is always visible as the conversation progresses.
    """

    def _make_todo_event(self) -> StreamEvent:
        return StreamEvent(
            message_type=MessageType.ASSISTANT,
            todo_list=[
                TodoItem(content="Task 1", status="in_progress", active_form="Doing task 1")
            ],
        )

    @pytest.mark.asyncio
    async def test_first_todo_posts_new_message(self, thread: MagicMock, runner: MagicMock) -> None:
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        await p.process(self._make_todo_event())

        embed_sends = [c for c in thread.send.call_args_list if "embed" in c.kwargs]
        assert len(embed_sends) == 1

    @pytest.mark.asyncio
    async def test_second_todo_deletes_old_and_reposts(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        """On the second TodoWrite, the previous message must be deleted and a new one posted."""
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        # First post — let the real flow run so todo_message is set.
        await p.process(self._make_todo_event())

        # Replace the stored reference with a spy so we can assert on delete().
        old_activity = MagicMock()
        old_activity.cancel = AsyncMock()
        p._state.todo_message = old_activity

        # Second update
        await p.process(self._make_todo_event())

        old_activity.cancel.assert_awaited_once()
        embed_sends = [c for c in thread.send.call_args_list if "embed" in c.kwargs]
        assert len(embed_sends) == 2  # initial post + repost

    @pytest.mark.asyncio
    async def test_todo_delete_failure_does_not_raise(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        """Delete failures (e.g. message already gone) must not crash the session."""
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        old_msg = MagicMock(spec=discord.Message)
        old_msg.delete = AsyncMock(side_effect=Exception("Unknown Message"))
        p._state.todo_message = old_msg

        # Must not raise — failure is suppressed.
        await p.process(self._make_todo_event())

    @pytest.mark.asyncio
    async def test_todo_message_none_after_send_failure(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        """A frontend send failure leaves no stale todo activity."""
        thread.send.side_effect = Exception("Server disconnected")
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        await p.process(self._make_todo_event())

        assert p._state.todo_message is None


class TestCcdbAttachmentsDelivery:
    """_on_complete() reads .ccdb-attachments-{thread_id} and sends listed files."""

    def _marker_name(self, thread: MagicMock) -> str:
        return f".ccdb-attachments-{thread.id}"

    @pytest.mark.asyncio
    async def test_sends_files_listed_in_marker(
        self, thread: MagicMock, runner: MagicMock, tmp_path
    ) -> None:
        from unittest.mock import patch

        runner.working_dir = str(tmp_path)
        f = tmp_path / "out.py"
        f.write_text("result = 42", encoding="utf-8")
        (tmp_path / self._marker_name(thread)).write_text(str(f) + "\n", encoding="utf-8")

        config = _make_config(thread, runner)
        p = EventProcessor(config)

        with patch.object(config.surface, "deliver_files", new_callable=AsyncMock) as mock_send:
            await p.process(_make_result_event(session_id="s1"))

        files = mock_send.await_args.args[0]
        assert [item.path for item in files] == [str(f)]

    @pytest.mark.asyncio
    async def test_marker_file_deleted_after_send(
        self, thread: MagicMock, runner: MagicMock, tmp_path
    ) -> None:
        from unittest.mock import patch

        runner.working_dir = str(tmp_path)
        f = tmp_path / "out.py"
        f.write_text("x = 1", encoding="utf-8")
        marker = tmp_path / self._marker_name(thread)
        marker.write_text(str(f) + "\n", encoding="utf-8")

        config = _make_config(thread, runner)
        p = EventProcessor(config)

        with patch.object(config.surface, "deliver_files", new_callable=AsyncMock):
            await p.process(_make_result_event(session_id="s1"))

        assert not marker.exists()

    @pytest.mark.asyncio
    async def test_no_marker_no_send(self, thread: MagicMock, runner: MagicMock, tmp_path) -> None:
        """When the marker is absent nothing is sent."""
        from unittest.mock import patch

        runner.working_dir = str(tmp_path)
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        with patch.object(config.surface, "deliver_files", new_callable=AsyncMock) as mock_send:
            await p.process(_make_result_event(session_id="s1"))

        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_not_sent_on_error(self, thread: MagicMock, runner: MagicMock, tmp_path) -> None:
        """Files are not sent when the session ends with an error."""
        from unittest.mock import patch

        runner.working_dir = str(tmp_path)
        f = tmp_path / "out.py"
        f.write_text("x = 1", encoding="utf-8")
        (tmp_path / self._marker_name(thread)).write_text(str(f) + "\n", encoding="utf-8")

        config = _make_config(thread, runner)
        p = EventProcessor(config)

        with patch.object(config.surface, "deliver_files", new_callable=AsyncMock) as mock_send:
            await p.process(_make_result_event(error="Claude crashed"))

        mock_send.assert_not_called()

    @pytest.mark.asyncio
    async def test_logging_when_marker_found(
        self, thread: MagicMock, runner: MagicMock, tmp_path, caplog
    ) -> None:
        """INFO log is emitted when marker is found and processed."""
        import logging
        from unittest.mock import patch

        runner.working_dir = str(tmp_path)
        f = tmp_path / "out.py"
        f.write_text("x = 1", encoding="utf-8")
        (tmp_path / self._marker_name(thread)).write_text(str(f) + "\n", encoding="utf-8")

        config = _make_config(thread, runner)
        p = EventProcessor(config)

        with (
            patch.object(config.surface, "deliver_files", new_callable=AsyncMock),
            caplog.at_level(logging.INFO, logger="claude_discord.cogs.event_processor"),
        ):
            await p.process(_make_result_event(session_id="s1"))

        assert any("ccdb-attachments" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_logging_when_marker_absent(
        self, thread: MagicMock, runner: MagicMock, tmp_path, caplog
    ) -> None:
        """DEBUG log is emitted when marker is absent."""
        import logging
        from unittest.mock import patch

        runner.working_dir = str(tmp_path)
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        with (
            patch.object(config.surface, "deliver_files", new_callable=AsyncMock),
            caplog.at_level(logging.DEBUG, logger="claude_discord.cogs.event_processor"),
        ):
            await p.process(_make_result_event(session_id="s1"))

        assert any("ccdb-attachments" in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_relative_path_resolved_against_working_dir(
        self, thread: MagicMock, runner: MagicMock, tmp_path
    ) -> None:
        """Relative paths are resolved against working_dir.

        Claude may write a bare filename instead of an absolute path.
        The bot must resolve it against the session working directory so the
        file is found even though the bot process has a different cwd.
        """
        from unittest.mock import patch

        runner.working_dir = str(tmp_path)
        f = tmp_path / "out.py"
        f.write_text("result = 42", encoding="utf-8")
        (tmp_path / self._marker_name(thread)).write_text("out.py\n", encoding="utf-8")

        config = _make_config(thread, runner)
        p = EventProcessor(config)

        with patch.object(config.surface, "deliver_files", new_callable=AsyncMock) as mock_send:
            await p.process(_make_result_event(session_id="s1"))

        files = mock_send.await_args.args[0]
        assert [item.path for item in files] == [str(f)]


class TestAttachmentThreadIsolation:
    """Marker file must be per-thread to prevent cross-contamination."""

    @pytest.mark.asyncio
    async def test_marker_uses_thread_id_in_filename(
        self, thread: MagicMock, runner: MagicMock, tmp_path
    ) -> None:
        """The marker file must include the thread ID to isolate concurrent sessions."""
        from unittest.mock import patch

        runner.working_dir = str(tmp_path)
        f = tmp_path / "out.py"
        f.write_text("result = 42", encoding="utf-8")
        # Write to the thread-specific marker (thread.id = 12345 from conftest)
        (tmp_path / f".ccdb-attachments-{thread.id}").write_text(str(f) + "\n", encoding="utf-8")

        config = _make_config(thread, runner)
        p = EventProcessor(config)

        with patch.object(config.surface, "deliver_files", new_callable=AsyncMock) as mock_send:
            await p.process(_make_result_event(session_id="s1"))

        files = mock_send.await_args.args[0]
        assert [item.path for item in files] == [str(f)]

    @pytest.mark.asyncio
    async def test_other_thread_marker_not_read(
        self, thread: MagicMock, runner: MagicMock, tmp_path
    ) -> None:
        """A marker for thread 99999 must NOT be read by thread 12345."""
        from unittest.mock import patch

        runner.working_dir = str(tmp_path)
        f = tmp_path / "secret.py"
        f.write_text("secret = True", encoding="utf-8")
        # Write to a DIFFERENT thread's marker
        (tmp_path / ".ccdb-attachments-99999").write_text(str(f) + "\n", encoding="utf-8")

        config = _make_config(thread, runner)
        p = EventProcessor(config)

        with patch.object(config.surface, "deliver_files", new_callable=AsyncMock) as mock_send:
            await p.process(_make_result_event(session_id="s1"))

        mock_send.assert_not_called()


class TestToolUseCount:
    """tool_use_count state tracking."""

    @pytest.mark.asyncio
    async def test_tool_use_count_increments(self, thread: MagicMock, runner: MagicMock) -> None:
        """Each tool-use event increments the session's tool_use_count."""
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        assert p._state.tool_use_count == 0
        for i in range(4):
            await p.process(_make_tool_event(tool_id=f"tool{i}"))
        assert p._state.tool_use_count == 4


class TestContextStatsPersistence:
    """context_window / context_used are saved to DB on session complete."""

    @pytest.mark.asyncio
    async def test_context_stats_saved_on_complete(
        self, thread: MagicMock, runner: MagicMock, tmp_path
    ) -> None:
        from claude_discord.database.models import init_db
        from claude_discord.database.repository import SessionRepository

        db_path = str(tmp_path / "sessions.db")
        await init_db(db_path)
        repo = SessionRepository(db_path)
        # Pre-save the session row so update_context_stats has a row to update
        await repo.save(thread_id=thread.id, session_id="s-ctx")

        config = _make_config(thread, runner, repo=repo)
        p = EventProcessor(config)

        result = _make_result_event(
            session_id="s-ctx",
            input_tokens=98200,
            output_tokens=12400,
            cache_read_tokens=23600,
            cache_creation_tokens=12000,
            context_window=200000,
        )
        await p.process(result)

        record = await repo.get(thread.id)
        assert record is not None
        assert record.context_window == 200000
        # context_used = input + cache_read + cache_creation
        assert record.context_used == 98200 + 23600 + 12000

    @pytest.mark.asyncio
    async def test_context_stats_skipped_when_no_repo(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        """Without a repo, context stats save is a no-op (no crash)."""
        config = _make_config(thread, runner)  # no repo
        p = EventProcessor(config)
        result = _make_result_event(
            session_id="s1",
            input_tokens=1000,
            context_window=200000,
        )
        # Should not raise
        await p.process(result)

    @pytest.mark.asyncio
    async def test_context_stats_skipped_when_no_context_window(
        self, thread: MagicMock, runner: MagicMock, tmp_path
    ) -> None:
        """If context_window is absent, skip DB write."""
        from claude_discord.database.models import init_db
        from claude_discord.database.repository import SessionRepository

        db_path = str(tmp_path / "sessions.db")
        await init_db(db_path)
        repo = SessionRepository(db_path)

        config = _make_config(thread, runner, repo=repo)
        p = EventProcessor(config)

        result = _make_result_event(session_id="s2")  # no context_window
        await p.process(result)

        record = await repo.get(thread.id)
        # Session saved but context stats not written
        assert record is None or record.context_window is None


class TestContextStatsUsesPerTurnUsage:
    """Context stats must use per-turn (last assistant) usage, not cumulative RESULT usage.

    The RESULT message's usage field sums token counts across ALL API calls in a session.
    For multi-turn sessions (tool use loops), this cumulative sum can easily exceed the
    context window size, producing wildly inaccurate context utilization percentages.

    The fix: track per-turn usage from assistant messages and use the last turn's values.
    """

    @pytest.mark.asyncio
    async def test_context_used_from_last_assistant_not_cumulative_result(
        self, thread: MagicMock, runner: MagicMock, tmp_path
    ) -> None:
        """Multi-turn session: context_used should reflect last turn, not cumulative."""
        from claude_discord.database.models import init_db
        from claude_discord.database.repository import SessionRepository

        db_path = str(tmp_path / "sessions.db")
        await init_db(db_path)
        repo = SessionRepository(db_path)
        await repo.save(thread_id=thread.id, session_id="s-per-turn")

        config = _make_config(thread, runner, repo=repo)
        p = EventProcessor(config)

        # Simulate turn 1: assistant with tool_use (35k context)
        turn1 = StreamEvent(
            message_type=MessageType.ASSISTANT,
            is_partial=False,
            input_tokens=3,
            output_tokens=44,
            cache_read_tokens=0,
            cache_creation_tokens=35000,
        )
        turn1.tool_use = ToolUseEvent(
            tool_id="t1",
            tool_name="Read",
            tool_input={},
            category=ToolCategory.READ,
        )
        await p.process(turn1)

        # Simulate turn 2: assistant final response (36k context, cache hit)
        turn2 = StreamEvent(
            message_type=MessageType.ASSISTANT,
            text="done",
            is_partial=False,
            input_tokens=100,
            output_tokens=20,
            cache_read_tokens=35000,
            cache_creation_tokens=1000,
        )
        await p.process(turn2)

        # RESULT with cumulative usage (would be 71k+ if summed naively)
        result = _make_result_event(
            session_id="s-per-turn",
            input_tokens=103,  # cumulative: 3 + 100
            output_tokens=64,  # cumulative
            cache_read_tokens=35000,  # cumulative
            cache_creation_tokens=36000,  # cumulative: 35000 + 1000
            context_window=200000,
        )
        await p.process(result)

        record = await repo.get(thread.id)
        assert record is not None
        # Should use last turn's values: 100 + 35000 + 1000 = 36100
        # NOT cumulative: 103 + 35000 + 36000 = 71103
        assert record.context_used == 100 + 35000 + 1000

    @pytest.mark.asyncio
    async def test_falls_back_to_result_when_no_assistant_usage(
        self, thread: MagicMock, runner: MagicMock, tmp_path
    ) -> None:
        """If no assistant usage was tracked, fall back to RESULT usage."""
        from claude_discord.database.models import init_db
        from claude_discord.database.repository import SessionRepository

        db_path = str(tmp_path / "sessions.db")
        await init_db(db_path)
        repo = SessionRepository(db_path)
        await repo.save(thread_id=thread.id, session_id="s-fallback")

        config = _make_config(thread, runner, repo=repo)
        p = EventProcessor(config)

        # Only RESULT, no assistant messages with usage
        result = _make_result_event(
            session_id="s-fallback",
            input_tokens=5000,
            output_tokens=200,
            cache_read_tokens=30000,
            cache_creation_tokens=0,
            context_window=200000,
        )
        await p.process(result)

        record = await repo.get(thread.id)
        assert record is not None
        assert record.context_used == 5000 + 30000 + 0

    @pytest.mark.asyncio
    async def test_message_delta_stream_event_corrects_stale_assistant_output_tokens(
        self, thread: MagicMock, runner: MagicMock, tmp_path
    ) -> None:
        """Regression test for the "2 in 3 out" display bug.

        An ASSISTANT event's own usage is a mid-generation snapshot — for a
        turn that ends with a short text block, output_tokens can be far
        below the true final count (captured live: assistant reported
        output_tokens=1 for a turn that actually used 3). The real final
        usage arrives moments later as a STREAM_EVENT whose parsed
        input_tokens/output_tokens come from the message_delta's usage
        (see claude_code_core.parser._parse_stream_event). That later event
        must win — not the assistant's stale snapshot — in both the
        completion-notice fields (event.output_tokens) and context stats.
        """
        from claude_discord.database.models import init_db
        from claude_discord.database.repository import SessionRepository

        db_path = str(tmp_path / "sessions.db")
        await init_db(db_path)
        repo = SessionRepository(db_path)
        await repo.save(thread_id=thread.id, session_id="s-message-delta")

        config = _make_config(thread, runner, repo=repo)
        p = EventProcessor(config)

        # ASSISTANT: mid-generation snapshot, output_tokens under-reported.
        assistant_turn = StreamEvent(
            message_type=MessageType.ASSISTANT,
            text="done",
            is_partial=False,
            input_tokens=2,
            output_tokens=1,
            cache_read_tokens=54432,
            cache_creation_tokens=2471,
        )
        await p.process(assistant_turn)

        # STREAM_EVENT (message_delta): the true final usage for that turn.
        message_delta = StreamEvent(
            message_type=MessageType.STREAM_EVENT,
            input_tokens=2,
            output_tokens=3,
            cache_read_tokens=54432,
            cache_creation_tokens=2471,
        )
        await p.process(message_delta)

        result = _make_result_event(
            session_id="s-message-delta",
            input_tokens=4,
            output_tokens=368,
            cache_read_tokens=54432,
            cache_creation_tokens=56903,
            context_window=200000,
        )
        await p.process(result)

        # The completion notice must show the corrected 3, not the stale 1.
        assert result.output_tokens == 3
        assert result.input_tokens == 2

        record = await repo.get(thread.id)
        assert record is not None
        assert record.context_used == 2 + 54432 + 2471


class TestRateLimitEventProcessing:
    """rate_limit_event is saved to usage_stats table."""

    @pytest.mark.asyncio
    async def test_rate_limit_event_saved_to_db(
        self, thread: MagicMock, runner: MagicMock, tmp_path
    ) -> None:
        from claude_discord.claude.types import RateLimitInfo
        from claude_discord.database.models import init_db
        from claude_discord.database.repository import UsageStatsRepository

        db_path = str(tmp_path / "sessions.db")
        await init_db(db_path)
        usage_repo = UsageStatsRepository(db_path)

        config = _make_config(thread, runner, usage_repo=usage_repo)
        p = EventProcessor(config)

        event = StreamEvent(
            message_type=MessageType.RATE_LIMIT_EVENT,
            rate_limit_info=RateLimitInfo(
                rate_limit_type="five_hour",
                status="allowed",
                utilization=0.61,
                resets_at=1234567890,
            ),
        )
        await p.process(event)

        rows = await usage_repo.get_latest()
        assert len(rows) == 1
        assert rows[0].rate_limit_type == "five_hour"
        assert rows[0].utilization == pytest.approx(0.61)

    @pytest.mark.asyncio
    async def test_rate_limit_event_skipped_when_no_usage_repo(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        """Without usage_repo, rate_limit_event is a no-op."""
        from claude_discord.claude.types import RateLimitInfo

        config = _make_config(thread, runner)
        p = EventProcessor(config)

        event = StreamEvent(
            message_type=MessageType.RATE_LIMIT_EVENT,
            rate_limit_info=RateLimitInfo(
                rate_limit_type="seven_day",
                status="allowed",
                utilization=0.4,
                resets_at=999,
            ),
        )
        await p.process(event)  # should not raise


class TestChatOnlyMode:
    """chat_only mode hides tool embeds, thinking, session chrome but keeps text."""

    def _make_chat_only_config(self, thread: MagicMock, runner: MagicMock, **kwargs) -> RunConfig:
        return _make_config(thread, runner, chat_only=True, **kwargs)

    @pytest.mark.asyncio
    async def test_no_session_start_embed(self, thread: MagicMock, runner: MagicMock) -> None:
        """chat_only skips the session_start embed for new sessions."""
        config = self._make_chat_only_config(thread, runner)
        p = EventProcessor(config)

        await p.process(StreamEvent(message_type=MessageType.SYSTEM, session_id="s1"))

        # No embed should be sent
        embed_sends = [c for c in thread.send.call_args_list if "embed" in c.kwargs]
        assert len(embed_sends) == 0

    @pytest.mark.asyncio
    async def test_thinking_not_shown(self, thread: MagicMock, runner: MagicMock) -> None:
        """chat_only hides thinking embeds."""
        config = self._make_chat_only_config(thread, runner)
        p = EventProcessor(config)

        event = StreamEvent(
            message_type=MessageType.ASSISTANT,
            thinking="I am thinking...",
            is_partial=False,
        )
        await p.process(event)

        embed_sends = [c for c in thread.send.call_args_list if "embed" in c.kwargs]
        assert len(embed_sends) == 0

    @pytest.mark.asyncio
    async def test_tool_use_no_embed(self, thread: MagicMock, runner: MagicMock) -> None:
        """chat_only does not post tool use embeds but still counts them."""
        config = self._make_chat_only_config(thread, runner)
        p = EventProcessor(config)

        await p.process(_make_tool_event("t1"))

        # No embed sent
        embed_sends = [c for c in thread.send.call_args_list if "embed" in c.kwargs]
        assert len(embed_sends) == 0
        # But tool_use_count is incremented
        assert p._state.tool_use_count == 1

    @pytest.mark.asyncio
    async def test_text_still_shown(self, thread: MagicMock, runner: MagicMock) -> None:
        """chat_only still sends text responses."""
        config = self._make_chat_only_config(thread, runner)
        p = EventProcessor(config)

        event = StreamEvent(message_type=MessageType.ASSISTANT, text="Hello!", is_partial=False)
        await p.process(event)

        text_sends = [
            c for c in thread.send.call_args_list if c.args and isinstance(c.args[0], str)
        ]
        assert any("Hello!" in c.args[0] for c in text_sends)

    @pytest.mark.asyncio
    async def test_tool_result_resets_status_only(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        """chat_only tool result resets status without updating embeds."""
        status = MagicMock()
        status.set_thinking = AsyncMock()
        status.set_tool = AsyncMock()
        config = self._make_chat_only_config(thread, runner, status=status)
        p = EventProcessor(config)

        result_event = StreamEvent(
            message_type=MessageType.USER,
            tool_result_id="t1",
            tool_result_content="output",
        )
        await p.process(result_event)

        status.set_thinking.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_complete_no_session_embed(self, thread: MagicMock, runner: MagicMock) -> None:
        """chat_only skips session_complete embed on RESULT."""
        status = MagicMock()
        status.set_done = AsyncMock()
        config = self._make_chat_only_config(thread, runner, status=status)
        p = EventProcessor(config)

        await p.process(_make_result_event(session_id="s1"))

        # No session_complete embed sent
        embed_sends = [c for c in thread.send.call_args_list if "embed" in c.kwargs]
        assert len(embed_sends) == 0
        # But done status is set
        status.set_done.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_ask_user_question_still_handled(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        """AskUserQuestion is always processed even in chat_only mode."""
        runner.interrupt = AsyncMock()
        config = self._make_chat_only_config(thread, runner)
        p = EventProcessor(config)

        ask = AskQuestion(
            question="Which option?",
            options=[AskOption(label="A"), AskOption(label="B")],
        )
        event = StreamEvent(
            message_type=MessageType.ASSISTANT,
            ask_questions=[ask],
        )
        await p.process(event)

        assert p.pending_ask == [ask]
        runner.interrupt.assert_called_once()

    @pytest.mark.asyncio
    async def test_compact_notification_hidden(self, thread: MagicMock, runner: MagicMock) -> None:
        """chat_only hides the compact notification message."""
        runner.interrupt = AsyncMock()
        thread.send = AsyncMock(return_value=MagicMock(embeds=[]))
        config = self._make_chat_only_config(thread, runner)
        p = EventProcessor(config)

        await p.process(StreamEvent(message_type=MessageType.SYSTEM, is_compact=True))

        # No compact message sent to thread (only interrupt happened)
        text_sends = [
            c for c in thread.send.call_args_list if c.args and isinstance(c.args[0], str)
        ]
        assert not any("compact" in str(c).lower() for c in text_sends)


class TestPermissionAutoApprove:
    """Permission requests are auto-approved when dangerously_skip_permissions is True.

    Workaround for CLI v2.1.78+ regression (upstream #35895) where
    --dangerously-skip-permissions fails to bypass file-level sensitive-path
    checks on Edit/Write tools.
    """

    @pytest.mark.asyncio
    async def test_auto_approve_when_yolo_enabled(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        """permission_request is auto-approved without Discord UI when yolo mode is on."""
        from claude_discord.claude.types import PermissionRequest

        runner.dangerously_skip_permissions = True
        runner.inject_tool_result = AsyncMock()
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        event = StreamEvent(
            message_type=MessageType.SYSTEM,
            permission_request=PermissionRequest(
                request_id="req-yolo-1",
                tool_name="Edit",
                tool_input={"file_path": "/home/user/.bashrc"},
            ),
        )
        await p.process(event)

        runner.inject_tool_result.assert_called_once_with("req-yolo-1", {"approved": True})
        # No embed/view posted to Discord
        thread.send.assert_not_called()

    @pytest.mark.asyncio
    async def test_shows_ui_when_yolo_disabled(self, thread: MagicMock, runner: MagicMock) -> None:
        """permission_request shows Allow/Deny buttons when yolo mode is off."""
        from claude_discord.claude.types import PermissionRequest

        runner.dangerously_skip_permissions = False
        runner.inject_tool_result = AsyncMock()
        config = _make_config(thread, runner)
        p = EventProcessor(config)

        event = StreamEvent(
            message_type=MessageType.SYSTEM,
            permission_request=PermissionRequest(
                request_id="req-normal-1",
                tool_name="Edit",
                tool_input={"file_path": "/home/user/.bashrc"},
            ),
        )
        await p.process(event)
        await _wait_for_prompt_message(thread)

        # inject_tool_result NOT called — user must click Allow/Deny
        runner.inject_tool_result.assert_not_called()
        # Discord embed + view posted
        thread.send.assert_called_once()
        await p.cancel_prompts()


class TestUserActionMentions:
    """Messages that pause Claude for button input mention the requester."""

    @pytest.mark.asyncio
    async def test_plan_approval_mentions_notify_user(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        runner.inject_tool_result = AsyncMock()
        config = _make_config(thread, runner, notify_user_id=42)
        p = EventProcessor(config)

        await p.process(
            StreamEvent(
                message_type=MessageType.ASSISTANT,
                text="Implementation plan",
                is_plan_approval=True,
            )
        )
        await _wait_for_prompt_message(thread)

        assert "<@42>" in _posted_mentions(thread)
        await p.cancel_prompts()

    @pytest.mark.asyncio
    async def test_permission_request_mentions_notify_user(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        from claude_discord.claude.types import PermissionRequest

        runner.dangerously_skip_permissions = False
        runner.inject_tool_result = AsyncMock()
        config = _make_config(thread, runner, notify_user_id=42)
        p = EventProcessor(config)

        await p.process(
            StreamEvent(
                message_type=MessageType.SYSTEM,
                permission_request=PermissionRequest(
                    request_id="req-normal-1",
                    tool_name="Edit",
                    tool_input={"file_path": "/home/user/.bashrc"},
                ),
            )
        )
        await _wait_for_prompt_message(thread)

        thread.send.assert_called_once()
        assert "<@42>" in _posted_mentions(thread)
        await p.cancel_prompts()

    @pytest.mark.asyncio
    async def test_elicitation_mentions_notify_user(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        from claude_discord.claude.types import ElicitationRequest

        runner.inject_tool_result = AsyncMock()
        config = _make_config(thread, runner, notify_user_id=42)
        p = EventProcessor(config)

        await p.process(
            StreamEvent(
                message_type=MessageType.SYSTEM,
                elicitation=ElicitationRequest(
                    request_id="elicit-1",
                    server_name="example",
                    mode="form-mode",
                    message="Need input",
                ),
            )
        )
        await _wait_for_prompt_message(thread)

        thread.send.assert_called_once()
        assert "<@42>" in _posted_mentions(thread)
        await p.cancel_prompts()


class TestRejectedResumeIsNotPersisted:
    """A resume the CLI rejected must not re-tag the stored session."""

    @pytest.mark.asyncio
    async def test_error_before_any_session_does_not_save(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        """`claude --resume <codex id>` echoes the ID in an error RESULT.

        Saving it would stamp backend="claude" on a Codex rollout ID, after
        which every backend check trusts the wrong tag and the thread stays
        dead.
        """
        repo = MagicMock()
        repo.save = AsyncMock()
        config = _make_config(thread, runner, repo=repo, session_id="codex-rollout")
        p = EventProcessor(config)

        await p.process(
            _make_result_event(
                session_id="codex-rollout",
                error="No conversation found with session ID: codex-rollout",
            )
        )

        repo.save.assert_not_called()

    @pytest.mark.asyncio
    async def test_error_after_session_opened_still_saves(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        """A turn that failed mid-way did open a real session — keep it."""
        repo = MagicMock()
        repo.save = AsyncMock()
        config = _make_config(thread, runner, repo=repo)
        p = EventProcessor(config)

        await p.process(StreamEvent(message_type=MessageType.SYSTEM, session_id="s1"))
        repo.save.reset_mock()
        await p.process(_make_result_event(session_id="s1", error="API 529"))

        repo.save.assert_called_once()
        assert repo.save.call_args.args[1] == "s1"

    @pytest.mark.asyncio
    async def test_successful_result_without_system_event_still_saves(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        repo = MagicMock()
        repo.save = AsyncMock()
        config = _make_config(thread, runner, repo=repo)
        p = EventProcessor(config)

        await p.process(_make_result_event(session_id="s2"))

        repo.save.assert_called_once()
