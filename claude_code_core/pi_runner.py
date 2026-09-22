"""pi coding agent CLI runner.

Spawns the ``pi`` CLI (https://github.com/earendil-works/pi) in its JSON event
mode as an async subprocess and yields StreamEvent objects, providing the same
interface as ClaudeRunner and CodexRunner.

Three properties of the CLI shape this module, and all three were measured
against pi 0.85.1 rather than read off the published event list:

* ``--session-id`` *creates* the session when it is missing and resumes it with
  full context when it exists. ccdb therefore mints the id itself and this
  runner needs none of the "resume target vanished" recovery that
  :mod:`codex_runner` carries — a lost session file degrades to a fresh session
  under the same id instead of an error.
* The turn's last event is ``agent_settled``, which follows ``agent_end`` and is
  not in the documented event union. Draining to it is what lets the process
  exit on its own.
* **A failed turn is not an error event.** It arrives as an assistant
  ``message_end`` carrying ``stopReason: "error"`` and an ``errorMessage``, and
  the process still exits 0. Parsed naively, a provider outage renders in
  Discord as a successful empty answer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from collections.abc import AsyncGenerator
from dataclasses import replace
from typing import Any

from .child_env import STRIPPED_ENV_KEYS
from .types import (
    TOOL_CATEGORIES,
    ImageData,
    MessageType,
    StreamEvent,
    ToolCategory,
    ToolUseEvent,
)

logger = logging.getLogger(__name__)

_UNSET = object()

# Thinking levels accepted by ``pi --thinking`` (pi 0.85.1). Validated before
# the value reaches argv; the CLI rejects a level the selected model does not
# support and that error reaches the thread.
VALID_PI_THINKING: frozenset[str] = frozenset(
    {"off", "minimal", "low", "medium", "high", "xhigh", "max"}
)

# pi documents that it has no built-in sandbox and shows no trust prompt in the
# non-interactive modes, so there is no flag that corresponds to ccdb's
# ``dangerously_skip_permissions``: unsandboxed *is* the only mode. Rather than
# let that be discovered later from a thread that already ran, the backend
# refuses to spawn until an operator says so once, in the deployment's
# environment. Same stance as the local backend's phone-home check — a property
# ccdb cannot provide is surfaced, not quietly dropped.
PI_UNSANDBOXED_ENV = "CCDB_PI_ALLOW_UNSANDBOXED"
UNSANDBOXED_REFUSAL = (
    "The pi backend runs without a sandbox: pi has no approval loop and no "
    f"sandbox of its own. Set {PI_UNSANDBOXED_ENV}=1 in the deployment "
    "environment to accept that, or use the claude/codex backends."
)

# Project trust governs whether pi loads project-local `.pi/` settings, skills
# and extensions. Off by default: a repository ccdb checks out should not be
# able to reconfigure the agent that is about to run inside it, even when the
# repository is the operator's own.
PI_APPROVE_PROJECT_ENV = "CCDB_PI_APPROVE_PROJECT"

# pi's built-in tool names mapped onto the names ccdb renders and categorises.
# Anything unmapped passes through under its own name.
_TOOL_NAME_MAP: dict[str, str] = {
    "read": "Read",
    "write": "Write",
    "edit": "Edit",
    "bash": "Bash",
    "powershell": "Bash",
    "grep": "Grep",
    "find": "Glob",
    "ls": "LS",
    "ask_question": "AskUserQuestion",
}

# pi names the file argument ``path``; ToolUseEvent.display_name reads
# ``file_path``. Translating here is what makes a pi tool embed read
# "Reading: /x/y.txt" instead of "Reading: unknown".
_TOOL_ARG_ALIASES: dict[str, str] = {"path": "file_path"}

# ``agent_settled`` is the real end of the stream; ``agent_end`` is the last
# event that carries anything. Completion is reported on the former so a turn
# is never marked done while events are still arriving.
_TERMINAL_EVENT = "agent_settled"

# Once the terminal event has been seen the rest of stdout is the CLI winding
# down. Bound that separately so a CLI that never closes stdout cannot hold the
# per-thread run slot for a whole turn timeout.
POST_COMPLETION_DRAIN_SECONDS = 10.0
PROCESS_EXIT_TIMEOUT_SECONDS = 10.0


def _text_of(message: dict[str, Any]) -> str:
    """Concatenate the ``text`` blocks of a pi message.

    ``thinking`` blocks sit in the same content list and are deliberately left
    out: they are the model's scratchpad, not its reply.
    """
    blocks = message.get("content") or []
    return "".join(b.get("text", "") for b in blocks if b.get("type") == "text")


def _thinking_of(message: dict[str, Any]) -> str:
    blocks = message.get("content") or []
    return "".join(b.get("thinking", "") for b in blocks if b.get("type") == "thinking")


def _tool_use_from(data: dict[str, Any]) -> ToolUseEvent:
    raw_name = data.get("toolName", "")
    name = _TOOL_NAME_MAP.get(raw_name, raw_name)
    args = data.get("args") or {}
    tool_input = {_TOOL_ARG_ALIASES.get(k, k): v for k, v in args.items()}
    return ToolUseEvent(
        tool_id=data.get("toolCallId", ""),
        tool_name=name,
        tool_input=tool_input,
        category=TOOL_CATEGORIES.get(name, ToolCategory.OTHER),
    )


def _tool_result_text(result: Any) -> str:
    """Flatten a pi tool result into the text ccdb renders.

    Results arrive as ``{"content": [{"type": "text", "text": ...}]}``; a tool
    that returns something else is rendered as its JSON rather than dropped.
    """
    if isinstance(result, dict) and isinstance(result.get("content"), list):
        return "".join(b.get("text", "") for b in result["content"] if isinstance(b, dict))
    if isinstance(result, str):
        return result
    return json.dumps(result, ensure_ascii=False)


def parse_pi_line(line: str) -> StreamEvent | None:
    """Parse a single line of ``pi --mode json`` output into a StreamEvent."""
    line = line.strip()
    if not line:
        return None

    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None

    event_type = data.get("type", "")

    # First line of every run: the session header. Its id is what ccdb stores
    # and hands back as ``--session-id`` on the next turn.
    if event_type == "session":
        return StreamEvent(
            raw=data,
            message_type=MessageType.SYSTEM,
            session_id=data.get("id"),
        )

    if event_type == "message_end":
        message = data.get("message") or {}
        if message.get("role") != "assistant":
            # ``user`` echoes the prompt back and ``toolResult`` duplicates the
            # tool_execution_end that already rendered.
            return None
        usage = message.get("usage") or {}
        if message.get("stopReason") == "error":
            return StreamEvent(
                raw=data,
                message_type=MessageType.RESULT,
                is_complete=True,
                error=message.get("errorMessage") or "pi reported an error",
            )
        return StreamEvent(
            raw=data,
            message_type=MessageType.ASSISTANT,
            text=_text_of(message) or None,
            thinking=_thinking_of(message) or None,
            input_tokens=usage.get("input"),
            output_tokens=usage.get("output"),
            cache_read_tokens=usage.get("cacheRead"),
            cache_creation_tokens=usage.get("cacheWrite"),
        )

    if event_type == "tool_execution_start":
        return StreamEvent(
            raw=data,
            message_type=MessageType.ASSISTANT,
            tool_use=_tool_use_from(data),
        )

    if event_type == "tool_execution_end":
        # USER, not ASSISTANT: EventProcessor only cancels a tool embed's live
        # elapsed timer on USER events (_on_tool_result). Tagging this
        # ASSISTANT leaves every pi tool timer running forever.
        return StreamEvent(
            raw=data,
            message_type=MessageType.USER,
            tool_result_id=data.get("toolCallId", ""),
            tool_result_content=_tool_result_text(data.get("result")),
        )

    if event_type == _TERMINAL_EVENT:
        return StreamEvent(raw=data, message_type=MessageType.RESULT, is_complete=True)

    if event_type in ("agent_start", "turn_start", "turn_end", "agent_end"):
        return StreamEvent(raw=data, message_type=MessageType.SYSTEM)

    return None


class PiRunner:
    """Manages a ``pi --mode json`` subprocess."""

    def __init__(
        self,
        command: str = "pi",
        model: str | None = None,
        permission_mode: str = "default",
        working_dir: str | None = None,
        timeout_seconds: int = 300,
        dangerously_skip_permissions: bool = False,
        allowed_tools: list[str] | None = None,
        api_port: int | None = None,
        api_secret: str | None = None,
        thread_id: int | None = None,
        append_system_prompt: str | None = None,
        images: list[ImageData] | None = None,
        effort: str | None = None,
        **_kwargs: object,
    ) -> None:
        self.command = command
        # ``None`` means "omit --model" so pi uses its own configured default.
        self.model = model
        # ccdb's ``effort`` maps onto pi's ``--thinking``. The level sets differ
        # from both Claude's and Codex's, hence a separate validation table.
        self.effort = effort
        self.permission_mode = permission_mode
        self.working_dir = working_dir
        self.timeout_seconds = timeout_seconds
        self.dangerously_skip_permissions = dangerously_skip_permissions
        self.allowed_tools = allowed_tools
        self.api_port = api_port
        self.api_secret = api_secret
        self.thread_id = thread_id
        self.append_system_prompt = append_system_prompt
        self.images = images
        self._process: asyncio.subprocess.Process | None = None
        self._interrupt_requested = False

    async def run(
        self,
        prompt: str,
        session_id: str | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Run the pi CLI for one turn and yield stream events."""
        if not _unsandboxed_execution_allowed():
            yield StreamEvent(
                raw={},
                message_type=MessageType.RESULT,
                is_complete=True,
                error=UNSANDBOXED_REFUSAL,
            )
            return

        self._interrupt_requested = False
        args = self._build_args(session_id)
        env = self._build_env()
        cwd = self.working_dir or os.getcwd()

        logger.info("Starting pi CLI: %s (cwd=%s)", " ".join(args[:6]) + " ...", cwd)

        self._process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
            limit=10 * 1024 * 1024,
        )

        logger.info("pi CLI started: pid=%s", self._process.pid)

        if self._process.stdin is not None:
            await self._send_prompt(prompt)

        try:
            async for event in self._read_stream():
                yield event
        except TimeoutError:
            logger.warning("pi CLI timed out after %ds", self.timeout_seconds)
            yield StreamEvent(
                raw={},
                message_type=MessageType.RESULT,
                is_complete=True,
                error=f"Timed out after {self.timeout_seconds} seconds",
            )
        finally:
            await self._cleanup()

    def clone(
        self,
        model: str | None = None,
        working_dir: str | None | object = _UNSET,
        thread_id: int | None = None,
        effort: str | None | object = _UNSET,
        append_system_prompt: str | None = None,
        **_kwargs: object,
    ) -> PiRunner:
        """Create a fresh runner with the same configuration but no process."""
        return PiRunner(
            command=self.command,
            model=model if model is not None else self.model,
            permission_mode=self.permission_mode,
            working_dir=(
                self.working_dir if working_dir is _UNSET else working_dir  # type: ignore[arg-type]
            ),
            timeout_seconds=self.timeout_seconds,
            dangerously_skip_permissions=self.dangerously_skip_permissions,
            allowed_tools=self.allowed_tools,
            api_port=self.api_port,
            api_secret=self.api_secret,
            thread_id=thread_id if thread_id is not None else self.thread_id,
            append_system_prompt=(
                append_system_prompt
                if append_system_prompt is not None
                else self.append_system_prompt
            ),
            images=self.images,
            effort=self.effort if effort is _UNSET else effort,  # type: ignore[arg-type]
        )

    async def interrupt(self) -> None:
        """Interrupt the subprocess with SIGINT.

        Measured on pi 0.85.1: SIGINT exits immediately without a terminal
        event, and the session file for an interrupted turn is never written —
        the turn is lost rather than resumable. Because ccdb owns the session
        id, the next turn simply recreates the session under the same id.
        """
        if self._process and self._process.returncode is None:
            self._interrupt_requested = True
            if os.name == "nt":
                self._process.terminate()
            else:
                self._process.send_signal(signal.SIGINT)
            try:
                await asyncio.wait_for(self._process.wait(), timeout=10)
            except TimeoutError:
                await self.kill()

    async def kill(self) -> None:
        """Terminate the subprocess."""
        if self._process and self._process.returncode is None:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except TimeoutError:
                self._process.kill()
                await self._process.wait()

    async def inject_tool_result(self, request_id: str, data: dict) -> None:
        """pi's json mode has no stdin channel; this is a no-op.

        RPC mode does have one (``prompt``/``steer``/``abort``), which is the
        motivation for a second pi backend later.
        """
        logger.debug("inject_tool_result called on PiRunner (no-op): %s", request_id)

    async def _send_prompt(self, prompt: str) -> None:
        """Write the prompt to stdin and close it.

        pi merges piped stdin into the initial prompt, so the prompt stays out
        of argv and large Discord attachments cannot trip E2BIG.
        """
        assert self._process is not None and self._process.stdin is not None
        try:
            self._process.stdin.write(prompt.encode())
            await self._process.stdin.drain()
            self._process.stdin.close()
            wait_closed = getattr(self._process.stdin, "wait_closed", None)
            if wait_closed is not None:
                await wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            logger.debug("pi stdin closed before prompt write completed", exc_info=True)
        except Exception:
            logger.warning("_send_prompt: failed to write to stdin", exc_info=True)

    def _build_args(self, session_id: str | None) -> list[str]:
        """Build command-line arguments for the pi CLI.

        Verified against pi 0.85.1::

            pi --mode json [--session-id <uuid>] [--model <provider/id>] ...

        ``--session-id`` is used rather than ``--session``: it takes an exact
        id and creates the session when it is missing, where ``--session``
        matches a partial UUID and fails on an unknown one.
        """
        args = [self.command, "--mode", "json"]

        if session_id:
            if not _is_uuid_like(session_id):
                raise ValueError(f"Invalid session_id format: {session_id!r}")
            args.extend(["--session-id", session_id])

        if self.model:
            args.extend(["--model", self.model])

        if self.effort:
            if self.effort not in VALID_PI_THINKING:
                raise ValueError(
                    f"Invalid pi thinking level {self.effort!r}; "
                    f"choose one of {', '.join(sorted(VALID_PI_THINKING))}"
                )
            args.extend(["--thinking", self.effort])

        if self.append_system_prompt:
            args.extend(["--append-system-prompt", self.append_system_prompt])

        if self.allowed_tools:
            # The only permission lever pi offers. ``permission_mode`` has no
            # CLI equivalent: pi has no approval loop to put into a mode.
            args.extend(["--tools", ",".join(self.allowed_tools)])

        # Project-local `.pi/` resources are ignored unless an operator opts in.
        args.append("--approve" if _project_trust_allowed() else "--no-approve")

        # `--` keeps a prompt that starts with a dash from being read as flags.
        # The prompt itself goes over stdin.
        args.append("--")
        return args

    _STRIPPED_ENV_KEYS = STRIPPED_ENV_KEYS

    def _build_env(self) -> dict[str, str]:
        """Build environment variables for the subprocess."""
        env = {k: v for k, v in os.environ.items() if k not in self._STRIPPED_ENV_KEYS}
        if self.api_port is not None:
            env["CCDB_API_URL"] = f"http://127.0.0.1:{self.api_port}"
        if self.api_secret is not None:
            env["CCDB_API_SECRET"] = self.api_secret
        if self.thread_id is not None:
            env["DISCORD_THREAD_ID"] = str(self.thread_id)
        return env

    def describe_api(self) -> str:
        """Return a short label for the endpoint this runner targets."""
        if self.model and "/" in self.model:
            provider = self.model.split("/", 1)[0]
            return f"pi ({provider})"
        return "pi (CLI-configured provider)"

    async def _read_stream(self) -> AsyncGenerator[StreamEvent, None]:
        """Read and parse stdout line by line.

        Token usage is reported by pi per assistant message, not once per turn,
        so it is accumulated here and attached to the terminal event: the
        largest ``input`` seen (context is re-sent each step, so summing it
        would multiply the same tokens) and the sum of ``output``.
        """
        if self._process is None or self._process.stdout is None:
            raise RuntimeError("Process not started")

        saw_terminal_event = False
        input_tokens: int | None = None
        output_tokens = 0
        cache_read_tokens = 0

        while True:
            read_timeout = (
                POST_COMPLETION_DRAIN_SECONDS
                if saw_terminal_event
                else (self.timeout_seconds or None)
            )
            try:
                line = await asyncio.wait_for(self._process.stdout.readline(), timeout=read_timeout)
            except TimeoutError:
                if not saw_terminal_event:
                    raise
                logger.warning(
                    "pi CLI kept stdout open %.0fs after the terminal event; terminating",
                    POST_COMPLETION_DRAIN_SECONDS,
                )
                return
            if not line:
                break
            event = parse_pi_line(line.decode("utf-8", errors="replace"))
            if event is None:
                continue

            if event.input_tokens is not None:
                input_tokens = max(input_tokens or 0, event.input_tokens)
            output_tokens += event.output_tokens or 0
            cache_read_tokens += event.cache_read_tokens or 0

            if event.is_complete:
                saw_terminal_event = True
                event = replace(
                    event,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens or None,
                    cache_read_tokens=cache_read_tokens or None,
                )
            yield event

        if self._process.returncode is None:
            try:
                await asyncio.wait_for(self._process.wait(), timeout=PROCESS_EXIT_TIMEOUT_SECONDS)
            except TimeoutError:
                logger.warning(
                    "pi CLI did not exit %.0fs after stdout closed; terminating",
                    PROCESS_EXIT_TIMEOUT_SECONDS,
                )
                if saw_terminal_event:
                    return
                raise

        if saw_terminal_event:
            return

        # No terminal event: either an intentional interrupt (SIGINT gives no
        # farewell) or a CLI that died. Either way the thread is owed a result,
        # or its status embed never finishes.
        if self._interrupt_requested:
            logger.info("pi CLI exited after an intentional interrupt")
            return

        stderr_text = ""
        if self._process.stderr:
            stderr_data = await self._process.stderr.read()
            stderr_text = stderr_data.decode("utf-8", errors="replace").strip()
        code = self._process.returncode
        logger.error("pi CLI ended without a terminal event (code=%s): %s", code, stderr_text[:200])
        error = f"pi CLI exited with code {code} before completing the turn"
        if stderr_text:
            error = f"{error}: {stderr_text[:1000]}"
        yield StreamEvent(
            raw={},
            message_type=MessageType.RESULT,
            is_complete=True,
            error=error,
        )

    async def _cleanup(self) -> None:
        """Ensure the subprocess is properly terminated."""
        await self.kill()
        self._process = None


def _is_uuid_like(value: str) -> bool:
    """Accept only the hex/dash shape pi mints, to keep argv injection out."""
    return bool(value) and all(c in "0123456789abcdefABCDEF-" for c in value)


def _env_flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in ("1", "true", "yes", "on")


def _unsandboxed_execution_allowed() -> bool:
    return _env_flag(PI_UNSANDBOXED_ENV)


def _project_trust_allowed() -> bool:
    return _env_flag(PI_APPROVE_PROJECT_ENV)
