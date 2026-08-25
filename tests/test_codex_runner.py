"""Tests for CodexRunner — OpenAI Codex CLI backend."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_code_core.backend import SessionBackend
from claude_code_core.codex_runner import CodexRunner, parse_codex_line
from claude_code_core.types import MessageType


@pytest.fixture(autouse=True)
def _clean_codex_sandbox_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate tests from CCDB_CODEX_SANDBOX_OVERRIDE in the ambient environment.

    A developer's real shell/.env (e.g. a live deployment) may export this to
    work around a host-specific sandbox issue — that must never leak into the
    argv-structure assertions below. Tests that specifically exercise the
    override still call monkeypatch.setenv() themselves, which layers on top
    of (and is undone independently of) this fixture's delenv.
    """
    monkeypatch.delenv("CCDB_CODEX_SANDBOX_OVERRIDE", raising=False)


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
        pid: int = 12345,
    ) -> None:
        self.stdout = _FakeStream(stdout_lines)
        self.stderr = _FakeStream(read_data=stderr)
        self.stdin = MagicMock()
        self.stdin.drain = AsyncMock()
        self.stdin.wait_closed = AsyncMock()
        self.returncode = returncode
        self.pid = pid

    async def wait(self) -> int:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


class _InterruptibleStream:
    def __init__(self, interrupted: asyncio.Event) -> None:
        self._interrupted = interrupted

    async def readline(self) -> bytes:
        await self._interrupted.wait()
        return b""


class _InterruptibleProcess(_FakeProcess):
    def __init__(self) -> None:
        super().__init__(returncode=0)
        self.returncode = None
        self.interrupted = asyncio.Event()
        self.stdout = _InterruptibleStream(self.interrupted)

    async def wait(self) -> int:
        await self.interrupted.wait()
        assert self.returncode is not None
        return self.returncode

    def send_signal(self, _signum: int) -> None:
        self.returncode = 1
        self.interrupted.set()


class _CompletesBeforeExitProcess(_FakeProcess):
    """Process that emits turn.completed before its natural exit is observed."""

    def __init__(self, completed_line: bytes) -> None:
        super().__init__(stdout_lines=[completed_line + b"\n"], returncode=0)
        self.returncode = None
        self.terminated = False
        self.waited = False

    async def wait(self) -> int:
        self.waited = True
        self.returncode = 0
        return 0

    def terminate(self) -> None:
        self.terminated = True
        super().terminate()


class TestCodexRunnerIsBackend:
    """CodexRunner must satisfy the SessionBackend protocol."""

    def test_is_session_backend(self) -> None:
        runner = CodexRunner(command="codex", model="o4-mini")
        assert isinstance(runner, SessionBackend)


class TestCodexRunnerBuildArgs:
    """Tests for _build_args() — Codex CLI flag assembly."""

    def test_basic_args(self) -> None:
        runner = CodexRunner(command="codex", model="o4-mini")
        args = runner._build_args("hello", session_id=None)
        assert args[0] == "codex"
        assert "exec" in args
        assert "--json" in args
        assert "--model" in args or "-m" in args
        assert "o4-mini" in args

    def test_resume_session(self) -> None:
        runner = CodexRunner(command="codex", model="o4-mini")
        args = runner._build_args("hello", session_id="0199a213-81c0-7800-8aa1-bbab2a035a53")
        assert "resume" in args
        assert "0199a213-81c0-7800-8aa1-bbab2a035a53" in args

    def test_never_emits_the_broken_ask_for_approval_flag(self) -> None:
        """`codex exec` rejects `--ask-for-approval` outright on codex-cli >= ~0.13x
        ("unexpected argument") — regression guard against reintroducing it.
        `permission_mode` has no CLI lever for Codex (exec has no approval loop)."""
        for mode in ("acceptEdits", "full", "none", "default", "auto", "plan"):
            runner = CodexRunner(command="codex", model="o4-mini", permission_mode=mode)
            args = runner._build_args("hello", session_id=None)
            assert "--ask-for-approval" not in args
            assert "-a" not in args

    def test_no_sandbox_flag_by_default(self) -> None:
        """Without CCDB_CODEX_SANDBOX_OVERRIDE set, ccdb must never pass --sandbox —
        Codex picks its own (config.toml-driven) default, unchanged from today."""
        runner = CodexRunner(command="codex", model="o4-mini")
        args = runner._build_args("hello", session_id=None)
        assert "--sandbox" not in args

    def test_sandbox_override_from_env(self, monkeypatch) -> None:
        monkeypatch.setenv("CCDB_CODEX_SANDBOX_OVERRIDE", "danger-full-access")
        runner = CodexRunner(command="codex", model="o4-mini")
        args = runner._build_args("hello", session_id=None)
        assert args[args.index("--sandbox") + 1] == "danger-full-access"

    def test_sandbox_override_precedes_resume_subcommand(self, monkeypatch) -> None:
        monkeypatch.setenv("CCDB_CODEX_SANDBOX_OVERRIDE", "danger-full-access")
        runner = CodexRunner(command="codex", model="o4-mini")
        sid = "0199a213-81c0-7800-8aa1-bbab2a035a53"
        args = runner._build_args("hello", session_id=sid)
        assert args.index("--sandbox") < args.index("resume")

    def test_invalid_sandbox_override_is_ignored(self, monkeypatch) -> None:
        """A typo'd override must fail closed — no --sandbox flag at all — rather
        than passing an unvalidated string straight to the subprocess argv."""
        monkeypatch.setenv("CCDB_CODEX_SANDBOX_OVERRIDE", "full-access")
        runner = CodexRunner(command="codex", model="o4-mini")
        args = runner._build_args("hello", session_id=None)
        assert "--sandbox" not in args

    def test_sandbox_override_not_applied_when_bypassing_permissions(self, monkeypatch) -> None:
        """--dangerously-bypass-approvals-and-sandbox already disables sandboxing;
        --sandbox must not also be added."""
        monkeypatch.setenv("CCDB_CODEX_SANDBOX_OVERRIDE", "danger-full-access")
        runner = CodexRunner(command="codex", model="o4-mini", dangerously_skip_permissions=True)
        args = runner._build_args("hello", session_id=None)
        assert "--sandbox" not in args
        assert "--dangerously-bypass-approvals-and-sandbox" in args

    def test_dangerously_skip_permissions(self) -> None:
        runner = CodexRunner(command="codex", model="o4-mini", dangerously_skip_permissions=True)
        args = runner._build_args("hello", session_id=None)
        assert "--yolo" in args or "--dangerously-bypass-approvals-and-sandbox" in args

    def test_working_dir_flag(self) -> None:
        runner = CodexRunner(command="codex", model="o4-mini", working_dir="/tmp/work")
        args = runner._build_args("hello", session_id=None)
        assert "--cd" in args or "-C" in args
        assert "/tmp/work" in args

    def test_skip_git_repo_check_always_present(self) -> None:
        """Working dirs are frequently plain folders, not git repos (e.g. the
        default CLAUDE_WORKING_DIR). Codex CLI refuses to run outside a git
        repo unless told otherwise, so ccdb must always pass this flag —
        Claude Code has no such restriction, and ccdb aims to be backend-
        agnostic from the user's point of view.
        """
        runner = CodexRunner(command="codex", model="o4-mini")
        args = runner._build_args("hello", session_id=None)
        assert "--skip-git-repo-check" in args

    def test_skip_git_repo_check_present_on_resume(self) -> None:
        runner = CodexRunner(command="codex", model="o4-mini")
        args = runner._build_args("hello", session_id="0199a213-81c0-7800-8aa1-bbab2a035a53")
        assert "--skip-git-repo-check" in args

    def test_prompt_is_not_in_args(self) -> None:
        runner = CodexRunner(command="codex", model="o4-mini")
        args = runner._build_args("hello world", session_id=None)
        assert "hello world" not in args
        assert args[-1] == "-"

    def test_large_prompt_is_not_in_args(self) -> None:
        runner = CodexRunner(command="codex", model="o4-mini")
        large_prompt = "x" * 200_000
        args = runner._build_args(large_prompt, session_id=None)
        assert large_prompt not in args
        assert args[-1] == "-"

    def test_session_id_validation(self) -> None:
        runner = CodexRunner(command="codex", model="o4-mini")
        with pytest.raises(ValueError, match="Invalid session_id"):
            runner._build_args("hello", session_id="'; DROP TABLE --")

    def test_no_model_omits_model_flag(self) -> None:
        # model=None (the new default) means: defer to the Codex CLI's own
        # config.toml default — never pass --model.
        runner = CodexRunner(command="codex")
        assert runner.model is None
        args = runner._build_args("hello", session_id=None)
        assert "--model" not in args
        assert "-m" not in args

    def test_empty_model_omits_model_flag(self) -> None:
        runner = CodexRunner(command="codex", model="")
        args = runner._build_args("hello", session_id=None)
        assert "--model" not in args

    def test_effort_injects_reasoning_config(self) -> None:
        runner = CodexRunner(command="codex", model="gpt-5.5", effort="high")
        args = runner._build_args("hello", session_id=None)
        assert "-c" in args
        assert "model_reasoning_effort=high" in args

    def test_no_effort_omits_reasoning_config(self) -> None:
        runner = CodexRunner(command="codex", model="gpt-5.5")
        args = runner._build_args("hello", session_id=None)
        assert not any(a.startswith("model_reasoning_effort=") for a in args)

    def test_invalid_effort_raises(self) -> None:
        runner = CodexRunner(command="codex", model="gpt-5.5", effort="bogus")
        with pytest.raises(ValueError, match="Invalid Codex effort"):
            runner._build_args("hello", session_id=None)

    @pytest.mark.parametrize(
        "session_id",
        [None, "019e29a0-d5b0-71f0-bdc0-46f09a06fdaf"],
    )
    def test_append_system_prompt_becomes_developer_instructions(
        self, session_id: str | None
    ) -> None:
        context = 'AI Lounge says: "check the other thread" 🎉\nThen announce your work.'
        runner = CodexRunner(command="codex", append_system_prompt=context)

        args = runner._build_args("user prompt stays on stdin", session_id=session_id)

        expected = f"developer_instructions={json.dumps(context, ensure_ascii=False)}"
        assert expected in args
        config_index = args.index(expected)
        assert args[config_index - 1] == "-c"
        assert "user prompt stays on stdin" not in args
        assert config_index < args.index("-")

    def test_no_system_prompt_omits_developer_instructions(self) -> None:
        runner = CodexRunner(command="codex")

        args = runner._build_args("hello", session_id=None)

        assert not any(arg.startswith("developer_instructions=") for arg in args)


class TestCodexRunnerClone:
    """Tests for clone() — creating a new runner with overrides."""

    def test_clone_preserves_config(self) -> None:
        runner = CodexRunner(command="codex", model="o4-mini", working_dir="/tmp")
        cloned = runner.clone()
        assert isinstance(cloned, CodexRunner)
        assert cloned.model == "o4-mini"
        assert cloned.working_dir == "/tmp"

    def test_clone_overrides_model(self) -> None:
        runner = CodexRunner(command="codex", model="o4-mini")
        cloned = runner.clone(model="gpt-5.4")
        assert cloned.model == "gpt-5.4"

    def test_clone_overrides_working_dir(self) -> None:
        runner = CodexRunner(command="codex", model="o4-mini", working_dir="/old")
        cloned = runner.clone(working_dir="/new")
        assert cloned.working_dir == "/new"

    def test_clone_returns_codex_runner_not_claude(self) -> None:
        runner = CodexRunner(command="codex", model="o4-mini")
        cloned = runner.clone(model="gpt-5.4")
        assert type(cloned) is CodexRunner

    def test_clone_preserves_effort(self) -> None:
        runner = CodexRunner(command="codex", model="gpt-5.5", effort="xhigh")
        cloned = runner.clone()
        assert cloned.effort == "xhigh"

    def test_clone_overrides_effort(self) -> None:
        runner = CodexRunner(command="codex", model="gpt-5.5", effort="high")
        cloned = runner.clone(effort="low")
        assert cloned.effort == "low"

    def test_clone_preserves_append_system_prompt(self) -> None:
        runner = CodexRunner(command="codex", append_system_prompt="existing context")

        cloned = runner.clone()

        assert cloned.append_system_prompt == "existing context"

    def test_clone_overrides_append_system_prompt(self) -> None:
        runner = CodexRunner(command="codex", append_system_prompt="old context")

        cloned = runner.clone(append_system_prompt="fresh lounge context")

        assert cloned.append_system_prompt == "fresh lounge context"


class TestCodexRunnerRun:
    @pytest.mark.asyncio
    async def test_turn_completed_waits_for_natural_process_exit(self, monkeypatch) -> None:
        """A terminal event must not release the session while Codex still owns it."""
        completed_line = json.dumps({"type": "turn.completed", "usage": {}}).encode()
        process = _CompletesBeforeExitProcess(completed_line)

        async def fake_create_subprocess_exec(*args, **kwargs):
            return process

        monkeypatch.setattr(
            "claude_code_core.codex_runner.asyncio.create_subprocess_exec",
            fake_create_subprocess_exec,
        )
        runner = CodexRunner(command="codex")

        events = [event async for event in runner.run("hello")]

        assert len(events) == 1
        assert events[0].is_complete is True
        assert process.waited is True
        assert process.terminated is False

    @pytest.mark.asyncio
    async def test_intentional_interrupt_is_not_reported_as_cli_error(
        self, monkeypatch, caplog
    ) -> None:
        process = _InterruptibleProcess()
        process_started = asyncio.Event()

        async def fake_create_subprocess_exec(*args, **kwargs):
            process_started.set()
            return process

        monkeypatch.setattr(
            "claude_code_core.codex_runner.asyncio.create_subprocess_exec",
            fake_create_subprocess_exec,
        )
        runner = CodexRunner(command="codex")

        async def collect_events() -> list:
            return [event async for event in runner.run("hello")]

        run_task = asyncio.create_task(collect_events())
        await process_started.wait()
        await runner.interrupt()
        events = await run_task

        assert events == []
        assert "Codex CLI exited with code 1" not in caplog.text

    @pytest.mark.asyncio
    async def test_resume_missing_rollout_falls_back_to_new_session(self, monkeypatch) -> None:
        stale_session = "13f2eb43-93cf-4df6-86d0-a20c035cc26e"
        started_line = json.dumps(
            {"type": "thread.started", "thread_id": "019f-new-session"}
        ).encode()
        completed_line = json.dumps({"type": "turn.completed", "usage": {}}).encode()
        processes = [
            _FakeProcess(
                stderr=(
                    b"Error: thread/resume: thread/resume failed: "
                    b"no rollout found for thread id 13f2eb43-93cf-4df6-86d0-a20c035cc26e"
                ),
                returncode=1,
                pid=100,
            ),
            _FakeProcess(
                stdout_lines=[started_line + b"\n", completed_line + b"\n"],
                returncode=0,
                pid=101,
            ),
        ]
        calls: list[tuple[str, ...]] = []

        async def fake_create_subprocess_exec(*args, **kwargs):
            calls.append(args)
            return processes.pop(0)

        monkeypatch.setattr(
            "claude_code_core.codex_runner.asyncio.create_subprocess_exec",
            fake_create_subprocess_exec,
        )
        runner = CodexRunner(command="codex", working_dir="/work")

        events = [event async for event in runner.run("hello", session_id=stale_session)]

        assert len(calls) == 2
        assert calls[0][:3] == ("codex", "exec", "resume")
        assert stale_session in calls[0]
        assert "resume" not in calls[1]
        assert "--cd" in calls[1]
        assert "/work" in calls[1]
        assert [event.error for event in events if event.error] == []
        assert events[0].session_id == "019f-new-session"

    @pytest.mark.asyncio
    async def test_non_rollout_resume_error_is_reported(self, monkeypatch) -> None:
        process = _FakeProcess(stderr=b"permission denied", returncode=1)

        async def fake_create_subprocess_exec(*args, **kwargs):
            return process

        monkeypatch.setattr(
            "claude_code_core.codex_runner.asyncio.create_subprocess_exec",
            fake_create_subprocess_exec,
        )
        runner = CodexRunner(command="codex")

        events = [
            event
            async for event in runner.run(
                "hello",
                session_id="019e29a0-d5b0-71f0-bdc0-46f09a06fdaf",
            )
        ]

        assert len(events) == 1
        assert events[0].error is not None
        assert "CLI exited with code 1" in events[0].error

    @pytest.mark.asyncio
    async def test_resume_stream_disconnect_rolls_over_with_text_context(
        self, monkeypatch, tmp_path
    ) -> None:
        """Image-heavy Codex sessions must not leave a thread permanently stuck."""
        stale_session = "019f68db-1a72-7bc1-a222-af76b9dd4cdb"
        sessions_dir = tmp_path / "sessions" / "2026" / "07" / "16"
        sessions_dir.mkdir(parents=True)
        rollout = sessions_dir / f"rollout-2026-07-16T11-56-57-{stale_session}.jsonl"
        records = [
            {
                "type": "event_msg",
                "payload": {"type": "user_message", "message": "Design a dashboard"},
            },
            {
                "type": "response_item",
                "payload": {
                    "type": "custom_tool_call_output",
                    "output": [
                        {"type": "input_image", "image_url": "data:image/png;base64,SECRET"}
                    ],
                },
            },
            {
                "type": "event_msg",
                "payload": {
                    "type": "agent_message",
                    "message": "I generated three variants and updated the files.",
                },
            },
        ]
        rollout.write_text("\n".join(json.dumps(record) for record in records) + "\n")

        disconnect_line = json.dumps(
            {
                "type": "error",
                "message": (
                    "stream disconnected before completion: "
                    "websocket closed by server before response.completed"
                ),
            }
        ).encode()
        started_line = json.dumps(
            {"type": "thread.started", "thread_id": "019f-replacement-session"}
        ).encode()
        completed_line = json.dumps({"type": "turn.completed", "usage": {}}).encode()
        first_process = _FakeProcess(stdout_lines=[disconnect_line + b"\n"], pid=100)
        replacement_process = _FakeProcess(
            stdout_lines=[started_line + b"\n", completed_line + b"\n"],
            pid=101,
        )
        processes = [first_process, replacement_process]
        calls: list[tuple[str, ...]] = []

        async def fake_create_subprocess_exec(*args, **kwargs):
            calls.append(args)
            return processes.pop(0)

        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        monkeypatch.setattr(
            "claude_code_core.codex_runner.asyncio.create_subprocess_exec",
            fake_create_subprocess_exec,
        )
        runner = CodexRunner(command="codex", working_dir="/work")

        events = [event async for event in runner.run("continue", session_id=stale_session)]

        assert len(calls) == 2
        assert calls[0][:3] == ("codex", "exec", "resume")
        assert "resume" not in calls[1]
        assert [event.error for event in events if event.error] == []
        assert events[0].session_id == "019f-replacement-session"
        replacement_process.stdin.write.assert_called_once()
        recovery_prompt = replacement_process.stdin.write.call_args.args[0].decode()
        assert "Design a dashboard" in recovery_prompt
        assert "I generated three variants and updated the files." in recovery_prompt
        assert "Current user message:\ncontinue" in recovery_prompt
        assert "data:image" not in recovery_prompt
        # Subprocess argv must still never contain prompt text.
        assert all("Design a dashboard" not in arg for call in calls for arg in call)

    @pytest.mark.asyncio
    async def test_new_session_stream_disconnect_does_not_retry(self, monkeypatch) -> None:
        disconnect_line = json.dumps(
            {
                "type": "error",
                "message": (
                    "stream disconnected before completion: "
                    "websocket closed by server before response.completed"
                ),
            }
        ).encode()
        process = _FakeProcess(stdout_lines=[disconnect_line + b"\n"])
        calls = 0

        async def fake_create_subprocess_exec(*args, **kwargs):
            nonlocal calls
            calls += 1
            return process

        monkeypatch.setattr(
            "claude_code_core.codex_runner.asyncio.create_subprocess_exec",
            fake_create_subprocess_exec,
        )
        runner = CodexRunner(command="codex")

        events = [event async for event in runner.run("hello")]

        assert calls == 1
        assert len(events) == 1
        assert events[0].error is not None
        assert "websocket closed" in events[0].error

    @pytest.mark.asyncio
    async def test_resume_disconnect_after_output_does_not_retry(self, monkeypatch) -> None:
        message_line = json.dumps(
            {
                "type": "item.completed",
                "item": {"id": "msg-1", "type": "agent_message", "text": "Partial output"},
            }
        ).encode()
        disconnect_line = json.dumps(
            {
                "type": "error",
                "message": (
                    "stream disconnected before completion: "
                    "websocket closed by server before response.completed"
                ),
            }
        ).encode()
        process = _FakeProcess(stdout_lines=[message_line + b"\n", disconnect_line + b"\n"])
        calls = 0

        async def fake_create_subprocess_exec(*args, **kwargs):
            nonlocal calls
            calls += 1
            return process

        monkeypatch.setattr(
            "claude_code_core.codex_runner.asyncio.create_subprocess_exec",
            fake_create_subprocess_exec,
        )
        runner = CodexRunner(command="codex")

        events = [
            event
            async for event in runner.run(
                "continue", session_id="019f68db-1a72-7bc1-a222-af76b9dd4cdb"
            )
        ]

        assert calls == 1
        assert [event.text for event in events if event.text] == ["Partial output"]
        assert len([event for event in events if event.error]) == 1


class TestParseCodexLine:
    """Tests for parse_codex_line() — Codex JSONL → StreamEvent."""

    def test_thread_started(self) -> None:
        line = json.dumps({"type": "thread.started", "thread_id": "abc-123"})
        event = parse_codex_line(line)
        assert event is not None
        assert event.message_type == MessageType.SYSTEM
        assert event.session_id == "abc-123"

    def test_item_completed_agent_message(self) -> None:
        line = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "item_1",
                    "type": "agent_message",
                    "text": "Hello, world!",
                },
            }
        )
        event = parse_codex_line(line)
        assert event is not None
        assert event.message_type == MessageType.ASSISTANT
        assert event.text == "Hello, world!"

    def test_item_started_command_execution(self) -> None:
        line = json.dumps(
            {
                "type": "item.started",
                "item": {
                    "id": "item_2",
                    "type": "command_execution",
                    "command": "ls -la",
                    "status": "in_progress",
                },
            }
        )
        event = parse_codex_line(line)
        assert event is not None
        assert event.tool_use is not None
        assert event.tool_use.tool_name == "Bash"
        assert event.tool_use.tool_input.get("command") == "ls -la"

    def test_turn_completed_with_usage(self) -> None:
        line = json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 1000,
                    "output_tokens": 200,
                    "cached_input_tokens": 500,
                    "reasoning_output_tokens": 50,
                },
            }
        )
        event = parse_codex_line(line)
        assert event is not None
        assert event.input_tokens == 1000
        assert event.output_tokens == 200
        assert event.cache_read_tokens == 500

    def test_item_completed_command_execution(self) -> None:
        line = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "item_2",
                    "type": "command_execution",
                    "command": "ls -la",
                    "output": "total 42\ndrwxr-xr-x ...",
                },
            }
        )
        event = parse_codex_line(line)
        assert event is not None
        assert event.tool_result_content is not None

    def test_command_execution_completion_routes_as_tool_result(self) -> None:
        """The completion must be a USER event so EventProcessor cancels the
        live timer via _on_tool_result. If it stays ASSISTANT, the timer runs
        forever and the embed is stuck on "Running ... Ns elapsed"."""
        line = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "item_2",
                    "type": "command_execution",
                    "command": "ls -la",
                    "output": "total 42",
                },
            }
        )
        event = parse_codex_line(line)
        assert event is not None
        assert event.message_type == MessageType.USER
        assert event.tool_result_id == "item_2"

    def test_invalid_json_returns_none(self) -> None:
        event = parse_codex_line("not valid json")
        assert event is None

    def test_empty_line_returns_none(self) -> None:
        event = parse_codex_line("")
        assert event is None

    def test_item_completed_file_changes(self) -> None:
        line = json.dumps(
            {
                "type": "item.completed",
                "item": {
                    "id": "item_3",
                    "type": "file_changes",
                    "text": "Modified src/main.py",
                },
            }
        )
        event = parse_codex_line(line)
        assert event is not None
        assert event.tool_use is not None
        assert event.tool_use.tool_name == "Edit"

    def test_file_changes_is_atomic_and_gets_synthetic_completion(self) -> None:
        """file_changes arrives as a single item.completed with no item.started.
        It opens a tool embed + live timer, so a synthetic tool result must be
        paired with it to stop the timer."""
        from claude_code_core.codex_runner import _atomic_tool_completion

        line = json.dumps(
            {
                "type": "item.completed",
                "item": {"id": "item_3", "type": "file_changes", "text": "Modified main.py"},
            }
        )
        event = parse_codex_line(line)
        assert event is not None
        completion = _atomic_tool_completion(event)
        assert completion is not None
        assert completion.message_type == MessageType.USER
        assert completion.tool_result_id == "item_3"

    def test_command_execution_start_is_not_atomic(self) -> None:
        """A command_execution start has its own completion event, so it must
        NOT receive a synthetic completion (that would double-cancel)."""
        from claude_code_core.codex_runner import _atomic_tool_completion

        line = json.dumps(
            {
                "type": "item.started",
                "item": {"id": "item_2", "type": "command_execution", "command": "ls"},
            }
        )
        event = parse_codex_line(line)
        assert event is not None
        assert _atomic_tool_completion(event) is None

    def test_turn_started(self) -> None:
        line = json.dumps({"type": "turn.started"})
        event = parse_codex_line(line)
        assert event is not None
        assert event.message_type == MessageType.SYSTEM

    def test_error_event(self) -> None:
        line = json.dumps({"type": "error", "message": "something broke"})
        event = parse_codex_line(line)
        assert event is not None
        assert event.error is not None
        assert event.is_complete is True


class TestCodexRunnerArgvStructure:
    """Strict structural tests — verify args match codex CLI's actual grammar.

    Codex CLI v0.124 grammar (verified against ``codex exec --help`` /
    ``codex exec resume --help``):

        codex exec [OPTIONS] [PROMPT]
        codex exec resume [OPTIONS] [SESSION_ID] [PROMPT]

    ``exec`` accepts: ``--json``, ``--model``, ``--sandbox``,
    ``--dangerously-bypass-approvals-and-sandbox``, ``--cd``.
    ``exec resume`` accepts the same flags EXCEPT ``--sandbox`` and ``--cd``
    (both cause exit code 2 when they appear after ``resume``; ``--sandbox``
    must instead precede it — see test_sandbox_override_precedes_resume_subcommand
    in TestCodexRunnerBuildArgs). ``--ask-for-approval`` is a global/
    interactive-only flag, rejected by ``exec`` outright on codex-cli >=
    ~0.13x ("unexpected argument") — ccdb never emits it (see
    test_never_emits_the_broken_ask_for_approval_flag).
    The resume positional args come AFTER all flags, with SESSION_ID before PROMPT.

    These tests guard against regressions like the one that shipped briefly
    in 3.0.0 where resume was invoked as ``codex resume <id> --json …`` —
    a structure codex rejects with ``error: unexpected argument '--json'
    found``.
    """

    def test_new_session_starts_with_exec(self) -> None:
        runner = CodexRunner(command="codex", model="gpt-5.4")
        args = runner._build_args("prompt", session_id=None)
        # First positional must be the command, second must be 'exec',
        # and 'resume' must NOT appear when starting a new session.
        assert args[0] == "codex"
        assert args[1] == "exec"
        assert "resume" not in args

    def test_resume_starts_with_exec_resume(self) -> None:
        sid = "019e29a0-d5b0-71f0-bdc0-46f09a06fdaf"
        runner = CodexRunner(command="codex", model="gpt-5.4")
        args = runner._build_args("prompt", session_id=sid)
        # The first three tokens must be exactly: codex exec resume
        assert args[:3] == ["codex", "exec", "resume"], (
            f"Codex resume must be invoked as `codex exec resume`, got: {args[:3]!r}"
        )

    def test_resume_flags_precede_session_id(self) -> None:
        sid = "019e29a0-d5b0-71f0-bdc0-46f09a06fdaf"
        runner = CodexRunner(
            command="codex",
            model="gpt-5.4",
            permission_mode="acceptEdits",
            working_dir="/work",
        )
        args = runner._build_args("hello", session_id=sid)
        sid_idx = args.index(sid)
        # --json, --model must both come before SESSION_ID.
        # NOTE: --cd is NOT supported by `codex exec resume` (only by `codex exec`).
        for flag in ("--json", "--model"):
            assert flag in args, f"{flag} missing from resume args"
            assert args.index(flag) < sid_idx, (
                f"{flag} should appear before SESSION_ID in codex exec resume"
            )

    def test_sandbox_override_precedes_resume_on_resumed_session(self, monkeypatch) -> None:
        """--sandbox is an exec-level option, rejected by `exec resume` when it
        appears after `resume` — the override must precede the subcommand."""
        monkeypatch.setenv("CCDB_CODEX_SANDBOX_OVERRIDE", "danger-full-access")
        sid = "019e29a0-d5b0-71f0-bdc0-46f09a06fdaf"
        runner = CodexRunner(command="codex", model="gpt-5.4")
        args = runner._build_args("hello", session_id=sid)
        assert args.index("--sandbox") < args.index("resume"), (
            "`codex exec resume --sandbox ...` is rejected by codex-cli with exit code 2"
        )

    def test_cd_flag_not_passed_on_resume(self) -> None:
        """codex exec resume does not accept --cd; must be omitted to avoid exit code 2."""
        sid = "019e29a0-d5b0-71f0-bdc0-46f09a06fdaf"
        runner = CodexRunner(command="codex", model="gpt-5.4", working_dir="/work")
        args = runner._build_args("hello", session_id=sid)
        assert "--cd" not in args, (
            "`codex exec resume` does not support --cd; passing it causes exit code 2"
        )

    def test_cd_flag_passed_on_new_session(self) -> None:
        """--cd must still be passed for new sessions."""
        runner = CodexRunner(command="codex", model="gpt-5.4", working_dir="/work")
        args = runner._build_args("hello", session_id=None)
        assert "--cd" in args
        assert "/work" in args

    def test_resume_session_id_before_stdin_marker(self) -> None:
        sid = "019e29a0-d5b0-71f0-bdc0-46f09a06fdaf"
        runner = CodexRunner(command="codex", model="gpt-5.4")
        args = runner._build_args("hello-prompt", session_id=sid)
        # SESSION_ID must come BEFORE the stdin marker in `codex exec resume`.
        assert "hello-prompt" not in args
        assert args.index(sid) < args.index("-"), (
            "SESSION_ID must precede stdin marker in codex exec resume"
        )

    def test_stdin_marker_is_always_last(self) -> None:
        runner = CodexRunner(command="codex", model="gpt-5.4")
        # New session
        args = runner._build_args("the-prompt", session_id=None)
        assert "the-prompt" not in args
        assert args[-1] == "-"
        # Resume
        args = runner._build_args("the-prompt", session_id="019e29a0-d5b0-71f0-bdc0-46f09a06fdaf")
        assert "the-prompt" not in args
        assert args[-1] == "-"

    def test_dangerously_bypass_flag_for_resume(self) -> None:
        sid = "019e29a0-d5b0-71f0-bdc0-46f09a06fdaf"
        runner = CodexRunner(
            command="codex",
            model="gpt-5.4",
            dangerously_skip_permissions=True,
        )
        args = runner._build_args("hi", session_id=sid)
        assert "--dangerously-bypass-approvals-and-sandbox" in args
        # Must precede SESSION_ID like the other flags
        assert args.index("--dangerously-bypass-approvals-and-sandbox") < args.index(sid)

    def test_no_lingering_resume_top_level_form(self) -> None:
        """`codex resume` (without `exec`) is NOT a valid codex CLI form."""
        sid = "019e29a0-d5b0-71f0-bdc0-46f09a06fdaf"
        runner = CodexRunner(command="codex", model="gpt-5.4")
        args = runner._build_args("hi", session_id=sid)
        # If 'resume' appears at index 1, that means we built `codex resume <id>`
        # which is rejected by codex. Must be at index 2 (after `exec`).
        if "resume" in args:
            assert args.index("resume") == 2, (
                f"resume must follow exec at index 2, got index {args.index('resume')}"
            )


class TestCodexRunnerStdin:
    """Prompt delivery tests for CodexRunner.

    Codex CLI reads instructions from stdin when the prompt positional is "-".
    This keeps large Discord attachment prompts out of argv and avoids E2BIG.
    """

    @pytest.mark.asyncio
    async def test_run_writes_prompt_to_stdin(self) -> None:
        import asyncio as _asyncio

        runner = CodexRunner(command="codex")
        large_prompt = "x" * 200_000

        written: list[bytes] = []

        def capture_write(data: bytes) -> None:
            written.append(data)

        mock_stdin = MagicMock()
        mock_stdin.write = capture_write
        mock_stdin.drain = AsyncMock()
        mock_stdin.close = MagicMock()
        mock_stdin.wait_closed = AsyncMock()

        mock_process = AsyncMock()
        mock_process.pid = 42
        mock_process.returncode = None
        mock_process.stdout = AsyncMock()
        mock_process.stdout.readline = AsyncMock(return_value=b"")
        mock_process.stderr = AsyncMock()
        mock_process.stderr.read = AsyncMock(return_value=b"")
        mock_process.stdin = mock_stdin
        mock_process.wait = AsyncMock(return_value=0)

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_process) as mock_exec,
            patch.object(runner, "_cleanup", new_callable=AsyncMock),
        ):
            _ = [event async for event in runner.run(large_prompt)]

        call_args = mock_exec.call_args.args
        call_kwargs = mock_exec.call_args.kwargs
        assert large_prompt not in call_args
        assert call_args[-1] == "-"
        assert call_kwargs["stdin"] == _asyncio.subprocess.PIPE
        assert written == [large_prompt.encode()]
        mock_stdin.close.assert_called_once()
