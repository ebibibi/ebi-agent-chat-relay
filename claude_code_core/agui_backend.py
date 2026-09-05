"""AG-UI HTTP/SSE backend for frontend-neutral relay sessions.

The implementation intentionally speaks the wire protocol directly instead of
depending on an agent framework.  ``aiohttp`` is imported only when this
backend runs, so Claude/Codex-only installations keep their small dependency
surface.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator, AsyncIterator
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit
from uuid import uuid4

from .types import (
    TOOL_CATEGORIES,
    ImageData,
    MessageType,
    StreamEvent,
    ToolCategory,
    ToolUseEvent,
)

if TYPE_CHECKING:
    from aiohttp import ClientResponse

logger = logging.getLogger(__name__)

_MAX_SSE_EVENT_BYTES = 1_048_576
_MAX_ERROR_CHARS = 1_000


class AgUiProtocolError(ValueError):
    """Raised when an AG-UI peer returns an invalid or unsafe stream."""


class AgUiEventMapper:
    """Statefully translate AG-UI wire events into relay ``StreamEvent`` objects."""

    def __init__(self) -> None:
        self._thread_id: str | None = None
        self._message_text: dict[str, str] = {}
        self._message_roles: dict[str, str] = {}
        self._tool_names: dict[str, str] = {}
        self._tool_args: dict[str, str] = {}
        self._chunk_tool_id: str | None = None
        self._chunk_tool_name: str | None = None
        self._chunk_tool_args = ""
        self._reasoning_parts: list[str] = []

    def feed(self, event: dict[str, Any]) -> list[StreamEvent]:
        """Map one decoded AG-UI event; unsupported event types are ignored."""
        event_type = event.get("type")
        if not isinstance(event_type, str):
            raise AgUiProtocolError("AG-UI event is missing a string type")
        if event_type != "TOOL_CALL_CHUNK" and self._chunk_tool_id is not None:
            # Compact chunk events are closed by the next non-chunk event, as
            # specified by the AG-UI client transform.
            pending = self._finish_tool_chunk()
            return [pending, *self.feed(event)]

        if event_type == "RUN_STARTED":
            thread_id = _required_string(event, "threadId")
            self._thread_id = thread_id
            return [StreamEvent(message_type=MessageType.SYSTEM, session_id=thread_id)]

        if event_type == "RUN_FINISHED":
            thread_id = _optional_string(event.get("threadId")) or self._thread_id
            outcome = event.get("outcome")
            if isinstance(outcome, dict) and outcome.get("type") == "interrupt":
                return [
                    StreamEvent(
                        message_type=MessageType.RESULT,
                        session_id=thread_id,
                        is_complete=True,
                        error=(
                            "AG-UI agent requested an interrupt response; "
                            "interrupt/resume is not supported yet"
                        ),
                    )
                ]
            return [
                StreamEvent(
                    message_type=MessageType.RESULT,
                    session_id=thread_id,
                    is_complete=True,
                )
            ]

        if event_type == "RUN_ERROR":
            message = _optional_string(event.get("message")) or "AG-UI agent run failed"
            return [
                StreamEvent(
                    message_type=MessageType.RESULT,
                    is_complete=True,
                    error=message[:_MAX_ERROR_CHARS],
                )
            ]

        if event_type == "TEXT_MESSAGE_START":
            message_id = _required_string(event, "messageId")
            self._message_text[message_id] = ""
            self._message_roles[message_id] = _optional_string(event.get("role")) or "assistant"
            return []

        if event_type == "TEXT_MESSAGE_CONTENT":
            message_id = _required_string(event, "messageId")
            if self._message_roles.get(message_id, "assistant") != "assistant":
                return []
            delta = _required_string(event, "delta", allow_empty=True)
            text = self._message_text.get(message_id, "") + delta
            self._message_text[message_id] = text
            if not delta:
                return []
            return [
                StreamEvent(
                    message_type=MessageType.ASSISTANT,
                    text=text,
                    is_partial=True,
                )
            ]

        if event_type == "TEXT_MESSAGE_END":
            message_id = _required_string(event, "messageId")
            role = self._message_roles.pop(message_id, "assistant")
            text = self._message_text.pop(message_id, "")
            if role != "assistant" or not text:
                return []
            return [
                StreamEvent(
                    message_type=MessageType.ASSISTANT,
                    text=text,
                    is_partial=False,
                )
            ]

        if event_type == "TEXT_MESSAGE_CHUNK":
            return self._feed_text_chunk(event)

        if event_type == "TOOL_CALL_START":
            tool_id = _required_string(event, "toolCallId")
            self._tool_names[tool_id] = _required_string(event, "toolCallName")
            self._tool_args[tool_id] = ""
            return []

        if event_type == "TOOL_CALL_ARGS":
            tool_id = _required_string(event, "toolCallId")
            delta = _required_string(event, "delta", allow_empty=True)
            self._tool_args[tool_id] = self._tool_args.get(tool_id, "") + delta
            return []

        if event_type == "TOOL_CALL_END":
            tool_id = _required_string(event, "toolCallId")
            name = self._tool_names.pop(tool_id, "unknown")
            raw_args = self._tool_args.pop(tool_id, "")
            tool_input = _parse_tool_input(raw_args)
            return [
                StreamEvent(
                    message_type=MessageType.ASSISTANT,
                    tool_use=ToolUseEvent(
                        tool_id=tool_id,
                        tool_name=name,
                        tool_input=tool_input,
                        category=TOOL_CATEGORIES.get(name, ToolCategory.OTHER),
                    ),
                )
            ]

        if event_type == "TOOL_CALL_RESULT":
            return [
                StreamEvent(
                    message_type=MessageType.USER,
                    tool_result_id=_required_string(event, "toolCallId"),
                    tool_result_content=_required_string(event, "content", allow_empty=True),
                )
            ]

        if event_type == "TOOL_CALL_CHUNK":
            return self._feed_tool_chunk(event)

        if event_type in {"REASONING_START", "THINKING_START"}:
            self._reasoning_parts.clear()
            return []

        if event_type in {"REASONING_MESSAGE_CONTENT", "THINKING_TEXT_MESSAGE_CONTENT"}:
            delta = _required_string(event, "delta", allow_empty=True)
            if delta:
                self._reasoning_parts.append(delta)
            return []

        if event_type in {"REASONING_END", "THINKING_END"}:
            thinking = "".join(self._reasoning_parts)
            self._reasoning_parts.clear()
            if not thinking:
                return []
            return [
                StreamEvent(
                    message_type=MessageType.ASSISTANT,
                    thinking=thinking,
                    is_partial=False,
                )
            ]

        return []

    def _feed_tool_chunk(self, event: dict[str, Any]) -> list[StreamEvent]:
        incoming_id = _optional_string(event.get("toolCallId"))
        incoming_name = _optional_string(event.get("toolCallName"))
        completed: list[StreamEvent] = []
        if self._chunk_tool_id is not None and incoming_id not in {None, self._chunk_tool_id}:
            completed.append(self._finish_tool_chunk())
        if self._chunk_tool_id is None:
            if incoming_id is None or incoming_name is None:
                raise AgUiProtocolError(
                    "first AG-UI TOOL_CALL_CHUNK requires toolCallId and toolCallName"
                )
            self._chunk_tool_id = incoming_id
            self._chunk_tool_name = incoming_name
            self._chunk_tool_args = ""
        delta = _optional_string(event.get("delta"))
        if delta:
            self._chunk_tool_args += delta
        return completed

    def _finish_tool_chunk(self) -> StreamEvent:
        tool_id = self._chunk_tool_id
        name = self._chunk_tool_name
        if tool_id is None or name is None:  # pragma: no cover - internal invariant
            raise AgUiProtocolError("AG-UI compact tool call has no identity")
        event = StreamEvent(
            message_type=MessageType.ASSISTANT,
            tool_use=ToolUseEvent(
                tool_id=tool_id,
                tool_name=name,
                tool_input=_parse_tool_input(self._chunk_tool_args),
                category=TOOL_CATEGORIES.get(name, ToolCategory.OTHER),
            ),
        )
        self._chunk_tool_id = None
        self._chunk_tool_name = None
        self._chunk_tool_args = ""
        return event

    def _feed_text_chunk(self, event: dict[str, Any]) -> list[StreamEvent]:
        """Expand the compact AG-UI text event into the normal message lifecycle."""
        message_id = _optional_string(event.get("messageId")) or f"chunk-{uuid4()}"
        role = _optional_string(event.get("role"))
        if message_id not in self._message_text:
            self._message_text[message_id] = ""
            self._message_roles[message_id] = role or "assistant"
        if self._message_roles[message_id] != "assistant":
            return []
        delta = _optional_string(event.get("delta")) or ""
        if not delta:
            return []
        text = self._message_text[message_id] + delta
        self._message_text[message_id] = text
        return [StreamEvent(message_type=MessageType.ASSISTANT, text=text, is_partial=True)]


class AgUiBackend:
    """Connect a relay session to a remote AG-UI HTTP agent."""

    def __init__(
        self,
        endpoint_url: str,
        *,
        auth_token: str | None = None,
        thread_id: int | str | None = None,
        model: str | None = None,
        command: str = "ag-ui",
        permission_mode: str = "default",
        working_dir: str | None = None,
        timeout_seconds: int = 300,
        dangerously_skip_permissions: bool = False,
        allowed_tools: list[str] | None = None,
        images: list[ImageData] | None = None,
        api_port: int | None = None,
        **_ignored: object,
    ) -> None:
        self.endpoint_url = _validate_endpoint_url(endpoint_url)
        self.auth_token = auth_token
        self.thread_id = thread_id
        self.model = model or "remote"
        self.command = command
        self.permission_mode = permission_mode
        self.working_dir = working_dir
        self.timeout_seconds = timeout_seconds
        self.dangerously_skip_permissions = dangerously_skip_permissions
        self.allowed_tools = allowed_tools
        self.images = images
        self.api_port = api_port
        self._response: ClientResponse | None = None
        self._interrupted = False

    def clone(self, **kwargs: object) -> AgUiBackend:
        """Clone configuration without sharing request/cancellation state."""
        config: dict[str, object] = {
            "endpoint_url": self.endpoint_url,
            "auth_token": self.auth_token,
            "thread_id": self.thread_id,
            "model": self.model,
            "command": self.command,
            "permission_mode": self.permission_mode,
            "working_dir": self.working_dir,
            "timeout_seconds": self.timeout_seconds,
            "dangerously_skip_permissions": self.dangerously_skip_permissions,
            "allowed_tools": self.allowed_tools,
            "images": self.images,
            "api_port": self.api_port,
        }
        config.update(kwargs)
        return AgUiBackend(**config)  # type: ignore[arg-type]

    async def run(
        self,
        prompt: str,
        session_id: str | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """POST one AG-UI run and translate its SSE event stream."""
        try:
            import aiohttp
        except ImportError:
            yield _terminal_error("AG-UI backend requires the 'agui' optional dependency")
            return

        self._interrupted = False
        thread_id = session_id or (
            str(self.thread_id) if self.thread_id is not None else str(uuid4())
        )
        run_id = str(uuid4())
        body = _build_run_input(
            prompt=prompt,
            thread_id=thread_id,
            run_id=run_id,
            images=self.images,
        )
        headers = {"Accept": "text/event-stream", "Content-Type": "application/json"}
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"

        timeout = aiohttp.ClientTimeout(
            total=None,
            connect=self.timeout_seconds or None,
            sock_read=self.timeout_seconds or None,
        )
        mapper = AgUiEventMapper()
        terminal_seen = False
        try:
            async with (
                aiohttp.ClientSession(timeout=timeout) as client,
                client.post(
                    self.endpoint_url,
                    json=body,
                    headers=headers,
                    allow_redirects=False,
                ) as response,
            ):
                self._response = response
                if response.status < 200 or response.status >= 300:
                    yield _terminal_error(f"AG-UI endpoint returned HTTP {response.status}")
                    return
                content_type = response.headers.get("Content-Type", "").lower()
                if "text/event-stream" not in content_type:
                    yield _terminal_error("AG-UI endpoint did not return text/event-stream")
                    return
                async for wire_event in _iter_sse_events(response.content):
                    if self._interrupted:
                        return
                    for mapped in mapper.feed(wire_event):
                        terminal_seen = terminal_seen or mapped.is_complete
                        yield mapped
        except AgUiProtocolError as exc:
            yield _terminal_error(str(exc))
            return
        except TimeoutError:
            if not self._interrupted:
                yield _terminal_error(
                    f"AG-UI request timed out after {self.timeout_seconds} seconds"
                )
            return
        except Exception:
            if not self._interrupted:
                logger.warning("AG-UI request failed", exc_info=True)
                yield _terminal_error("AG-UI endpoint request failed")
            return
        finally:
            self._response = None

        if not terminal_seen and not self._interrupted:
            yield _terminal_error("AG-UI stream ended without a terminal event")

    async def interrupt(self) -> None:
        """Stop reading the active remote stream."""
        self._interrupted = True
        if self._response is not None:
            self._response.close()

    async def kill(self) -> None:
        """Terminate the active request (same transport action as interrupt)."""
        await self.interrupt()

    async def inject_tool_result(self, request_id: str, data: dict) -> None:
        """Reject CLI-specific approval injection that AG-UI does not define."""
        raise RuntimeError(
            f"AG-UI backend cannot inject a result for request {request_id!r}; "
            "use an AG-UI interrupt/resume flow"
        )

    def _build_env(self) -> dict[str, str]:
        """AG-UI starts no subprocess and therefore exposes no child environment."""
        return {}

    def describe_api(self) -> str:
        """Return a safe display label without exposing the configured URL or token."""
        return "AG-UI"


def _build_run_input(
    *,
    prompt: str,
    thread_id: str,
    run_id: str,
    images: list[ImageData] | None,
) -> dict[str, Any]:
    content: str | list[dict[str, Any]] = prompt
    if images:
        content = [{"type": "text", "text": prompt}]
        content.extend(
            {
                "type": "image",
                "source": {
                    "type": "data",
                    "value": image.data,
                    "mimeType": image.media_type,
                },
            }
            for image in images
        )
    return {
        "threadId": thread_id,
        "runId": run_id,
        "state": {},
        "messages": [{"id": str(uuid4()), "role": "user", "content": content}],
        "tools": [],
        "context": [],
        "forwardedProps": {},
    }


async def _iter_sse_events(content: Any) -> AsyncIterator[dict[str, Any]]:
    """Parse bounded UTF-8 JSON SSE frames from an aiohttp response body."""
    buffer = bytearray()
    data_lines: list[bytes] = []
    frame_bytes = 0

    async for chunk in content.iter_chunked(8192):
        buffer.extend(chunk)
        while True:
            newline = buffer.find(b"\n")
            if newline < 0:
                if frame_bytes + len(buffer) > _MAX_SSE_EVENT_BYTES:
                    raise AgUiProtocolError("AG-UI SSE event exceeded the size limit")
                break
            line = bytes(buffer[:newline]).rstrip(b"\r")
            del buffer[: newline + 1]
            frame_bytes += len(line) + 1
            if frame_bytes > _MAX_SSE_EVENT_BYTES:
                raise AgUiProtocolError("AG-UI SSE event exceeded the size limit")
            if not line:
                if data_lines:
                    yield _decode_sse_json(data_lines)
                data_lines = []
                frame_bytes = 0
                continue
            if line.startswith(b":"):
                continue
            if line == b"data":
                data_lines.append(b"")
            elif line.startswith(b"data:"):
                value = line[5:]
                if value.startswith(b" "):
                    value = value[1:]
                data_lines.append(value)

    if buffer:
        frame_bytes += len(buffer)
        if frame_bytes > _MAX_SSE_EVENT_BYTES:
            raise AgUiProtocolError("AG-UI SSE event exceeded the size limit")
        line = bytes(buffer).rstrip(b"\r")
        if line.startswith(b"data:"):
            value = line[5:]
            data_lines.append(value[1:] if value.startswith(b" ") else value)
    if data_lines:
        yield _decode_sse_json(data_lines)


def _decode_sse_json(data_lines: list[bytes]) -> dict[str, Any]:
    payload = b"\n".join(data_lines)
    try:
        decoded = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AgUiProtocolError("AG-UI endpoint returned invalid JSON SSE data") from exc
    if not isinstance(decoded, dict):
        raise AgUiProtocolError("AG-UI SSE data must be a JSON object")
    return decoded


def _parse_tool_input(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}
    return value if isinstance(value, dict) else {"value": value}


def _required_string(
    event: dict[str, Any],
    key: str,
    *,
    allow_empty: bool = False,
) -> str:
    value = event.get(key)
    if not isinstance(value, str) or (not value and not allow_empty):
        raise AgUiProtocolError(f"AG-UI event has invalid {key}")
    return value


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _validate_endpoint_url(endpoint_url: str) -> str:
    parsed = urlsplit(endpoint_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("AG-UI endpoint URL must use http or https")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("AG-UI endpoint URL must not contain credentials")
    return endpoint_url


def _terminal_error(message: str) -> StreamEvent:
    return StreamEvent(message_type=MessageType.RESULT, is_complete=True, error=message)
