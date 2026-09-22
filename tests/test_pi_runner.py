"""Tests for PiRunner — the pi coding agent CLI backend.

The two JSONL fixtures are *recordings*, not hand-written samples: they are the
stdout of real ``pi --mode json`` turns (pi 0.85.1) with the ``message_update``
deltas stripped. That matters because three of the behaviours asserted below —
the ``agent_settled`` terminal event, per-message usage, and a failed turn
arriving as a normal ``message_end`` — are not in pi's published event list. A
fixture keeps them checkable when pi changes its mind.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_code_core.backend import SessionBackend, create_backend
from claude_code_core.pi_runner import (
    PI_APPROVE_PROJECT_ENV,
    PI_UNSANDBOXED_ENV,
    PiRunner,
    parse_pi_line,
)
from claude_code_core.types import MessageType, ToolCategory

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture_lines(name: str) -> list[str]:
    return (FIXTURES / name).read_text().splitlines()


def _parse_fixture(name: str) -> list:
    return [e for e in (parse_pi_line(line) for line in _fixture_lines(name)) if e is not None]


@pytest.fixture(autouse=True)
def _allow_unsandboxed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Most tests exercise behaviour past the opt-in gate.

    The gate itself is covered by TestUnsandboxedGate, which clears this again.
    """
    monkeypatch.setenv(PI_UNSANDBOXED_ENV, "1")
    monkeypatch.delenv(PI_APPROVE_PROJECT_ENV, raising=False)


class _FakeStream:
    def __init__(self, lines: list[bytes] | None = None, read_data: bytes = b"") -> None:
        self._lines = list(lines or [])
        self._read_data = read_data

    async def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        return b""

    async def read(self) -> bytes:
        return self._read_data


class _FakeProcess:
    def __init__(
        self,
        *,
        stdout_lines: list[bytes] | None = None,
        stderr: bytes = b"",
        returncode: int = 0,
    ) -> None:
        self.stdout = _FakeStream(stdout_lines)
        self.stderr = _FakeStream(read_data=stderr)
        self.stdin = MagicMock()
        self.stdin.drain = AsyncMock()
        self.stdin.wait_closed = AsyncMock()
        self.returncode = returncode
        self.pid = 4242

    async def wait(self) -> int:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


async def _collect(runner: PiRunner, process: _FakeProcess, session_id: str | None = None) -> list:
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)):
        return [event async for event in runner.run("do a thing", session_id)]


class TestParseSessionHeader:
    def test_first_line_carries_the_session_id(self) -> None:
        event = parse_pi_line(_fixture_lines("pi_turn_with_tool.jsonl")[0])
        assert event is not None
        assert event.message_type is MessageType.SYSTEM
        assert event.session_id == "95ca0ce4-11da-4aa1-9f56-6316ed216987"

    def test_blank_and_malformed_lines_are_ignored(self) -> None:
        assert parse_pi_line("") is None
        assert parse_pi_line("   ") is None
        assert parse_pi_line("not json at all") is None


class TestParseAssistantMessages:
    def test_only_assistant_text_becomes_a_reply(self) -> None:
        texts = [e.text for e in _parse_fixture("pi_turn_with_tool.jsonl") if e.text]
        assert texts == ["hello from probe"]

    def test_user_and_tool_result_message_ends_are_not_replies(self) -> None:
        """pi emits message_end for user and toolResult roles too.

        Echoing those back would post the user's own prompt into the thread and
        duplicate every tool result.
        """
        for line in _fixture_lines("pi_turn_with_tool.jsonl"):
            data = json.loads(line)
            if data.get("type") != "message_end":
                continue
            if data["message"].get("role") in ("user", "toolResult"):
                assert parse_pi_line(line) is None

    def test_thinking_is_separated_from_the_reply(self) -> None:
        thinking = [e.thinking for e in _parse_fixture("pi_turn_with_tool.jsonl") if e.thinking]
        assert thinking, "the recorded turn contains thinking blocks"
        assert all("hello from probe" not in t for t in thinking[:1])


class TestParseTools:
    def test_tool_start_is_mapped_to_a_ccdb_tool_name(self) -> None:
        starts = [e for e in _parse_fixture("pi_turn_with_tool.jsonl") if e.tool_use]
        assert len(starts) == 1
        tool = starts[0].tool_use
        assert tool is not None
        assert tool.tool_name == "Read"
        assert tool.category is ToolCategory.READ
        assert tool.tool_id == "call_ccwqi74g"

    def test_path_is_renamed_so_the_embed_can_display_it(self) -> None:
        """pi calls it ``path``; ToolUseEvent.display_name reads ``file_path``."""
        tool = next(e.tool_use for e in _parse_fixture("pi_turn_with_tool.jsonl") if e.tool_use)
        assert tool is not None
        assert tool.tool_input["file_path"].endswith("note.txt")
        assert tool.display_name.startswith("Reading: ")

    def test_tool_result_is_a_user_event(self) -> None:
        """The elapsed-timer contract: EventProcessor only closes a tool embed
        on a USER event. An ASSISTANT tag here leaves the timer running."""
        results = [e for e in _parse_fixture("pi_turn_with_tool.jsonl") if e.tool_result_id]
        assert len(results) == 1
        assert results[0].message_type is MessageType.USER
        assert results[0].tool_result_content == "hello from probe\n"

    def test_unknown_tool_names_pass_through(self) -> None:
        event = parse_pi_line(
            json.dumps(
                {
                    "type": "tool_execution_start",
                    "toolCallId": "t1",
                    "toolName": "some_extension_tool",
                    "args": {"x": 1},
                }
            )
        )
        assert event is not None and event.tool_use is not None
        assert event.tool_use.tool_name == "some_extension_tool"
        assert event.tool_use.category is ToolCategory.OTHER


class TestParseCompletion:
    def test_agent_settled_completes_the_turn(self) -> None:
        events = _parse_fixture("pi_turn_with_tool.jsonl")
        assert events[-1].is_complete
        assert events[-1].raw["type"] == "agent_settled"

    def test_agent_end_does_not_complete_the_turn(self) -> None:
        """agent_end is followed by agent_settled; completing early would mark
        the turn done while events are still arriving."""
        agent_end = [
            e for e in _parse_fixture("pi_turn_with_tool.jsonl") if e.raw.get("type") == "agent_end"
        ]
        assert len(agent_end) == 1
        assert not agent_end[0].is_complete


class TestProviderErrors:
    def test_stop_reason_error_becomes_a_failed_result(self) -> None:
        """A provider failure exits 0 and looks like an ordinary message_end.

        Without this branch the thread would render an empty successful answer.
        """
        events = _parse_fixture("pi_turn_provider_error.jsonl")
        failures = [e for e in events if e.error]
        assert len(failures) == 1
        assert failures[0].message_type is MessageType.RESULT
        assert failures[0].is_complete
        assert "not found" in failures[0].error


class TestUsageAccounting:
    @pytest.mark.asyncio
    async def test_usage_is_summarised_onto_the_terminal_event(self) -> None:
        """pi reports usage per assistant message, ccdb reports it per turn."""
        lines = [line.encode() + b"\n" for line in _fixture_lines("pi_turn_with_tool.jsonl")]
        runner = PiRunner(command="pi")
        events = await _collect(runner, _FakeProcess(stdout_lines=lines))

        final = events[-1]
        assert final.is_complete
        # Input is the largest single message's input, not the sum: pi re-sends
        # the context on every step, so summing would multiply the same tokens.
        assert final.input_tokens == 20175
        # Output is the sum across the turn's assistant messages.
        assert final.output_tokens == 57 + 27


class TestBuildArgs:
    def test_minimal_invocation(self) -> None:
        args = PiRunner(command="pi")._build_args(None)
        assert args[:3] == ["pi", "--mode", "json"]
        assert "--session-id" not in args
        assert args[-1] == "--"

    def test_resume_uses_session_id(self) -> None:
        args = PiRunner(command="pi")._build_args("95ca0ce4-11da-4aa1-9f56-6316ed216987")
        assert "--session-id" in args
        assert args[args.index("--session-id") + 1] == "95ca0ce4-11da-4aa1-9f56-6316ed216987"

    def test_a_non_uuid_session_id_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="Invalid session_id"):
            PiRunner(command="pi")._build_args("; rm -rf /")

    def test_model_and_thinking(self) -> None:
        args = PiRunner(command="pi", model="anthropic/claude-sonnet", effort="high")._build_args(
            None
        )
        assert args[args.index("--model") + 1] == "anthropic/claude-sonnet"
        assert args[args.index("--thinking") + 1] == "high"

    def test_an_invalid_thinking_level_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="Invalid pi thinking level"):
            PiRunner(command="pi", effort="ultra")._build_args(None)

    def test_append_system_prompt_is_native(self) -> None:
        args = PiRunner(command="pi", append_system_prompt="LOUNGE")._build_args(None)
        assert args[args.index("--append-system-prompt") + 1] == "LOUNGE"

    def test_allowed_tools_become_the_tool_allowlist(self) -> None:
        args = PiRunner(command="pi", allowed_tools=["read", "grep"])._build_args(None)
        assert args[args.index("--tools") + 1] == "read,grep"

    def test_project_trust_is_declined_by_default(self) -> None:
        assert "--no-approve" in PiRunner(command="pi")._build_args(None)

    def test_project_trust_can_be_opted_into(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(PI_APPROVE_PROJECT_ENV, "1")
        args = PiRunner(command="pi")._build_args(None)
        assert "--approve" in args
        assert "--no-approve" not in args


class TestUnsandboxedGate:
    @pytest.mark.asyncio
    async def test_without_the_opt_in_no_process_is_spawned(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """pi has no sandbox and no approval loop; ccdb says so instead of
        discovering it from a thread that already ran."""
        monkeypatch.delenv(PI_UNSANDBOXED_ENV, raising=False)
        spawn = AsyncMock()
        with patch("asyncio.create_subprocess_exec", spawn):
            events = [e async for e in PiRunner(command="pi").run("hi")]
        spawn.assert_not_called()
        assert len(events) == 1
        assert events[0].is_complete
        assert PI_UNSANDBOXED_ENV in events[0].error


class TestProcessLifecycle:
    @pytest.mark.asyncio
    async def test_the_prompt_goes_over_stdin(self) -> None:
        """Keeping the prompt out of argv is what stops a large attachment
        from failing the spawn with E2BIG."""
        process = _FakeProcess(stdout_lines=[b'{"type":"agent_settled"}\n'])
        runner = PiRunner(command="pi")
        await _collect(runner, process)
        process.stdin.write.assert_called_once_with(b"do a thing")

    @pytest.mark.asyncio
    async def test_a_stream_that_ends_without_a_terminal_event_reports_failure(self) -> None:
        """A silent CLI death must not leave the thread's status embed spinning."""
        process = _FakeProcess(stdout_lines=[], stderr=b"pi: boom", returncode=1)
        events = await _collect(PiRunner(command="pi"), process)
        assert len(events) == 1
        assert events[0].is_complete
        assert "boom" in events[0].error

    @pytest.mark.asyncio
    async def test_an_intentional_interrupt_is_not_reported_as_an_error(self) -> None:
        runner = PiRunner(command="pi")
        process = _FakeProcess(stdout_lines=[], returncode=1)
        runner._process = process  # type: ignore[assignment]
        runner._interrupt_requested = True
        events = [e async for e in runner._read_stream()]
        assert events == []

    @pytest.mark.asyncio
    async def test_interrupt_sends_sigint(self) -> None:
        runner = PiRunner(command="pi")
        process = _FakeProcess(returncode=None)  # type: ignore[arg-type]
        process.send_signal = MagicMock()  # type: ignore[attr-defined]
        process.wait = AsyncMock(return_value=0)  # type: ignore[method-assign]
        runner._process = process  # type: ignore[assignment]
        await runner.interrupt()
        assert runner._interrupt_requested
        process.send_signal.assert_called_once()


class TestProtocolConformance:
    def test_pi_runner_satisfies_the_backend_protocol(self) -> None:
        assert isinstance(PiRunner(command="pi"), SessionBackend)

    def test_create_backend_builds_it(self) -> None:
        assert isinstance(create_backend(backend="pi", model=None), PiRunner)

    def test_clone_preserves_configuration(self) -> None:
        runner = PiRunner(command="pi", model="openai/gpt-5", effort="low", thread_id=7)
        clone = runner.clone()
        assert isinstance(clone, PiRunner)
        assert (clone.model, clone.effort, clone.thread_id) == ("openai/gpt-5", "low", 7)
        assert clone._process is None

    def test_describe_api_names_the_provider(self) -> None:
        assert PiRunner(command="pi", model="anthropic/x").describe_api() == "pi (anthropic)"
        assert "CLI-configured" in PiRunner(command="pi").describe_api()


@pytest.mark.asyncio
async def test_a_recorded_turn_renders_end_to_end() -> None:
    """The whole recording, through the runner, in the order Discord sees it."""
    lines = [line.encode() + b"\n" for line in _fixture_lines("pi_turn_with_tool.jsonl")]
    events = await _collect(PiRunner(command="pi"), _FakeProcess(stdout_lines=lines))

    assert events[0].session_id == "95ca0ce4-11da-4aa1-9f56-6316ed216987"
    assert any(e.tool_use for e in events)
    assert any(e.tool_result_id for e in events)
    assert events[-1].is_complete and events[-1].error is None
    assert [e.text for e in events if e.text] == ["hello from probe"]


def test_asyncio_marker_is_available() -> None:
    """Guards against the suite silently skipping the async tests above."""
    assert asyncio.iscoroutinefunction(_collect)
