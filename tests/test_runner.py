"""Tests for ClaudeRunner argument building and environment handling."""

from __future__ import annotations

import os
import signal as signal_module
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_code_core.codex_runner import CodexRunner
from claude_discord.claude.runner import ClaudeRunner, _resolve_windows_cmd
from claude_discord.claude.types import ImageData


class TestBuildArgs:
    """Tests for _build_args method."""

    def setup_method(self) -> None:
        self.runner = ClaudeRunner(command="claude", model="sonnet")

    def test_basic_args(self) -> None:
        args = self.runner._build_args("hello", session_id=None)
        assert args[0] == "claude"
        assert "-p" in args
        assert "--output-format" in args
        assert "stream-json" in args
        assert "--model" in args
        assert "sonnet" in args
        # Prompt is sent via stdin (stream-json input), NOT as a CLI argument
        assert "--input-format" in args
        assert "hello" not in args
        assert "--" not in args

    def test_session_id_valid_uuid(self) -> None:
        sid = "241e0726-bbc3-40e7-9db0-086823acde26"
        args = self.runner._build_args("hello", session_id=sid)
        assert "--resume" in args
        assert sid in args

    def test_session_id_rejects_injection(self) -> None:
        with pytest.raises(ValueError, match="Invalid session_id"):
            self.runner._build_args("hello", session_id="--malicious-flag")

    def test_session_id_rejects_spaces(self) -> None:
        with pytest.raises(ValueError, match="Invalid session_id"):
            self.runner._build_args("hello", session_id="abc def")

    def test_prompt_not_in_args(self) -> None:
        """Prompt is always sent via stdin, never as a CLI argument (prevents flag injection)."""
        args = self.runner._build_args("--help", session_id=None)
        assert "--help" not in args  # dangerous prompt not in args
        assert "--" not in args  # no -- separator needed

    def test_allowed_tools(self) -> None:
        runner = ClaudeRunner(allowed_tools=["Bash", "Read"])
        args = runner._build_args("hello", session_id=None)
        assert "--allowedTools" in args
        assert "Bash,Read" in args

    def test_dangerously_skip_permissions(self) -> None:
        runner = ClaudeRunner(dangerously_skip_permissions=True)
        args = runner._build_args("hello", session_id=None)
        assert "--dangerously-skip-permissions" in args

    def test_no_dangerously_skip_by_default(self) -> None:
        runner = ClaudeRunner()
        args = runner._build_args("hello", session_id=None)
        assert "--dangerously-skip-permissions" not in args

    def test_auto_mode_suppresses_dangerously_skip(self) -> None:
        """When permission_mode='auto', --dangerously-skip-permissions is NOT added."""
        runner = ClaudeRunner(permission_mode="auto", dangerously_skip_permissions=True)
        args = runner._build_args("hello", session_id=None)
        assert "--dangerously-skip-permissions" not in args
        idx = args.index("--permission-mode")
        assert args[idx + 1] == "auto"

    def test_auto_mode_without_yolo(self) -> None:
        """auto mode works without dangerously_skip_permissions flag."""
        runner = ClaudeRunner(permission_mode="auto")
        args = runner._build_args("hello", session_id=None)
        assert "--dangerously-skip-permissions" not in args
        idx = args.index("--permission-mode")
        assert args[idx + 1] == "auto"

    def test_plan_mode_suppresses_dangerously_skip(self) -> None:
        """When permission_mode='plan', --dangerously-skip-permissions is NOT added."""
        runner = ClaudeRunner(permission_mode="plan", dangerously_skip_permissions=True)
        args = runner._build_args("hello", session_id=None)
        assert "--dangerously-skip-permissions" not in args

    def test_yolo_still_works_with_other_modes(self) -> None:
        """dangerously_skip_permissions works with non-auto/plan modes."""
        runner = ClaudeRunner(permission_mode="acceptEdits", dangerously_skip_permissions=True)
        args = runner._build_args("hello", session_id=None)
        assert "--dangerously-skip-permissions" in args

    def test_include_partial_messages_default(self) -> None:
        runner = ClaudeRunner()
        args = runner._build_args("hello", session_id=None)
        assert "--include-partial-messages" in args

    def test_include_partial_messages_disabled(self) -> None:
        runner = ClaudeRunner(include_partial_messages=False)
        args = runner._build_args("hello", session_id=None)
        assert "--include-partial-messages" not in args

    def test_append_system_prompt_included(self) -> None:
        """--append-system-prompt flag is added when set."""
        runner = ClaudeRunner(append_system_prompt="You are in a concurrent env.")
        args = runner._build_args("hello", session_id=None)
        assert "--append-system-prompt" in args
        idx = args.index("--append-system-prompt")
        assert args[idx + 1] == "You are in a concurrent env."

    def test_always_uses_stream_json_input(self) -> None:
        """All sessions use stream-json input for permission responses."""
        runner = ClaudeRunner()
        args = runner._build_args("hello", session_id=None)
        idx = args.index("--input-format")
        assert args[idx + 1] == "stream-json"

    def test_no_append_system_prompt_by_default(self) -> None:
        runner = ClaudeRunner()
        args = runner._build_args("hello", session_id=None)
        assert "--append-system-prompt" not in args

    def test_clone_propagates_append_system_prompt(self) -> None:
        """clone() with append_system_prompt overrides the parent value."""
        base = ClaudeRunner(append_system_prompt="old context")
        cloned = base.clone(append_system_prompt="new context")
        assert cloned.append_system_prompt == "new context"

    def test_clone_inherits_append_system_prompt(self) -> None:
        """clone() without append_system_prompt inherits parent value."""
        base = ClaudeRunner(append_system_prompt="persistent context")
        cloned = base.clone()
        assert cloned.append_system_prompt == "persistent context"

    def test_clone_none_inherits_parent_append_system_prompt(self) -> None:
        """clone(append_system_prompt=None) inherits parent value (None means 'not provided')."""
        base = ClaudeRunner(append_system_prompt="ctx")
        cloned = base.clone(append_system_prompt=None)
        assert cloned.append_system_prompt == "ctx"  # inherits parent

    def test_fork_session_adds_flag(self) -> None:
        """--fork-session is added to args when fork_session=True and session_id is provided."""
        runner = ClaudeRunner(fork_session=True)
        args = runner._build_args("hi", session_id="abc123")
        assert "--fork-session" in args

    def test_fork_session_flag_after_resume(self) -> None:
        """--fork-session appears after --resume <session_id> in the arg list."""
        runner = ClaudeRunner(fork_session=True)
        args = runner._build_args("hi", session_id="abc123")
        resume_idx = args.index("--resume")
        fork_idx = args.index("--fork-session")
        assert fork_idx > resume_idx

    def test_fork_session_without_session_id_no_flag(self) -> None:
        """--fork-session is NOT added when no session_id is given (nothing to fork)."""
        runner = ClaudeRunner(fork_session=True)
        args = runner._build_args("hi", session_id=None)
        assert "--fork-session" not in args

    def test_fork_session_false_no_flag(self) -> None:
        """--fork-session is NOT added by default."""
        runner = ClaudeRunner()
        args = runner._build_args("hi", session_id="abc123")
        assert "--fork-session" not in args

    def test_clone_propagates_fork_session(self) -> None:
        """clone(fork_session=True) sets fork_session on the cloned runner."""
        base = ClaudeRunner()
        cloned = base.clone(fork_session=True)
        assert cloned.fork_session is True

    def test_clone_fork_session_defaults_false(self) -> None:
        """clone() without fork_session yields fork_session=False."""
        base = ClaudeRunner(fork_session=True)
        cloned = base.clone()
        # fork_session is NOT inherited — it's per-invocation, not a base setting
        assert cloned.fork_session is False

    def test_clone_working_dir_override(self) -> None:
        """clone(working_dir=...) overrides the parent's working_dir."""
        base = ClaudeRunner(working_dir="/global/dir")
        cloned = base.clone(working_dir="/session/specific/dir")
        assert cloned.working_dir == "/session/specific/dir"
        assert base.working_dir == "/global/dir"  # original unchanged

    def test_clone_working_dir_inherits_when_not_provided(self) -> None:
        """clone() without working_dir inherits the parent's value."""
        base = ClaudeRunner(working_dir="/global/dir")
        cloned = base.clone()
        assert cloned.working_dir == "/global/dir"


class TestBuildEnv:
    """Tests for _build_env method."""

    def test_strips_claudecode(self) -> None:
        os.environ["CLAUDECODE"] = "1"
        try:
            runner = ClaudeRunner()
            env = runner._build_env()
            assert "CLAUDECODE" not in env
        finally:
            del os.environ["CLAUDECODE"]

    def test_strips_discord_token(self) -> None:
        os.environ["DISCORD_BOT_TOKEN"] = "secret-token"
        try:
            runner = ClaudeRunner()
            env = runner._build_env()
            assert "DISCORD_BOT_TOKEN" not in env
        finally:
            del os.environ["DISCORD_BOT_TOKEN"]

    def test_strips_discord_token_alt(self) -> None:
        os.environ["DISCORD_TOKEN"] = "secret-token"
        try:
            runner = ClaudeRunner()
            env = runner._build_env()
            assert "DISCORD_TOKEN" not in env
        finally:
            del os.environ["DISCORD_TOKEN"]

    @pytest.mark.parametrize(
        "name",
        ["CCDB_TEAMS_APP_PASSWORD", "CCDB_TEAMS_QUEUE_URL"],
    )
    def test_strips_teams_credentials(self, name: str) -> None:
        os.environ[name] = "secret"
        try:
            for runner in (ClaudeRunner(), CodexRunner()):
                assert name not in runner._build_env()
        finally:
            del os.environ[name]

    def test_preserves_path(self) -> None:
        runner = ClaudeRunner()
        env = runner._build_env()
        assert "PATH" in env

    def test_injects_ccdb_api_url_when_api_port_set(self) -> None:
        runner = ClaudeRunner(api_port=8099)
        env = runner._build_env()
        assert env["CCDB_API_URL"] == "http://127.0.0.1:8099"

    def test_no_ccdb_api_url_when_api_port_not_set(self) -> None:
        # Remove CCDB_API_URL from the process env so it isn't inherited
        original = os.environ.pop("CCDB_API_URL", None)
        try:
            runner = ClaudeRunner()
            env = runner._build_env()
            assert "CCDB_API_URL" not in env
        finally:
            if original is not None:
                os.environ["CCDB_API_URL"] = original

    def test_injects_ccdb_api_secret_when_set(self) -> None:
        runner = ClaudeRunner(api_port=8099, api_secret="my-secret")
        env = runner._build_env()
        assert env["CCDB_API_SECRET"] == "my-secret"

    def test_no_ccdb_api_secret_when_not_set(self) -> None:
        runner = ClaudeRunner(api_port=8099)
        env = runner._build_env()
        assert "CCDB_API_SECRET" not in env

    def test_reads_cli_env_overlay_file(self, tmp_path: Path) -> None:
        overlay = tmp_path / "overlay.env"
        overlay.write_text("MY_CUSTOM_VAR=hello\nANOTHER_VAR=world\n")
        os.environ["CCDB_CLI_ENV_FILE"] = str(overlay)
        try:
            runner = ClaudeRunner()
            env = runner._build_env()
            assert env["MY_CUSTOM_VAR"] == "hello"
            assert env["ANOTHER_VAR"] == "world"
        finally:
            del os.environ["CCDB_CLI_ENV_FILE"]

    def test_cli_env_overlay_skips_comments_and_blanks(self, tmp_path: Path) -> None:
        overlay = tmp_path / "overlay.env"
        overlay.write_text("# comment\n\nVALID_KEY=value\n")
        os.environ["CCDB_CLI_ENV_FILE"] = str(overlay)
        try:
            runner = ClaudeRunner()
            env = runner._build_env()
            assert env["VALID_KEY"] == "value"
            assert "#" not in {k[0] for k in env if k.startswith("#")}
        finally:
            del os.environ["CCDB_CLI_ENV_FILE"]

    def test_cli_env_overlay_missing_file_is_ignored(self) -> None:
        os.environ["CCDB_CLI_ENV_FILE"] = "/nonexistent/overlay.env"
        try:
            runner = ClaudeRunner()
            env = runner._build_env()
            assert "PATH" in env  # still works
        finally:
            del os.environ["CCDB_CLI_ENV_FILE"]

    def test_cli_env_overlay_not_set_no_effect(self) -> None:
        original = os.environ.pop("CCDB_CLI_ENV_FILE", None)
        try:
            runner = ClaudeRunner()
            env = runner._build_env()
            assert "PATH" in env
        finally:
            if original is not None:
                os.environ["CCDB_CLI_ENV_FILE"] = original

    def test_disables_background_tasks(self) -> None:
        runner = ClaudeRunner()
        env = runner._build_env()
        assert env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"

    def test_cli_env_overlay_overrides_process_env(self, tmp_path: Path) -> None:
        overlay = tmp_path / "overlay.env"
        overlay.write_text("PATH=/custom/path\n")
        os.environ["CCDB_CLI_ENV_FILE"] = str(overlay)
        try:
            runner = ClaudeRunner()
            env = runner._build_env()
            assert env["PATH"] == "/custom/path"
        finally:
            del os.environ["CCDB_CLI_ENV_FILE"]


class TestClone:
    """Tests for clone method."""

    def test_clone_preserves_config(self) -> None:
        runner = ClaudeRunner(
            command="/usr/bin/claude",
            model="opus",
            permission_mode="bypassPermissions",
            working_dir="/tmp",
            timeout_seconds=120,
            allowed_tools=["Bash", "Read"],
            dangerously_skip_permissions=True,
            include_partial_messages=False,
        )
        cloned = runner.clone()
        assert cloned.command == runner.command
        assert cloned.model == runner.model
        assert cloned.permission_mode == runner.permission_mode
        assert cloned.working_dir == runner.working_dir
        assert cloned.timeout_seconds == runner.timeout_seconds
        assert cloned.allowed_tools == runner.allowed_tools
        assert cloned.dangerously_skip_permissions == runner.dangerously_skip_permissions
        assert cloned.include_partial_messages == runner.include_partial_messages
        assert cloned._process is None


class TestInterrupt:
    """Tests for interrupt() method."""

    @pytest.mark.asyncio
    async def test_interrupt_no_process_is_noop(self) -> None:
        """interrupt() on a runner with no process should not raise."""
        runner = ClaudeRunner()
        await runner.interrupt()  # should not raise

    @pytest.mark.asyncio
    async def test_interrupt_already_exited_is_noop(self) -> None:
        """interrupt() when process already exited should not send a signal."""
        runner = ClaudeRunner()
        mock_process = MagicMock()
        mock_process.returncode = 0
        runner._process = mock_process
        await runner.interrupt()
        mock_process.send_signal.assert_not_called()

    @pytest.mark.asyncio
    async def test_interrupt_sends_sigint(self) -> None:
        """interrupt() sends SIGINT (Unix) or terminate (Windows) to the running process."""
        runner = ClaudeRunner()
        mock_process = MagicMock()
        mock_process.returncode = None
        mock_process.wait = AsyncMock(return_value=0)
        runner._process = mock_process

        await runner.interrupt()

        if os.name == "nt":
            mock_process.terminate.assert_called_once()
        else:
            mock_process.send_signal.assert_called_once_with(signal_module.SIGINT)

    @pytest.mark.asyncio
    async def test_interrupt_falls_back_to_kill_on_timeout(self) -> None:
        """interrupt() calls kill() if the process doesn't stop within the timeout."""
        runner = ClaudeRunner()
        mock_process = MagicMock()
        mock_process.returncode = None
        mock_process.wait = AsyncMock(return_value=0)
        runner._process = mock_process

        with (
            patch("asyncio.wait_for", side_effect=TimeoutError),
            patch.object(runner, "kill", new_callable=AsyncMock) as mock_kill,
        ):
            await runner.interrupt()

        if os.name == "nt":
            mock_process.terminate.assert_called_once()
        else:
            mock_process.send_signal.assert_called_once_with(signal_module.SIGINT)
        mock_kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_interrupt_falls_back_to_kill_on_asyncio_timeout(self) -> None:
        """interrupt() calls kill() on asyncio.TimeoutError (Python 3.10 compat).

        On Python 3.10, asyncio.TimeoutError is NOT a subclass of the built-in
        TimeoutError.  This test verifies the correct exception is caught so
        interrupt() doesn't propagate an unhandled exception to the caller.
        """
        import asyncio

        runner = ClaudeRunner()
        mock_process = MagicMock()
        mock_process.returncode = None
        mock_process.wait = AsyncMock(return_value=0)
        runner._process = mock_process

        with (
            patch("asyncio.wait_for", side_effect=asyncio.TimeoutError),
            patch.object(runner, "kill", new_callable=AsyncMock) as mock_kill,
        ):
            await runner.interrupt()  # must not raise

        if os.name == "nt":
            mock_process.terminate.assert_called_once()
        else:
            mock_process.send_signal.assert_called_once_with(signal_module.SIGINT)
        mock_kill.assert_called_once()


class TestKill:
    """Tests for kill() method."""

    @pytest.mark.asyncio
    async def test_kill_force_kills_on_asyncio_timeout(self) -> None:
        """kill() force-kills the process on asyncio.TimeoutError (Python 3.10 compat).

        On Python 3.10, asyncio.TimeoutError is NOT a subclass of the built-in
        TimeoutError.  Verify kill() still calls process.kill() rather than
        propagating an unhandled exception.
        """
        import asyncio

        runner = ClaudeRunner()
        mock_process = MagicMock()
        mock_process.returncode = None
        mock_process.wait = AsyncMock(return_value=0)
        runner._process = mock_process

        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
            await runner.kill()  # must not raise

        mock_process.kill.assert_called_once()


class TestRunTimeout:
    """Tests for timeout handling in run()."""

    @pytest.mark.asyncio
    async def test_run_yields_error_on_asyncio_timeout(self) -> None:
        """run() yields a timeout error event on asyncio.TimeoutError (Python 3.10 compat).

        On Python 3.10, asyncio.TimeoutError is NOT a subclass of the built-in
        TimeoutError.  Verify run() catches it and yields a proper error event
        instead of propagating the exception to callers.
        """

        runner = ClaudeRunner(timeout_seconds=5)

        mock_process = AsyncMock()
        mock_process.returncode = None
        mock_process.stdout = AsyncMock()
        mock_process.stderr = AsyncMock()

        async def _stream_raises():
            raise TimeoutError
            yield  # make it an async generator

        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_process),
            patch.object(runner, "_read_stream", _stream_raises),
            patch.object(runner, "_cleanup", new_callable=AsyncMock),
        ):
            events = [event async for event in runner.run("hello")]

        assert len(events) == 1
        assert events[0].is_complete
        assert events[0].error is not None
        assert "imed out" in events[0].error


class TestSignalKillSuppression:
    """Tests that signal-killed processes (negative returncode) don't emit error events."""

    @pytest.mark.asyncio
    async def test_signal_kill_does_not_yield_error_event(self) -> None:
        """A process killed by signal (returncode < 0) exits silently — no error embed."""
        runner = ClaudeRunner()
        mock_process = AsyncMock()
        mock_process.stdout = AsyncMock()
        mock_process.stderr = AsyncMock()
        mock_process.returncode = -2  # SIGINT kill
        mock_process.stdout.readline = AsyncMock(return_value=b"")
        mock_process.stderr.read = AsyncMock(return_value=b"")
        mock_process.wait = AsyncMock(return_value=-2)
        runner._process = mock_process

        events = [event async for event in runner._read_stream()]
        error_events = [e for e in events if e.error]
        assert error_events == [], "Signal kill should not produce error events"

    @pytest.mark.asyncio
    async def test_positive_nonzero_returncode_yields_error(self) -> None:
        """A process that exits with a positive non-zero code yields an error event."""
        runner = ClaudeRunner()
        mock_process = AsyncMock()
        mock_process.stdout = AsyncMock()
        mock_process.stderr = AsyncMock()
        mock_process.returncode = 1
        mock_process.stdout.readline = AsyncMock(return_value=b"")
        mock_process.stderr.read = AsyncMock(return_value=b"error details")
        mock_process.wait = AsyncMock(return_value=1)
        runner._process = mock_process

        events = [event async for event in runner._read_stream()]
        error_events = [e for e in events if e.error]
        assert len(error_events) == 1
        assert "1" in error_events[0].error

    def test_clone_with_model_override(self) -> None:
        """clone() with model= overrides the runner's model for that clone."""
        runner = ClaudeRunner(model="sonnet")
        cloned = runner.clone(model="opus")
        assert cloned.model == "opus"
        assert runner.model == "sonnet"  # original unchanged

    def test_clone_without_model_override_preserves_model(self) -> None:
        """clone() without model= keeps the original model."""
        runner = ClaudeRunner(model="haiku")
        cloned = runner.clone()
        assert cloned.model == "haiku"

    def test_clone_allowed_tools_override(self) -> None:
        """clone(allowed_tools=[...]) overrides the parent's tool list."""
        runner = ClaudeRunner(allowed_tools=["Bash", "Read"])
        cloned = runner.clone(allowed_tools=["Write"])
        assert cloned.allowed_tools == ["Write"]
        assert runner.allowed_tools == ["Bash", "Read"]  # original unchanged

    def test_clone_allowed_tools_unset_inherits(self) -> None:
        """clone() without allowed_tools inherits from the parent."""
        runner = ClaudeRunner(allowed_tools=["Bash", "Read"])
        cloned = runner.clone()
        assert cloned.allowed_tools == ["Bash", "Read"]

    def test_clone_allowed_tools_none_clears(self) -> None:
        """clone(allowed_tools=None) sets no restrictions (different from _UNSET)."""
        runner = ClaudeRunner(allowed_tools=["Bash", "Read"])
        cloned = runner.clone(allowed_tools=None)
        assert cloned.allowed_tools is None


class TestImageStreamJson:
    """Tests for --input-format stream-json image attachment support.

    Images are downloaded by prompt_builder, base64-encoded, and passed as
    ``ImageData`` objects.  The runner sends them as base64-type content blocks
    via stream-json stdin.
    """

    _SAMPLE_IMG = ImageData(data="aGVsbG8=", media_type="image/png")

    def test_no_image_flag_in_args(self) -> None:
        """_build_args() must NOT produce --image flags (flag does not exist)."""
        runner = ClaudeRunner(images=[self._SAMPLE_IMG])
        args = runner._build_args("look at this", session_id=None)
        assert "--image" not in args

    def test_stream_json_input_format_added_for_images(self) -> None:
        """_build_args() adds --input-format stream-json when images is set."""
        runner = ClaudeRunner(images=[self._SAMPLE_IMG])
        args = runner._build_args("look at this", session_id=None)
        assert "--input-format" in args
        idx = args.index("--input-format")
        assert args[idx + 1] == "stream-json"

    def test_prompt_not_in_cli_args_when_images_present(self) -> None:
        """Prompt must NOT appear as a CLI arg when using stream-json input."""
        runner = ClaudeRunner(images=[self._SAMPLE_IMG])
        args = runner._build_args("look at this", session_id=None)
        assert "--" not in args
        assert "look at this" not in args

    def test_stream_json_input_always_present(self) -> None:
        """--input-format stream-json is always added (needed for permission responses)."""
        runner = ClaudeRunner()
        args = runner._build_args("hello", session_id=None)
        assert "--input-format" in args
        idx = args.index("--input-format")
        assert args[idx + 1] == "stream-json"

    def test_prompt_never_in_cli_args(self) -> None:
        """Prompt is always sent via stdin, not as a CLI argument."""
        runner = ClaudeRunner()
        args = runner._build_args("hello", session_id=None)
        assert "hello" not in args
        assert "--" not in args

    @pytest.mark.asyncio
    async def test_send_stream_json_message_base64_format(self) -> None:
        """Images are sent as base64-type blocks with media_type."""
        import json

        img = ImageData(data="aW1hZ2VkYXRh", media_type="image/png")
        runner = ClaudeRunner(images=[img])

        written: list[bytes] = []

        def capture_write(data: bytes) -> None:
            written.append(data)

        stdin_mock = MagicMock()
        stdin_mock.write = capture_write
        stdin_mock.drain = AsyncMock()

        mock_process = MagicMock()
        mock_process.stdin = stdin_mock
        runner._process = mock_process

        await runner._send_stream_json_message("describe this image")

        assert len(written) == 1
        payload = json.loads(written[0].decode())

        content = payload["message"]["content"]
        assert len(content) == 2

        img_block = content[0]
        assert img_block["type"] == "image"
        assert img_block["source"]["type"] == "base64"
        assert img_block["source"]["media_type"] == "image/png"
        assert img_block["source"]["data"] == "aW1hZ2VkYXRh"

        text_block = content[1]
        assert text_block["type"] == "text"
        assert text_block["text"] == "describe this image"

    @pytest.mark.asyncio
    async def test_send_stream_json_message_empty_prompt_omits_text_block(self) -> None:
        """Empty prompt must NOT add a text block."""
        import json

        img = ImageData(data="aW1hZ2VkYXRh", media_type="image/jpeg")
        runner = ClaudeRunner(images=[img])

        written: list[bytes] = []

        def capture_write(data: bytes) -> None:
            written.append(data)

        stdin_mock = MagicMock()
        stdin_mock.write = capture_write
        stdin_mock.drain = AsyncMock()

        mock_process = MagicMock()
        mock_process.stdin = stdin_mock
        runner._process = mock_process

        await runner._send_stream_json_message("")

        assert len(written) == 1
        payload = json.loads(written[0].decode())
        content = payload["message"]["content"]

        assert len(content) == 1
        assert content[0]["type"] == "image"

    @pytest.mark.asyncio
    async def test_run_uses_pipe_stdin_and_sends_base64_image(self) -> None:
        """run() uses stdin=PIPE and sends base64 image data via stdin."""
        import asyncio as _asyncio
        import json

        img = ImageData(data="aW1hZ2VkYXRh", media_type="image/png")
        runner = ClaudeRunner(images=[img])

        written: list[bytes] = []

        def capture_write(data: bytes) -> None:
            written.append(data)

        mock_stdin = MagicMock()
        mock_stdin.write = capture_write
        mock_stdin.drain = AsyncMock()

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
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_process,
            ) as mock_exec,
            patch.object(runner, "_cleanup", new_callable=AsyncMock),
        ):
            _ = [event async for event in runner.run("what do you see?")]

        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs["stdin"] == _asyncio.subprocess.PIPE

        assert len(written) == 1

        payload = json.loads(written[0].decode())
        content = payload["message"]["content"]

        image_blocks = [c for c in content if c.get("type") == "image"]
        assert len(image_blocks) == 1
        assert image_blocks[0]["source"]["type"] == "base64"
        assert image_blocks[0]["source"]["media_type"] == "image/png"

    @pytest.mark.asyncio
    async def test_run_uses_pipe_stdin_for_text_only(self) -> None:
        """run() uses stdin=PIPE even for text-only sessions (needed for permission responses)."""
        import asyncio as _asyncio
        import json

        runner = ClaudeRunner()

        written: list[bytes] = []

        def capture_write(data: bytes) -> None:
            written.append(data)

        mock_stdin = MagicMock()
        mock_stdin.write = capture_write
        mock_stdin.drain = AsyncMock()

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
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_process,
            ) as mock_exec,
            patch.object(runner, "_cleanup", new_callable=AsyncMock),
        ):
            _ = [event async for event in runner.run("hello")]

        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs["stdin"] == _asyncio.subprocess.PIPE

        # Prompt sent as stream-json message via stdin
        assert len(written) == 1
        payload = json.loads(written[0].decode())
        assert payload["type"] == "user"
        text_blocks = [c for c in payload["message"]["content"] if c.get("type") == "text"]
        assert any("hello" in b["text"] for b in text_blocks)

    @pytest.mark.asyncio
    async def test_multiple_images_all_sent(self) -> None:
        """Multiple images are all included as separate base64-type image blocks."""
        import json

        imgs = [
            ImageData(data="aW1n", media_type="image/png"),
            ImageData(data="anBn", media_type="image/jpeg"),
        ]
        runner = ClaudeRunner(images=imgs)

        written: list[bytes] = []

        def capture_write(data: bytes) -> None:
            written.append(data)

        stdin_mock = MagicMock()
        stdin_mock.write = capture_write
        stdin_mock.drain = AsyncMock()

        mock_process = MagicMock()
        mock_process.stdin = stdin_mock
        runner._process = mock_process

        await runner._send_stream_json_message("compare these")

        payload = json.loads(written[0].decode())
        content = payload["message"]["content"]

        image_blocks = [c for c in content if c.get("type") == "image"]
        assert len(image_blocks) == 2
        assert image_blocks[0]["source"]["data"] == "aW1n"
        assert image_blocks[1]["source"]["data"] == "anBn"


class TestLargePromptStdin:
    """Tests for large prompt stdin passthrough to avoid ARG_MAX / E2BIG errors.

    When a text-only prompt exceeds the OS command-line argument size limit,
    the runner must switch to --input-format stream-json and send the prompt
    via stdin instead of as a positional CLI argument.
    """

    def test_large_prompt_uses_stream_json_in_args(self) -> None:
        """_build_args() uses --input-format stream-json for large prompts."""
        runner = ClaudeRunner()
        large_prompt = "x" * 200_000  # 200KB — well above any reasonable ARG_MAX
        args = runner._build_args(large_prompt, session_id=None)
        assert "--input-format" in args
        idx = args.index("--input-format")
        assert args[idx + 1] == "stream-json"

    def test_large_prompt_not_in_cli_args(self) -> None:
        """Large prompt must NOT appear as a positional CLI argument."""
        runner = ClaudeRunner()
        large_prompt = "x" * 200_000
        args = runner._build_args(large_prompt, session_id=None)
        assert "--" not in args
        assert large_prompt not in args

    def test_small_prompt_also_uses_stream_json(self) -> None:
        """Small prompts also use stream-json (all prompts go via stdin now)."""
        runner = ClaudeRunner()
        args = runner._build_args("hello", session_id=None)
        assert "--input-format" in args
        assert "hello" not in args
        assert "--" not in args

    @pytest.mark.asyncio
    async def test_run_uses_pipe_stdin_for_large_prompt(self) -> None:
        """run() uses stdin=PIPE and sends the prompt via stdin for large prompts."""
        import asyncio as _asyncio
        import json

        runner = ClaudeRunner()
        large_prompt = "x" * 200_000

        written: list[bytes] = []

        def capture_write(data: bytes) -> None:
            written.append(data)

        mock_stdin = MagicMock()
        mock_stdin.write = capture_write
        mock_stdin.drain = AsyncMock()

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
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_process,
            ) as mock_exec,
            patch.object(runner, "_cleanup", new_callable=AsyncMock),
        ):
            _ = [event async for event in runner.run(large_prompt)]

        # Must use stdin=PIPE
        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs["stdin"] == _asyncio.subprocess.PIPE

        # Must have sent the prompt via stdin as stream-json
        assert len(written) == 1
        payload = json.loads(written[0].decode())
        assert payload["type"] == "user"
        content = payload["message"]["content"]
        text_blocks = [c for c in content if c.get("type") == "text"]
        assert len(text_blocks) == 1
        assert text_blocks[0]["text"] == large_prompt

    @pytest.mark.asyncio
    async def test_run_uses_pipe_stdin_for_small_prompt(self) -> None:
        """run() uses stdin=PIPE even for small prompts (permission responses need it)."""
        import asyncio as _asyncio
        import json

        runner = ClaudeRunner()

        written: list[bytes] = []

        def capture_write(data: bytes) -> None:
            written.append(data)

        mock_stdin = MagicMock()
        mock_stdin.write = capture_write
        mock_stdin.drain = AsyncMock()

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
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_process,
            ) as mock_exec,
            patch.object(runner, "_cleanup", new_callable=AsyncMock),
        ):
            _ = [event async for event in runner.run("hello")]

        call_kwargs = mock_exec.call_args[1]
        assert call_kwargs["stdin"] == _asyncio.subprocess.PIPE

        # Prompt sent via stdin
        assert len(written) == 1
        payload = json.loads(written[0].decode())
        assert payload["type"] == "user"


class TestResolveWindowsCmd:
    """Tests for _resolve_windows_cmd helper.

    All tests run on every OS.  File system interactions use tmp_path so no
    actual Windows installation is required.
    """

    # A minimal npm-generated .cmd wrapper.  Real wrappers have more boilerplate
    # but the critical line always matches: "%~dp0\<relative\path\to\script.js>"
    _NPM_CMD_TEMPLATE = (
        '@ECHO off\r\nGOTO start\r\n:start\r\nSET _prog=node\r\n"%~dp0\\{rel_path}" %*\r\n'
    )

    def _make_cmd(self, tmp_path: Path, rel_js: str) -> Path:
        """Write a minimal .cmd wrapper and the target .js file (Windows separators in rel_js)."""
        js_path = tmp_path.joinpath(*rel_js.replace("\\", "/").split("/"))
        js_path.parent.mkdir(parents=True, exist_ok=True)
        js_path.write_text("// cli entry\n")

        cmd_path = tmp_path / "claude.cmd"
        cmd_path.write_text(self._NPM_CMD_TEMPLATE.format(rel_path=rel_js))
        return cmd_path

    def test_parses_npm_wrapper_and_returns_node_js(self, tmp_path: Path) -> None:
        """Primary path: regex extracts JS path from .cmd content."""
        rel = r"node_modules\@anthropic-ai\claude-code\cli.js"
        cmd_path = self._make_cmd(tmp_path, rel)

        with patch("claude_code_core.runner.shutil.which", return_value="/usr/bin/node"):
            result = _resolve_windows_cmd(cmd_path)

        assert result is not None
        assert result[0] == "/usr/bin/node"
        assert result[1].endswith("cli.js")

    def test_falls_back_to_node_modules_heuristic(self, tmp_path: Path) -> None:
        """Fallback path: .cmd has no parseable JS ref, but node_modules exists."""
        # Write a .cmd file without the "%~dp0\..." pattern
        cmd_path = tmp_path / "claude.cmd"
        cmd_path.write_text("@ECHO off\r\nREM non-standard wrapper\r\n")

        # Create the fallback cli.js location
        cli_js = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
        cli_js.parent.mkdir(parents=True, exist_ok=True)
        cli_js.write_text("// cli entry\n")

        with patch("claude_code_core.runner.shutil.which", return_value="node"):
            result = _resolve_windows_cmd(cmd_path)

        assert result is not None
        assert result[1] == str(cli_js)

    def test_returns_none_when_js_not_found(self, tmp_path: Path) -> None:
        """Both paths fail: .cmd wrapper points to a non-existent .js file."""
        cmd_path = tmp_path / "claude.cmd"
        # Regex will match but the resolved path won't exist
        cmd_path.write_text(r'"%~dp0\node_modules\@anthropic-ai\claude-code\cli.js"' + "\r\n")
        # Do NOT create cli.js — both primary and fallback should fail

        result = _resolve_windows_cmd(cmd_path)
        assert result is None

    def test_returns_none_on_unreadable_cmd(self, tmp_path: Path) -> None:
        """OSError while reading the .cmd file: both paths should be attempted."""
        cmd_path = tmp_path / "ghost.cmd"
        # File does not exist → read_text raises FileNotFoundError (subclass of OSError)

        result = _resolve_windows_cmd(cmd_path)
        assert result is None

    def test_shutil_which_fallback_when_node_not_on_path(self, tmp_path: Path) -> None:
        """When node is not on PATH, falls back to bare 'node' string."""
        rel = r"node_modules\@anthropic-ai\claude-code\cli.js"
        cmd_path = self._make_cmd(tmp_path, rel)

        with patch("claude_code_core.runner.shutil.which", return_value=None):
            result = _resolve_windows_cmd(cmd_path)

        assert result is not None
        assert result[0] == "node"

    def test_build_args_patches_cmd_on_win32(self, tmp_path: Path) -> None:
        """_build_args replaces .cmd with [node, cli.js] when sys.platform == win32."""
        rel = r"node_modules\@anthropic-ai\claude-code\cli.js"
        cmd_path = self._make_cmd(tmp_path, rel)

        runner = ClaudeRunner(command=str(cmd_path), model="sonnet")

        with (
            patch("claude_code_core.runner.sys.platform", "win32"),
            patch("claude_code_core.runner.shutil.which", return_value="/usr/bin/node"),
        ):
            args = runner._build_args("hello", session_id=None)

        assert args[0] == "/usr/bin/node"
        assert args[1].endswith("cli.js")

    def test_build_args_unchanged_on_linux(self, tmp_path: Path) -> None:
        """_build_args does not touch the command on non-Windows platforms."""
        cmd_path = tmp_path / "claude.cmd"
        cmd_path.write_text("#!/bin/sh\n")

        runner = ClaudeRunner(command=str(cmd_path), model="sonnet")

        with patch("claude_code_core.runner.sys.platform", "linux"):
            args = runner._build_args("hello", session_id=None)

        assert args[0] == str(cmd_path)
