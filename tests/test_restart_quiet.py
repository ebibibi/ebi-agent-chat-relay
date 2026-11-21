"""A reserved restart waits until nobody has used the bot for a while."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from claude_discord.restart_reservation import RestartReservation, quiet_seconds_from_env
from claude_discord.session_slots import SessionSlotScheduler


class _World:
    """A clock and a last-human-activity time the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1000.0
        self.last: float | None = None

    def clock(self) -> float:
        return self.now

    def activity(self) -> float | None:
        return self.last

    def act(self) -> None:
        self.last = self.now


def _reservation(slots, world, restart, quiet=900.0) -> RestartReservation:
    return RestartReservation(
        slots,
        restart,
        poll_interval=0.002,
        quiet_seconds=quiet,
        last_activity=world.activity,
        clock=world.clock,
    )


async def _settle() -> None:
    await asyncio.sleep(0.02)


async def test_waits_for_quiet_without_holding_the_queue() -> None:
    slots = SessionSlotScheduler(2)
    world = _World()
    world.act()
    restart = AsyncMock()
    reservation = _reservation(slots, world, restart)

    assert reservation.reserve(requested_by="t", reason="deploy")
    await _settle()
    assert reservation.status()["phase"] == "waiting_for_quiet"
    assert not slots.held
    # New work still starts while waiting.
    lease = await asyncio.wait_for(slots.acquire(1), 0.1)
    slots.release(lease)

    world.now += 899
    await _settle()
    restart.assert_not_called()

    world.now += 2
    await _settle()
    restart.assert_awaited_once()


async def test_a_person_acting_during_the_drain_releases_the_hold() -> None:
    slots = SessionSlotScheduler(2)
    world = _World()
    running = await slots.acquire(1)
    restart = AsyncMock()
    reservation = _reservation(slots, world, restart)

    reservation.reserve()
    assert slots.held and reservation.status()["phase"] == "draining"

    world.now += 1
    world.act()  # the person sends the next message while a turn is finishing
    await _settle()
    assert not slots.held
    assert reservation.status()["phase"] == "waiting_for_quiet"
    texts = [e["text"] for e in reservation.status()["events"]]
    assert any("someone is using the bot" in t for t in texts)

    slots.release(running)
    await _settle()
    restart.assert_not_called()  # still within the quiet period

    world.now += 901
    await _settle()
    restart.assert_awaited_once()


async def test_urgent_skips_the_quiet_wait_and_is_not_interrupted() -> None:
    slots = SessionSlotScheduler(2)
    world = _World()
    world.act()
    running = await slots.acquire(1)
    restart = AsyncMock()
    reservation = _reservation(slots, world, restart)

    reservation.reserve(urgent=True, reason="security fix")
    assert slots.held
    world.now += 1
    world.act()
    await _settle()
    assert slots.held  # not released by activity
    slots.release(running)
    await _settle()
    restart.assert_awaited_once()


async def test_second_request_joins_and_can_make_it_urgent() -> None:
    slots = SessionSlotScheduler(2)
    world = _World()
    world.act()
    restart = AsyncMock()
    reservation = _reservation(slots, world, restart)

    assert reservation.reserve(requested_by="a")
    assert not reservation.reserve(requested_by="b")
    assert reservation.status()["requests"] == 2
    await _settle()
    restart.assert_not_called()

    assert not reservation.reserve(requested_by="c", urgent=True)
    await _settle()
    restart.assert_awaited_once()


async def test_restart_now_and_postpone() -> None:
    slots = SessionSlotScheduler(2)
    world = _World()
    world.act()
    restart = AsyncMock()
    reservation = _reservation(slots, world, restart)
    reservation.reserve()

    assert reservation.postpone(3600, requested_by="me")
    world.now += 901  # quiet, but postponed
    await _settle()
    restart.assert_not_called()
    assert reservation.status()["not_before"] == 1000 + 3600

    assert reservation.restart_now(requested_by="me")
    await _settle()
    restart.assert_awaited_once()


async def test_postpone_while_draining_releases_the_queue() -> None:
    slots = SessionSlotScheduler(1)
    world = _World()
    running = await slots.acquire(1)
    restart = AsyncMock()
    reservation = _reservation(slots, world, restart)
    reservation.reserve()
    assert slots.held

    reservation.postpone(600)
    assert not slots.held
    slots.release(running)
    await _settle()
    restart.assert_not_called()
    world.now += 601
    await _settle()
    restart.assert_awaited_once()


async def test_nothing_to_answer_when_nothing_is_reserved() -> None:
    reservation = _reservation(SessionSlotScheduler(1), _World(), AsyncMock())
    assert not reservation.restart_now()
    assert not reservation.postpone(60)


async def test_every_requester_is_resumed() -> None:
    slots = SessionSlotScheduler(1)
    world = _World()
    world.act()
    repo = AsyncMock()
    reservation = RestartReservation(
        slots,
        AsyncMock(),
        resume_repo=repo,
        poll_interval=0.002,
        quiet_seconds=10,
        last_activity=world.activity,
        clock=world.clock,
    )
    reservation.reserve(resume_thread_id=1, resume_prompt="check 1")
    reservation.reserve(resume_thread_id=2, resume_prompt="check 2")
    world.now += 11
    await _settle()
    assert sorted(c.args[0] for c in repo.mark.call_args_list) == [1, 2]


def test_quiet_seconds_from_env(monkeypatch) -> None:
    monkeypatch.delenv("CCDB_RESTART_QUIET_SECONDS", raising=False)
    assert quiet_seconds_from_env() == 900
    monkeypatch.setenv("CCDB_RESTART_QUIET_SECONDS", "60")
    assert quiet_seconds_from_env() == 60
    monkeypatch.setenv("CCDB_RESTART_QUIET_SECONDS", "soon")
    assert quiet_seconds_from_env() == 900
