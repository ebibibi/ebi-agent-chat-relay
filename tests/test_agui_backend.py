"""AG-UI backend protocol mapping and HTTP integration tests."""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import patch

import pytest
from aiohttp import web

from claude_code_core.agui_backend import (
    AgUiBackend,
    AgUiEventMapper,
    AgUiProtocolError,
    _iter_sse_events,
)
from claude_code_core.codex_runner import CodexRunner
from claude_code_core.privacy.backend import AnonymizingBackend
from claude_code_core.runner import ClaudeRunner
from claude_code_core.types import ImageData, MessageType, ToolCategory
from claude_discord.backend_factory import BackendFactory
from claude_discord.backend_settings import ALL_BACKENDS
from claude_discord.cogs.event_processor import _backend_name_from_runner


def _only(mapper: AgUiEventMapper, event: dict[str, object]):
    mapped = mapper.feed(event)
    assert len(mapped) == 1
    return mapped[0]


@pytest.mark.parametrize("active", [True, False])
async def test_agui_uses_read_idle_deadline_instead_of_total_runtime(active: bool) -> None:
    async def handler(request: web.Request) -> web.StreamResponse:
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        if active:
            for _ in range(6):
                await asyncio.sleep(0.03)
                await response.write(b": heartbeat\n\n")
            await response.write(b'data: {"type":"RUN_FINISHED","threadId":"t"}\n\n')
        else:
            await asyncio.sleep(0.15)
        return response

    server, url = await _serve(handler)
    try:
        backend = AgUiBackend(endpoint_url=url, timeout_seconds=0.1)
        events = [event async for event in backend.run("test")]
        assert bool(events[-1].error) is not active
    finally:
        await server.cleanup()


class TestAgUiEventMapper:
    def test_rejects_event_without_string_type(self) -> None:
        with pytest.raises(AgUiProtocolError, match="string type"):
            AgUiEventMapper().feed({"type": 123})

    def test_run_lifecycle_maps_to_session_and_completion(self) -> None:
        mapper = AgUiEventMapper()

        started = _only(
            mapper,
            {"type": "RUN_STARTED", "threadId": "thread-1", "runId": "run-1"},
        )
        assert started.message_type is MessageType.SYSTEM
        assert started.session_id == "thread-1"

        finished = _only(
            mapper,
            {"type": "RUN_FINISHED", "threadId": "thread-1", "runId": "run-1"},
        )
        assert finished.message_type is MessageType.RESULT
        assert finished.session_id == "thread-1"
        assert finished.is_complete is True

    def test_text_deltas_become_cumulative_partial_events(self) -> None:
        mapper = AgUiEventMapper()
        assert (
            mapper.feed(
                {
                    "type": "TEXT_MESSAGE_START",
                    "messageId": "message-1",
                    "role": "assistant",
                }
            )
            == []
        )

        first = _only(
            mapper,
            {"type": "TEXT_MESSAGE_CONTENT", "messageId": "message-1", "delta": "Hel"},
        )
        second = _only(
            mapper,
            {"type": "TEXT_MESSAGE_CONTENT", "messageId": "message-1", "delta": "lo"},
        )
        final = _only(
            mapper,
            {"type": "TEXT_MESSAGE_END", "messageId": "message-1"},
        )

        assert (first.text, first.is_partial) == ("Hel", True)
        assert (second.text, second.is_partial) == ("Hello", True)
        assert (final.text, final.is_partial) == ("Hello", False)

    def test_tool_call_is_emitted_after_arguments_are_complete(self) -> None:
        mapper = AgUiEventMapper()
        assert (
            mapper.feed(
                {
                    "type": "TOOL_CALL_START",
                    "toolCallId": "tool-1",
                    "toolCallName": "search",
                }
            )
            == []
        )
        assert (
            mapper.feed({"type": "TOOL_CALL_ARGS", "toolCallId": "tool-1", "delta": '{"q":'}) == []
        )
        assert (
            mapper.feed({"type": "TOOL_CALL_ARGS", "toolCallId": "tool-1", "delta": '"AG-UI"}'})
            == []
        )

        call = _only(mapper, {"type": "TOOL_CALL_END", "toolCallId": "tool-1"})
        assert call.message_type is MessageType.ASSISTANT
        assert call.tool_use is not None
        assert call.tool_use.tool_id == "tool-1"
        assert call.tool_use.tool_name == "search"
        assert call.tool_use.tool_input == {"q": "AG-UI"}
        assert call.tool_use.category is ToolCategory.OTHER

        result = _only(
            mapper,
            {
                "type": "TOOL_CALL_RESULT",
                "messageId": "result-1",
                "toolCallId": "tool-1",
                "content": "found",
            },
        )
        assert result.message_type is MessageType.USER
        assert result.tool_result_id == "tool-1"
        assert result.tool_result_content == "found"

    def test_compact_tool_chunks_are_closed_before_run_finishes(self) -> None:
        mapper = AgUiEventMapper()
        assert (
            mapper.feed(
                {
                    "type": "TOOL_CALL_CHUNK",
                    "toolCallId": "tool-compact",
                    "toolCallName": "lookup",
                    "delta": '{"q":',
                }
            )
            == []
        )
        assert mapper.feed({"type": "TOOL_CALL_CHUNK", "delta": '"relay"}'}) == []

        events = mapper.feed({"type": "RUN_FINISHED", "threadId": "thread-1", "runId": "run-1"})
        assert len(events) == 2
        assert events[0].tool_use is not None
        assert events[0].tool_use.tool_id == "tool-compact"
        assert events[0].tool_use.tool_input == {"q": "relay"}
        assert events[1].is_complete is True

    def test_compact_tool_chunk_switch_closes_previous_call(self) -> None:
        mapper = AgUiEventMapper()
        mapper.feed(
            {
                "type": "TOOL_CALL_CHUNK",
                "toolCallId": "one",
                "toolCallName": "first",
                "delta": "[]",
            }
        )
        completed = mapper.feed(
            {
                "type": "TOOL_CALL_CHUNK",
                "toolCallId": "two",
                "toolCallName": "second",
                "delta": "not-json",
            }
        )
        assert completed[0].tool_use is not None
        assert completed[0].tool_use.tool_input == {"value": []}
        final = mapper.feed({"type": "RUN_ERROR", "message": "stop"})
        assert final[0].tool_use is not None
        assert final[0].tool_use.tool_input == {"raw": "not-json"}

    def test_first_compact_tool_chunk_requires_identity(self) -> None:
        with pytest.raises(AgUiProtocolError, match="toolCallId"):
            AgUiEventMapper().feed({"type": "TOOL_CALL_CHUNK", "delta": "{}"})

    def test_compact_text_chunks_stream_and_ignore_non_assistant_roles(self) -> None:
        mapper = AgUiEventMapper()
        first = _only(
            mapper,
            {
                "type": "TEXT_MESSAGE_CHUNK",
                "messageId": "assistant-1",
                "role": "assistant",
                "delta": "a",
            },
        )
        second = _only(
            mapper,
            {"type": "TEXT_MESSAGE_CHUNK", "messageId": "assistant-1", "delta": "b"},
        )
        assert (first.text, second.text) == ("a", "ab")
        assert (
            mapper.feed(
                {
                    "type": "TEXT_MESSAGE_CHUNK",
                    "messageId": "user-1",
                    "role": "user",
                    "delta": "hidden",
                }
            )
            == []
        )
        assert mapper.feed({"type": "TEXT_MESSAGE_CHUNK", "messageId": "empty"}) == []

    def test_empty_or_non_assistant_text_and_unknown_events_are_ignored(self) -> None:
        mapper = AgUiEventMapper()
        mapper.feed({"type": "TEXT_MESSAGE_START", "messageId": "u", "role": "user"})
        assert mapper.feed({"type": "TEXT_MESSAGE_CONTENT", "messageId": "u", "delta": "x"}) == []
        assert mapper.feed({"type": "TEXT_MESSAGE_END", "messageId": "u"}) == []
        assert mapper.feed({"type": "CUSTOM", "name": "ignored", "value": {}}) == []
        assert mapper.feed({"type": "REASONING_START"}) == []
        assert mapper.feed({"type": "REASONING_END"}) == []

    def test_reasoning_is_emitted_only_when_complete(self) -> None:
        mapper = AgUiEventMapper()
        assert mapper.feed({"type": "REASONING_START"}) == []
        assert (
            mapper.feed(
                {"type": "REASONING_MESSAGE_CONTENT", "messageId": "r", "delta": "checking"}
            )
            == []
        )
        reasoning = _only(mapper, {"type": "REASONING_END"})
        assert reasoning.thinking == "checking"
        assert reasoning.is_partial is False

    def test_run_error_is_terminal_and_does_not_expose_raw_payload(self) -> None:
        mapper = AgUiEventMapper()
        error = _only(
            mapper,
            {"type": "RUN_ERROR", "message": "agent failed", "secret": "do-not-copy"},
        )
        assert error.message_type is MessageType.RESULT
        assert error.is_complete is True
        assert error.error == "agent failed"
        assert error.raw == {}

    def test_interrupt_outcome_fails_explicitly_instead_of_reporting_success(self) -> None:
        mapper = AgUiEventMapper()
        finished = _only(
            mapper,
            {
                "type": "RUN_FINISHED",
                "threadId": "thread-1",
                "runId": "run-1",
                "outcome": {
                    "type": "interrupt",
                    "interrupts": [{"id": "approval-1", "value": {"question": "Continue?"}}],
                },
            },
        )
        assert finished.is_complete is True
        assert finished.error is not None
        assert "interrupt" in finished.error.lower()


async def _serve(handler) -> tuple[web.AppRunner, str]:
    app = web.Application()
    app.router.add_post("/agent", handler)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    sockets = site._server.sockets  # type: ignore[union-attr]
    port = sockets[0].getsockname()[1]
    return runner, f"http://127.0.0.1:{port}/agent"


class TestAgUiBackendHttp:
    async def test_posts_standard_input_and_streams_sse(self) -> None:
        captured: dict[str, object] = {}

        async def handler(request: web.Request) -> web.StreamResponse:
            captured["body"] = await request.json()
            captured["authorization"] = request.headers.get("Authorization")
            response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
            await response.prepare(request)
            frames = [
                {"type": "RUN_STARTED", "threadId": "remote-thread", "runId": "run-1"},
                {
                    "type": "TEXT_MESSAGE_START",
                    "messageId": "message-1",
                    "role": "assistant",
                },
                {
                    "type": "TEXT_MESSAGE_CONTENT",
                    "messageId": "message-1",
                    "delta": "hello",
                },
                {"type": "TEXT_MESSAGE_END", "messageId": "message-1"},
                {
                    "type": "RUN_FINISHED",
                    "threadId": "remote-thread",
                    "runId": "run-1",
                },
            ]
            for frame in frames:
                await response.write(f"data: {json.dumps(frame)}\n\n".encode())
            await response.write_eof()
            return response

        server, url = await _serve(handler)
        try:
            backend = AgUiBackend(endpoint_url=url, auth_token="token", thread_id=42)
            events = [event async for event in backend.run("Hello")]
        finally:
            await server.cleanup()

        body = captured["body"]
        assert isinstance(body, dict)
        assert body["threadId"] == "42"
        assert body["messages"][0]["role"] == "user"
        assert body["messages"][0]["content"] == "Hello"
        assert body["state"] == {}
        assert body["tools"] == []
        assert body["context"] == []
        assert body["forwardedProps"] == {}
        assert captured["authorization"] == "Bearer token"
        assert events[0].session_id == "remote-thread"
        assert events[-1].is_complete is True

    async def test_resume_uses_stored_session_as_thread_id(self) -> None:
        thread_ids: list[str] = []

        async def handler(request: web.Request) -> web.Response:
            thread_ids.append((await request.json())["threadId"])
            body = (
                'data: {"type":"RUN_STARTED","threadId":"saved-thread","runId":"r"}\n\n'
                'data: {"type":"RUN_FINISHED","threadId":"saved-thread","runId":"r"}\n\n'
            )
            return web.Response(text=body, content_type="text/event-stream")

        server, url = await _serve(handler)
        try:
            backend = AgUiBackend(endpoint_url=url, thread_id=42)
            _ = [event async for event in backend.run("Again", session_id="saved-thread")]
        finally:
            await server.cleanup()

        assert thread_ids == ["saved-thread"]

    async def test_http_error_is_bounded_and_does_not_echo_response_body(self) -> None:
        async def handler(_request: web.Request) -> web.Response:
            return web.Response(status=401, text="secret upstream diagnostic")

        server, url = await _serve(handler)
        try:
            backend = AgUiBackend(endpoint_url=url)
            events = [event async for event in backend.run("Hello")]
        finally:
            await server.cleanup()

        assert len(events) == 1
        assert events[0].is_complete is True
        assert events[0].error == "AG-UI endpoint returned HTTP 401"
        assert "secret" not in events[0].error

    async def test_does_not_follow_redirects_with_authorization_header(self) -> None:
        redirected_requests = 0

        async def redirect(request: web.Request) -> web.Response:
            target = f"http://127.0.0.1:{request.url.port}/redirected"
            raise web.HTTPFound(target)

        async def redirected(_request: web.Request) -> web.Response:
            nonlocal redirected_requests
            redirected_requests += 1
            return web.Response(text="should not be reached")

        app = web.Application()
        app.router.add_post("/agent", redirect)
        app.router.add_route("*", "/redirected", redirected)
        server = web.AppRunner(app)
        await server.setup()
        site = web.TCPSite(server, "127.0.0.1", 0)
        await site.start()
        sockets = site._server.sockets  # type: ignore[union-attr]
        url = f"http://127.0.0.1:{sockets[0].getsockname()[1]}/agent"
        try:
            backend = AgUiBackend(endpoint_url=url, auth_token="secret")
            events = [event async for event in backend.run("Hello")]
        finally:
            await server.cleanup()

        assert redirected_requests == 0
        assert events[-1].error == "AG-UI endpoint returned HTTP 302"

    async def test_rejects_non_http_endpoint(self) -> None:
        try:
            AgUiBackend(endpoint_url="file:///etc/passwd")
        except ValueError as exc:
            assert "http" in str(exc)
        else:
            raise AssertionError("non-HTTP AG-UI URL was accepted")

    def test_rejects_credentials_embedded_in_endpoint(self) -> None:
        with pytest.raises(ValueError, match="credentials"):
            AgUiBackend(endpoint_url="https://user:password@agent.example/run")

    async def test_rejects_wrong_content_type(self) -> None:
        async def handler(_request: web.Request) -> web.Response:
            return web.json_response({"type": "RUN_FINISHED"})

        server, url = await _serve(handler)
        try:
            events = [event async for event in AgUiBackend(endpoint_url=url).run("Hello")]
        finally:
            await server.cleanup()
        assert events[-1].error == "AG-UI endpoint did not return text/event-stream"

    async def test_invalid_sse_json_and_missing_terminal_are_visible_errors(self) -> None:
        async def invalid(_request: web.Request) -> web.Response:
            return web.Response(text="data: not-json\n\n", content_type="text/event-stream")

        server, url = await _serve(invalid)
        try:
            invalid_events = [event async for event in AgUiBackend(endpoint_url=url).run("x")]
        finally:
            await server.cleanup()
        assert invalid_events[-1].error == "AG-UI endpoint returned invalid JSON SSE data"

        async def incomplete(_request: web.Request) -> web.Response:
            return web.Response(
                text='data: {"type":"RUN_STARTED","threadId":"t","runId":"r"}\n\n',
                content_type="text/event-stream",
            )

        server, url = await _serve(incomplete)
        try:
            incomplete_events = [event async for event in AgUiBackend(endpoint_url=url).run("x")]
        finally:
            await server.cleanup()
        assert incomplete_events[-1].error == "AG-UI stream ended without a terminal event"

    async def test_image_input_uses_standard_inline_data_source(self) -> None:
        captured: dict[str, object] = {}

        async def handler(request: web.Request) -> web.Response:
            captured.update(await request.json())
            body = (
                'data: {"type":"RUN_STARTED","threadId":"t","runId":"r"}\n\n'
                'data: {"type":"RUN_FINISHED","threadId":"t","runId":"r"}\n\n'
            )
            return web.Response(text=body, content_type="text/event-stream")

        server, url = await _serve(handler)
        try:
            backend = AgUiBackend(
                endpoint_url=url,
                images=[ImageData(data="aGVsbG8=", media_type="image/png")],
            )
            _ = [event async for event in backend.run("inspect")]
        finally:
            await server.cleanup()
        content = captured["messages"][0]["content"]
        assert content[0] == {"type": "text", "text": "inspect"}
        assert content[1]["source"] == {
            "type": "data",
            "value": "aGVsbG8=",
            "mimeType": "image/png",
        }

    async def test_clone_keeps_configuration_without_sharing_active_request(self) -> None:
        backend = AgUiBackend(
            endpoint_url="https://agent.example/run",
            auth_token="token",
            thread_id=42,
            timeout_seconds=123,
        )
        cloned = backend.clone(thread_id=99)
        assert isinstance(cloned, AgUiBackend)
        assert cloned.endpoint_url == backend.endpoint_url
        assert cloned.auth_token == "token"
        assert cloned.thread_id == 99
        assert cloned.timeout_seconds == 123

    async def test_backend_control_methods_fail_closed_and_hide_configuration(self) -> None:
        backend = AgUiBackend(
            endpoint_url="https://agent.example/run",
            auth_token="secret",
        )
        assert backend._build_env() == {}
        assert backend.describe_api() == "AG-UI"
        with pytest.raises(RuntimeError, match="interrupt/resume"):
            await backend.inject_tool_result("request-1", {})
        await backend.kill()
        assert backend._interrupted is True


class _ChunkedContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_chunked(self, _size: int):
        for chunk in self._chunks:
            yield chunk


class TestSseParser:
    async def test_comments_multiline_data_crlf_and_unterminated_frame(self) -> None:
        content = _ChunkedContent(
            [
                b': keep-alive\r\ndata: {"type":\r\n',
                b'data: "CUSTOM", "name": "x"}\r\n\r\n',
                b'data: {"type":"RUN_ERROR","message":"x"}',
            ]
        )
        events = [event async for event in _iter_sse_events(content)]
        assert events == [
            {"type": "CUSTOM", "name": "x"},
            {"type": "RUN_ERROR", "message": "x"},
        ]

    async def test_rejects_non_object_and_oversized_frames(self) -> None:
        with pytest.raises(AgUiProtocolError, match="JSON object"):
            _ = [event async for event in _iter_sse_events(_ChunkedContent([b"data: []\n\n"]))]
        oversized = b"data: " + (b"x" * 1_048_577)
        with pytest.raises(AgUiProtocolError, match="size limit"):
            _ = [event async for event in _iter_sse_events(_ChunkedContent([oversized]))]


class TestAgUiBackendWiring:
    def test_main_config_reads_agui_settings(self) -> None:
        from claude_discord.main import load_config

        with (
            patch("claude_discord.main.load_dotenv"),
            patch.dict(
                "os.environ",
                {
                    "DISCORD_BOT_TOKEN": "fake-token",
                    "DISCORD_CHANNEL_ID": "123",
                    "CCDB_BACKEND": "agui",
                    "CCDB_AGUI_URL": "https://agent.example/run",
                    "CCDB_AGUI_TOKEN": "upstream-secret",
                },
                clear=True,
            ),
        ):
            config = load_config()

        assert config["backend"] == "agui"
        assert config["agui_url"] == "https://agent.example/run"
        assert config["agui_token"] == "upstream-secret"
        assert config["model"] == ""

    def test_backend_is_selectable(self) -> None:
        assert "agui" in ALL_BACKENDS

    def test_factory_builds_agui_with_remote_configuration(self) -> None:
        factory = BackendFactory(
            claude_command="claude",
            codex_command="codex",
            agui_url="https://agent.example/run",
            agui_token="secret",
            permission_mode="acceptEdits",
            working_dir=None,
            timeout_seconds=300,
            dangerously_skip_permissions=False,
            allowed_tools=None,
            append_system_prompt=None,
            effort=None,
        )
        backend = factory.build(backend="agui", thread_id=42)
        assert isinstance(backend, AgUiBackend)
        assert backend.endpoint_url == "https://agent.example/run"
        assert backend.auth_token == "secret"
        assert backend.thread_id == 42
        assert _backend_name_from_runner(backend) == "agui"

    def test_backend_name_survives_privacy_wrapper(self) -> None:
        backend = AgUiBackend(endpoint_url="https://agent.example/run")
        wrapped = AnonymizingBackend(backend, gateway=object())  # type: ignore[arg-type]
        assert _backend_name_from_runner(wrapped) == "agui"

    def test_factory_fails_closed_when_agui_url_is_missing(self) -> None:
        factory = BackendFactory(
            claude_command="claude",
            codex_command="codex",
            permission_mode="acceptEdits",
            working_dir=None,
            timeout_seconds=300,
            dangerously_skip_permissions=False,
            allowed_tools=None,
            append_system_prompt=None,
            effort=None,
        )
        try:
            factory.build(backend="agui")
        except ValueError as exc:
            assert "CCDB_AGUI_URL" in str(exc)
        else:
            raise AssertionError("AG-UI backend started without an endpoint")

    def test_agui_token_is_not_exposed_to_cli_backends(self) -> None:
        os.environ["CCDB_AGUI_URL"] = "https://internal-agent.example/run"
        os.environ["CCDB_AGUI_TOKEN"] = "upstream-secret"
        try:
            for runner in (ClaudeRunner(), CodexRunner()):
                env = runner._build_env()
                assert "CCDB_AGUI_URL" not in env
                assert "CCDB_AGUI_TOKEN" not in env
        finally:
            del os.environ["CCDB_AGUI_URL"]
            del os.environ["CCDB_AGUI_TOKEN"]
