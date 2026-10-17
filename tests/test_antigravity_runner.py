"""Tests for AntigravityRunner — the Google Antigravity CLI (``agy``) backend.

The ``agy_*.jsonl`` fixtures are *recordings* of ``agy --print`` with
``--output-format stream-json`` (agy 1.3.5), with only the scratch directory
path rewritten. Two behaviours asserted below are not documented anywhere and
fail quietly when parsed naively:

* ``result.usage`` is the conversation's running total, not the turn's — a
  resumed turn reports every earlier turn's tokens again.
* In headless mode a tool that needs approval is auto-denied and the turn ends
  with ``status: "SUCCESS"``, an empty ``response`` and ``denied_actions``.
  Rendered as-is, that is a successful empty answer.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_code_core.antigravity_runner import (
    AntigravityRunner,
    AntigravityStreamParser,
)
from claude_code_core.backend import SessionBackend, create_backend
from claude_code_core.types import MessageType, ToolCategory

FIXTURES = Path(__file__).parent / "fixtures"
CONVERSATION = "e4218aba-608e-4ce8-b155-651bd45b47c7"


def _lines(name: str) -> list[str]:
    return (FIXTURES / name).read_text().splitlines()


def _parse(name: str) -> list:
    parser = AntigravityStreamParser()
    return [event for line in _lines(name) for event in parser.feed(line)]


class _FakeStream:
    def __init__(self, lines: list[bytes] | None = None, read_data: bytes = b"") -> None:
        self._lines = list(lines or [])
        self._read_data = read_data

    async def readline(self) -> bytes:
        return self._lines.pop(0) if self._lines else b""

    async def read(self) -> bytes:
        return self._read_data


class _FakeProcess:
    def __init__(
        self, *, stdout_lines: list[bytes] | None = None, stderr: bytes = b"", returncode: int = 0
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


async def _collect(runner: AntigravityRunner, process: _FakeProcess, session_id=None) -> list:
    with patch("asyncio.create_subprocess_exec", AsyncMock(return_value=process)) as spawn:
        events = [event async for event in runner.run("do a thing", session_id)]
    runner.last_spawn = spawn  # type: ignore[attr-defined]
    return events


class TestParseRecordedTurns:
    def test_init_carries_the_conversation_id(self) -> None:
        first = _parse("agy_turn_with_tool.jsonl")[0]
        assert first.message_type is MessageType.SYSTEM
        assert first.session_id == CONVERSATION

    def test_tool_step_becomes_a_ccdb_tool_with_its_command(self) -> None:
        tools = [e.tool_use for e in _parse("agy_turn_with_tool.jsonl") if e.tool_use]
        assert len(tools) == 1
        assert tools[0].tool_name == "Bash"
        assert tools[0].tool_input["command"] == "cat a.txt"
        assert tools[0].category is ToolCategory.COMMAND
        assert tools[0].display_name == "Running: cat a.txt"

    def test_tool_result_is_a_user_event_for_the_same_tool(self) -> None:
        events = _parse("agy_turn_with_tool.jsonl")
        tool = next(e.tool_use for e in events if e.tool_use)
        results = [e for e in events if e.tool_result_id]
        assert len(results) == 1
        # USER, so EventProcessor stops the tool embed's live timer.
        assert results[0].message_type is MessageType.USER
        assert results[0].tool_result_id == tool.tool_id
        assert results[0].tool_result_content == "alpha\r\n"

    def test_text_streams_as_partials_then_one_complete_reply(self) -> None:
        events = _parse("agy_turn_with_tool.jsonl")
        partials = [e.text for e in events if e.is_partial]
        assert partials and all(p for p in partials)
        assert partials[-1].endswith("is `alpha`.")
        replies = [
            e.text
            for e in events
            if e.text and not e.is_partial and e.message_type is MessageType.ASSISTANT
        ]
        assert replies == ["The output of [a.txt](file:///work/scratch/a.txt) is `alpha`."]

    def test_successful_turn_completes_without_error(self) -> None:
        result = _parse("agy_turn_with_tool.jsonl")[-1]
        assert result.message_type is MessageType.RESULT
        assert result.is_complete
        assert result.error is None
        assert result.session_id == CONVERSATION

    def test_turn_usage_is_built_from_steps(self) -> None:
        result = _parse("agy_turn_with_tool.jsonl")[-1]
        # Largest per-step input (context is re-sent each step), summed output.
        assert result.input_tokens == 12028
        assert result.output_tokens == 161 + 251

    def test_resumed_turn_reports_this_turn_not_the_conversation_total(self) -> None:
        events = _parse("agy_turn_resumed.jsonl")
        assert events[0].session_id == CONVERSATION
        assert [
            e.text
            for e in events
            if e.text and not e.is_partial and e.message_type is MessageType.ASSISTANT
        ] == ["alpha"]
        result = events[-1]
        # result.usage says 36356 / 462 — the whole conversation so far.
        assert result.input_tokens == 12548
        assert result.output_tokens == 50

    def test_unknown_model_is_an_error_result(self) -> None:
        events = _parse("agy_turn_unknown_model.jsonl")
        assert len(events) == 1
        assert events[0].is_complete
        assert events[0].error is not None
        assert "not recognized" in events[0].error

    def test_headless_permission_denial_is_not_an_empty_success(self) -> None:
        result = _parse("agy_turn_permission_denied.jsonl")[-1]
        assert result.is_complete
        assert result.error is not None
        assert "RunCommand" in result.error
        assert "CCDB_DANGEROUSLY_SKIP_PERMISSIONS" in result.error

    def test_denial_with_an_answer_keeps_the_answer_and_warns(self) -> None:
        parser = AntigravityStreamParser()
        line = json.dumps(
            {
                "event": "result",
                "result": {
                    "conversation_id": CONVERSATION,
                    "status": "SUCCESS",
                    "response": "I could not run it.",
                    "denied_actions": [{"action": "command", "display_name": "RunCommand"}],
                },
            }
        )
        warning, result = parser.feed(line)
        assert warning.message_type is MessageType.SYSTEM
        assert warning.session_id is None
        assert "RunCommand" in (warning.text or "")
        assert result.error is None
        assert result.text == "I could not run it."

    def test_blank_and_malformed_lines_are_ignored(self) -> None:
        parser = AntigravityStreamParser()
        assert parser.feed("") == []
        assert parser.feed("not json") == []
        assert parser.feed('{"event": "something_new"}') == []


class TestBuildArgs:
    def test_prompt_never_reaches_argv(self) -> None:
        args = AntigravityRunner(command="agy")._build_args(None)
        assert args[:7] == [
            "agy",
            "--print",
            "",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
        ]
        assert "do a thing" not in args

    def test_resume_uses_conversation(self) -> None:
        args = AntigravityRunner()._build_args(CONVERSATION)
        assert args[args.index("--conversation") + 1] == CONVERSATION

    def test_invalid_session_id_is_refused(self) -> None:
        with pytest.raises(ValueError):
            AntigravityRunner()._build_args("--dangerously-skip-permissions")

    def test_model_and_effort(self) -> None:
        args = AntigravityRunner(model="claude-opus-4-6-thinking", effort="high")._build_args(None)
        assert args[args.index("--model") + 1] == "claude-opus-4-6-thinking"
        assert args[args.index("--effort") + 1] == "high"

    def test_flag_shaped_model_is_refused(self) -> None:
        with pytest.raises(ValueError):
            AntigravityRunner(model="--sandbox")._build_args(None)

    def test_invalid_effort_is_refused(self) -> None:
        with pytest.raises(ValueError):
            AntigravityRunner(effort="ultra")._build_args(None)

    def test_skip_permissions_maps_to_the_agy_flag(self) -> None:
        args = AntigravityRunner(dangerously_skip_permissions=True)._build_args(None)
        assert "--dangerously-skip-permissions" in args
        assert "--mode" not in args

    def test_permission_mode_maps_to_agy_mode(self) -> None:
        args = AntigravityRunner(permission_mode="acceptEdits")._build_args(None)
        assert args[args.index("--mode") + 1] == "accept-edits"
        assert "--dangerously-skip-permissions" not in args
        assert "--mode" not in AntigravityRunner(permission_mode="default")._build_args(None)

    def test_bypass_permissions_mode_skips_permissions(self) -> None:
        args = AntigravityRunner(permission_mode="bypassPermissions")._build_args(None)
        assert "--dangerously-skip-permissions" in args


class TestRun:
    async def test_prompt_goes_over_stdin_as_a_stream_input_message(self) -> None:
        lines = [(line + "\n").encode() for line in _lines("agy_turn_resumed.jsonl")]
        process = _FakeProcess(stdout_lines=lines)
        runner = AntigravityRunner(command="agy")
        events = await _collect(runner, process, CONVERSATION)
        written = process.stdin.write.call_args[0][0].decode()
        assert json.loads(written) == {"event": "user", "message": {"content": "do a thing"}}
        process.stdin.close.assert_called_once()
        assert events[-1].is_complete and events[-1].error is None

    async def test_unenforceable_tool_allowlist_is_refused(self) -> None:
        """agy has no tool allowlist flag; skipping permissions would run every tool."""
        runner = AntigravityRunner(dangerously_skip_permissions=True, allowed_tools=["Read"])
        events = await _collect(runner, _FakeProcess())
        assert len(events) == 1
        assert events[0].error is not None and "CCDB_ALLOWED_TOOLS" in events[0].error
        runner.last_spawn.assert_not_called()  # type: ignore[attr-defined]

    @pytest.mark.parametrize("mode", ["auto", "acceptEdits", "bypassPermissions"])
    async def test_allowlist_is_refused_when_agy_approves_on_its_own(self, mode: str) -> None:
        """`--mode auto` / `accept-edits` let agy approve tools itself, bypassing the list."""
        runner = AntigravityRunner(permission_mode=mode, allowed_tools=["Read"])
        events = await _collect(runner, _FakeProcess())
        assert len(events) == 1
        assert events[0].error is not None and "CCDB_ALLOWED_TOOLS" in events[0].error
        runner.last_spawn.assert_not_called()  # type: ignore[attr-defined]

    @pytest.mark.parametrize("mode", ["default", "plan"])
    async def test_allowlist_runs_when_agy_denies_by_default(self, mode: str) -> None:
        """Default (auto-deny) and plan approve nothing, so the list cannot be exceeded."""
        lines = [(line + "\n").encode() for line in _lines("agy_turn_resumed.jsonl")]
        runner = AntigravityRunner(permission_mode=mode, allowed_tools=["Read"])
        events = await _collect(runner, _FakeProcess(stdout_lines=lines))
        runner.last_spawn.assert_called_once()  # type: ignore[attr-defined]
        assert events[-1].is_complete and events[-1].error is None

    async def test_exit_without_result_is_an_error(self) -> None:
        process = _FakeProcess(stdout_lines=[], stderr=b"boom", returncode=2)
        events = await _collect(AntigravityRunner(), process)
        assert events[-1].is_complete
        assert "code 2" in (events[-1].error or "")
        assert "boom" in (events[-1].error or "")


class TestFactory:
    def test_create_backend_builds_the_runner(self) -> None:
        runner = create_backend(backend="antigravity", model=None, command="/opt/agy")
        assert isinstance(runner, AntigravityRunner)
        assert isinstance(runner, SessionBackend)
        assert runner.command == "/opt/agy"

    def test_clone_keeps_configuration(self) -> None:
        runner = AntigravityRunner(command="/opt/agy", model="m", dangerously_skip_permissions=True)
        cloned = runner.clone(thread_id=7, effort="low")
        assert isinstance(cloned, AntigravityRunner)
        assert cloned.command == "/opt/agy"
        assert cloned.model == "m"
        assert cloned.effort == "low"
        assert cloned.thread_id == 7
        assert cloned.dangerously_skip_permissions

    def test_env_strips_the_bot_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "secret")
        env = AntigravityRunner(api_port=9999, thread_id=5)._build_env()
        assert "DISCORD_BOT_TOKEN" not in env
        assert env["CCDB_API_URL"] == "http://127.0.0.1:9999"
        assert env["DISCORD_THREAD_ID"] == "5"


class TestWiring:
    def test_backend_is_selectable(self) -> None:
        from claude_discord.backend_settings import ALL_BACKENDS

        assert "antigravity" in ALL_BACKENDS

    def test_factory_uses_the_configured_command(self) -> None:
        from claude_discord.backend_factory import BackendFactory

        factory = BackendFactory(
            claude_command="claude",
            codex_command="codex",
            permission_mode="acceptEdits",
            working_dir=None,
            timeout_seconds=60,
            dangerously_skip_permissions=False,
            allowed_tools=None,
            append_system_prompt=None,
            effort=None,
            agy_command="/home/me/.local/bin/agy",
        )
        assert factory.command_for("antigravity") == "/home/me/.local/bin/agy"
        runner = factory.build(backend="antigravity")
        assert isinstance(runner, AntigravityRunner)
        assert runner.command == "/home/me/.local/bin/agy"
        assert runner.model is None

    def test_factory_defaults_to_agy_on_path(self) -> None:
        from claude_discord.backend_factory import BackendFactory

        factory = BackendFactory(
            claude_command="claude",
            codex_command="codex",
            permission_mode="default",
            working_dir=None,
            timeout_seconds=60,
            dangerously_skip_permissions=False,
            allowed_tools=None,
            append_system_prompt=None,
            effort=None,
        )
        assert factory.command_for("antigravity") == "agy"

    def test_event_processor_tags_the_backend(self) -> None:
        from claude_discord.cogs.event_processor import _backend_name_from_runner

        assert _backend_name_from_runner(AntigravityRunner()) == "antigravity"

    def test_effort_levels_are_offered(self) -> None:
        from claude_discord.cogs.backend_command import EFFORT_ORDER, VALID_EFFORTS

        assert EFFORT_ORDER["antigravity"] == ["low", "medium", "high", "xhigh", "max"]
        assert VALID_EFFORTS["antigravity"] == frozenset(EFFORT_ORDER["antigravity"])
