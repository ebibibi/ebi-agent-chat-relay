"""``/console/api/restart``: show a pending restart, restart now or later."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.console.restart_routes import RestartRoutes
from claude_discord.restart_reservation import (
    RestartReservation,
    get_restart_reservation,
    set_restart_reservation,
)
from claude_discord.session_slots import SessionSlotScheduler


@pytest.fixture
def reservation():
    original = get_restart_reservation()
    clock = {"now": 1000.0}
    res = RestartReservation(
        SessionSlotScheduler(1),
        AsyncMock(),
        poll_interval=0.002,
        quiet_seconds=900,
        last_activity=lambda: 1000.0,
        clock=lambda: clock["now"],
    )
    set_restart_reservation(res)
    yield res
    if res.reserved:
        res.cancel()
    set_restart_reservation(original)


def _app() -> web.Application:
    app = web.Application()
    RestartRoutes(lambda request: "me@example.com").register(app.router, "/console/api")
    return app


async def test_nothing_reserved() -> None:
    original = get_restart_reservation()
    set_restart_reservation(None)
    try:
        async with TestClient(TestServer(_app())) as client:
            assert await (await client.get("/console/api/restart")).json() == {"reserved": False}
            assert (await client.post("/console/api/restart/now")).status == 404
    finally:
        set_restart_reservation(original)


async def test_shows_and_postpones_then_restarts_now(reservation) -> None:
    reservation.reserve(requested_by="auto-upgrade", reason="1 file(s) need a restart")
    async with TestClient(TestServer(_app())) as client:
        state = await (await client.get("/console/api/restart")).json()
        assert state["reserved"] and state["phase"] == "waiting_for_quiet"
        assert state["quiet_at"] == 1900

        later = await client.post("/console/api/restart/later", json={"minutes": 30})
        assert later.status == 200
        assert (await later.json())["not_before"] == 1000 + 1800

        for bad in (0, "x", True, 99999):
            response = await client.post("/console/api/restart/later", json={"minutes": bad})
            assert response.status == 400

        now = await client.post("/console/api/restart/now")
        assert now.status == 200
        assert (await now.json())["urgent"] is True
