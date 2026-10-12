"""Reserved restart: hold the queue, wait for running sessions, keep the queue."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.ext.api_server import ApiServer
from claude_discord.restart_reservation import (
    QUEUED_RESTART_REASON,
    RESERVED_RESTART_REASON,
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

    assert await persist_queued_runs(slots, repo) == [10, 30]
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
    assert await persist_queued_runs(slots, repo) == [2]
    assert await persist_queued_runs(slots, None) == []


# --- the reservation ----------------------------------------------------------


def _resume_repo() -> MagicMock:
    repo = MagicMock()
    repo.mark = AsyncMock(return_value=1)
    repo.delete_by_thread = AsyncMock()
    return repo


async def test_restarts_only_after_running_sessions_finish_and_saves_queue_first() -> None:
    slots = SessionSlotScheduler(2)
    running = await slots.acquire(1)
    order: list[str] = []
    repo = _resume_repo()
    repo.mark.side_effect = lambda *a, **k: order.append(f"save {a[0]}")

    async def restart() -> None:
        order.append("restart")

    reservation = RestartReservation(slots, restart, resume_repo=repo, poll_interval=0.005)
    assert reservation.reserve(requested_by="tester", reason="deploy")
    assert slots.held

    # A request arriving now waits, even though a slot is free.
    late = await _queued(slots, 2, resume_prompt="late")
    await asyncio.sleep(0.03)
    assert order == [] and not late.done()
    assert reservation.status()["phase"] == "draining"

    slots.release(running)
    await _until(lambda: order == ["save 2", "restart"])
    assert not late.done()


async def test_the_requesting_thread_is_resumed_unless_it_has_a_queued_message() -> None:
    slots = SessionSlotScheduler(1)
    slots.hold()
    await _queued(slots, 5, resume_prompt="the user's follow-up")
    repo = _resume_repo()
    reservation = RestartReservation(slots, AsyncMock(), resume_repo=repo, poll_interval=0.005)

    reservation.reserve(resume_thread_id=5, resume_prompt="verify")
    await _until(lambda: not reservation.reserved)
    # One row per thread: the queued message wins, the verify prompt is not written over it.
    assert [(c.args[0], c.kwargs["reason"]) for c in repo.mark.call_args_list] == [
        (5, QUEUED_RESTART_REASON)
    ]

    repo.mark.reset_mock()
    reservation.reserve(resume_thread_id=6, resume_prompt="verify")
    await _until(lambda: not reservation.reserved)
    assert [(c.args[0], c.kwargs["reason"]) for c in repo.mark.call_args_list] == [
        (5, QUEUED_RESTART_REASON),
        (6, RESERVED_RESTART_REASON),
    ]


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


async def test_a_failed_restart_releases_the_queue_and_drops_saved_rows() -> None:
    slots = SessionSlotScheduler(1)
    slots.hold()
    waiter = await _queued(slots, 4, resume_prompt="queued")

    repo = _resume_repo()
    restart = AsyncMock(side_effect=RuntimeError("no sudo"))
    reservation = RestartReservation(slots, restart, resume_repo=repo, poll_interval=0.005)
    reservation.reserve(resume_thread_id=8)
    await _until(lambda: not reservation.reserved)

    assert not slots.held
    status = reservation.status()
    assert status["phase"] == "idle"
    assert "no sudo" in str(status["last_error"])
    # The queue runs in this process now; a later restart must not replay it again.
    dropped = sorted(c.args[0] for c in repo.delete_by_thread.call_args_list)
    saved = sorted(c.args[0] for c in repo.mark.call_args_list)
    assert dropped == saved == [4, 8]
    await asyncio.sleep(0)
    assert waiter.done()


async def test_gives_up_after_max_wait_and_releases_the_queue() -> None:
    slots = SessionSlotScheduler(1)
    await slots.acquire(1)
    restart = AsyncMock()
    reservation = RestartReservation(slots, restart, poll_interval=0.005)
    reservation.reserve(max_wait_seconds=0.02)
    await _until(lambda: not reservation.reserved)
    assert not slots.held
    assert "timed out" in str(reservation.status()["last_error"])
    restart.assert_not_called()


# --- restart action -------------------------------------------------------------


async def test_without_a_command_the_bot_closes_for_its_supervisor() -> None:
    bot = MagicMock()
    bot.close = AsyncMock()
    await build_restart_action(bot, command="")()
    bot.close.assert_awaited_once()


async def test_a_command_that_does_not_end_this_process_is_a_failure() -> None:
    bot = MagicMock()
    bot.close = AsyncMock()
    with pytest.raises(RuntimeError, match="exited with 1"):
        await build_restart_action(bot, command="false", grace_seconds=1)()
    with pytest.raises(RuntimeError, match="not restarted"):
        await build_restart_action(bot, command="true", grace_seconds=0.01)()
    with pytest.raises(RuntimeError, match="still running"):
        await build_restart_action(bot, command="sleep 5", grace_seconds=0.05)()
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


async def test_api_rejects_a_bad_max_wait(slots) -> None:
    api = ApiServer(repo=MagicMock(), bot=MagicMock())
    async with TestClient(TestServer(api.app)) as client:
        for bad in (0, -5, "soon"):
            response = await client.post("/api/restart", json={"max_wait_seconds": bad})
            assert response.status == 400
