"""Changing the session limit at runtime, and stopping a run for good."""

from __future__ import annotations

import asyncio

import pytest

from claude_discord.session_slots import SessionSlotScheduler, SlotActionError


async def _run(slots: SessionSlotScheduler, key: int):
    entry = await slots.acquire(key, label=f"t{key}")
    return entry


async def test_raising_the_limit_starts_waiting_runs() -> None:
    slots = SessionSlotScheduler(1)
    first = await _run(slots, 1)
    second = asyncio.ensure_future(_run(slots, 2))
    await asyncio.sleep(0)
    assert not second.done() and slots.is_waiting(2)
    slots.set_max_slots(2)
    await asyncio.sleep(0)
    assert second.done() and slots.is_running(2)
    slots.release(first)
    slots.release(second.result())


async def test_lowering_the_limit_never_interrupts_running_runs() -> None:
    slots = SessionSlotScheduler(2)
    a = await _run(slots, 1)
    b = await _run(slots, 2)
    slots.set_max_slots(1)
    assert slots.running_count == 2  # both keep running
    third = asyncio.ensure_future(_run(slots, 3))
    await asyncio.sleep(0)
    slots.release(a)
    await asyncio.sleep(0)
    assert not third.done()  # still over the new limit
    slots.release(b)
    await asyncio.sleep(0)
    assert third.done()
    slots.release(third.result())


@pytest.mark.parametrize("bad", [0, 33, -1, True, "4", 2.5])
def test_a_bad_limit_is_refused(bad) -> None:
    slots = SessionSlotScheduler(4)
    with pytest.raises(ValueError):
        slots.set_max_slots(bad)
    assert slots.max_slots == 4


async def test_stop_interrupts_without_requeueing() -> None:
    slots = SessionSlotScheduler(2)
    entry = await _run(slots, 1)
    calls: list[str] = []

    async def interrupt() -> None:
        calls.append("interrupt")

    entry.on_pause = interrupt
    await slots.stop(1)
    assert calls == ["interrupt"] and entry.pause_requested is False
    with pytest.raises(SlotActionError):
        await slots.stop(99)
    bare = await _run(slots, 2)
    with pytest.raises(SlotActionError):
        await slots.stop(2)  # no interrupt registered
    slots.release(entry)
    slots.release(bare)
