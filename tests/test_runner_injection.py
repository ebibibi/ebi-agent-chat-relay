"""Messages delivered into a running Claude turn (issue #1129).

The fake process mirrors what claude 2.1.300 was measured to do with
``--input-format stream-json --replay-user-messages``: every user line it
consumes is echoed back with ``isReplay: true`` and the uuid it was sent with,
a line sent while a tool runs is folded into the same turn, and a line sent
after a result starts another turn in the same process.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import patch

import pytest

from claude_code_core.runner import ClaudeRunner
from claude_code_core.types import MessageType


class _FakeStdin:
    def __init__(self, proc: _FakeProcess) -> None:
        self._proc = proc
        self.fail_drain = False

    def write(self, data: bytes) -> None:
        for raw in data.decode().splitlines():
            self._proc.received.append(json.loads(raw))

    async def drain(self) -> None:
        if self.fail_drain:
            raise BrokenPipeError

    def close(self) -> None:
        pass


class _FakeStdout:
    def __init__(self) -> None:
        self.lines: asyncio.Queue[bytes] = asyncio.Queue()

    async def readline(self) -> bytes:
        return await self.lines.get()


class _FakeStderr:
    async def read(self) -> bytes:
        return b""


class _FakeProcess:
    def __init__(self) -> None:
        self.pid = 4242
        self.returncode: int | None = None
        self.received: list[dict[str, Any]] = []
        self.stdin = _FakeStdin(self)
        self.stdout = _FakeStdout()
        self.stderr = _FakeStderr()

    def emit(self, payload: dict[str, Any]) -> None:
        self.stdout.lines.put_nowait((json.dumps(payload) + "\n").encode())

    def echo(self, index: int) -> None:
        """Echo the *index*-th user line the CLI received, as it does on consuming it."""
        sent = self.received[index]
        self.emit(
            {
                "type": "user",
                "message": sent["message"],
                "uuid": sent["uuid"],
                "isReplay": True,
                "parent_tool_use_id": None,
            }
        )

    def result(self, text: str = "ok") -> None:
        self.emit({"type": "result", "subtype": "success", "result": text, "session_id": "abc"})

    def eof(self) -> None:
        self.stdout.lines.put_nowait(b"")

    async def wait(self) -> int:
        self.returncode = 0 if self.returncode is None else self.returncode
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15
        self.eof()

    def kill(self) -> None:
        self.terminate()

    def send_signal(self, _sig: int) -> None:
        self.terminate()


def _text(entry: dict[str, Any]) -> str:
    return "".join(b.get("text", "") for b in entry["message"]["content"])


async def _start(runner: ClaudeRunner, proc: _FakeProcess):
    """Start runner.run() and return the generator once the prompt was written."""

    async def _fake_exec(*_args: Any, **_kwargs: Any) -> _FakeProcess:
        return proc

    patcher = patch("asyncio.create_subprocess_exec", _fake_exec)
    patcher.start()
    gen = runner.run("first")
    return gen, patcher


class TestArgs:
    def test_replay_user_messages_is_requested(self) -> None:
        args = ClaudeRunner()._build_args("hi", session_id=None)
        assert "--replay-user-messages" in args


class TestInjection:
    @pytest.mark.asyncio
    async def test_rejected_before_the_process_starts(self) -> None:
        runner = ClaudeRunner()
        assert await runner.inject_user_message("hello") is False

    @pytest.mark.asyncio
    async def test_mid_turn_message_is_written_and_acknowledged(self) -> None:
        runner = ClaudeRunner()
        proc = _FakeProcess()
        gen, patcher = await _start(runner, proc)
        try:
            proc.emit({"type": "system", "subtype": "init", "session_id": "abc"})
            first = await gen.__anext__()
            assert first.message_type == MessageType.SYSTEM

            assert await runner.inject_user_message("also do X") is True
            assert _text(proc.received[1]) == "also do X"
            assert proc.received[1]["uuid"]

            proc.echo(0)
            proc.echo(1)
            proc.result("done")
            proc.eof()
            events = [e async for e in gen]
        finally:
            patcher.stop()

        acks = [e for e in events if e.injected_text is not None]
        assert [e.injected_text for e in acks] == ["also do X"]
        results = [e for e in events if e.is_complete]
        assert len(results) == 1
        assert results[0].turn_continues is False
        assert runner.unconsumed_messages() == []

    @pytest.mark.asyncio
    async def test_reader_keeps_going_while_a_message_is_unconsumed(self) -> None:
        """A message sent while the final text streams runs as a second turn."""
        runner = ClaudeRunner()
        proc = _FakeProcess()
        gen, patcher = await _start(runner, proc)
        try:
            proc.emit({"type": "system", "subtype": "init", "session_id": "abc"})
            await gen.__anext__()
            assert await runner.inject_user_message("one more thing") is True

            proc.echo(0)
            proc.result("first answer")
            proc.emit({"type": "system", "subtype": "init", "session_id": "abc"})
            proc.echo(1)
            proc.result("second answer")
            events = [e async for e in gen]
        finally:
            patcher.stop()

        results = [e for e in events if e.is_complete]
        assert [r.turn_continues for r in results] == [True, False]
        assert [r.text for r in results] == ["first answer", "second answer"]

    @pytest.mark.asyncio
    async def test_rejected_once_the_final_result_was_read(self) -> None:
        runner = ClaudeRunner()
        proc = _FakeProcess()
        gen, patcher = await _start(runner, proc)
        try:
            proc.emit({"type": "system", "subtype": "init", "session_id": "abc"})
            events = [await gen.__anext__()]
            proc.echo(0)
            proc.result("answer")
            async for event in gen:
                events.append(event)
                if event.is_complete:
                    # The consumer is still handling the final result: too late.
                    assert await runner.inject_user_message("late") is False
        finally:
            patcher.stop()
        assert len(proc.received) == 1

    @pytest.mark.asyncio
    async def test_unconsumed_messages_survive_an_early_exit(self) -> None:
        runner = ClaudeRunner()
        proc = _FakeProcess()
        gen, patcher = await _start(runner, proc)
        try:
            proc.emit({"type": "system", "subtype": "init", "session_id": "abc"})
            await gen.__anext__()
            assert await runner.inject_user_message("never read") is True
            await runner.interrupt()
            assert await runner.inject_user_message("after stop") is False
            _ = [e async for e in gen]
        finally:
            patcher.stop()
        assert [m.text for m in runner.unconsumed_messages()] == ["never read"]

    @pytest.mark.asyncio
    async def test_failed_write_still_belongs_to_the_runner(self) -> None:
        """Accepted means the runner owns it: a failed drain is reported as unconsumed."""
        runner = ClaudeRunner()
        proc = _FakeProcess()
        gen, patcher = await _start(runner, proc)
        try:
            proc.emit({"type": "system", "subtype": "init", "session_id": "abc"})
            await gen.__anext__()
            proc.stdin.fail_drain = True
            assert await runner.inject_user_message("lost pipe") is True
            proc.eof()
            _ = [e async for e in gen]
        finally:
            patcher.stop()
        assert [m.text for m in runner.unconsumed_messages()] == ["lost pipe"]

    @pytest.mark.asyncio
    async def test_subagent_user_lines_are_not_acknowledgements(self) -> None:
        runner = ClaudeRunner()
        proc = _FakeProcess()
        gen, patcher = await _start(runner, proc)
        try:
            proc.emit({"type": "system", "subtype": "init", "session_id": "abc"})
            await gen.__anext__()
            assert await runner.inject_user_message("steer") is True
            proc.emit(
                {
                    "type": "user",
                    "message": {"role": "user", "content": [{"type": "text", "text": "steer"}]},
                    "parent_tool_use_id": "toolu_1",
                }
            )
            proc.result("first")
            proc.echo(1)
            proc.result("second")
            events = [e async for e in gen]
        finally:
            patcher.stop()
        assert [e.turn_continues for e in events if e.is_complete] == [True, False]
        assert [e.injected_text for e in events if e.injected_text] == ["steer"]
