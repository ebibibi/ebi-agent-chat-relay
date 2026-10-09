"""SessionSlotScheduler: the concurrency limit with a reorderable queue."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from claude_discord.session_slots import (
    SessionSlotScheduler,
    SlotActionError,
    SlotPriority,
)


async def _queued(slots: SessionSlotScheduler, key: int, **kwargs) -> asyncio.Task:
    task = asyncio.ensure_future(slots.acquire(key, **kwargs))
    await asyncio.sleep(0)
    return task


def test_rejects_zero_slots() -> None:
    with pytest.raises(ValueError):
        SessionSlotScheduler(0)


async def test_starts_immediately_while_slots_are_free() -> None:
    slots = SessionSlotScheduler(2)
    assert not slots.would_wait()
    entry = await slots.acquire(1)
    assert entry.running
    assert slots.running_count == 1


async def test_waiters_start_in_arrival_order() -> None:
    slots = SessionSlotScheduler(1)
    first = await slots.acquire(1)
    a = await _queued(slots, 2)
    b = await _queued(slots, 3)
    assert slots.would_wait()
    slots.release(first)
    await asyncio.sleep(0)
    assert a.done() and not b.done()


async def test_prioritize_jumps_the_queue() -> None:
    slots = SessionSlotScheduler(1)
    first = await slots.acquire(1)
    a = await _queued(slots, 2)
    b = await _queued(slots, 3)
    slots.prioritize(3)
    assert slots.position(3) == 1
    slots.release(first)
    await asyncio.sleep(0)
    assert b.done() and not a.done()


async def test_defer_lets_later_arrivals_go_first_and_still_starts() -> None:
    slots = SessionSlotScheduler(1)
    first = await slots.acquire(1)
    a = await _queued(slots, 2)
    b = await _queued(slots, 3)
    slots.defer(2)
    assert slots.position(2) == 2
    slots.release(first)
    await asyncio.sleep(0)
    assert b.done() and not a.done()
    slots.release(b.result())
    await asyncio.sleep(0)
    assert a.done()


async def test_deferred_waiter_does_not_block_normal_arrival() -> None:
    slots = SessionSlotScheduler(1)
    first = await slots.acquire(1)
    await _queued(slots, 2, priority=SlotPriority.DEFERRED)
    slots.release(first)
    await asyncio.sleep(0)
    # The deferred one took the free slot; nothing is ahead of a new normal run
    # other than the running count.
    assert slots.running_count == 1


async def test_prioritize_requires_a_waiting_thread() -> None:
    slots = SessionSlotScheduler(1)
    await slots.acquire(1)
    with pytest.raises(SlotActionError) as running:
        slots.prioritize(1)
    assert running.value.reason == "already_running"
    with pytest.raises(SlotActionError) as unknown:
        slots.defer(99)
    assert unknown.value.reason == "not_queued"


async def test_pause_interrupts_and_flags_the_running_entry() -> None:
    slots = SessionSlotScheduler(1)
    entry = await slots.acquire(1)
    entry.on_pause = AsyncMock()
    await _queued(slots, 2)
    await slots.pause(1)
    entry.on_pause.assert_awaited_once()
    assert entry.pause_requested
    with pytest.raises(SlotActionError) as again:
        await slots.pause(1)
    assert again.value.reason == "already_pausing"


async def test_pause_is_refused_when_nobody_waits() -> None:
    slots = SessionSlotScheduler(1)
    entry = await slots.acquire(1)
    entry.on_pause = AsyncMock()
    with pytest.raises(SlotActionError) as exc:
        await slots.pause(1)
    assert exc.value.reason == "nothing_waiting"
    entry.on_pause.assert_not_awaited()
    assert not entry.pause_requested


async def test_pause_requires_a_running_pausable_thread() -> None:
    slots = SessionSlotScheduler(1)
    await slots.acquire(1)
    await _queued(slots, 2)
    with pytest.raises(SlotActionError) as waiting:
        await slots.pause(2)
    assert waiting.value.reason == "not_running"
    with pytest.raises(SlotActionError) as no_handler:
        await slots.pause(1)
    assert no_handler.value.reason == "not_pausable"


async def test_cancelled_waiter_leaves_the_queue() -> None:
    slots = SessionSlotScheduler(1)
    first = await slots.acquire(1)
    waiter = await _queued(slots, 2)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert slots.position(2) is None
    slots.release(first)
    assert slots.running_count == 0


async def test_snapshot_lists_running_then_queue_order() -> None:
    slots = SessionSlotScheduler(1)
    await slots.acquire(1, label="busy")
    await _queued(slots, 2, label="a")
    await _queued(slots, 3, label="b")
    slots.prioritize(3)
    snap = [i.as_dict() for i in slots.snapshot()]
    assert [(s["thread_id"], s["state"], s["position"]) for s in snap] == [
        ("1", "running", None),
        ("3", "waiting", 1),
        ("2", "waiting", 2),
    ]
    assert snap[1]["priority"] == "prioritized"
