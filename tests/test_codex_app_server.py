"""App-server protocol acceptance and lifecycle, using a real stdio peer."""

from __future__ import annotations

import asyncio
import json
import sys
from unittest.mock import patch

import pytest

from claude_code_core.codex_runner import CodexRunner
from claude_code_core.local_backend import LocalCodexRunner

PEER = r"""
import json, sys

thread = "a1b2-1234"
for line in sys.stdin:
    m = json.loads(line)
    method = m.get("method")
    if "id" not in m:
        continue
    result = {}
    if method == "thread/start" or method == "thread/resume":
        result = {"thread": {"id": thread}}
    if method == "turn/start":
        result = {"turn": {"id": "turn1"}}
    if method == "turn/steer":
        if m["params"]["input"][0]["text"] == "reject":
            print(
                json.dumps(
                    {"id": m["id"], "error": {"code": -32600, "message": "no active turn to steer"}}
                ),
                flush=True,
            )
            continue
        result = {"turnId": "turn1"}
    print(json.dumps({"id": m["id"], "result": result}), flush=True)
    if method == "turn/start":
        print(
            json.dumps(
                {
                    "method": "item/started",
                    "params": {
                        "threadId": thread,
                        "turnId": "turn1",
                        "item": {"type": "commandExecution", "id": "cmd1", "command": "sleep 1"},
                    },
                }
            ),
            flush=True,
        )
    if method == "turn/steer" and m["params"]["input"][0]["text"] != "reject":
        print(json.dumps({"method": "item/completed", "params": {
            "threadId": thread, "turnId": "turn1", "item": {
                "type": "userMessage", "clientId": m["params"].get("clientUserMessageId")
            }
        }}), flush=True)
        for item in [
            {"type": "commandExecution", "id": "cmd1", "aggregatedOutput": "ok"},
            {"type": "agentMessage", "id": "msg1", "text": "steered"},
        ]:
            print(
                json.dumps(
                    {
                        "method": "item/completed",
                        "params": {"threadId": thread, "turnId": "turn1", "item": item},
                    }
                ),
                flush=True,
            )
        print(
            json.dumps(
                {
                    "method": "thread/tokenUsage/updated",
                    "params": {
                        "threadId": thread,
                        "tokenUsage": {
                            "last": {"inputTokens": 11, "cachedInputTokens": 3, "outputTokens": 5}
                        },
                    },
                }
            ),
            flush=True,
        )
        print(
            json.dumps(
                {
                    "method": "turn/completed",
                    "params": {
                        "threadId": thread,
                        "turn": {"id": "turn1", "status": "completed", "error": None},
                    },
                }
            ),
            flush=True,
        )
"""


@pytest.fixture
def transport(monkeypatch):
    monkeypatch.setenv("CCDB_CODEX_TRANSPORT", "app-server")


def test_opt_in_and_local_stays_exec(transport):
    assert CodexRunner()._use_app_server()
    assert not LocalCodexRunner()._use_app_server()


async def test_inactive_and_exec_decline():
    assert await CodexRunner().inject_user_message("hello") is False


async def test_acknowledged_steer_and_clean_eof(transport, tmp_path):
    peer = tmp_path / "peer.py"
    peer.write_text(PEER)
    spawn = asyncio.create_subprocess_exec
    launches = []

    async def start(*args, **kwargs):
        launches.append((args, kwargs))
        return await spawn(sys.executable, str(peer), **kwargs)

    runner = CodexRunner(working_dir=str(tmp_path), timeout_seconds=3)
    events = []
    active = asyncio.Event()

    async def consume():
        async for event in runner.run("initial"):
            events.append(event)
            if event.tool_use:
                active.set()

    with patch("asyncio.create_subprocess_exec", side_effect=start):
        task = asyncio.create_task(consume())
        await asyncio.wait_for(active.wait(), 5)
        assert await runner.inject_user_message("reject") is False
        assert await runner.inject_user_message("followup") is True
        await asyncio.wait_for(task, 5)
    assert launches[0][0][1:4] == ("app-server", "--listen", "stdio://")
    assert any(e.session_id == "a1b2-1234" for e in events)
    assert any(e.text == "steered" for e in events)
    assert any(e.tool_result_id == "cmd1" and e.tool_result_content == "ok" for e in events)
    terminal = [e for e in events if e.is_complete]
    assert len(terminal) == 1
    assert terminal[0].input_tokens == 11
    assert terminal[0].cache_read_tokens == 3
    assert terminal[0].output_tokens == 5
    assert not terminal[0].error
    assert [e.injected_text for e in events if e.injected_text] == ["followup"]
    assert runner.unconsumed_messages() == []
    assert runner._process.returncode == 0
    assert await runner.inject_user_message("late") is False


async def test_invalid_resume_rejected_before_spawn(transport):
    with pytest.raises(ValueError, match="session_id"):
        _ = [e async for e in CodexRunner().run("hello", "--malicious")]


def test_app_server_args_preserve_config(transport, monkeypatch):
    from claude_code_core.codex_app_server import app_server_args

    monkeypatch.setenv("CCDB_CODEX_SANDBOX_OVERRIDE", "read-only")
    args = app_server_args(CodexRunner(effort="high", append_system_prompt='a\n"b"'))
    assert 'sandbox_mode="read-only"' in args
    assert 'approval_policy="never"' in args
    assert "developer_instructions=" + json.dumps('a\n"b"') in args
    assert "model_reasoning_effort=high" in args


def test_native_app_server_forces_sandbox():
    from claude_code_core.execution.native import rewrite_codex_argv

    args = rewrite_codex_argv(
        ("codex", "app-server", "-c", 'sandbox_mode="danger-full-access"'), "workspace-write"
    )
    assert args[-2:] == ("-c", 'sandbox_mode="workspace-write"')


@pytest.mark.parametrize(
    "status,error,expected",
    [
        ("failed", {"message": "provider failed"}, "provider failed"),
        ("failed", None, "Codex turn failed"),
        ("interrupted", None, None),
    ],
)
async def test_terminal_status_and_resume(transport, tmp_path, status, error, expected):
    peer = tmp_path / "peer.py"
    # A terminal event may arrive before turn/start's response is consumed.
    terminal = {
        "method": "turn/completed",
        "params": {
            "threadId": "a1b2-1234",
            "turn": {"id": "turn1", "status": status, "error": error},
        },
    }
    script = PEER[: PEER.index('    if method == "turn/start":\n        print(')]
    script += (
        '\n    if method == "turn/start":\n        print('
        + repr(json.dumps(terminal))
        + ", flush=True)\n"
    )
    peer.write_text(script)
    spawn = asyncio.create_subprocess_exec

    async def start(*args, **kwargs):
        return await spawn(sys.executable, str(peer), **kwargs)

    with patch("asyncio.create_subprocess_exec", side_effect=start):
        runner = CodexRunner(working_dir=str(tmp_path), timeout_seconds=2)
        events = [e async for e in runner.run("hello", "a1b2-1234")]
    assert events[-1].is_complete
    assert events[-1].error == expected
    assert runner._process.returncode == 0


@pytest.mark.parametrize("script", ["pass", 'print("malformed", flush=True)'])
async def test_eof_and_malformed_peer_fail_cleanly(transport, tmp_path, script):
    peer = tmp_path / "peer.py"
    peer.write_text(script)
    spawn = asyncio.create_subprocess_exec

    async def start(*args, **kwargs):
        return await spawn(sys.executable, str(peer), **kwargs)

    with patch("asyncio.create_subprocess_exec", side_effect=start):
        runner = CodexRunner(working_dir=str(tmp_path), timeout_seconds=2)
        events = [e async for e in runner.run("hello")]
    assert len(events) == 1
    assert events[0].is_complete and events[0].error
    assert runner._process.returncode is not None


async def test_reader_separates_server_request_ids_and_acks():
    from unittest.mock import AsyncMock, MagicMock

    from claude_code_core.codex_app_server import AppServerSession

    session = AppServerSession(CodexRunner())
    process = MagicMock()
    process.stdin.drain = AsyncMock()
    process.stdout = asyncio.StreamReader()
    session.process = process
    response = asyncio.get_running_loop().create_future()
    session._pending[1] = response
    for data in [
        {"id": 1, "method": "item/commandExecution/requestApproval", "params": {}},
        {"id": 1, "result": {"turnId": "turn1"}},
    ]:
        process.stdout.feed_data((json.dumps(data) + "\n").encode())
    process.stdout.feed_eof()
    await session._read()
    assert response.result() == {"turnId": "turn1"}
    written = json.loads(process.stdin.write.call_args.args[0])
    assert written["error"]["code"] == -32601


async def test_steer_timeout_is_not_reported_as_explicit_rejection(monkeypatch):
    from unittest.mock import AsyncMock

    from claude_code_core.codex_app_server import AppServerSession

    session = AppServerSession(CodexRunner())
    session.thread_id = "a1b2"
    session.turn_id = "turn1"
    monkeypatch.setattr(session, "_request", AsyncMock(side_effect=TimeoutError))
    assert await session.inject_user_message("hello") is True
    assert [m.text for m in session.outstanding.values()] == ["hello"]


@pytest.mark.parametrize("kind", ["fileChange", "mcpToolCall"])
def test_tools_always_close_their_rendering_timer(kind):
    from claude_code_core.codex_app_server import item_events

    item = {"type": kind, "id": "t1"}
    (start,) = item_events({"method": "item/started", "params": {"item": item}})
    (end,) = item_events({"method": "item/completed", "params": {"item": item}})
    assert start.tool_use.tool_id == end.tool_result_id == "t1"


async def test_native_app_server_refuses_danger_override(transport, monkeypatch):
    monkeypatch.setenv("CCDB_EXECUTION_ALLOWED_MODES", "host,native")
    monkeypatch.setenv("CCDB_CODEX_SANDBOX_OVERRIDE", "danger-full-access")
    runner = CodexRunner()
    runner.execution_mode = "native"
    with patch("asyncio.create_subprocess_exec") as spawn:
        events = [e async for e in runner.run("hello")]
    assert events[-1].error
    assert "contradicts" in events[-1].error
    spawn.assert_not_called()


async def test_missing_resume_retries_only_before_turn_start(transport, tmp_path):
    peer = tmp_path / "peer.py"
    script = PEER.replace(
        "    result = {}",
        """    if method == "thread/resume":
        print(json.dumps({"id": m["id"], "error": {
            "code": -32600, "message": "no rollout found for thread id"
        }}), flush=True)
        continue
    result = {}""",
    )
    terminal = {
        "method": "turn/completed",
        "params": {
            "threadId": "a1b2-1234",
            "turn": {"id": "turn1", "status": "completed"},
        },
    }
    script = script[: script.index('    if method == "turn/start":\n        print(')]
    script += (
        '\n    if method == "turn/start":\n        print('
        + repr(json.dumps(terminal))
        + ", flush=True)\n"
    )
    peer.write_text(script)
    spawn = asyncio.create_subprocess_exec

    async def start(*args, **kwargs):
        return await spawn(sys.executable, str(peer), **kwargs)

    with patch("asyncio.create_subprocess_exec", side_effect=start):
        events = [e async for e in CodexRunner(working_dir=str(tmp_path)).run("hello", "dead-beef")]
    assert any(e.session_id == "a1b2-1234" for e in events)
    assert events[-1].is_complete and not events[-1].error


async def test_timeout_interrupts_turn_and_closes_process(transport, tmp_path):
    peer = tmp_path / "peer.py"
    script = PEER[: PEER.index('    if method == "turn/start":\n        print(')]
    peer.write_text(script)
    spawn = asyncio.create_subprocess_exec

    async def start(*args, **kwargs):
        return await spawn(sys.executable, str(peer), **kwargs)

    with patch("asyncio.create_subprocess_exec", side_effect=start):
        runner = CodexRunner(working_dir=str(tmp_path), timeout_seconds=0.01)
        events = [e async for e in runner.run("hello")]
    assert events[-1].error and events[-1].is_complete
    assert runner._process.returncode == 0
    assert runner._app_server is None


async def test_native_app_server_removes_bypass_effect(transport, monkeypatch, tmp_path):
    monkeypatch.setenv("CCDB_EXECUTION_ALLOWED_MODES", "host,native")
    from claude_code_core.codex_app_server import app_server_args
    from claude_code_core.execution import prepare_launch

    runner = CodexRunner(dangerously_skip_permissions=True)
    launch = await prepare_launch(
        backend="codex",
        requested_mode="native",
        argv=app_server_args(runner),
        env=runner._build_env(),
        cwd=str(tmp_path),
    )
    assert launch.argv[-2:] == ("-c", 'sandbox_mode="workspace-write"')


async def test_completion_before_steer_ack_does_not_lose_acceptance(transport, tmp_path):
    peer = tmp_path / "peer.py"
    script = PEER.replace(
        '    print(json.dumps({"id": m["id"], "result": result}), flush=True)',
        """    if method != "turn/steer":
        print(json.dumps({"id": m["id"], "result": result}), flush=True)""",
    )
    script += """
    if method == "turn/steer" and m["params"]["input"][0]["text"] != "reject":
        print(json.dumps({"id": m["id"], "result": result}), flush=True)
"""
    assert script != PEER
    peer.write_text(script)
    spawn = asyncio.create_subprocess_exec

    async def start(*args, **kwargs):
        return await spawn(sys.executable, str(peer), **kwargs)

    runner = CodexRunner(working_dir=str(tmp_path), timeout_seconds=2)
    started = asyncio.Event()
    events = []

    async def consume():
        async for event in runner.run("hello"):
            events.append(event)
            if event.tool_use:
                started.set()

    with patch("asyncio.create_subprocess_exec", side_effect=start):
        task = asyncio.create_task(consume())
        await asyncio.wait_for(started.wait(), 3)
        assert await runner.inject_user_message("hello")
        await asyncio.wait_for(task, 3)
    assert len([e for e in events if e.is_complete]) == 1
    assert runner._process.returncode == 0


async def test_closing_runner_generator_immediately_closes_transport(transport, tmp_path):
    peer = tmp_path / "peer.py"
    peer.write_text(PEER)
    spawn = asyncio.create_subprocess_exec

    async def start(*args, **kwargs):
        return await spawn(sys.executable, str(peer), **kwargs)

    runner = CodexRunner(working_dir=str(tmp_path), timeout_seconds=2)
    with patch("asyncio.create_subprocess_exec", side_effect=start):
        stream = runner.run("hello")
        event = await anext(stream)
        assert event.session_id
        await stream.aclose()
        assert runner._process.returncode is not None
        assert runner._app_server is None


def test_native_does_not_mistake_model_value_for_subcommand():
    from claude_code_core.execution.native import rewrite_codex_argv

    argv = (
        "codex",
        "exec",
        "--model",
        "app-server",
        "--dangerously-bypass-approvals-and-sandbox",
        "-",
    )
    result = rewrite_codex_argv(argv, "workspace-write")
    assert "--dangerously-bypass-approvals-and-sandbox" not in result
    assert result[1:4] == ("exec", "--sandbox", "workspace-write")


@pytest.mark.parametrize("output", ["null", "[]", '{"id": []}', '{"id": 1, "error": "bad"}'])
async def test_invalid_protocol_shape_yields_error_and_cleans_up(transport, tmp_path, output):
    peer = tmp_path / "peer.py"
    peer.write_text("print(" + repr(output) + ", flush=True)")
    spawn = asyncio.create_subprocess_exec

    async def start(*args, **kwargs):
        return await spawn(sys.executable, str(peer), **kwargs)

    with patch("asyncio.create_subprocess_exec", side_effect=start):
        runner = CodexRunner(working_dir=str(tmp_path), timeout_seconds=1)
        events = [e async for e in runner.run("hello")]
    assert events[-1].is_complete and events[-1].error
    assert runner._app_server is None
    assert runner._process.returncode is not None


async def test_interrupt_before_turn_start_does_not_start_work(transport, tmp_path):
    peer = tmp_path / "peer.py"
    peer.write_text(
        PEER.replace(
            '    if method == "turn/start":\n        result',
            '    if method == "turn/start":\n'
            '        raise RuntimeError("work should not start")\n        result',
        )
    )
    spawn = asyncio.create_subprocess_exec

    async def start(*args, **kwargs):
        return await spawn(sys.executable, str(peer), **kwargs)

    runner = CodexRunner(working_dir=str(tmp_path), timeout_seconds=1)
    with patch("asyncio.create_subprocess_exec", side_effect=start):
        stream = runner.run("hello")
        assert (await anext(stream)).session_id
        await runner.interrupt()
        remaining = [e async for e in stream]
    assert not any(e.error for e in remaining)
    assert runner._process.returncode is not None


async def test_shared_injection_contract_tracks_echo_and_unconsumed():
    from unittest.mock import AsyncMock

    from claude_code_core.codex_app_server import AppServerSession
    from claude_code_core.types import ImageData

    runner = CodexRunner()
    session = AppServerSession(runner)
    runner._app_server = session
    session.thread_id, session.turn_id = "a1b2", "turn1"
    request = AsyncMock(return_value={"turnId": "turn1"})
    with patch.object(session, "_request", request):
        assert await runner.inject_user_message("hello", [ImageData("YWJj", "image/png")])
    (message,) = runner.unconsumed_messages()
    assert message.text == "hello"
    params = request.call_args.args[1]
    assert params["clientUserMessageId"] == message.uuid
    assert params["input"][1] == {"type": "image", "url": "data:image/png;base64,YWJj"}
    # Echo is correlated by client ID, not by text (repeated inputs are valid).
    ack = session.acknowledge_user_message({"id": "server-id", "clientId": message.uuid})
    assert ack.injected_text == "hello"
    assert runner.unconsumed_messages() == []
    assert session.acknowledge_user_message({"clientId": "unrelated"}) is None


async def test_shared_injection_owns_ambiguous_write_and_reports_unconsumed():
    from unittest.mock import AsyncMock

    from claude_code_core.codex_app_server import AppServerSession

    runner = CodexRunner()
    session = AppServerSession(runner)
    runner._app_server = session
    session.thread_id, session.turn_id = "a1b2", "turn1"
    with patch.object(session, "_request", AsyncMock(side_effect=ConnectionError)):
        assert await runner.inject_user_message("hello") is True
    assert [m.text for m in runner.unconsumed_messages()] == ["hello"]


async def test_echo_is_recorded_even_when_renderer_is_paused():
    from unittest.mock import AsyncMock, MagicMock

    from claude_code_core.codex_app_server import AppServerSession
    from claude_code_core.types import InjectedMessage

    session = AppServerSession(CodexRunner())
    session.thread_id, session.turn_id = "a1b2", "turn1"
    session.outstanding["client1"] = InjectedMessage("client1", "hello")
    process = MagicMock()
    process.stdin.drain = AsyncMock()
    process.stdout = asyncio.StreamReader()
    session.process = process
    process.stdout.feed_data(
        (
            json.dumps(
                {
                    "method": "item/completed",
                    "params": {
                        "threadId": "a1b2",
                        "turnId": "turn1",
                        "item": {"type": "userMessage", "clientId": "client1"},
                    },
                }
            )
            + "\n"
        ).encode()
    )
    process.stdout.feed_eof()
    await session._read()
    assert session.outstanding == {}
