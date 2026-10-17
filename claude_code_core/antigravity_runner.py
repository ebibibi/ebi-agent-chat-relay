"""Google Antigravity CLI (``agy``) runner.

Spawns ``agy --print`` with NDJSON in and out as an async subprocess and yields
StreamEvent objects, providing the same interface as ClaudeRunner and PiRunner.

Measured against agy 1.3.5 rather than read off documentation:

* The prompt goes over **stdin** as one ``--input-format stream-json`` message
  (``{"event": "user", "message": {"content": ...}}``). Plain piped text is
  not read, and putting the prompt in argv would expose it to flag parsing and
  to the per-argument size limit.
* ``--conversation <id>`` resumes. An id agy does not know is *not* an error:
  it warns on stderr and starts a new conversation, whose id arrives in the
  ``init`` event — so ccdb stores whatever ``init`` reports and needs no
  resume-recovery path.
* ``result.usage`` is the **conversation's** running total. A resumed turn
  reports every earlier turn again, so per-turn usage is rebuilt from the
  ``agent_response`` steps of this run.
* In headless mode a tool that needs approval is **auto-denied**, and the turn
  still ends ``status: "SUCCESS"`` with ``denied_actions`` and, typically, an
  empty ``response``. Rendered naively that is a successful empty answer.

Permissions: ``dangerously_skip_permissions`` (or ``bypassPermissions``) maps
to ``--dangerously-skip-permissions``; ``acceptEdits``/``plan``/``auto`` map to
``--mode``. Without the skip flag agy fails closed (auto-deny), so unlike pi
no separate opt-in is needed to spawn it. agy has no tool allowlist flag, so a
configured ``allowed_tools`` cannot be enforced whenever agy may approve tools
on its own (skipped permissions, ``acceptEdits``, ``auto``); the runner refuses
that combination instead of silently exceeding the list. Only agy's default
(auto-deny) and ``plan`` run with an allowlist.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
from collections.abc import AsyncGenerator
from typing import Any

from .child_env import STRIPPED_ENV_KEYS
from .execution import ExecutionRefusedError, carry_execution_mode, prepare_launch, refusal_event
from .types import TOOL_CATEGORIES, ImageData, MessageType, StreamEvent, ToolCategory, ToolUseEvent

logger = logging.getLogger(__name__)

_UNSET = object()

BACKEND_NAME = "antigravity"

# ``agy --effort`` (agy 1.3.5).
VALID_AGY_EFFORTS: frozenset[str] = frozenset({"low", "medium", "high", "xhigh", "max"})

# ccdb permission_mode -> ``agy --mode``. Anything else leaves agy's default
# (request-review, which auto-denies in headless mode).
_MODE_MAP: dict[str, str] = {"acceptEdits": "accept-edits", "plan": "plan", "auto": "auto"}

# Modes in which agy approves nothing on its own, so a ccdb tool allowlist cannot
# be exceeded: agy's default (headless auto-deny) and plan. Every other mapping
# (skip-permissions, accept-edits, auto) lets agy approve tools by itself.
_ALLOWLIST_SAFE_MODES: frozenset[str] = frozenset({"default", "plan"})

ALLOWLIST_REFUSAL = (
    "The antigravity backend cannot enforce CCDB_ALLOWED_TOOLS: agy has no tool allowlist, "
    "and with permissions skipped or permission mode acceptEdits/auto it approves tools on "
    "its own. Unset CCDB_ALLOWED_TOOLS, use permission mode default or plan without "
    "CCDB_DANGEROUSLY_SKIP_PERMISSIONS, or use another backend."
)

# agy tool names -> the names ccdb renders and categorises. Only ``run_command``
# was recorded; the rest follow agy's tool list and only affect the embed label.
_TOOL_NAME_MAP: dict[str, str] = {
    "run_command": "Bash",
    "view_file": "Read",
    "write_to_file": "Write",
    "replace_file_content": "Edit",
    "multi_replace_file_content": "Edit",
    "grep_search": "Grep",
    "find_by_name": "Glob",
    "list_dir": "LS",
    "search_web": "WebSearch",
    "read_url_content": "WebFetch",
    "ask_question": "AskUserQuestion",
}
# agy's PascalCase parameters -> the keys ToolUseEvent.display_name reads.
_TOOL_ARG_ALIASES: dict[str, str] = {
    "CommandLine": "command",
    "AbsolutePath": "file_path",
    "TargetFile": "file_path",
    "Query": "query",
    "Url": "url",
}

_SESSION_ID_RE = re.compile(r"^[0-9a-fA-F-]+$")
# Model ids are things like ``claude-opus-4-6-thinking``; never flag-shaped.
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")

POST_COMPLETION_DRAIN_SECONDS = 10.0
PROCESS_EXIT_TIMEOUT_SECONDS = 10.0


def _denial_text(denied: list[Any]) -> str:
    names = ", ".join(
        str(d.get("display_name") or d.get("action") or "?") for d in denied if isinstance(d, dict)
    )
    return (
        f"agy denied {names or 'a tool'}: headless mode cannot ask for approval. "
        "Set CCDB_DANGEROUSLY_SKIP_PERMISSIONS=true for this deployment, or add an allow "
        "rule under permissions.allow in agy's settings.json."
    )


class AntigravityStreamParser:
    """Turns ``agy --output-format stream-json`` lines into StreamEvents.

    Stateful: agent text arrives as deltas per step, and per-turn usage has to
    be summed from the steps because the final ``result`` reports the whole
    conversation.
    """

    def __init__(self) -> None:
        self._text: dict[int, str] = {}
        self._tools_started: set[int] = set()
        self._session_id: str | None = None
        self._input_tokens: int | None = None
        self._output_tokens = 0
        self._cache_read_tokens = 0
        self.saw_result = False

    def feed(self, line: str) -> list[StreamEvent]:
        line = line.strip()
        if not line:
            return []
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return []
        if not isinstance(data, dict):
            return []
        kind = data.get("event")
        if kind == "init":
            self._session_id = data.get("conversation_id") or None
            return [
                StreamEvent(raw=data, message_type=MessageType.SYSTEM, session_id=self._session_id)
            ]
        if kind == "step_update":
            return self._step(data, data.get("step_update") or {})
        if kind == "result":
            return self._result(data, data.get("result") or {})
        return []

    def _step(self, raw: dict, step: dict) -> list[StreamEvent]:
        index = step.get("step_index", -1)
        step_type = step.get("step_type")
        done = step.get("state") == "DONE"
        if step_type == "agent_response":
            text = self._text.get(index, "") + (step.get("text_delta") or "")
            self._text[index] = text
            if not done:
                if not text.strip():
                    return []
                return [
                    StreamEvent(
                        raw=raw, message_type=MessageType.ASSISTANT, text=text, is_partial=True
                    )
                ]
            usage = step.get("usage") or {}
            if usage.get("input_tokens") is not None:
                self._input_tokens = max(self._input_tokens or 0, usage["input_tokens"])
            self._output_tokens += usage.get("output_tokens") or 0
            self._cache_read_tokens += usage.get("cache_read_tokens") or 0
            return [
                StreamEvent(
                    raw=raw,
                    message_type=MessageType.ASSISTANT,
                    text=text.rstrip() or None,
                    input_tokens=usage.get("input_tokens"),
                    output_tokens=usage.get("output_tokens"),
                    cache_read_tokens=usage.get("cache_read_tokens"),
                )
            ]
        if step_type == "tool":
            info = step.get("tool_info") or {}
            tool_id = f"{step.get('conversation_id', '')}:{index}"
            events: list[StreamEvent] = []
            if index not in self._tools_started:
                self._tools_started.add(index)
                raw_name = info.get("name") or step.get("tool_name") or ""
                name = _TOOL_NAME_MAP.get(raw_name, raw_name)
                params = info.get("parameters") or {}
                events.append(
                    StreamEvent(
                        raw=raw,
                        message_type=MessageType.ASSISTANT,
                        tool_use=ToolUseEvent(
                            tool_id=tool_id,
                            tool_name=name,
                            tool_input={
                                _TOOL_ARG_ALIASES.get(str(k), str(k)): v for k, v in params.items()
                            },
                            category=TOOL_CATEGORIES.get(name, ToolCategory.OTHER),
                        ),
                    )
                )
            if done:
                output = info.get("output")
                # USER: EventProcessor stops the tool embed's timer on USER events.
                events.append(
                    StreamEvent(
                        raw=raw,
                        message_type=MessageType.USER,
                        tool_result_id=tool_id,
                        tool_result_content=output if isinstance(output, str) else "",
                    )
                )
            return events
        return []

    def _result(self, raw: dict, result: dict) -> list[StreamEvent]:
        self.saw_result = True
        usage = {
            "input_tokens": self._input_tokens,
            "output_tokens": self._output_tokens or None,
            "cache_read_tokens": self._cache_read_tokens or None,
        }
        session_id = result.get("conversation_id") or self._session_id
        if result.get("status") != "SUCCESS":
            error = result.get("error") or f"agy ended with status {result.get('status')!r}"
            return [
                StreamEvent(
                    raw=raw,
                    message_type=MessageType.RESULT,
                    is_complete=True,
                    session_id=session_id,
                    error=str(error),
                    **usage,
                )
            ]
        response = (result.get("response") or "").rstrip()
        denied = result.get("denied_actions") or []
        if denied and not response:
            return [
                StreamEvent(
                    raw=raw,
                    message_type=MessageType.RESULT,
                    is_complete=True,
                    session_id=session_id,
                    error=_denial_text(denied),
                    **usage,
                )
            ]
        events: list[StreamEvent] = []
        if denied:
            # SYSTEM text without a session_id renders as a warning notice.
            events.append(
                StreamEvent(raw={}, message_type=MessageType.SYSTEM, text=_denial_text(denied))
            )
        events.append(
            StreamEvent(
                raw=raw,
                message_type=MessageType.RESULT,
                is_complete=True,
                session_id=session_id,
                text=response or None,
                **usage,
            )
        )
        return events


class AntigravityRunner:
    """Manages one ``agy --print`` subprocess per turn."""

    execution_mode: str | None = None
    execution_served: str | None = None

    def __init__(
        self,
        command: str = "agy",
        model: str | None = None,
        permission_mode: str = "default",
        working_dir: str | None = None,
        timeout_seconds: int = 300,
        dangerously_skip_permissions: bool = False,
        allowed_tools: list[str] | None = None,
        api_port: int | None = None,
        api_secret: str | None = None,
        thread_id: int | None = None,
        images: list[ImageData] | None = None,
        effort: str | None = None,
        **_kwargs: object,
    ) -> None:
        self.command = command
        # ``None`` omits --model so agy uses its own configured default.
        self.model = model
        self.effort = effort
        self.permission_mode = permission_mode
        self.working_dir = working_dir
        self.timeout_seconds = timeout_seconds
        self.dangerously_skip_permissions = dangerously_skip_permissions
        self.allowed_tools = allowed_tools
        self.api_port = api_port
        self.api_secret = api_secret
        self.thread_id = thread_id
        self.images = images
        self._process: asyncio.subprocess.Process | None = None
        self._interrupt_requested = False

    def _skips_permissions(self) -> bool:
        return self.dangerously_skip_permissions or self.permission_mode == "bypassPermissions"

    def _self_approves(self) -> bool:
        """True when agy may approve a tool without asking (the allowlist would not hold).

        Unknown modes count as self-approving: refusing is the safe direction.
        """
        return self._skips_permissions() or self.permission_mode not in _ALLOWLIST_SAFE_MODES

    async def run(
        self, prompt: str, session_id: str | None = None
    ) -> AsyncGenerator[StreamEvent, None]:
        """Run agy for one turn and yield stream events."""
        if self.allowed_tools and self._self_approves():
            yield StreamEvent(
                raw={}, message_type=MessageType.RESULT, is_complete=True, error=ALLOWLIST_REFUSAL
            )
            return

        self._interrupt_requested = False
        args = self._build_args(session_id)
        env = self._build_env()
        cwd = self.working_dir or os.getcwd()
        logger.info("Starting agy CLI: %s (cwd=%s)", " ".join(args[:8]) + " ...", cwd)

        try:
            launch = await prepare_launch(
                backend=BACKEND_NAME,
                requested_mode=self.execution_mode,
                argv=args,
                env=env,
                cwd=cwd,
            )
        except ExecutionRefusedError as exc:
            yield refusal_event(exc)
            return
        self.execution_served = launch.mode

        self._process = await asyncio.create_subprocess_exec(
            *launch.argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=launch.cwd,
            env=launch.env,
            limit=10 * 1024 * 1024,
        )
        logger.info("agy CLI started: pid=%s", self._process.pid)
        await self._send_prompt(prompt)

        try:
            async for event in self._read_stream():
                yield event
        except TimeoutError:
            logger.warning("agy CLI timed out after %ds", self.timeout_seconds)
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
        **_kwargs: object,
    ) -> AntigravityRunner:
        """Create a fresh runner with the same configuration but no process."""
        cloned = AntigravityRunner(
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
            images=self.images,
            effort=self.effort if effort is _UNSET else effort,  # type: ignore[arg-type]
        )
        return carry_execution_mode(self, cloned)

    async def interrupt(self) -> None:
        """Interrupt with SIGINT, escalating to terminate after 10 seconds."""
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
        """agy's print mode has no channel for answering a tool; no-op."""
        logger.debug("inject_tool_result called on AntigravityRunner (no-op): %s", request_id)

    async def _send_prompt(self, prompt: str) -> None:
        """Write the prompt as one stream-input message and close stdin (ends the run)."""
        if self._process is None or self._process.stdin is None:
            return
        message = json.dumps({"event": "user", "message": {"content": prompt}}) + "\n"
        try:
            self._process.stdin.write(message.encode())
            await self._process.stdin.drain()
            self._process.stdin.close()
            wait_closed = getattr(self._process.stdin, "wait_closed", None)
            if wait_closed is not None:
                await wait_closed()
        except (BrokenPipeError, ConnectionResetError):
            logger.debug("agy stdin closed before prompt write completed", exc_info=True)
        except Exception:
            logger.warning("agy: failed to write the prompt to stdin", exc_info=True)

    def _build_args(self, session_id: str | None) -> list[str]:
        """Build argv. ``--print ""`` selects print mode; the prompt comes over stdin."""
        args = [
            self.command,
            "--print",
            "",
            "--input-format",
            "stream-json",
            "--output-format",
            "stream-json",
        ]
        if session_id:
            if not _SESSION_ID_RE.match(session_id):
                raise ValueError(f"Invalid session_id format: {session_id!r}")
            args.extend(["--conversation", session_id])
        if self.model:
            if not _MODEL_RE.match(self.model):
                raise ValueError(f"Invalid agy model id: {self.model!r}")
            args.extend(["--model", self.model])
        if self.effort:
            if self.effort not in VALID_AGY_EFFORTS:
                raise ValueError(
                    f"Invalid agy effort {self.effort!r}; "
                    f"choose one of {', '.join(sorted(VALID_AGY_EFFORTS))}"
                )
            args.extend(["--effort", self.effort])
        if self._skips_permissions():
            args.append("--dangerously-skip-permissions")
        elif self.permission_mode in _MODE_MAP:
            args.extend(["--mode", _MODE_MAP[self.permission_mode]])
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
        return "Antigravity (agy)"

    async def _read_stream(self) -> AsyncGenerator[StreamEvent, None]:
        """Read and parse stdout until the ``result`` event and process exit."""
        if self._process is None or self._process.stdout is None:
            raise RuntimeError("Process not started")
        parser = AntigravityStreamParser()

        while True:
            read_timeout = (
                POST_COMPLETION_DRAIN_SECONDS
                if parser.saw_result
                else (self.timeout_seconds or None)
            )
            try:
                line = await asyncio.wait_for(self._process.stdout.readline(), timeout=read_timeout)
            except TimeoutError:
                if not parser.saw_result:
                    raise
                logger.warning("agy kept stdout open after its result; terminating")
                return
            if not line:
                break
            for event in parser.feed(line.decode("utf-8", errors="replace")):
                yield event

        if self._process.returncode is None:
            try:
                await asyncio.wait_for(self._process.wait(), timeout=PROCESS_EXIT_TIMEOUT_SECONDS)
            except TimeoutError:
                logger.warning("agy did not exit after stdout closed; terminating")
                if parser.saw_result:
                    return
                raise

        if parser.saw_result or self._interrupt_requested:
            return

        stderr_text = ""
        if self._process.stderr:
            stderr_text = (await self._process.stderr.read()).decode("utf-8", "replace").strip()
        code = self._process.returncode
        logger.error("agy ended without a result (code=%s): %s", code, stderr_text[:200])
        error = f"agy exited with code {code} before completing the turn"
        if stderr_text:
            error = f"{error}: {stderr_text[:1000]}"
        yield StreamEvent(raw={}, message_type=MessageType.RESULT, is_complete=True, error=error)

    async def _cleanup(self) -> None:
        await self.kill()
        self._process = None
