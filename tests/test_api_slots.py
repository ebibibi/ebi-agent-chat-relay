"""GET /api/slots and POST /api/slots/{thread_id}/{action}, plus the /queue command."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.cogs.session_queue import SessionQueueCog
from claude_discord.ext.api_server import ApiServer
from claude_discord.session_slots import (
    SessionSlotScheduler,
    get_session_slots,
    set_session_slots,
)


@pytest.fixture
def slots():
    original = get_session_slots()
    scheduler = SessionSlotScheduler(1)
    set_session_slots(scheduler)
    yield scheduler
    set_session_slots(original)


def _api() -> ApiServer:
    return ApiServer(repo=MagicMock(), bot=MagicMock())


async def _fill(slots: SessionSlotScheduler) -> None:
    running = await slots.acquire(1, label="busy")
    running.on_pause = AsyncMock()
    for key in (2, 3):
        asyncio.ensure_future(slots.acquire(key, label=f"t{key}"))
    await asyncio.sleep(0)


async def test_list_slots_reports_running_and_queue(slots) -> None:
    await _fill(slots)
    async with TestClient(TestServer(_api().app)) as client:
        body = await (await client.get("/api/slots")).json()
    assert body["max_slots"] == 1
    assert [r["thread_id"] for r in body["running"]] == ["1"]
    assert [(w["thread_id"], w["position"]) for w in body["waiting"]] == [("2", 1), ("3", 2)]


async def test_prioritize_moves_thread_to_front(slots) -> None:
    await _fill(slots)
    async with TestClient(TestServer(_api().app)) as client:
        response = await client.post("/api/slots/3/prioritize")
        assert response.status == 200
        assert (await response.json())["position"] == 1


async def test_pause_interrupts_running_thread(slots) -> None:
    await _fill(slots)
    async with TestClient(TestServer(_api().app)) as client:
        response = await client.post("/api/slots/1/pause")
    assert response.status == 200
    assert slots.snapshot()[0].pause_requested


async def test_errors_map_to_status_codes(slots) -> None:
    await slots.acquire(1)
    async with TestClient(TestServer(_api().app)) as client:
        assert (await client.post("/api/slots/1/explode")).status == 400
        assert (await client.post("/api/slots/abc/defer")).status == 400
        assert (await client.post("/api/slots/9/defer")).status == 404
        conflict = await client.post("/api/slots/1/prioritize")
        assert conflict.status == 409
        assert (await conflict.json())["reason"] == "already_running"
        nothing_waiting = await client.post("/api/slots/1/pause")
        assert nothing_waiting.status == 409


async def test_list_slots_without_a_limit() -> None:
    original = get_session_slots()
    set_session_slots(None)
    try:
        async with TestClient(TestServer(_api().app)) as client:
            body = await (await client.get("/api/slots")).json()
            assert body == {"max_slots": None, "running": [], "waiting": []}
            assert (await client.post("/api/slots/1/pause")).status == 503
    finally:
        set_session_slots(original)


def _interaction(user_id: int) -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = user_id
    interaction.response.send_message = AsyncMock()
    return interaction


async def test_queue_command_shows_controls(slots) -> None:
    await _fill(slots)
    cog = SessionQueueCog(MagicMock())
    interaction = _interaction(1)
    await cog.queue_command.callback(cog, interaction)
    kwargs = interaction.response.send_message.call_args.kwargs
    assert kwargs["ephemeral"] is True
    assert "1/1 running" in kwargs["embed"].title
    # prioritize + defer for the queue, pause for the running thread
    assert len(kwargs["view"].children) == 3


async def test_queue_command_honours_allowed_users(slots) -> None:
    cog = SessionQueueCog(MagicMock(), allowed_user_ids={42})
    interaction = _interaction(7)
    await cog.queue_command.callback(cog, interaction)
    args = interaction.response.send_message.call_args
    assert "permission" in args.args[0]


async def test_wait_buttons_refuse_users_outside_allowed_ids() -> None:
    from claude_discord.discord_ui.slot_views import SlotWaitView

    scheduler = SessionSlotScheduler(1, allowed_user_ids={42})
    await scheduler.acquire(1)
    asyncio.ensure_future(scheduler.acquire(2))
    asyncio.ensure_future(scheduler.acquire(3))
    await asyncio.sleep(0)
    view = SlotWaitView(scheduler, 3)

    stranger = _interaction(7)
    await view.prioritize_button.callback(stranger)
    assert "permission" in stranger.response.send_message.call_args.args[0]
    assert scheduler.position(3) == 2

    owner = _interaction(42)
    await view.prioritize_button.callback(owner)
    assert scheduler.position(3) == 1
