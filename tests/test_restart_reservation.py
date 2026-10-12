"""Reserved restart: hold the queue, wait for running sessions, keep the queue."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.ext.api_server import ApiServer
from claude_discord.restart_reservation import (
    QUEUED_RESTART_REASON,
    RestartReservation,
    build_restart_action,
    get_restart_reservation,
    persist_queued_runs,
    set_restart_reservation,
)
from claude_discord.session_slots import (
    SessionSlotScheduler,
    SlotActionError,
    get_session_slots,
    set_session_slots,
)


async def _queued(slots: SessionSlotScheduler, key: int, **kwargs) -> asyncio.Task:
    task = asyncio.ensure_future(slots.acquire(key, **kwargs))
    await asyncio.sleep(0)
    return task


async def _until(predicate, *, limit: int = 200) -> None:
    for _ in range(limit):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition never became true")


# --- scheduler hold --------------------------------------------------------


async def test_a_held_queue_starts_nobody_even_with_free_slots() -> None:
    slots = SessionSlotScheduler(3)
    slots.hold()
    assert slots.would_wait()
    waiter = await _queued(slots, 1, resume_prompt="hi")
    assert not waiter.done()
    assert slots.is_waiting(1)

    slots.unhold()
    await asyncio.sleep(0)
    assert waiter.done()
    assert slots.running_count == 1


async def test_hold_does_not_touch_running_sessions() -> None:
    slots = SessionSlotScheduler(1)
    running = await slots.acquire(1)
    slots.hold()
    waiter = await _queued(slots, 2)
    slots.release(running)
    await asyncio.sleep(0)
    assert not waiter.done()
    assert slots.running_count == 0


async def test_pause_is_refused_while_held() -> None:
    slots = SessionSlotScheduler(1)
    running = await slots.acquire(1)
    running.on_pause = AsyncMock()
    await _queued(slots, 2)
    slots.hold()
    with pytest.raises(SlotActionError) as exc:
        await slots.pause(1)
    assert exc.value.reason == "held"
    running.on_pause.assert_not_called()


async def test_waiting_entries_keep_prompt_and_start_order() -> None:
    slots = SessionSlotScheduler(1)
    slots.hold()
    await _queued(slots, 1, resume_prompt="first")
    await _queued(slots, 2, resume_prompt="second")
    slots.prioritize(2)
    assert [(e.thread_key, e.resume_prompt) for e in slots.waiting_entries()] == [
        (2, "second"),
        (1, "first"),
    ]


# --- persisting the queue ---------------------------------------------------


async def test_persist_writes_each_queued_prompt_in_queue_order() -> None:
    slots = SessionSlotScheduler(1)
    slots.hold()
    await _queued(slots, 10, resume_prompt="a")
    await _queued(slots, 20)  # nothing to replay
    await _queued(slots, 30, resume_prompt="c")
    repo = MagicMock()
    repo.mark = AsyncMock(return_value=1)

    assert await persist_queued_runs(slots, repo) == 2
    assert [(c.args[0], c.kwargs) for c in repo.mark.call_args_list] == [
        (10, {"reason": QUEUED_RESTART_REASON, "resume_prompt": "a"}),
        (30, {"reason": QUEUED_RESTART_REASON, "resume_prompt": "c"}),
    ]


async def test_persist_survives_a_failing_row_and_no_repo() -> None:
    slots = SessionSlotScheduler(1)
    slots.hold()
    await _queued(slots, 1, resume_prompt="a")
    await _queued(slots, 2, resume_prompt="b")
    repo = MagicMock()
    repo.mark = AsyncMock(side_effect=[RuntimeError("disk"), 1])
    assert await persist_queued_runs(slots, repo) == 1
    assert await persist_queued_runs(slots, None) == 0


# --- the reservation ----------------------------------------------------------


async def test_restarts_only_after_running_sessions_finish_and_saves_queue_first() -> None:
    slots = SessionSlotScheduler(2)
    running = await slots.acquire(1)
    order: list[str] = []

    async def persist() -> int:
        order.append("persist")
        return 0

    async def restart() -> None:
        order.append("restart")

    reservation = RestartReservation(slots, restart, persist=persist, poll_interval=0.005)
    assert reservation.reserve(requested_by="tester", reason="deploy")
    assert slots.held

    # A request arriving now waits, even though a slot is free.
    late = await _queued(slots, 2, resume_prompt="late")
    await asyncio.sleep(0.03)
    assert order == [] and not late.done()
    assert reservation.status()["phase"] == "draining"

    slots.release(running)
    await _until(lambda: order == ["persist", "restart"])
    assert not late.done()


async def test_a_second_reserve_is_reported_not_repeated() -> None:
    slots = SessionSlotScheduler(1)
    await slots.acquire(1)
    reservation = RestartReservation(slots, AsyncMock(), poll_interval=0.005)
    assert reservation.reserve()
    assert not reservation.reserve()
    reservation.cancel()


async def test_cancel_releases_the_queue() -> None:
    slots = SessionSlotScheduler(2)
    await slots.acquire(1)
    restart = AsyncMock()
    reservation = RestartReservation(slots, restart, poll_interval=0.005)
    reservation.reserve()
    waiter = await _queued(slots, 2)

    assert reservation.cancel()
    await asyncio.sleep(0)
    assert waiter.done()
    assert not slots.held and not reservation.reserved
    await asyncio.sleep(0.02)
    restart.assert_not_called()
    assert not reservation.cancel()


async def test_a_failed_restart_releases_the_queue_and_reports_why() -> None:
    slots = SessionSlotScheduler(1)
    restart = AsyncMock(side_effect=RuntimeError("no sudo"))
    reservation = RestartReservation(slots, restart, poll_interval=0.005)
    reservation.reserve()
    await _until(lambda: not reservation.reserved)
    assert not slots.held
    status = reservation.status()
    assert status["phase"] == "idle"
    assert "no sudo" in str(status["last_error"])


# --- restart action -------------------------------------------------------------


async def test_without_a_command_the_bot_closes_for_its_supervisor() -> None:
    bot = MagicMock()
    bot.close = AsyncMock()
    await build_restart_action(bot, command="")()
    bot.close.assert_awaited_once()


async def test_a_configured_command_runs_and_its_failure_is_raised() -> None:
    bot = MagicMock()
    bot.close = AsyncMock()
    await build_restart_action(bot, command="true")()
    with pytest.raises(RuntimeError):
        await build_restart_action(bot, command="false")()
    bot.close.assert_not_called()


# --- REST API ---------------------------------------------------------------------


@pytest.fixture
def slots():
    original_slots = get_session_slots()
    original_reservation = get_restart_reservation()
    scheduler = SessionSlotScheduler(1)
    set_session_slots(scheduler)
    set_restart_reservation(None)
    yield scheduler
    reservation = get_restart_reservation()
    if reservation is not None and reservation.reserved:
        reservation.cancel()
    set_session_slots(original_slots)
    set_restart_reservation(original_reservation)


async def test_api_reserve_status_and_cancel(slots) -> None:
    await slots.acquire(1)  # keeps the reservation draining
    api = ApiServer(repo=MagicMock(), bot=MagicMock())
    async with TestClient(TestServer(api.app)) as client:
        idle = await (await client.get("/api/restart")).json()
        assert idle["reserved"] is False and idle["running"] == 1

        first = await client.post("/api/restart", json={"reason": "deploy v2"})
        assert first.status == 202
        body = await first.json()
        assert body["status"] == "reserved" and body["reason"] == "deploy v2"

        again = await client.post("/api/restart")
        assert again.status == 200
        assert (await again.json())["status"] == "already_reserved"

        state = await (await client.get("/api/restart")).json()
        assert state["reserved"] is True and state["phase"] == "draining"
        assert (await (await client.get("/api/slots")).json())["held"] is True

        cancelled = await client.delete("/api/restart")
        assert cancelled.status == 200
        assert (await client.delete("/api/restart")).status == 404
    assert not slots.held


async def test_api_rejects_a_non_object_body(slots) -> None:
    api = ApiServer(repo=MagicMock(), bot=MagicMock())
    async with TestClient(TestServer(api.app)) as client:
        response = await client.post("/api/restart", json=["x"])
        assert response.status == 400


async def test_api_resumes_the_requesting_thread_after_restart(slots) -> None:
    from claude_discord.restart_reservation import RESERVED_RESTART_REASON

    resume_repo = MagicMock()
    resume_repo.mark = AsyncMock(return_value=1)
    bot = MagicMock()
    bot.close = AsyncMock()
    api = ApiServer(repo=MagicMock(), bot=bot, resume_repo=resume_repo)
    slots.hold()
    asyncio.ensure_future(slots.acquire(9, resume_prompt="queued"))
    await asyncio.sleep(0)
    async with TestClient(TestServer(api.app)) as client:
        response = await client.post(
            "/api/restart",
            json={"resume_thread_id": "42", "resume_prompt": "verify the deploy"},
        )
        assert response.status == 202
        await _until(lambda: bot.close.await_count == 1)
    marks = [(c.args[0], c.kwargs["reason"]) for c in resume_repo.mark.call_args_list]
    assert marks == [(9, QUEUED_RESTART_REASON), (42, RESERVED_RESTART_REASON)]
    assert resume_repo.mark.call_args.kwargs["resume_prompt"] == "verify the deploy"


async def test_api_rejects_a_bad_resume_thread_id(slots) -> None:
    api = ApiServer(repo=MagicMock(), bot=MagicMock())
    async with TestClient(TestServer(api.app)) as client:
        response = await client.post("/api/restart", json={"resume_thread_id": "abc"})
        assert response.status == 400
