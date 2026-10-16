"""Claude Code CLI runner.

Spawns `claude -p --output-format stream-json` as an async subprocess
and yields StreamEvent objects.

Security note: We use create_subprocess_exec (not shell=True) to safely
pass user prompts as arguments without shell injection risk.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import signal
import sys
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

from .account_pool import AccountBinding
from .api_provider import detect_api_provider
from .child_env import STRIPPED_ENV_KEYS, strip_transport_credentials
from .execution import ExecutionRefusedError, carry_execution_mode, prepare_launch, refusal_event
from .parser import parse_line
from .types import ImageData, InjectedMessage, MessageType, StreamEvent

# Re-export for backward compatibility
__all__ = ["ClaudeRunner", "ImageData"]

logger = logging.getLogger(__name__)

# Sentinel to distinguish "not provided" from None (which means "no tool restrictions").
_UNSET = object()


def _resolve_windows_cmd(cmd_path: Path) -> list[str] | None:
    """Resolve a Windows npm .cmd/.bat wrapper to ``[node, cli_js]``.

    npm installs a thin ``.cmd`` wrapper that references the real ``.js``
    entry-point via the ``%~dp0`` batch variable (the wrapper's own directory).
    ``create_subprocess_exec`` cannot execute ``.cmd`` files directly, so we
    read the wrapper, extract the ``.js`` path, and prepend the ``node``
    executable.

    Returns ``[node_exe, js_path]`` on success, ``None`` if the wrapper cannot
    be resolved (caller falls back to the original command).
    """
    try:
        content = cmd_path.read_text(encoding="utf-8", errors="ignore")
        # Older npm writes "%~dp0\x.js", current cmd-shim "%dp0%\x.js".
        match = re.search(r'"(?:%~dp0|%dp0%)\\([^"]+\.js)"', content)
        if match:
            cli_js = cmd_path.parent.joinpath(*match.group(1).split("\\"))
            if cli_js.exists():
                node = shutil.which("node") or "node"
                return [node, str(cli_js)]
    except OSError:
        pass

    cli_js = cmd_path.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
    if cli_js.exists():
        node = shutil.which("node") or "node"
        return [node, str(cli_js)]

    logger.warning(
        "Windows .cmd wrapper %s could not be resolved to a Node.js script; "
        "Claude CLI will likely fail to start",
        cmd_path,
    )
    return None


def resolve_cli_argv(command: str) -> list[str] | None:
    """The argv prefix that starts the CLI *command*, or None when it cannot be run safely.

    On Windows a CLI installed by npm is a ``.cmd`` wrapper, which
    ``create_subprocess_exec`` cannot start, and which must not be started
    through ``cmd.exe`` with untrusted arguments (they would be re-parsed by the
    batch interpreter). It is resolved to ``[node, cli.js]`` instead; a wrapper
    that cannot be resolved yields None. Elsewhere the command is used as is.
    """
    found = shutil.which(command) or command
    if sys.platform == "win32" and found.lower().endswith((".cmd", ".bat")):
        return _resolve_windows_cmd(Path(found))
    return [found]


# How long the reader waits, after a RESULT, for the CLI to read a message that
# was delivered into the turn. Measured: it starts the next turn within ~1s.
_ACK_AFTER_RESULT_SECONDS = 60


class ClaudeRunner:
    """Manages Claude Code CLI subprocess execution."""

    # Execution environment (claude_code_core.execution). ``None`` = deployment
    # default; ``execution_served`` records which one ran the last spawn.
    execution_mode: str | None = None
    execution_served: str | None = None

    def __init__(
        self,
        command: str = "claude",
        model: str = "sonnet",
        permission_mode: str = "acceptEdits",
        working_dir: str | None = None,
        timeout_seconds: int = 300,
        allowed_tools: list[str] | None = None,
        dangerously_skip_permissions: bool = False,
        include_partial_messages: bool = True,
        api_port: int | None = None,
        api_secret: str | None = None,
        thread_id: int | None = None,
        append_system_prompt: str | None = None,
        images: list[ImageData] | None = None,
        fork_session: bool = False,
        effort: str | None = None,
    ) -> None:
        self.command = command
        self.model = model
        self.permission_mode = permission_mode
        self.working_dir = working_dir
        self.timeout_seconds = timeout_seconds
        self.allowed_tools = allowed_tools
        self.dangerously_skip_permissions = dangerously_skip_permissions
        self.include_partial_messages = include_partial_messages
        self.api_port = api_port
        self.api_secret = api_secret
        self.thread_id = thread_id
        self.append_system_prompt = append_system_prompt
        self.images = images
        self.fork_session = fork_session
        self.effort = effort
        # Account-pool profile this runner spawns as (CLAUDE_CONFIG_DIR). None
        # means the relay's own environment — the behaviour without a pool.
        self.account: AccountBinding | None = None
        self._process: asyncio.subprocess.Process | None = None
        # Messages delivered into the running turn (issue #1129). Admission is
        # open only between the first prompt and the final RESULT; a message
        # stays in _outstanding until the CLI echoes its uuid back.
        self._accepting = False
        self._outstanding: dict[str, InjectedMessage] = {}

    async def run(
        self,
        prompt: str,
        session_id: str | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Run Claude Code CLI and yield stream events.

        Uses create_subprocess_exec (not shell) to avoid injection risks.
        The prompt is passed as a direct argument to the claude binary.

        Args:
            prompt: The user's message/prompt.
            session_id: Optional session ID to resume.

        Yields:
            StreamEvent objects parsed from stream-json output.
        """
        args = self._build_args(prompt, session_id)
        env = self._build_env()
        cwd = self.working_dir or os.getcwd()

        logger.info(
            "Starting Claude CLI: %s (cwd=%s, pid will follow)",
            " ".join(args[:6]) + " ...",
            cwd,
        )

        stdin_mode = asyncio.subprocess.PIPE

        try:
            launch = await prepare_launch(
                backend="claude", requested_mode=self.execution_mode, argv=args, env=env, cwd=cwd
            )
        except ExecutionRefusedError as exc:
            yield refusal_event(exc)
            return
        self.execution_served = launch.mode

        self._process = await asyncio.create_subprocess_exec(
            *launch.argv,
            stdin=stdin_mode,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=launch.cwd,
            env=launch.env,
            limit=10 * 1024 * 1024,
        )

        logger.info("Claude CLI started: pid=%s", self._process.pid)

        self._outstanding = {}
        if self._process.stdin is not None:
            await self._send_stream_json_message(prompt)
            self._accepting = True

        try:
            async for event in self._read_stream():
                yield event
        except TimeoutError:
            logger.warning("Claude CLI timed out after %ds", self.timeout_seconds)
            yield StreamEvent(
                raw={},
                message_type=MessageType.RESULT,
                is_complete=True,
                error=f"Timed out after {self.timeout_seconds} seconds",
            )
        finally:
            self._accepting = False
            await self._cleanup()

    async def inject_user_message(self, text: str, images: list[ImageData] | None = None) -> bool:
        """Deliver *text* into the running turn, as the CLI's own UI does mid-turn.

        Returns False when the turn cannot take it (not started, finishing,
        stopped) — the caller then runs it as the next turn. True hands the
        message to this runner: it is either read by the agent or reported by
        :meth:`unconsumed_messages` once the run ends, never both.
        """
        process = self._process
        if not self._accepting or process is None or process.returncode is not None:
            return False
        if process.stdin is None:
            return False
        message = InjectedMessage(uuid=str(uuid.uuid4()), text=text, images=tuple(images or ()))
        # No await between the admission check and this: the reader cannot
        # finish the run in between, so an accepted message is always tracked.
        self._outstanding[message.uuid] = message
        try:
            process.stdin.write(self._user_line(text, list(message.images), message.uuid))
            await process.stdin.drain()
        except Exception:
            logger.warning("inject_user_message: failed to write to stdin", exc_info=True)
        return True

    def unconsumed_messages(self) -> list[InjectedMessage]:
        """Injected messages the CLI never read, in the order they were sent."""
        return list(self._outstanding.values())

    def clone(
        self,
        thread_id: int | None = None,
        model: str | None = None,
        append_system_prompt: str | None = None,
        allowed_tools: list[str] | None | object = _UNSET,
        fork_session: bool = False,
        working_dir: str | None | object = _UNSET,
        effort: str | None | object = _UNSET,
    ) -> ClaudeRunner:
        """Create a fresh runner with the same configuration but no active process."""
        cloned = ClaudeRunner(
            command=self.command,
            model=model if model is not None else self.model,
            permission_mode=self.permission_mode,
            working_dir=(
                self.working_dir if working_dir is _UNSET else working_dir  # type: ignore[arg-type]
            ),
            timeout_seconds=self.timeout_seconds,
            allowed_tools=(
                self.allowed_tools if allowed_tools is _UNSET else allowed_tools  # type: ignore[arg-type]
            ),
            dangerously_skip_permissions=self.dangerously_skip_permissions,
            include_partial_messages=self.include_partial_messages,
            api_port=self.api_port,
            api_secret=self.api_secret,
            thread_id=thread_id if thread_id is not None else self.thread_id,
            append_system_prompt=(
                append_system_prompt
                if append_system_prompt is not None
                else self.append_system_prompt
            ),
            fork_session=fork_session,
            effort=(
                self.effort if effort is _UNSET else effort  # type: ignore[arg-type]
            ),
        )
        cloned.account = self.account
        return carry_execution_mode(self, cloned)

    async def inject_tool_result(self, request_id: str, data: dict) -> None:
        """Send a tool result or permission/elicitation response via stdin."""
        if self._process is None or self._process.stdin is None:
            logger.warning("inject_tool_result: no active process stdin, ignoring")
            return
        payload = {"request_id": request_id, **data}
        line = json.dumps(payload) + "\n"
        try:
            self._process.stdin.write(line.encode())
            await self._process.stdin.drain()
            logger.debug("Injected tool result for request %s", request_id)
        except Exception:
            logger.warning("inject_tool_result: failed to write to stdin", exc_info=True)

    async def _send_stream_json_message(self, prompt: str) -> None:
        """Write the initial user message to stdin in stream-json format."""
        assert self._process is not None and self._process.stdin is not None
        line = self._user_line(prompt, self.images or [], str(uuid.uuid4()))
        try:
            self._process.stdin.write(line)
            await self._process.stdin.drain()
            logger.debug("Sent stream-json user message (%d image(s))", len(self.images or []))
        except Exception:
            logger.warning("_send_stream_json_message: failed to write to stdin", exc_info=True)

    @staticmethod
    def _user_line(prompt: str, images: list[ImageData], message_uuid: str) -> bytes:
        """One stream-json user line. The CLI echoes *message_uuid* when it reads it."""
        content: list[dict] = []
        for img in images:
            content.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": img.media_type,
                        "data": img.data,
                    },
                }
            )
            logger.debug("Added base64 image (%s, %d chars)", img.media_type, len(img.data))

        if prompt:
            content.append({"type": "text", "text": prompt})

        message = {
            "type": "user",
            "uuid": message_uuid,
            "message": {"role": "user", "content": content},
        }
        return (json.dumps(message) + "\n").encode()

    async def interrupt(self) -> None:
        """Interrupt the subprocess with SIGINT (graceful stop)."""
        self._accepting = False
        if self._process and self._process.returncode is None:
            if os.name == "nt":
                self._process.terminate()
            else:
                self._process.send_signal(signal.SIGINT)
            try:
                await asyncio.wait_for(self._process.wait(), timeout=10)
            except TimeoutError:
                await self.kill()

    async def kill(self) -> None:
        """Terminate the subprocess, force-killing if it doesn't stop in time."""
        self._accepting = False
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except TimeoutError:
                self._process.kill()
                await self._process.wait()

    def _build_args(self, prompt: str, session_id: str | None) -> list[str]:
        """Build command-line arguments for claude CLI.

        All arguments are passed as a list to create_subprocess_exec,
        which does NOT invoke a shell, preventing injection.
        """
        args = [
            self.command,
            "-p",
            "--output-format",
            "stream-json",
            "--model",
            self.model,
            "--permission-mode",
            self.permission_mode,
            "--verbose",
        ]

        if self.include_partial_messages:
            args.append("--include-partial-messages")

        args.append("--include-hook-events")

        if self.dangerously_skip_permissions and self.permission_mode not in (
            "auto",
            "plan",
        ):
            args.append("--dangerously-skip-permissions")

        if self.allowed_tools:
            args.extend(["--allowedTools", ",".join(self.allowed_tools)])

        if session_id:
            if not re.match(r"^[a-f0-9\-]+$", session_id):
                raise ValueError(f"Invalid session_id format: {session_id!r}")
            args.extend(["--resume", session_id])
            if self.fork_session:
                args.append("--fork-session")

        if self.effort:
            args.extend(["--effort", self.effort])

        if self.append_system_prompt:
            args.extend(["--append-system-prompt", self.append_system_prompt])

        args.extend(["--input-format", "stream-json"])
        # Echo each user line as the CLI reads it: how ccdb learns that a
        # message delivered into the running turn was picked up.
        args.append("--replay-user-messages")

        if sys.platform == "win32" and args[0].lower().endswith((".cmd", ".bat")):
            resolved = _resolve_windows_cmd(Path(args[0]))
            if resolved:
                args = resolved + args[1:]

        return args

    _STRIPPED_ENV_KEYS = STRIPPED_ENV_KEYS

    def _build_env(self) -> dict[str, str]:
        """Build environment variables for the subprocess.

        Strips CLAUDECODE (nesting detection) and known secret variables
        so that the CLI process cannot read them via Bash tool.
        """
        env = {k: v for k, v in os.environ.items() if k not in self._STRIPPED_ENV_KEYS}
        overlay_path = os.environ.get("CCDB_CLI_ENV_FILE")
        if overlay_path:
            try:
                for line in Path(overlay_path).read_text().splitlines():
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        env[key] = value
            except OSError:
                logger.debug("CLI env overlay file not found: %s", overlay_path)
        env = strip_transport_credentials(env)
        if self.account is not None:
            # After the overlay: the profile chosen for this turn is more
            # specific than a deployment-wide CLAUDE_CONFIG_DIR.
            env.update(self.account.env)
        if self.api_port is not None:
            env["CCDB_API_URL"] = f"http://127.0.0.1:{self.api_port}"
        if self.api_secret is not None:
            env["CCDB_API_SECRET"] = self.api_secret
        if self.thread_id is not None:
            env["DISCORD_THREAD_ID"] = str(self.thread_id)
        env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] = "1"
        return env

    def describe_api(self) -> str:
        """Return a short label for the API endpoint this runner targets.

        Derived from the final subprocess environment so CLI env overlays
        (e.g. an Azure Foundry switch) are reflected accurately.
        """
        return detect_api_provider(self._build_env())

    async def _read_stream(self) -> AsyncGenerator[StreamEvent, None]:
        """Read and parse stdout line by line."""
        if self._process is None or self._process.stdout is None:
            raise RuntimeError("Process not started")

        line_count = 0
        after_result = False
        while True:
            # Each line is progress, not a fresh total runtime budget. Long
            # active sessions may run indefinitely; silent streams cannot.
            timeout: float | None = self.timeout_seconds or None
            if after_result:
                # The CLI starts the next turn at once when it has unread
                # input; silence means it never will.
                timeout = min(timeout or _ACK_AFTER_RESULT_SECONDS, _ACK_AFTER_RESULT_SECONDS)
            try:
                line = await asyncio.wait_for(self._process.stdout.readline(), timeout=timeout)
            except TimeoutError:
                if not after_result:
                    raise
                logger.warning(
                    "Claude CLI left %d injected message(s) unread", len(self._outstanding)
                )
                self._accepting = False
                return
            if not line:
                logger.info("Claude CLI stdout EOF after %d lines", line_count)
                break
            line_count += 1
            decoded = line.decode("utf-8", errors="replace")
            if line_count <= 3:
                logger.info("Claude CLI stdout line %d: %.100s", line_count, decoded.strip())
            event = parse_line(decoded)
            if event:
                if event.replay_uuid is not None:
                    acked = self._outstanding.pop(event.replay_uuid, None)
                    if acked is not None:
                        event.injected_text = acked.text
                        after_result = False
                if event.is_complete:
                    if self._outstanding and not event.error:
                        event.turn_continues = True
                        after_result = True
                    else:
                        # Closed before the consumer sees the result, with no
                        # await in between: a later message is never accepted
                        # by a run that has stopped reading.
                        self._accepting = False
                yield event
                if event.is_complete and not event.turn_continues:
                    return

        if self._process.returncode is None:
            await asyncio.wait_for(self._process.wait(), timeout=10)

        if self._process.returncode is not None and self._process.returncode > 0:
            stderr_data = b""
            if self._process.stderr:
                stderr_data = await self._process.stderr.read()
            stderr_text = stderr_data.decode("utf-8", errors="replace").strip()
            logger.error(
                "Claude CLI exited with code %d: %s",
                self._process.returncode,
                stderr_text[:200],
            )
            yield StreamEvent(
                raw={},
                message_type=MessageType.RESULT,
                is_complete=True,
                error=f"CLI exited with code {self._process.returncode}",
            )

    async def _cleanup(self) -> None:
        """Ensure the subprocess is properly terminated after run() exits."""
        await self.kill()
