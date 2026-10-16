"""Opt-in Codex app-server transport, measured against codex-cli 0.162.1.

One stdout reader owns framing; RPC replies never depend on the renderer
advancing the event generator. A steer is accepted only by its server ACK.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import uuid
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from .execution import ExecutionRefusedError, prepare_launch, refusal_event
from .types import ImageData, InjectedMessage, MessageType, StreamEvent, ToolCategory, ToolUseEvent

if TYPE_CHECKING:
    from .codex_runner import CodexRunner

logger = logging.getLogger(__name__)
RPC_TIMEOUT = 10.0


class RpcError(RuntimeError):
    """Explicit server rejection of an RPC request."""


def app_server_args(runner: CodexRunner) -> list[str]:
    # Reuse the exec builder's validation and config encoding, but never copy
    # exec-only flags into this different subcommand.
    from .codex_runner import _resolve_codex_sandbox_override

    legacy = runner._build_args("", None)
    args = [runner.command, "app-server", "--listen", "stdio://"]
    for index, arg in enumerate(legacy[:-1]):
        if arg == "-c":
            args.extend(["-c", legacy[index + 1]])
    args.extend(["-c", 'approval_policy="never"'])
    sandbox = (
        "danger-full-access"
        if runner.dangerously_skip_permissions
        else _resolve_codex_sandbox_override()
    )
    if sandbox:
        args.extend(["-c", f"sandbox_mode={json.dumps(sandbox)}"])
    return args


class AppServerSession:
    def __init__(self, runner: CodexRunner) -> None:
        self.runner = runner
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self.process: asyncio.subprocess.Process | None = None
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._notifications: asyncio.Queue[dict[str, Any] | StreamEvent | None] = asyncio.Queue()
        self._reader: asyncio.Task[None] | None = None
        self._stderr: asyncio.Task[None] | None = None
        self._usage: dict[str, Any] = {}
        self._closing = False
        self.outstanding: dict[str, InjectedMessage] = {}

    async def _write(self, message: dict[str, Any]) -> None:
        if self.process is None or self.process.stdin is None:
            raise ConnectionError("Codex app-server is not running")
        self.process.stdin.write((json.dumps(message, ensure_ascii=False) + "\n").encode())
        await self.process.stdin.drain()

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._reader is None or self._reader.done():
            raise ConnectionError("Codex app-server reader is closed")
        self._next_id += 1
        request_id = self._next_id
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._write({"id": request_id, "method": method, "params": params})
            return await asyncio.wait_for(asyncio.shield(future), RPC_TIMEOUT)
        finally:
            self._pending.pop(request_id, None)
            if not future.done():
                future.cancel()

    async def _read(self) -> None:
        assert self.process is not None and self.process.stdout is not None
        try:
            while line := await self.process.stdout.readline():
                data = json.loads(line)
                if not isinstance(data, dict):
                    raise ValueError("Expected a JSON-RPC object")
                if "id" in data and not isinstance(data["id"], (str, int)):
                    raise ValueError("Invalid JSON-RPC id")
                if "error" in data and not isinstance(data["error"], dict):
                    raise ValueError("Invalid JSON-RPC error")
                response_id = data.get("id")
                if "method" in data:
                    if "id" in data:
                        # No unattended approval/UI can be granted by the relay.
                        await self._write(
                            {
                                "id": data["id"],
                                "error": {
                                    "code": -32601,
                                    "message": "Interactive requests are unavailable",
                                },
                            }
                        )
                    else:
                        params = data.get("params", {})
                        if (
                            data["method"] == "item/completed"
                            and params.get("threadId") == self.thread_id
                            and params.get("turnId") == self.turn_id
                            and params.get("item", {}).get("type") == "userMessage"
                        ):
                            acknowledgement = self.acknowledge_user_message(params["item"])
                            if acknowledgement is not None:
                                await self._notifications.put(acknowledgement)
                        await self._notifications.put(data)
                elif (
                    isinstance(response_id, int)
                    and (future := self._pending.get(response_id)) is not None
                    and not future.done()
                ):
                    if "error" in data:
                        future.set_exception(RpcError(data["error"].get("message", "RPC failed")))
                    else:
                        future.set_result(data.get("result", {}))
        except (OSError, ValueError, ConnectionError, TypeError, AttributeError):
            logger.warning("Codex app-server stream failed", exc_info=True)
        finally:
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(ConnectionError("Codex app-server disconnected"))
            await self._notifications.put(None)

    async def _drain_stderr(self) -> None:
        assert self.process is not None and self.process.stderr is not None
        # Drain without retaining potentially sensitive diagnostics or allowing
        # a full stderr pipe to deadlock the protocol.
        while await self.process.stderr.read(65536):
            pass

    async def inject_user_message(self, text: str, images: list[ImageData] | None = None) -> bool:
        """Own the input until its correlated userMessage echo, as Claude does."""
        if self._closing or self.thread_id is None or self.turn_id is None:
            return False
        message = InjectedMessage(uuid=str(uuid.uuid4()), text=text, images=tuple(images or ()))
        self.outstanding[message.uuid] = message
        inputs = user_input(text, images)
        try:
            await self._request(
                "turn/steer",
                {
                    "threadId": self.thread_id,
                    "expectedTurnId": self.turn_id,
                    "clientUserMessageId": message.uuid,
                    "input": inputs,
                },
            )
        except RpcError:
            self.outstanding.pop(message.uuid, None)
            return False
        except (TimeoutError, ConnectionError, OSError):
            # Shared contract: True transfers ownership to the runner. Never
            # both return False (immediate queue) and report it as unconsumed.
            logger.warning("Codex steer delivery uncertain; retaining unconsumed input")
        return True

    def acknowledge_user_message(self, item: dict[str, Any]) -> StreamEvent | None:
        client_id = item.get("clientId")
        message = self.outstanding.pop(client_id, None) if isinstance(client_id, str) else None
        if message is None:
            return None
        return StreamEvent(raw={}, message_type=MessageType.USER, injected_text=message.text)

    async def interrupt(self) -> bool:
        if self.thread_id and self.turn_id:
            await self._request(
                "turn/interrupt",
                {
                    "threadId": self.thread_id,
                    "turnId": self.turn_id,
                },
            )
            return True
        return False

    async def run(self, prompt: str, session_id: str | None) -> AsyncGenerator[StreamEvent, None]:
        if session_id and not re.fullmatch(r"[a-f0-9\-]+", session_id):
            raise ValueError(f"Invalid session_id format: {session_id!r}")
        runner = self.runner
        try:
            launch = await prepare_launch(
                backend=runner._execution_backend,
                requested_mode=runner.execution_mode,
                argv=app_server_args(runner),
                env=runner._build_env(),
                cwd=runner.working_dir or os.getcwd(),
            )
        except ExecutionRefusedError as exc:
            yield refusal_event(exc)
            return
        runner.execution_served = launch.mode
        if runner._interrupt_requested:
            return
        try:
            self.process = await asyncio.create_subprocess_exec(
                *launch.argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=launch.cwd,
                env=launch.env,
                limit=10 * 1024 * 1024,
            )
            runner._process = self.process
            self._reader = asyncio.create_task(self._read())
            self._stderr = asyncio.create_task(self._drain_stderr())
            await self._request("initialize", {"clientInfo": {"name": "ccdb", "version": "1"}})
            await self._write({"method": "initialized", "params": {}})
            # The server resolves this against its own cwd, including an SSH
            # environment's mapped directory. Never send the host-only path.
            params: dict[str, Any] = {"cwd": ".", "approvalPolicy": "never"}
            if runner.model:
                params["model"] = runner.model
            if session_id:
                params["threadId"] = session_id
            try:
                response = await self._request(
                    "thread/resume" if session_id else "thread/start", params
                )
            except RpcError as exc:
                from .codex_runner import _is_missing_rollout_error

                if not session_id or not _is_missing_rollout_error(str(exc)):
                    raise
                # Safe only before turn/start: no tools or user input ran yet.
                params.pop("threadId", None)
                response = await self._request("thread/start", params)
            self.thread_id = response["thread"]["id"]
            yield StreamEvent(
                raw={"type": "thread.started", "thread_id": self.thread_id},
                message_type=MessageType.SYSTEM,
                session_id=self.thread_id,
            )
            if runner._interrupt_requested:
                return
            turn_params: dict[str, Any] = {
                "threadId": self.thread_id,
                "input": user_input(prompt, runner.images),
            }
            if runner.effort:
                turn_params["effort"] = runner.effort
            response = await self._request("turn/start", turn_params)
            self.turn_id = response["turn"]["id"]
            if runner._interrupt_requested:
                await self.interrupt()
            while True:
                notification = await asyncio.wait_for(
                    self._notifications.get(),
                    runner.timeout_seconds or None,
                )
                if notification is None:
                    if not runner._interrupt_requested:
                        yield StreamEvent(
                            raw={},
                            message_type=MessageType.RESULT,
                            is_complete=True,
                            error="Codex app-server disconnected before turn completion",
                        )
                    return
                if isinstance(notification, StreamEvent):
                    yield notification
                    continue
                params = notification.get("params", {})
                if params.get("threadId") != self.thread_id:
                    continue
                if params.get("turnId", self.turn_id) != self.turn_id:
                    continue
                method = notification["method"]
                if method == "thread/tokenUsage/updated":
                    self._usage = params.get("tokenUsage", {}).get("last", {})
                if method == "turn/completed":
                    if params.get("turn", {}).get("id") != self.turn_id:
                        continue
                    self.turn_id = None
                    self._closing = True
                    # ACKs are read by the independent reader before teardown.
                    if self._pending:
                        await asyncio.wait(tuple(self._pending.values()), timeout=RPC_TIMEOUT)
                    turn = params["turn"]
                    error = (turn.get("error") or {}).get("message")
                    if turn.get("status") not in ("completed", "interrupted") and not error:
                        error = "Codex turn failed"
                    yield StreamEvent(
                        raw=notification,
                        message_type=MessageType.RESULT,
                        is_complete=True,
                        error=error,
                        input_tokens=self._usage.get("inputTokens"),
                        output_tokens=self._usage.get("outputTokens"),
                        cache_read_tokens=self._usage.get("cachedInputTokens"),
                    )
                    return
                for event in item_events(notification):
                    yield event
        except (
            TimeoutError,
            RpcError,
            ConnectionError,
            OSError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
        ) as exc:
            if runner._interrupt_requested:
                return
            yield StreamEvent(
                raw={},
                message_type=MessageType.RESULT,
                is_complete=True,
                error=f"Codex app-server: {exc or 'request timed out'}",
            )
        finally:
            self._closing = True
            await self.close()
            self.turn_id = None

    async def close(self) -> None:
        if self.process is not None:
            if self.turn_id:
                with contextlib.suppress(TimeoutError, ConnectionError, RpcError, OSError):
                    await self.interrupt()
            if self.process.stdin:
                self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 5)
            except TimeoutError:
                await self.runner.kill()
        for task in (self._reader, self._stderr):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task


def user_input(text: str, images: list[ImageData] | None = None) -> list[dict[str, str]]:
    return [
        {"type": "text", "text": text},
        *(
            {"type": "image", "url": f"data:{image.media_type};base64,{image.data}"}
            for image in images or ()
        ),
    ]


def item_events(data: dict[str, Any]) -> list[StreamEvent]:
    method = data.get("method")
    item = data.get("params", {}).get("item", {})
    kind = item.get("type")
    if method == "item/completed" and kind == "agentMessage":
        return [
            StreamEvent(raw=data, message_type=MessageType.ASSISTANT, text=item.get("text", ""))
        ]
    if method == "item/started" and kind in ("commandExecution", "fileChange", "mcpToolCall"):
        name, category, value = {
            "commandExecution": (
                "Bash",
                ToolCategory.COMMAND,
                {"command": item.get("command", "")},
            ),
            "fileChange": ("Edit", ToolCategory.EDIT, {"changes": item.get("changes", [])}),
            "mcpToolCall": (item.get("tool", "MCP"), ToolCategory.OTHER, item.get("arguments", {})),
        }[kind]
        return [
            StreamEvent(
                raw=data,
                message_type=MessageType.ASSISTANT,
                tool_use=ToolUseEvent(
                    tool_id=item.get("id", ""),
                    tool_name=name,
                    tool_input=value,
                    category=category,
                ),
            )
        ]
    if method == "item/completed" and kind in ("commandExecution", "fileChange", "mcpToolCall"):
        return [
            StreamEvent(
                raw=data,
                message_type=MessageType.USER,
                tool_result_id=item.get("id", ""),
                tool_result_content=item.get("aggregatedOutput") or "",
            )
        ]
    return []
