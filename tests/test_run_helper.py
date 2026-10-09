"""Tests for _run_helper module: streaming, intermediate text, tool results."""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_discord.claude.types import (
    AskOption,
    AskQuestion,
    ImageData,
    MessageType,
    StreamEvent,
    ToolCategory,
    ToolUseEvent,
)
from claude_discord.cogs import _run_helper as _rh_module
from claude_discord.cogs._run_helper import (
    TOOL_RESULT_MAX_CHARS,
    _make_error_embed,
    _truncate_result,
    configure_session_limit,
    run_claude_in_thread,
    run_claude_with_config,
)
from claude_discord.cogs.run_config import RunConfig
from claude_discord.concurrency import SessionRegistry
from claude_discord.discord_ui.streaming_manager import StreamingMessageManager
from claude_discord.discord_ui.tool_timer import LiveToolTimer
from claude_discord.session_slots import (
    SessionSlotScheduler,
    get_session_slots,
    set_session_slots,
)


class TestTruncateResult:
    def test_short_content_unchanged(self) -> None:
        assert _truncate_result("hello") == "hello"

    def test_exact_limit_unchanged(self) -> None:
        text = "x" * TOOL_RESULT_MAX_CHARS
        assert _truncate_result(text) == text

    def test_long_content_truncated(self) -> None:
        text = "x" * (TOOL_RESULT_MAX_CHARS + 100)
        result = _truncate_result(text)
        assert result.endswith("... (truncated)")
        assert len(result) < len(text)

    def test_empty_content(self) -> None:
        assert _truncate_result("") == ""


class TestStreamingMessageManager:
    @pytest.fixture
    def thread(self) -> MagicMock:
        t = MagicMock(spec=discord.Thread)
        msg = MagicMock(spec=discord.Message)
        t.send = AsyncMock(return_value=msg)
        msg.edit = AsyncMock()
        return t

    @pytest.mark.asyncio
    async def test_has_content_initially_false(self, thread: MagicMock) -> None:
        mgr = StreamingMessageManager(thread)
        assert mgr.has_content is False

    @pytest.mark.asyncio
    async def test_append_sets_has_content(self, thread: MagicMock) -> None:
        mgr = StreamingMessageManager(thread)
        await mgr.append("hello")
        assert mgr.has_content is True

    @pytest.mark.asyncio
    async def test_finalize_returns_buffer(self, thread: MagicMock) -> None:
        mgr = StreamingMessageManager(thread)
        mgr._buffer = "test content"
        result = await mgr.finalize()
        assert result == "test content"

    @pytest.mark.asyncio
    async def test_finalize_sends_message(self, thread: MagicMock) -> None:
        mgr = StreamingMessageManager(thread)
        mgr._buffer = "test content"
        await mgr.finalize()
        thread.send.assert_called_once_with("test content")

    @pytest.mark.asyncio
    async def test_append_after_finalize_ignored(self, thread: MagicMock) -> None:
        mgr = StreamingMessageManager(thread)
        mgr._buffer = "first"
        await mgr.finalize()
        await mgr.append("second")
        # Buffer should still be "first" — append after finalize is ignored
        assert mgr._buffer == "first"

    @pytest.mark.asyncio
    async def test_flush_survives_connection_error(self, thread: MagicMock) -> None:
        """Non-discord.HTTPException errors (e.g. ServerDisconnectedError) must not propagate.

        Previously only discord.HTTPException was caught, so aiohttp.ClientError
        (which ServerDisconnectedError inherits from) would propagate and crash
        the entire session on bot shutdown.
        """
        thread.send.side_effect = Exception("Server disconnected")
        mgr = StreamingMessageManager(thread)
        mgr._buffer = "hello"
        # Should not raise — connection errors are suppressed.
        await mgr.finalize()


class TestPartialMessageStreaming:
    """Tests for partial message streaming behavior introduced with --include-partial-messages.

    stream-json delivers the FULL accumulated text on each partial event, not just a delta.
    The handler must compute deltas and stream them into a single Discord message
    (edit in-place) rather than creating new messages for every partial event.
    """

    @pytest.fixture
    def thread(self) -> MagicMock:
        t = MagicMock(spec=discord.Thread)
        t.id = 99999
        msg = MagicMock(spec=discord.Message)
        msg.edit = AsyncMock()
        t.send = AsyncMock(return_value=msg)
        return t

    @pytest.fixture
    def runner(self) -> MagicMock:
        return MagicMock()

    def _make_async_gen(self, events: list[StreamEvent]):
        async def gen(*args, **kwargs):
            for e in events:
                yield e

        return gen

    @pytest.mark.asyncio
    async def test_partial_text_does_not_flood_discord(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        """Multiple partial text events should not each create a new Discord message.

        With --include-partial-messages, stream-json sends the same message many times
        with growing text. We must NOT call thread.send() on every partial update.
        """
        events = [
            StreamEvent(message_type=MessageType.SYSTEM, session_id="s1"),
            StreamEvent(message_type=MessageType.ASSISTANT, text="I'll", is_partial=True),
            StreamEvent(message_type=MessageType.ASSISTANT, text="I'll read", is_partial=True),
            StreamEvent(
                message_type=MessageType.ASSISTANT,
                text="I'll read the file.",
                is_partial=False,  # complete
                tool_use=ToolUseEvent(
                    tool_id="t1",
                    tool_name="Read",
                    tool_input={"file_path": "/tmp/test.py"},
                    category=ToolCategory.READ,
                ),
            ),
            StreamEvent(message_type=MessageType.USER, tool_result_id="t1"),
            StreamEvent(
                message_type=MessageType.RESULT,
                is_complete=True,
                session_id="s1",
                cost_usd=0.01,
                duration_ms=1000,
            ),
        ]
        runner.run = self._make_async_gen(events)

        await run_claude_in_thread(thread, runner, None, "test", None)

        # Partial events must NOT each create a new message.
        # The first partial triggers thread.send() once (to create the message),
        # then subsequent partials/tool edits it in-place via msg.edit().
        text_sends = [
            c for c in thread.send.call_args_list if c.args and isinstance(c.args[0], str)
        ]
        # "I'll read the file." should appear at most once as a fresh send
        assert len([c for c in text_sends if "I'll" in c.args[0]]) <= 1

    @pytest.mark.asyncio
    async def test_partial_text_finalizes_before_tool_embed(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        """Streaming text must be finalized before a tool embed is posted."""
        events = [
            StreamEvent(message_type=MessageType.SYSTEM, session_id="s1"),
            StreamEvent(message_type=MessageType.ASSISTANT, text="Reading now...", is_partial=True),
            StreamEvent(
                message_type=MessageType.ASSISTANT,
                text="Reading now...",
                is_partial=False,
                tool_use=ToolUseEvent(
                    tool_id="t1",
                    tool_name="Read",
                    tool_input={"file_path": "/tmp/x.py"},
                    category=ToolCategory.READ,
                ),
            ),
            StreamEvent(message_type=MessageType.USER, tool_result_id="t1"),
            StreamEvent(
                message_type=MessageType.RESULT,
                is_complete=True,
                session_id="s1",
                cost_usd=0.01,
                duration_ms=500,
            ),
        ]
        runner.run = self._make_async_gen(events)

        await run_claude_in_thread(thread, runner, None, "test", None)

        # At least one non-embed send (the text) and at least one embed send (tool)
        text_sends = [
            c for c in thread.send.call_args_list if c.args and isinstance(c.args[0], str)
        ]
        embed_sends = [c for c in thread.send.call_args_list if "embed" in c.kwargs]
        assert len(text_sends) >= 1
        assert len(embed_sends) >= 1

    @pytest.mark.asyncio
    async def test_partial_thinking_not_posted_as_embed(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        """Partial thinking events must NOT each create a thinking embed.

        Only the final complete thinking block should produce an embed.
        """
        events = [
            StreamEvent(message_type=MessageType.SYSTEM, session_id="s1"),
            # Partial thinking events (simulating --include-partial-messages)
            StreamEvent(message_type=MessageType.ASSISTANT, thinking="Let me", is_partial=True),
            StreamEvent(
                message_type=MessageType.ASSISTANT,
                thinking="Let me think about this...",
                is_partial=True,
            ),
            # Complete message with final thinking + text
            StreamEvent(
                message_type=MessageType.ASSISTANT,
                thinking="Let me think about this carefully.",
                text="Here is my answer.",
                is_partial=False,
            ),
            StreamEvent(
                message_type=MessageType.RESULT,
                is_complete=True,
                text="Here is my answer.",
                session_id="s1",
                cost_usd=0.01,
                duration_ms=1000,
            ),
        ]
        runner.run = self._make_async_gen(events)

        await run_claude_in_thread(thread, runner, None, "test", None)

        embed_sends = [c for c in thread.send.call_args_list if "embed" in c.kwargs]
        thinking_embeds = [
            c
            for c in embed_sends
            if hasattr(c.kwargs.get("embed"), "title")
            and "Thinking" in (c.kwargs["embed"].title or "")
        ]
        # Exactly ONE thinking embed — the final complete one, not each partial
        assert len(thinking_embeds) == 1

    @pytest.mark.asyncio
    async def test_is_partial_detection_in_parser(self) -> None:
        """Assistant messages are treated as complete content blocks.

        The current Claude Code CLI emits one fully-assembled block per
        ``assistant`` message and delivers token-level partials as separate
        ``stream_event`` messages (which ccdb ignores). ``stop_reason`` is no
        longer populated on the ``assistant`` message — so a ``null`` value must
        NOT be interpreted as "still streaming", otherwise ``not is_partial``
        gated handlers (plan approval, thinking display) never fire.
        """
        from claude_discord.claude.parser import parse_line

        # stop_reason: null — current CLI shape — must be COMPLETE, not partial.
        null_stop = parse_line(
            '{"type": "assistant", "message": {"stop_reason": null, "content": '
            '[{"type": "text", "text": "hello"}]}}'
        )
        assert null_stop is not None
        assert null_stop.is_partial is False

        complete = parse_line(
            '{"type": "assistant", "message": {"stop_reason": "end_turn", "content": '
            '[{"type": "text", "text": "hello"}]}}'
        )
        assert complete is not None
        assert complete.is_partial is False

        tool_stop = parse_line(
            '{"type": "assistant", "message": {"stop_reason": "tool_use", "content": '
            '[{"type": "text", "text": "hello"}]}}'
        )
        assert tool_stop is not None
        assert tool_stop.is_partial is False

    @pytest.mark.asyncio
    async def test_exit_plan_mode_block_is_actionable(self) -> None:
        """An ExitPlanMode block with stop_reason: null must fire plan approval.

        Regression test for the "session stuck running" hang: the CLI presents
        a plan via ExitPlanMode (stop_reason null on the assistant message);
        if the parser marks it partial, the ``not is_partial`` gate suppresses
        the approval UI and the CLI blocks forever waiting for an answer.
        """
        from claude_discord.claude.parser import parse_line

        event = parse_line(
            '{"type": "assistant", "message": {"stop_reason": null, "content": '
            '[{"type": "tool_use", "id": "t1", "name": "ExitPlanMode", '
            '"input": {"plan": "do the thing"}}]}}'
        )
        assert event is not None
        assert event.is_plan_approval is True
        assert event.is_partial is False  # gate must allow the approval UI to show


class TestRunClaudeInThread:
    """Integration tests for run_claude_in_thread with mocked runner."""

    @pytest.fixture
    def thread(self) -> MagicMock:
        t = MagicMock(spec=discord.Thread)
        t.id = 12345
        t.send = AsyncMock(return_value=MagicMock(spec=discord.Message))
        return t

    @pytest.fixture
    def repo(self) -> MagicMock:
        r = MagicMock()
        r.save = AsyncMock()
        return r

    @pytest.fixture
    def runner(self) -> MagicMock:
        r = MagicMock()
        return r

    def _make_async_gen(self, events: list[StreamEvent]):
        """Create a mock async generator from a list of events."""

        async def gen(*args, **kwargs):
            for e in events:
                yield e

        return gen

    @pytest.mark.asyncio
    async def test_intermediate_text_posted_immediately(
        self, thread: MagicMock, runner: MagicMock, repo: MagicMock
    ) -> None:
        """Intermediate assistant text should be posted to thread, not just accumulated."""
        events = [
            StreamEvent(message_type=MessageType.SYSTEM, session_id="sess-1"),
            StreamEvent(message_type=MessageType.ASSISTANT, text="I'll read the file now."),
            StreamEvent(
                message_type=MessageType.ASSISTANT,
                tool_use=ToolUseEvent(
                    tool_id="t1",
                    tool_name="Read",
                    tool_input={"file_path": "/tmp/test.py"},
                    category=ToolCategory.READ,
                ),
            ),
            StreamEvent(message_type=MessageType.USER, tool_result_id="t1"),
            StreamEvent(message_type=MessageType.ASSISTANT, text="Here's what I found."),
            StreamEvent(
                message_type=MessageType.RESULT,
                is_complete=True,
                text="Here's what I found.",
                session_id="sess-1",
                cost_usd=0.01,
                duration_ms=2000,
            ),
        ]
        runner.run = self._make_async_gen(events)

        await run_claude_in_thread(thread, runner, repo, "test", None)

        # Check that intermediate text was posted (not just final)
        send_calls = thread.send.call_args_list
        text_messages = [c for c in send_calls if c.args and isinstance(c.args[0], str)]
        assert len(text_messages) >= 2  # "I'll read the file now." + "Here's what I found."
        assert text_messages[0].args[0] == "I'll read the file now."

    @pytest.mark.asyncio
    async def test_tool_result_updates_embed(
        self, thread: MagicMock, runner: MagicMock, repo: MagicMock
    ) -> None:
        """Tool result content should update the tool use embed."""
        tool_msg = MagicMock(spec=discord.Message)
        tool_msg.edit = AsyncMock()
        tool_msg.embeds = [MagicMock(title="📖 Reading: /tmp/test.py...")]
        thread.send = AsyncMock(return_value=tool_msg)

        events = [
            StreamEvent(message_type=MessageType.SYSTEM, session_id="sess-1"),
            StreamEvent(
                message_type=MessageType.ASSISTANT,
                tool_use=ToolUseEvent(
                    tool_id="t1",
                    tool_name="Read",
                    tool_input={"file_path": "/tmp/test.py"},
                    category=ToolCategory.READ,
                ),
            ),
            StreamEvent(
                message_type=MessageType.USER,
                tool_result_id="t1",
                tool_result_content="print('hello world')",
            ),
            StreamEvent(
                message_type=MessageType.RESULT,
                is_complete=True,
                session_id="sess-1",
                cost_usd=0.01,
                duration_ms=1000,
            ),
        ]
        runner.run = self._make_async_gen(events)

        await run_claude_in_thread(thread, runner, repo, "test", None)

        # Tool message should have been edited with result
        tool_msg.edit.assert_called()

    @pytest.mark.asyncio
    async def test_thinking_posted_as_embed(
        self, thread: MagicMock, runner: MagicMock, repo: MagicMock
    ) -> None:
        """Extended thinking should be posted as a spoiler embed."""
        events = [
            StreamEvent(message_type=MessageType.SYSTEM, session_id="sess-1"),
            StreamEvent(message_type=MessageType.ASSISTANT, thinking="Let me analyze this..."),
            StreamEvent(
                message_type=MessageType.RESULT,
                is_complete=True,
                text="Done!",
                session_id="sess-1",
                cost_usd=0.01,
                duration_ms=500,
            ),
        ]
        runner.run = self._make_async_gen(events)

        await run_claude_in_thread(thread, runner, repo, "test", None)

        # Check that thinking embed was sent
        embed_calls = [c for c in thread.send.call_args_list if "embed" in c.kwargs]
        thinking_embeds = [
            c
            for c in embed_calls
            if hasattr(c.kwargs.get("embed"), "title")
            and "Thinking" in (c.kwargs["embed"].title or "")
        ]
        assert len(thinking_embeds) == 1
        # Description must use a plain code block (no spoiler) for guaranteed readability
        embed = thinking_embeds[0].kwargs["embed"]
        assert embed.description is not None
        assert embed.description.startswith("```")
        assert embed.description.endswith("```")
        assert "||" not in embed.description

    @pytest.mark.asyncio
    async def test_redacted_thinking_posted_as_embed(
        self, thread: MagicMock, runner: MagicMock, repo: MagicMock
    ) -> None:
        """A redacted_thinking block should post a placeholder embed."""
        events = [
            StreamEvent(message_type=MessageType.SYSTEM, session_id="sess-1"),
            StreamEvent(message_type=MessageType.ASSISTANT, has_redacted_thinking=True),
            StreamEvent(
                message_type=MessageType.RESULT,
                is_complete=True,
                text="Done.",
                session_id="sess-1",
            ),
        ]
        runner.run = self._make_async_gen(events)

        await run_claude_in_thread(thread, runner, repo, "test", None)

        embed_calls = [c for c in thread.send.call_args_list if "embed" in c.kwargs]
        titles = [c.kwargs["embed"].title or "" for c in embed_calls]
        assert any("redacted" in t.lower() for t in titles)

    @pytest.mark.asyncio
    async def test_error_handling(
        self, thread: MagicMock, runner: MagicMock, repo: MagicMock
    ) -> None:
        """Errors should be posted as error embeds."""
        events = [
            StreamEvent(
                message_type=MessageType.RESULT,
                is_complete=True,
                error="Something went wrong",
            ),
        ]
        runner.run = self._make_async_gen(events)

        await run_claude_in_thread(thread, runner, repo, "test", None)

        embed_calls = [c for c in thread.send.call_args_list if "embed" in c.kwargs]
        assert any("Error" in (c.kwargs["embed"].title or "") for c in embed_calls)

    @pytest.mark.asyncio
    async def test_unexpected_exception_includes_detail_in_embed(
        self, thread: MagicMock, runner: MagicMock, repo: MagicMock
    ) -> None:
        """When an unexpected exception occurs, the error embed should include the detail."""

        async def _failing_run(*args, **kwargs):
            raise AttributeError("'str' object has no attribute 'get'")
            yield  # make it an async generator  # noqa: RUF028

        runner.run = _failing_run

        await run_claude_in_thread(thread, runner, repo, "test", None)

        embed_calls = [c for c in thread.send.call_args_list if "embed" in c.kwargs]
        descriptions = [c.kwargs["embed"].description or "" for c in embed_calls]
        assert any("AttributeError" in d for d in descriptions)
        assert any("'str' object has no attribute 'get'" in d for d in descriptions)

    @pytest.mark.asyncio
    async def test_error_embed_send_failure_does_not_raise(
        self, thread: MagicMock, runner: MagicMock, repo: MagicMock
    ) -> None:
        """If thread.send fails when sending the error embed, it should not crash.

        This happens during bot shutdown: the Discord connection closes, then
        the session fails, and the error-embed send also fails (ServerDisconnectedError).
        The exception handler must suppress the secondary failure.
        """

        async def _failing_run(*args, **kwargs):
            raise Exception("Server disconnected")
            yield  # make it an async generator

        runner.run = _failing_run

        # Also make the error-embed send fail to simulate closed connection
        thread.send.side_effect = Exception("Server disconnected")

        # Should not raise even though both the session and the error-embed send fail
        result = await run_claude_in_thread(thread, runner, repo, "test", None)
        assert result is None  # returns None because session_id was never set

    @pytest.mark.asyncio
    async def test_duplicate_final_text_not_reposted(
        self, thread: MagicMock, runner: MagicMock, repo: MagicMock
    ) -> None:
        """If result.text == last posted text, don't post it again."""
        events = [
            StreamEvent(message_type=MessageType.SYSTEM, session_id="sess-1"),
            StreamEvent(message_type=MessageType.ASSISTANT, text="Final answer."),
            StreamEvent(
                message_type=MessageType.RESULT,
                is_complete=True,
                text="Final answer.",
                session_id="sess-1",
                cost_usd=0.01,
                duration_ms=500,
            ),
        ]
        runner.run = self._make_async_gen(events)

        await run_claude_in_thread(thread, runner, repo, "test", None)

        # "Final answer." should appear only once as text
        text_messages = [
            c for c in thread.send.call_args_list if c.args and isinstance(c.args[0], str)
        ]
        final_msgs = [c for c in text_messages if c.args[0] == "Final answer."]
        assert len(final_msgs) == 1

    @pytest.mark.asyncio
    async def test_duplicate_not_reposted_when_result_text_differs_slightly(
        self, thread: MagicMock, runner: MagicMock, repo: MagicMock
    ) -> None:
        """Even if result.text differs slightly from ASSISTANT text, don't re-send.

        The RESULT event's `result` field can have subtle formatting differences
        (trailing whitespace, newlines) compared to the ASSISTANT event text.
        The old string-comparison guard would fail in this case and send the
        text a second time. The flag-based guard prevents this.
        """
        events = [
            StreamEvent(message_type=MessageType.SYSTEM, session_id="sess-1"),
            StreamEvent(message_type=MessageType.ASSISTANT, text="Final answer."),
            StreamEvent(
                message_type=MessageType.RESULT,
                is_complete=True,
                text="Final answer.\n",  # trailing newline — subtle difference
                session_id="sess-1",
                cost_usd=0.01,
                duration_ms=500,
            ),
        ]
        runner.run = self._make_async_gen(events)

        await run_claude_in_thread(thread, runner, repo, "test", None)

        # Text should appear only once despite the trailing newline difference
        text_messages = [
            c for c in thread.send.call_args_list if c.args and isinstance(c.args[0], str)
        ]
        final_msgs = [c for c in text_messages if "Final answer." in c.args[0]]
        assert len(final_msgs) == 1

    @pytest.mark.asyncio
    async def test_repo_none_skips_save(self, thread: MagicMock, runner: MagicMock) -> None:
        """When repo is None (automated workflows), session save should be skipped."""
        events = [
            StreamEvent(message_type=MessageType.SYSTEM, session_id="sess-1"),
            StreamEvent(message_type=MessageType.ASSISTANT, text="Done."),
            StreamEvent(
                message_type=MessageType.RESULT,
                is_complete=True,
                text="Done.",
                session_id="sess-1",
                cost_usd=0.01,
                duration_ms=500,
            ),
        ]
        runner.run = self._make_async_gen(events)

        # Should not raise even with repo=None
        result = await run_claude_in_thread(thread, runner, None, "test", None)
        assert result == "sess-1"

    @pytest.mark.asyncio
    async def test_timeout_error_uses_timeout_embed(
        self, thread: MagicMock, runner: MagicMock, repo: MagicMock
    ) -> None:
        """Timeout errors should use timeout_embed, not the generic error_embed."""
        events = [
            StreamEvent(
                message_type=MessageType.RESULT,
                is_complete=True,
                error="Timed out after 300 seconds",
            ),
        ]
        runner.run = self._make_async_gen(events)

        await run_claude_in_thread(thread, runner, repo, "test", None)

        embed_calls = [c for c in thread.send.call_args_list if "embed" in c.kwargs]
        assert any("timed out" in (c.kwargs["embed"].title or "").lower() for c in embed_calls)
        # Must NOT be the generic "Error" embed
        assert not any(c.kwargs["embed"].title == "❌ Error" for c in embed_calls)

    @pytest.mark.asyncio
    async def test_non_timeout_error_uses_error_embed(
        self, thread: MagicMock, runner: MagicMock, repo: MagicMock
    ) -> None:
        """Non-timeout errors should still use the generic error_embed."""
        events = [
            StreamEvent(
                message_type=MessageType.RESULT,
                is_complete=True,
                error="CLI exited with code 1",
            ),
        ]
        runner.run = self._make_async_gen(events)

        await run_claude_in_thread(thread, runner, repo, "test", None)

        embed_calls = [c for c in thread.send.call_args_list if "embed" in c.kwargs]
        assert any("Error" in (c.kwargs["embed"].title or "") for c in embed_calls)

    @pytest.mark.asyncio
    async def test_session_start_embed_sent_only_once_for_multiple_system_events(
        self, thread: MagicMock, runner: MagicMock, repo: MagicMock
    ) -> None:
        """session_start_embed must be sent exactly once even when Claude emits
        multiple SYSTEM events (e.g. init + hook feedback events with session_id).

        Regression test for: Claude Code emits 3+ SYSTEM events per session when
        hooks are configured (init + UserPromptSubmit hook partial + complete),
        each with session_id, causing 3 identical session-start embeds to appear.
        """
        events = [
            # Simulates: init SYSTEM message
            StreamEvent(message_type=MessageType.SYSTEM, session_id="sess-1"),
            # Simulates: hook feedback (UserPromptSubmit partial) — also has session_id
            StreamEvent(message_type=MessageType.SYSTEM, session_id="sess-1"),
            # Simulates: hook feedback (UserPromptSubmit complete) — also has session_id
            StreamEvent(message_type=MessageType.SYSTEM, session_id="sess-1"),
            StreamEvent(
                message_type=MessageType.RESULT,
                is_complete=True,
                session_id="sess-1",
                cost_usd=0.001,
                duration_ms=500,
            ),
        ]
        runner.run = self._make_async_gen(events)

        await run_claude_in_thread(thread, runner, repo, "test", None)

        start_embeds = [
            c
            for c in thread.send.call_args_list
            if "embed" in c.kwargs and "session started" in (c.kwargs["embed"].title or "").lower()
        ]
        assert len(start_embeds) == 1, (
            f"Expected exactly 1 session_start_embed, got {len(start_embeds)}"
        )


class TestStopViewRunnerSync:
    """Regression tests for stop button not interrupting the active subprocess.

    When _run_helper clones the runner to inject --append-system-prompt, the
    StopView must be updated to point at the clone that actually owns the live
    subprocess.  Without this, Stop sends SIGINT to the original runner whose
    _process is None and has no effect.

    See: https://github.com/ebibibi/ebi-agent-chat-relay/issues/174
    """

    @pytest.fixture
    def thread(self) -> MagicMock:
        t = MagicMock(spec=discord.Thread)
        t.id = 55555
        t.send = AsyncMock(return_value=MagicMock(spec=discord.Message))
        return t

    def _simple_events(self) -> list[StreamEvent]:
        return [
            StreamEvent(message_type=MessageType.SYSTEM, session_id="sess-x"),
            StreamEvent(
                message_type=MessageType.RESULT,
                is_complete=True,
                session_id="sess-x",
                cost_usd=0.001,
                duration_ms=100,
            ),
        ]

    def _make_async_gen(self, events: list[StreamEvent]):
        async def gen(*args, **kwargs):
            for e in events:
                yield e

        return gen

    @pytest.mark.asyncio
    @pytest.mark.real_system_context
    async def test_stop_view_updated_to_cloned_runner(self, thread: MagicMock) -> None:
        """stop_view.update_runner() is called when a clone is created for system context.

        When registry/lounge is configured, _run_helper calls runner.clone() and
        the resulting clone is the one that runs the subprocess.  The StopView
        must be redirected to that clone so the Stop button actually works.
        """
        original_runner = MagicMock()
        original_runner.working_dir = None
        original_runner.interrupt = AsyncMock()

        cloned_runner = MagicMock()
        cloned_runner.interrupt = AsyncMock()
        cloned_runner.run = self._make_async_gen(self._simple_events())

        # clone() returns a *different* object simulating the system-context clone
        original_runner.clone.return_value = cloned_runner

        stop_view = MagicMock()
        stop_view.bump = AsyncMock()
        stop_view.disable = AsyncMock()

        registry = SessionRegistry()

        config = RunConfig(
            thread=thread,
            runner=original_runner,
            prompt="hello",
            stop_view=stop_view,
            registry=registry,
        )

        await run_claude_with_config(config)

        # stop_view must have been updated to point at the clone
        stop_view.update_runner.assert_called_once_with(cloned_runner)

    @pytest.mark.asyncio
    async def test_stop_view_not_updated_when_no_clone(self, thread: MagicMock) -> None:
        """When no system context exists, no clone is created and update_runner is not called."""
        runner = MagicMock()
        runner.working_dir = None
        runner.run = self._make_async_gen(self._simple_events())
        # clone should NOT be called if there is no system context

        stop_view = MagicMock()
        stop_view.bump = AsyncMock()
        stop_view.disable = AsyncMock()

        # No registry, no lounge_repo → system_context is None → no clone
        config = RunConfig(
            thread=thread,
            runner=runner,
            prompt="hello",
            stop_view=stop_view,
        )

        await run_claude_with_config(config)

        stop_view.update_runner.assert_not_called()


class TestMakeErrorEmbed:
    """Unit tests for the _make_error_embed router function."""

    def test_timeout_message_returns_timeout_embed(self) -> None:
        embed = _make_error_embed("Timed out after 300 seconds")
        assert "timed out" in embed.title.lower()

    def test_timeout_message_includes_seconds(self) -> None:
        embed = _make_error_embed("Timed out after 120 seconds")
        assert "120" in embed.description

    def test_generic_error_returns_error_embed(self) -> None:
        embed = _make_error_embed("Something went wrong")
        assert embed.title == "❌ Error"
        assert "Something went wrong" in embed.description

    def test_partial_timeout_text_uses_error_embed(self) -> None:
        # "Timed out" not at start — should NOT match
        embed = _make_error_embed("Process Timed out after 300 seconds")
        assert embed.title == "❌ Error"


class TestConcurrencyIntegration:
    """Tests that run_claude_in_thread integrates with SessionRegistry."""

    @pytest.fixture
    def thread(self) -> MagicMock:
        t = MagicMock(spec=discord.Thread)
        t.id = 12345
        t.send = AsyncMock(return_value=MagicMock(spec=discord.Message))
        return t

    @pytest.fixture
    def repo(self) -> MagicMock:
        r = MagicMock()
        r.save = AsyncMock()
        return r

    @pytest.fixture
    def runner(self) -> MagicMock:
        r = MagicMock()
        r.working_dir = None
        # clone() returns the same mock so run() assignments in tests carry over.
        r.clone.return_value = r
        return r

    def _make_async_gen(self, events: list[StreamEvent]):
        async def gen(*args, **kwargs):
            for e in events:
                yield e

        return gen

    def _simple_events(self) -> list[StreamEvent]:
        return [
            StreamEvent(message_type=MessageType.SYSTEM, session_id="sess-1"),
            StreamEvent(
                message_type=MessageType.RESULT,
                is_complete=True,
                text="Done.",
                session_id="sess-1",
                cost_usd=0.01,
                duration_ms=500,
            ),
        ]

    @pytest.mark.asyncio
    @pytest.mark.real_system_context
    async def test_concurrency_notice_injected_as_system_prompt(
        self, thread: MagicMock, runner: MagicMock, repo: MagicMock
    ) -> None:
        """When registry is provided, concurrency notice goes into --append-system-prompt.

        The user prompt is passed unchanged; the notice is injected as an ephemeral
        system prompt so it does NOT accumulate in session history.
        """
        registry = SessionRegistry()
        captured_prompt = []

        async def capturing_gen(prompt, **kwargs):
            captured_prompt.append(prompt)
            for e in self._simple_events():
                yield e

        runner.run = capturing_gen

        await run_claude_in_thread(thread, runner, repo, "fix the bug", None, registry=registry)

        # Prompt is unchanged — notice moved to --append-system-prompt via clone().
        assert len(captured_prompt) == 1
        assert captured_prompt[0] == "fix the bug"

        # clone() must have been called with the concurrency notice as system prompt.
        runner.clone.assert_called_once()
        _, kwargs = runner.clone.call_args
        system_prompt = kwargs.get("append_system_prompt", "")
        assert "[CONCURRENCY NOTICE" in system_prompt

    @pytest.mark.asyncio
    @pytest.mark.real_system_context
    async def test_file_delivery_mentions_substantial_written_deliverables(
        self, thread: MagicMock, runner: MagicMock, repo: MagicMock
    ) -> None:
        captured_prompt = []

        async def capturing_gen(prompt, **kwargs):
            captured_prompt.append(prompt)
            for e in self._simple_events():
                yield e

        runner.run = capturing_gen

        await run_claude_in_thread(thread, runner, repo, "write a reply draft", None)

        assert captured_prompt == ["write a reply draft"]
        runner.clone.assert_called_once()
        system_prompt = runner.clone.call_args.kwargs.get("append_system_prompt", "")
        assert "substantial written deliverable" in system_prompt
        assert "Markdown file" in system_prompt

    @pytest.mark.asyncio
    @pytest.mark.real_system_context
    async def test_session_registered_during_run(
        self, thread: MagicMock, runner: MagicMock, repo: MagicMock
    ) -> None:
        """Session should be registered in the registry while running."""
        registry = SessionRegistry()
        registered_during_run = []

        original_events = self._simple_events()

        async def capturing_gen(prompt, **kwargs):
            # Capture registry state during execution
            registered_during_run.extend(registry.list_active())
            for e in original_events:
                yield e

        runner.run = capturing_gen

        await run_claude_in_thread(thread, runner, repo, "fix the bug", None, registry=registry)

        assert len(registered_during_run) == 1
        assert registered_during_run[0].thread_id == 12345

    @pytest.mark.asyncio
    async def test_session_unregistered_after_run(
        self, thread: MagicMock, runner: MagicMock, repo: MagicMock
    ) -> None:
        """Session should be removed from registry after completion."""
        registry = SessionRegistry()
        runner.run = self._make_async_gen(self._simple_events())

        await run_claude_in_thread(thread, runner, repo, "fix the bug", None, registry=registry)

        assert registry.list_active() == []

    @pytest.mark.asyncio
    async def test_session_unregistered_on_error(
        self, thread: MagicMock, runner: MagicMock, repo: MagicMock
    ) -> None:
        """Session should be removed from registry even if an error occurs."""
        registry = SessionRegistry()

        async def failing_gen(prompt, **kwargs):
            raise RuntimeError("boom")
            yield  # make it a generator  # noqa: E501

        runner.run = failing_gen

        await run_claude_in_thread(thread, runner, repo, "fix the bug", None, registry=registry)

        assert registry.list_active() == []

    @pytest.mark.asyncio
    @pytest.mark.real_system_context
    async def test_other_sessions_in_system_prompt(
        self, thread: MagicMock, runner: MagicMock, repo: MagicMock
    ) -> None:
        """When other sessions exist, their info should appear in --append-system-prompt."""
        registry = SessionRegistry()
        registry.register(9999, "running /goodmorning", "/home/ebi")

        captured_prompt = []

        async def capturing_gen(prompt, **kwargs):
            captured_prompt.append(prompt)
            for e in self._simple_events():
                yield e

        runner.run = capturing_gen

        await run_claude_in_thread(thread, runner, repo, "fix the bug", None, registry=registry)

        # User prompt is unchanged.
        assert len(captured_prompt) == 1
        assert captured_prompt[0] == "fix the bug"

        # Other session info is in the system prompt, not the user message.
        _, kwargs = runner.clone.call_args
        system_prompt = kwargs.get("append_system_prompt", "")
        assert "/goodmorning" in system_prompt

    @pytest.mark.asyncio
    async def test_no_registry_no_clone(
        self, thread: MagicMock, runner: MagicMock, repo: MagicMock
    ) -> None:
        """Without registry or lounge, runner is used directly (no clone needed)."""
        captured_prompt = []

        async def capturing_gen(prompt, **kwargs):
            captured_prompt.append(prompt)
            for e in self._simple_events():
                yield e

        runner.run = capturing_gen

        await run_claude_in_thread(thread, runner, repo, "fix the bug", None)

        assert captured_prompt[0] == "fix the bug"
        # No system context → runner.clone should not have been called.
        runner.clone.assert_not_called()


class TestLiveToolTimer:
    """Tests for LiveToolTimer elapsed-time embed updates."""

    def _bash_tool(self) -> ToolUseEvent:
        return ToolUseEvent(
            tool_id="t1",
            tool_name="Bash",
            tool_input={"command": "az login --use-device-code"},
            category=ToolCategory.COMMAND,
        )

    def _make_msg(self) -> MagicMock:
        msg = MagicMock(spec=discord.Message)
        msg.edit = AsyncMock()
        return msg

    @pytest.mark.asyncio
    async def test_timer_updates_embed_after_interval(self) -> None:
        """After TOOL_TIMER_INTERVAL seconds, the embed should be updated with elapsed time."""
        import claude_discord.discord_ui.tool_timer as tt

        msg = self._make_msg()
        timer = LiveToolTimer(msg, self._bash_tool())

        original_interval = tt.TOOL_TIMER_INTERVAL
        tt.TOOL_TIMER_INTERVAL = 0.01  # speed up for test
        try:
            task = timer.start()
            await asyncio.sleep(0.05)  # allow at least one tick
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        finally:
            tt.TOOL_TIMER_INTERVAL = original_interval

        msg.edit.assert_called()
        # Elapsed time must be in description (title stays stable across ticks)
        call_embed = msg.edit.call_args.kwargs.get("embed")
        assert call_embed is not None
        assert call_embed.description is not None
        assert "s" in call_embed.description  # e.g. "⏳ 0s elapsed..."
        assert "s)" not in call_embed.title  # title must NOT contain elapsed time

    @pytest.mark.asyncio
    async def test_timer_cancelled_stops_updates(self) -> None:
        """After cancellation before the first interval tick, only the immediate
        0s edit (posted on start) should have been called — no further tick edits."""
        import claude_discord.discord_ui.tool_timer as tt

        msg = self._make_msg()
        timer = LiveToolTimer(msg, self._bash_tool())

        original_interval = tt.TOOL_TIMER_INTERVAL
        tt.TOOL_TIMER_INTERVAL = 0.01
        try:
            task = timer.start()
            await asyncio.sleep(0.005)  # cancel before first tick
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        finally:
            tt.TOOL_TIMER_INTERVAL = original_interval

        # The timer now shows 0s elapsed immediately on start (one edit),
        # then stops — so exactly 1 edit call is expected, not 0.
        assert msg.edit.call_count == 1

    @pytest.mark.asyncio
    async def test_run_claude_cancels_timer_on_tool_result(self) -> None:
        """Timer task should be cancelled when the tool result arrives."""
        thread = MagicMock(spec=discord.Thread)
        thread.id = 11111
        tool_msg = MagicMock(spec=discord.Message)
        tool_msg.edit = AsyncMock()
        tool_msg.embeds = [MagicMock(title="🔧 Running: az login...")]
        thread.send = AsyncMock(return_value=tool_msg)

        repo = MagicMock()
        repo.save = AsyncMock()
        runner = MagicMock()

        events = [
            StreamEvent(message_type=MessageType.SYSTEM, session_id="sess-1"),
            StreamEvent(
                message_type=MessageType.ASSISTANT,
                tool_use=ToolUseEvent(
                    tool_id="t1",
                    tool_name="Bash",
                    tool_input={"command": "az login --use-device-code"},
                    category=ToolCategory.COMMAND,
                ),
            ),
            StreamEvent(
                message_type=MessageType.USER,
                tool_result_id="t1",
                tool_result_content="Device login complete",
            ),
            StreamEvent(
                message_type=MessageType.RESULT,
                is_complete=True,
                session_id="sess-1",
                cost_usd=0.01,
                duration_ms=5000,
            ),
        ]

        async def gen(*args, **kwargs):
            for e in events:
                yield e

        runner.run = gen

        import claude_discord.discord_ui.tool_timer as tt

        original_interval = tt.TOOL_TIMER_INTERVAL
        tt.TOOL_TIMER_INTERVAL = 100  # ensure timer never fires during this test
        try:
            await run_claude_in_thread(thread, runner, repo, "login", None)
        finally:
            tt.TOOL_TIMER_INTERVAL = original_interval

        # All timers should be cleared after run completes
        # (verified indirectly: no ghost tasks, session finishes cleanly)


class TestImageOnlyRunConfig:
    """Regression tests: image-only messages (empty prompt) through run_claude_with_config.

    The bug: RunConfig.__post_init__ rejected empty prompts unconditionally,
    but image-only Discord messages produce prompt="" with valid images.
    """

    _SAMPLE_IMAGE = ImageData(data="aGVsbG8=", media_type="image/png")

    @pytest.fixture
    def thread(self) -> MagicMock:
        t = MagicMock(spec=discord.Thread)
        msg = MagicMock(spec=discord.Message)
        t.send = AsyncMock(return_value=msg)
        msg.edit = AsyncMock()
        t.id = 42
        return t

    def _simple_events(self) -> list[StreamEvent]:
        return [
            StreamEvent(message_type=MessageType.SYSTEM, session_id="sess-img"),
            StreamEvent(
                message_type=MessageType.RESULT,
                is_complete=True,
                session_id="sess-img",
                cost_usd=0.001,
                duration_ms=100,
            ),
        ]

    def _make_async_gen(self, events: list[StreamEvent]):
        async def gen(*args, **kwargs):
            for e in events:
                yield e

        return gen

    @pytest.mark.asyncio
    async def test_empty_prompt_with_images_completes(self, thread: MagicMock) -> None:
        """run_claude_with_config with prompt='' and images must complete without error."""
        runner = MagicMock()
        runner.working_dir = None
        runner.images = None
        runner.run = self._make_async_gen(self._simple_events())

        config = RunConfig(
            thread=thread,
            runner=runner,
            prompt="",
            images=[self._SAMPLE_IMAGE],
        )

        session_id = await run_claude_with_config(config)
        assert session_id == "sess-img"

    @pytest.mark.asyncio
    async def test_images_injected_into_runner(self, thread: MagicMock) -> None:
        """Images from config must be set on the runner before run() is called."""
        runner = MagicMock()
        runner.working_dir = None
        runner.images = None
        runner.run = self._make_async_gen(self._simple_events())

        config = RunConfig(
            thread=thread,
            runner=runner,
            prompt="",
            images=[self._SAMPLE_IMAGE],
        )

        await run_claude_with_config(config)
        assert runner.images == [self._SAMPLE_IMAGE]


class TestCompactRerun:
    """compact_boundary → interrupt → rerun-with-guardrail integration tests.

    When compact_boundary fires, EventProcessor interrupts the runner and sets
    compact_occurred=True. run_claude_with_config should then rerun with a
    guardrail injected into --append-system-prompt and post_compact_rerun=True.
    """

    @pytest.fixture
    def thread(self) -> MagicMock:
        t = MagicMock(spec=discord.Thread)
        msg = MagicMock(spec=discord.Message)
        t.send = AsyncMock(return_value=msg)
        msg.edit = AsyncMock()
        t.id = 42
        return t

    def _make_async_gen(self, events: list[StreamEvent]):
        async def gen(*args, **kwargs):
            for e in events:
                yield e

        return gen

    @pytest.mark.asyncio
    async def test_compact_triggers_rerun(self, thread: MagicMock) -> None:
        """After compact_boundary, run_claude_with_config must call runner.run() a second time."""
        runner = MagicMock()
        runner.working_dir = None
        runner.images = None
        runner.interrupt = AsyncMock()

        compact_events = [
            StreamEvent(message_type=MessageType.SYSTEM, session_id="sess-1"),
            StreamEvent(message_type=MessageType.SYSTEM, is_compact=True),
        ]
        rerun_events = [
            StreamEvent(message_type=MessageType.SYSTEM, session_id="sess-1"),
            StreamEvent(
                message_type=MessageType.RESULT,
                is_complete=True,
                session_id="sess-1",
                cost_usd=0.01,
                duration_ms=100,
            ),
        ]

        call_count = 0

        async def mock_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            events = compact_events if call_count == 1 else rerun_events
            for e in events:
                yield e

        runner.run = mock_run
        runner.clone = MagicMock(return_value=runner)

        config = RunConfig(thread=thread, runner=runner, prompt="check X", session_id="sess-1")
        session_id = await run_claude_with_config(config)

        assert call_count == 2, f"Expected 2 runner.run calls, got {call_count}"
        assert session_id == "sess-1"

    @pytest.mark.asyncio
    @pytest.mark.real_system_context
    async def test_compact_rerun_injects_guardrail_via_clone(self, thread: MagicMock) -> None:
        """The rerun invokes runner.clone() with an append_system_prompt guardrail."""
        runner = MagicMock()
        runner.working_dir = None
        runner.images = None
        runner.interrupt = AsyncMock()

        compact_events = [
            StreamEvent(message_type=MessageType.SYSTEM, session_id="sess-2"),
            StreamEvent(message_type=MessageType.SYSTEM, is_compact=True),
        ]
        rerun_events = [
            StreamEvent(message_type=MessageType.SYSTEM, session_id="sess-2"),
            StreamEvent(
                message_type=MessageType.RESULT,
                is_complete=True,
                session_id="sess-2",
                cost_usd=0.01,
                duration_ms=100,
            ),
        ]

        call_count = 0

        async def mock_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            events = compact_events if call_count == 1 else rerun_events
            for e in events:
                yield e

        # Clone returns a runner that delivers rerun_events.
        cloned_runner = MagicMock()
        cloned_runner.working_dir = None
        cloned_runner.images = None
        cloned_runner.interrupt = AsyncMock()
        cloned_runner.run = mock_run  # same mock; call_count distinguishes runs

        runner.run = mock_run
        runner.clone = MagicMock(return_value=cloned_runner)

        config = RunConfig(thread=thread, runner=runner, prompt="check X", session_id="sess-2")
        await run_claude_with_config(config)

        # runner.clone() must have been called for the rerun (guardrail injection).
        runner.clone.assert_called()
        clone_kwargs = runner.clone.call_args.kwargs
        prompt_arg = clone_kwargs.get("append_system_prompt", "")
        assert prompt_arg is not None and len(prompt_arg) > 0, (
            "Guardrail must be injected via append_system_prompt"
        )

    @pytest.mark.asyncio
    async def test_no_rerun_loop_when_compact_on_rerun(self, thread: MagicMock) -> None:
        """When compact fires during a post_compact_rerun, do NOT interrupt again (no loop)."""
        runner = MagicMock()
        runner.working_dir = None
        runner.images = None
        runner.interrupt = AsyncMock()

        events = [
            StreamEvent(message_type=MessageType.SYSTEM, session_id="sess-3"),
            StreamEvent(message_type=MessageType.SYSTEM, is_compact=True),
            StreamEvent(
                message_type=MessageType.RESULT,
                is_complete=True,
                session_id="sess-3",
                cost_usd=0.01,
                duration_ms=100,
            ),
        ]

        runner.run = self._make_async_gen(events)
        runner.clone = MagicMock(return_value=runner)

        config = RunConfig(thread=thread, runner=runner, prompt="test", post_compact_rerun=True)
        # Should complete without infinite loop; interrupt must not be called.
        session_id = await run_claude_with_config(config)

        runner.interrupt.assert_not_awaited()
        assert session_id == "sess-3"

    @pytest.mark.asyncio
    @pytest.mark.real_system_context
    async def test_compact_interrupts_cloned_runner_not_original(self, thread: MagicMock) -> None:
        """interrupt() must target the cloned runner (which owns the process), not config.runner.

        Regression test for #306: when system_context causes runner.clone(), the
        EventProcessor still held a reference to the original runner.  Calling
        interrupt() on the original runner was a no-op (no process), so Claude
        kept running invisibly while should_drain=True suppressed all output.
        """
        original_runner = MagicMock()
        original_runner.working_dir = None
        original_runner.images = None
        original_runner.interrupt = AsyncMock()
        original_runner.model = "test-model"

        cloned_runner = MagicMock()
        cloned_runner.working_dir = None
        cloned_runner.images = None
        cloned_runner.interrupt = AsyncMock()
        cloned_runner.model = "test-model"

        compact_events = [
            StreamEvent(message_type=MessageType.SYSTEM, session_id="sess-306"),
            StreamEvent(message_type=MessageType.SYSTEM, is_compact=True),
        ]
        rerun_events = [
            StreamEvent(message_type=MessageType.SYSTEM, session_id="sess-306"),
            StreamEvent(
                message_type=MessageType.RESULT,
                is_complete=True,
                session_id="sess-306",
                cost_usd=0.01,
                duration_ms=100,
            ),
        ]

        call_count = 0

        async def mock_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            events = compact_events if call_count == 1 else rerun_events
            for e in events:
                yield e

        # First clone (system context injection) returns cloned_runner.
        # Second clone (rerun guardrail) also returns cloned_runner.
        original_runner.clone = MagicMock(return_value=cloned_runner)
        cloned_runner.clone = MagicMock(return_value=cloned_runner)
        cloned_runner.run = mock_run
        # original_runner.run should NOT be called (clone is used instead).

        # Provide a registry so that _build_system_context() returns non-None,
        # which triggers runner.clone() — the scenario where the bug occurs.
        registry = MagicMock(spec=SessionRegistry)
        registry.build_concurrency_notice.return_value = "notice"
        registry.list_others.return_value = []

        config = RunConfig(
            thread=thread,
            runner=original_runner,
            prompt="check X",
            session_id="sess-306",
            registry=registry,
            chat_only=True,
        )
        await run_claude_with_config(config)

        # The cloned runner (with the active process) must be interrupted.
        cloned_runner.interrupt.assert_awaited()
        # The original runner must NOT be interrupted (it has no process).
        original_runner.interrupt.assert_not_awaited()


class TestGlobalSessionSemaphore:
    """Tests for the global session slot limiter in _run_helper."""

    @pytest.fixture(autouse=True)
    def _reset_semaphore(self):
        """Save and restore module-level semaphore state around each test."""
        orig = get_session_slots()
        yield
        set_session_slots(orig)

    @pytest.fixture
    def thread(self) -> MagicMock:
        t = MagicMock(spec=discord.Thread)
        t.id = 77777
        t.send = AsyncMock(return_value=MagicMock(spec=discord.Message))
        return t

    def _simple_events(self, session_id: str = "sess-sem") -> list[StreamEvent]:
        return [
            StreamEvent(message_type=MessageType.SYSTEM, session_id=session_id),
            StreamEvent(
                message_type=MessageType.RESULT,
                is_complete=True,
                session_id=session_id,
                cost_usd=0.001,
                duration_ms=100,
            ),
        ]

    def _make_async_gen(self, events: list[StreamEvent]):
        async def gen(*args, **kwargs):
            for e in events:
                yield e

        return gen

    def test_configure_session_limit_sets_semaphore(self) -> None:
        configure_session_limit(5)
        slots = get_session_slots()
        assert isinstance(slots, SessionSlotScheduler)
        assert slots.max_slots == 5

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrent_sessions(self, thread: MagicMock) -> None:
        """With max=2, a 3rd session must wait until one finishes."""
        configure_session_limit(2)

        gate = asyncio.Event()
        started = asyncio.Event()

        async def slow_gen(*args, **kwargs):
            started.set()
            await gate.wait()
            for e in self._simple_events():
                yield e

        runner = MagicMock()
        runner.working_dir = None
        runner.images = None
        runner.run = slow_gen

        config = RunConfig(thread=thread, runner=runner, prompt="test")

        # Start 2 sessions that block on `gate`
        t1 = asyncio.create_task(run_claude_with_config(config))
        await started.wait()
        started.clear()

        t2 = asyncio.create_task(run_claude_with_config(config))
        await started.wait()
        started.clear()

        # 3rd session should not start immediately
        t3 = asyncio.create_task(run_claude_with_config(config))
        await asyncio.sleep(0.05)
        assert not started.is_set(), "3rd session should be waiting for a semaphore slot"

        # Release all
        gate.set()
        await asyncio.gather(t1, t2, t3)

    @pytest.mark.asyncio
    async def test_waiting_message_sent_when_full(self, thread: MagicMock) -> None:
        """When all slots are taken, a waiting message should be posted."""
        configure_session_limit(1)

        gate = asyncio.Event()
        started = asyncio.Event()

        async def slow_gen(*args, **kwargs):
            started.set()
            await gate.wait()
            for e in self._simple_events():
                yield e

        runner = MagicMock()
        runner.working_dir = None
        runner.images = None
        runner.run = slow_gen

        config = RunConfig(thread=thread, runner=runner, prompt="test")

        t1 = asyncio.create_task(run_claude_with_config(config))
        await started.wait()

        # 2nd session should see the semaphore locked and post a waiting message
        t2 = asyncio.create_task(run_claude_with_config(config))
        await asyncio.sleep(0.05)

        gate.set()
        await asyncio.gather(t1, t2)

        # The wait is now an out-of-band notice rather than a raw message, so
        # it arrives as an embed the surface built.
        waiting_msgs = [
            c
            for c in thread.send.call_args_list
            if "\u23f3" in str(c.kwargs.get("embed", "").description or "")
        ]
        assert len(waiting_msgs) >= 1

    @pytest.mark.asyncio
    async def test_semaphore_released_on_error(self, thread: MagicMock) -> None:
        """Semaphore must be released even when runner.run() raises."""
        configure_session_limit(1)

        async def failing_gen(*args, **kwargs):
            raise RuntimeError("boom")
            yield  # noqa: E501

        runner = MagicMock()
        runner.working_dir = None
        runner.images = None
        runner.run = failing_gen

        config = RunConfig(thread=thread, runner=runner, prompt="test")
        await run_claude_with_config(config)

        slots = get_session_slots()
        assert slots is not None
        assert slots.running_count == 0, "Slot should be released after error"

    @pytest.mark.asyncio
    async def test_no_semaphore_when_not_configured(self, thread: MagicMock) -> None:
        """When configure_session_limit was never called, no limiting occurs."""
        set_session_slots(None)

        runner = MagicMock()
        runner.working_dir = None
        runner.images = None
        runner.run = self._make_async_gen(self._simple_events())

        config = RunConfig(thread=thread, runner=runner, prompt="test")
        result = await run_claude_with_config(config)
        assert result == "sess-sem"

    @pytest.mark.asyncio
    async def test_paused_session_yields_its_slot_and_resumes_automatically(
        self, thread: MagicMock
    ) -> None:
        """Pause frees the slot for the waiter, then the paused session continues."""
        configure_session_limit(1)
        slots = get_session_slots()
        assert slots is not None

        interrupted = asyncio.Event()
        calls: list[tuple[str, str | None]] = []

        def make_runner(thread_session: str, block_first: bool) -> MagicMock:
            async def gen(prompt, session_id=None):
                calls.append((prompt, session_id))
                yield StreamEvent(message_type=MessageType.SYSTEM, session_id=thread_session)
                # Only the original turn blocks; the resumed turn has another prompt.
                if block_first and prompt == "long job":
                    await interrupted.wait()
                    return
                yield StreamEvent(
                    message_type=MessageType.RESULT,
                    is_complete=True,
                    session_id=thread_session,
                    cost_usd=0.001,
                    duration_ms=100,
                )

            runner = MagicMock()
            runner.working_dir = None
            runner.images = None
            runner.run = gen
            runner.interrupt = AsyncMock(side_effect=lambda: interrupted.set())
            return runner

        other_thread = MagicMock(spec=discord.Thread)
        other_thread.id = 88888
        other_thread.send = AsyncMock(return_value=MagicMock(spec=discord.Message))

        long_task = asyncio.create_task(
            run_claude_with_config(
                RunConfig(thread=thread, runner=make_runner("sess-long", True), prompt="long job")
            )
        )
        while not calls:
            await asyncio.sleep(0.01)
        urgent_task = asyncio.create_task(
            run_claude_with_config(
                RunConfig(
                    thread=other_thread,
                    runner=make_runner("sess-urgent", False),
                    prompt="urgent",
                )
            )
        )
        while slots.position(88888) is None:
            await asyncio.sleep(0.01)

        await slots.pause(77777)
        assert await asyncio.wait_for(urgent_task, 2) == "sess-urgent"
        assert await asyncio.wait_for(long_task, 2) == "sess-long"

        prompts = [c[0] for c in calls]
        # The urgent run started before the paused one continued.
        assert prompts.index("urgent") < prompts.index(_rh_module.PAUSE_RESUME_PROMPT)
        assert (_rh_module.PAUSE_RESUME_PROMPT, "sess-long") in calls
        assert slots.running_count == 0

    @pytest.mark.asyncio
    async def test_compact_rerun_does_not_deadlock(self, thread: MagicMock) -> None:
        """With max=1, compact rerun must not deadlock (semaphore released before rerun)."""
        configure_session_limit(1)

        compact_events = [
            StreamEvent(message_type=MessageType.SYSTEM, session_id="sess-dl"),
            StreamEvent(message_type=MessageType.SYSTEM, is_compact=True),
        ]
        rerun_events = [
            StreamEvent(message_type=MessageType.SYSTEM, session_id="sess-dl"),
            StreamEvent(
                message_type=MessageType.RESULT,
                is_complete=True,
                session_id="sess-dl",
                cost_usd=0.01,
                duration_ms=100,
            ),
        ]

        call_count = 0

        async def mock_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            events = compact_events if call_count == 1 else rerun_events
            for e in events:
                yield e

        runner = MagicMock()
        runner.working_dir = None
        runner.images = None
        runner.interrupt = AsyncMock()
        runner.run = mock_run
        runner.clone = MagicMock(return_value=runner)

        config = RunConfig(thread=thread, runner=runner, prompt="test", session_id="sess-dl")

        # Must complete within 5 seconds — deadlock would hang forever
        result = await asyncio.wait_for(run_claude_with_config(config), timeout=5.0)
        assert result == "sess-dl"
        assert call_count == 2


class TestDrainAllowsResultEvent:
    """RESULT events must be processed even when should_drain is True.

    Regression test for #366: should_drain skips ALL events including RESULT,
    which prevents _on_complete() from firing and silently drops voice response
    callbacks and other completion hooks.
    """

    @pytest.fixture
    def thread(self) -> MagicMock:
        t = MagicMock(spec=discord.Thread)
        msg = MagicMock(spec=discord.Message)
        t.send = AsyncMock(return_value=msg)
        msg.edit = AsyncMock()
        t.id = 36600
        return t

    def _make_async_gen(self, events: list[StreamEvent]):
        async def gen(*args, **kwargs):
            for e in events:
                yield e

        return gen

    @pytest.mark.asyncio
    async def test_result_processed_during_ask_drain(self, thread: MagicMock) -> None:
        """When AskUserQuestion triggers drain, RESULT must still reach _on_complete."""
        from unittest.mock import patch

        runner = MagicMock()
        runner.working_dir = None
        runner.images = None
        runner.interrupt = AsyncMock()
        runner.model = "test-model"

        events = [
            StreamEvent(message_type=MessageType.SYSTEM, session_id="sess-366"),
            StreamEvent(
                message_type=MessageType.ASSISTANT,
                ask_questions=[AskQuestion(question="Pick one", options=[AskOption(label="A")])],
            ),
            StreamEvent(message_type=MessageType.ASSISTANT, text="drained text"),
            StreamEvent(
                message_type=MessageType.RESULT,
                is_complete=True,
                session_id="sess-366",
                cost_usd=0.01,
                duration_ms=100,
            ),
        ]
        runner.run = self._make_async_gen(events)
        runner.clone = MagicMock(return_value=runner)

        config = RunConfig(thread=thread, runner=runner, prompt="test", session_id="sess-366")
        with patch(
            "claude_discord.cogs._run_helper.collect_ask_answers",
            new_callable=AsyncMock,
            return_value=None,
        ):
            await run_claude_with_config(config)

        send_calls = [str(c) for c in thread.send.call_args_list]
        assert any("embed" in c for c in send_calls), (
            "RESULT event must reach _on_complete even during drain"
        )

    @pytest.mark.asyncio
    async def test_non_result_events_still_drained(self, thread: MagicMock) -> None:
        """Non-RESULT events after should_drain must still be skipped."""
        from unittest.mock import patch

        runner = MagicMock()
        runner.working_dir = None
        runner.images = None
        runner.interrupt = AsyncMock()
        runner.model = "test-model"

        events = [
            StreamEvent(message_type=MessageType.SYSTEM, session_id="sess-366b"),
            StreamEvent(
                message_type=MessageType.ASSISTANT,
                ask_questions=[AskQuestion(question="Pick one", options=[AskOption(label="A")])],
            ),
            StreamEvent(message_type=MessageType.ASSISTANT, text="should be drained"),
            StreamEvent(
                message_type=MessageType.RESULT,
                is_complete=True,
                session_id="sess-366b",
                cost_usd=0.01,
                duration_ms=100,
            ),
        ]
        runner.run = self._make_async_gen(events)
        runner.clone = MagicMock(return_value=runner)

        config = RunConfig(thread=thread, runner=runner, prompt="test", session_id="sess-366b")
        with patch(
            "claude_discord.cogs._run_helper.collect_ask_answers",
            new_callable=AsyncMock,
            return_value=None,
        ):
            await run_claude_with_config(config)

        all_text = " ".join(str(c) for c in thread.send.call_args_list)
        assert "should be drained" not in all_text, (
            "Non-RESULT text must be drained when should_drain is True"
        )


class TestResultSink:
    """run_claude_with_config must fire config.result_sink at the terminal state."""

    @pytest.fixture
    def thread(self) -> MagicMock:
        t = MagicMock(spec=discord.Thread)
        t.id = 777
        t.send = AsyncMock(return_value=MagicMock(spec=discord.Message))
        return t

    @pytest.fixture
    def runner(self) -> MagicMock:
        return MagicMock()

    def _gen(self, events):
        async def gen(*args, **kwargs):
            for e in events:
                yield e

        return gen

    @pytest.mark.asyncio
    async def test_sink_receives_final_text_on_success(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        captured: list[tuple] = []

        async def sink(text, error):
            captured.append((text, error))

        events = [
            StreamEvent(message_type=MessageType.SYSTEM, session_id="s1"),
            StreamEvent(message_type=MessageType.ASSISTANT, text="Final reply."),
            StreamEvent(
                message_type=MessageType.RESULT,
                is_complete=True,
                text="Final reply.",
                session_id="s1",
                cost_usd=0.01,
                duration_ms=100,
            ),
        ]
        runner.run = self._gen(events)

        config = RunConfig(thread=thread, runner=runner, prompt="hi", result_sink=sink)
        await run_claude_with_config(config)

        assert captured == [("Final reply.", None)]

    @pytest.mark.asyncio
    async def test_sink_receives_error_on_exception(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        captured: list[tuple] = []

        async def sink(text, error):
            captured.append((text, error))

        async def boom(*args, **kwargs):
            raise RuntimeError("stream blew up")
            yield  # pragma: no cover

        runner.run = boom

        config = RunConfig(thread=thread, runner=runner, prompt="hi", result_sink=sink)
        await run_claude_with_config(config)

        assert len(captured) == 1
        text, error = captured[0]
        assert text is None
        assert "stream blew up" in error

    @pytest.mark.asyncio
    async def test_sink_receives_error_on_in_stream_result_error(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        """An API/CLI error surfaced via the RESULT event (not a raised
        exception) must reach the sink as an error, not an empty 'done'."""
        captured: list[tuple] = []

        async def sink(text, error):
            captured.append((text, error))

        events = [
            StreamEvent(message_type=MessageType.SYSTEM, session_id="s1"),
            StreamEvent(
                message_type=MessageType.RESULT,
                is_complete=True,
                error="API Error: 429 rate limited",
                session_id="s1",
                cost_usd=0.0,
                duration_ms=50,
            ),
        ]
        runner.run = self._gen(events)

        config = RunConfig(thread=thread, runner=runner, prompt="hi", result_sink=sink)
        await run_claude_with_config(config)

        assert len(captured) == 1
        text, error = captured[0]
        assert error is not None
        assert "429" in error

    @pytest.mark.asyncio
    async def test_no_sink_is_a_noop(self, thread: MagicMock, runner: MagicMock) -> None:
        events = [
            StreamEvent(message_type=MessageType.SYSTEM, session_id="s1"),
            StreamEvent(
                message_type=MessageType.RESULT,
                is_complete=True,
                text="ok",
                session_id="s1",
                cost_usd=0.0,
                duration_ms=10,
            ),
        ]
        runner.run = self._gen(events)
        config = RunConfig(thread=thread, runner=runner, prompt="hi")
        # Must not raise when result_sink is None.
        await run_claude_with_config(config)
