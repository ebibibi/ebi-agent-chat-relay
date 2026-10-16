"""Messages sent while a turn runs reach that turn (issue #1129).

``run_claude_with_config`` publishes the runner that owns the process so
frontends can deliver into it, carries messages the agent never read into any
continuation it starts, and otherwise says they were not read.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from types import SimpleNamespace
from typing import Any

import pytest

from claude_code_core.memory_surface import MemorySurface
from claude_code_core.types import InjectedMessage, MessageType, StreamEvent
from claude_discord import live_runs
from claude_discord.cogs._run_helper import run_claude_with_config
from claude_discord.cogs.event_processor import EventProcessor
from claude_discord.cogs.run_config import RunConfig


class _Runner:
    """A backend whose run() lets the test act while the turn is in flight."""

    working_dir = "/tmp/example"
    model = "test-model"
    images = None
    account = None

    def __init__(self, events: list[StreamEvent], unread: list[str] | None = None) -> None:
        self._events = events
        self._unread = unread or []
        self.injected: list[str] = []
        self.prompts: list[str] = []
        self.during_run: Any = None

    def clone(self, **_kwargs: Any) -> _Runner:
        return self

    def describe_api(self) -> str | None:
        return None

    async def run(self, prompt: str, session_id: str | None = None) -> AsyncGenerator:
        self.prompts.append(prompt)
        for event in self._events:
            if self.during_run is not None:
                await self.during_run()
                self.during_run = None
            yield event

    async def inject_user_message(self, text: str, images: Any = None) -> bool:
        self.injected.append(text)
        return True

    def unconsumed_messages(self) -> list[InjectedMessage]:
        unread, self._unread = self._unread, []
        return [InjectedMessage(uuid=str(i), text=t) for i, t in enumerate(unread)]

    async def interrupt(self) -> None:
        pass


def _result(text: str = "ok", *, turn_continues: bool = False) -> StreamEvent:
    return StreamEvent(
        message_type=MessageType.RESULT,
        is_complete=True,
        session_id="s-1",
        text=text,
        turn_continues=turn_continues,
    )


def _text(text: str) -> StreamEvent:
    return StreamEvent(message_type=MessageType.ASSISTANT, text=text)


async def test_a_running_turn_takes_messages_for_its_conversation() -> None:
    surface = MemorySurface()
    runner = _Runner([_text("working"), _result()])
    delivered: list[bool] = []

    async def _send() -> None:
        delivered.append(await live_runs.try_inject(surface.thread_key, "also X"))

    runner.during_run = _send
    await run_claude_with_config(RunConfig(surface=surface, runner=runner, prompt="go"))

    assert delivered == [True]
    assert runner.injected == ["also X"]
    # Once the run is over, nothing is listening any more.
    assert live_runs.get(surface.thread_key) is None
    assert await live_runs.try_inject(surface.thread_key, "later") is False


async def test_no_live_run_means_the_caller_queues() -> None:
    assert await live_runs.try_inject(987654321, "hello") is False


async def test_a_backend_without_an_input_channel_is_skipped() -> None:
    run = live_runs.LiveRun(runner=SimpleNamespace())
    live_runs.register(55, run)
    try:
        assert await live_runs.try_inject(55, "hello") is False
    finally:
        live_runs.unregister(55, run)


async def test_a_run_about_to_pause_refuses() -> None:
    runner = _Runner([])
    run = live_runs.LiveRun(runner=runner, slot=SimpleNamespace(pause_requested=True))
    live_runs.register(56, run)
    try:
        assert await live_runs.try_inject(56, "hello") is False
    finally:
        live_runs.unregister(56, run)
    assert runner.injected == []


async def test_unread_messages_are_reported_when_the_run_just_ends() -> None:
    surface = MemorySurface()
    runner = _Runner([_result()], unread=["please also deploy"])

    await run_claude_with_config(RunConfig(surface=surface, runner=runner, prompt="go"))

    assert runner.prompts == ["go"], "a stopped run must not start the unread work by itself"
    titles = [n.title for n in surface.notices]
    assert "Not read by the agent" in titles
    notice = next(n for n in surface.notices if n.title == "Not read by the agent")
    assert "please also deploy" in (notice.body or "")


async def test_unread_messages_ride_along_with_a_pause_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import claude_discord.cogs._run_helper as helper

    surface = MemorySurface()
    runner = _Runner([_result()], unread=["switch to plan B"])
    slot = SimpleNamespace(pause_requested=True, on_pause=None)
    acquired: list[Any] = []

    async def _acquire(_config: Any, _slots: Any) -> Any:
        acquired.append(_config)
        return slot if len(acquired) == 1 else None  # the resume runs unslotted

    monkeypatch.setattr(
        helper, "get_session_slots", lambda: SimpleNamespace(release=lambda _slot: None)
    )
    monkeypatch.setattr(helper, "_acquire_slot", _acquire)

    await run_claude_with_config(RunConfig(surface=surface, runner=runner, prompt="go"))

    assert len(runner.prompts) == 2
    assert "switch to plan B" in runner.prompts[1]


async def test_an_intermediate_result_is_not_the_end() -> None:
    surface = MemorySurface()
    runner = _Runner([])
    processor = EventProcessor(RunConfig(surface=surface, runner=runner, prompt="go"))

    await processor.process(_text("first answer"))
    await processor.process(_result("first answer", turn_continues=True))
    assert not [n for n in surface.notices if n.title == "Done"]

    await processor.process(
        StreamEvent(message_type=MessageType.USER, injected_text="also X", replay_uuid="u")
    )
    await processor.process(_result("second answer"))

    # The second turn produced no streamed text: its RESULT text is the reply.
    assert "second answer" in surface.conformance_sent_text
    assert [n.title for n in surface.notices].count("Done") == 1


async def test_a_run_under_other_settings_is_not_given_the_message() -> None:
    """After /backend local or /sandbox, a reply must not reach the old process."""
    runner = _Runner([])
    runner.execution_mode = None
    run = live_runs.LiveRun(runner=runner, backend="claude")
    live_runs.register(57, run)
    try:
        assert await live_runs.try_inject(57, "secret", backend="local") is False
        assert (
            await live_runs.try_inject(57, "x", backend="claude", execution_mode="bwrap") is False
        )
        assert await live_runs.try_inject(57, "ok", backend="claude", execution_mode=None) is True
    finally:
        live_runs.unregister(57, run)
    assert runner.injected == ["ok"]


async def test_a_waiting_message_is_not_overtaken() -> None:
    """A reply queued behind the turn keeps later replies out of that turn."""
    runner = _Runner([])
    run = live_runs.LiveRun(runner=runner)
    live_runs.register(58, run)
    try:
        release = live_runs.hold(58)
        assert await live_runs.try_inject(58, "B") is False
        release()
        release()  # idempotent
        assert await live_runs.try_inject(58, "C") is True
        with live_runs.queued(58):
            assert await live_runs.try_inject(58, "D") is False
        assert await live_runs.try_inject(58, "E") is True
    finally:
        live_runs.unregister(58, run)
    assert runner.injected == ["C", "E"]


async def test_a_cancelled_run_still_reports_unread_messages() -> None:
    import asyncio

    surface = MemorySurface()
    started = asyncio.Event()

    class _Hangs(_Runner):
        async def run(self, prompt: str, session_id: str | None = None) -> AsyncGenerator:
            started.set()
            await asyncio.Event().wait()
            yield  # pragma: no cover

    runner = _Hangs([], unread=["do Y too"])
    task = asyncio.create_task(
        run_claude_with_config(RunConfig(surface=surface, runner=runner, prompt="go"))
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert live_runs.get(surface.thread_key) is None
    notice = next(n for n in surface.notices if n.title == "Not read by the agent")
    assert "do Y too" in (notice.body or "")


async def test_unread_images_ride_along_with_a_continuation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import claude_discord.cogs._run_helper as helper
    from claude_code_core.types import ImageData

    picture = ImageData(data="aGk=", media_type="image/png")

    class _ImageRunner(_Runner):
        def unconsumed_messages(self) -> list[InjectedMessage]:
            unread, self._unread = self._unread, []
            return [InjectedMessage(uuid="1", text="", images=(picture,)) for _ in unread]

    surface = MemorySurface()
    runner = _ImageRunner([_result()], unread=["image only"])
    slot = SimpleNamespace(pause_requested=True, on_pause=None)
    configs: list[Any] = []

    async def _acquire(config: Any, _slots: Any) -> Any:
        configs.append(config)
        return slot if len(configs) == 1 else None

    monkeypatch.setattr(
        helper, "get_session_slots", lambda: SimpleNamespace(release=lambda _s: None)
    )
    monkeypatch.setattr(helper, "_acquire_slot", _acquire)

    await run_claude_with_config(RunConfig(surface=surface, runner=runner, prompt="go"))

    assert configs[1].images == [picture]
    assert "image" in runner.prompts[1]


async def test_an_empty_second_turn_does_not_inherit_the_first_answer() -> None:
    surface = MemorySurface()
    processor = EventProcessor(RunConfig(surface=surface, runner=_Runner([]), prompt="go"))

    await processor.process(_text("FIRST ANSWER"))
    await processor.process(_result("FIRST ANSWER", turn_continues=True))
    await processor.process(
        StreamEvent(message_type=MessageType.USER, injected_text="and?", replay_uuid="u")
    )
    await processor.process(_result(""))

    assert processor.final_assistant_text != "FIRST ANSWER"
