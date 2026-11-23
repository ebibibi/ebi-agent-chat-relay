"""The night settings and the morning brief on the control plane (#1476)."""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.console.console_state import ConsoleStateRepository
from claude_discord.console.night import STATE_NIGHT
from claude_discord.console.night_desk import NightDesk
from claude_discord.database.models import init_db
from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.ext.api_server import ApiServer


def _item(item_id: str, status: str = "waiting", **extra: Any) -> dict[str, Any]:
    return {
        "id": item_id,
        "thread_id": item_id[1:],
        "title": f"Thread {item_id}",
        "status": status,
        "running": False,
        "last_activity_at": "2026-11-14T10:00:00Z",
        **extra,
    }


@pytest.fixture
async def api():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(path)
    notifications = NotificationRepository(path)
    await notifications.init_db()
    bot = MagicMock()
    bot.cogs = {}
    server = ApiServer(repo=notifications, bot=bot, default_channel_id=1, host="127.0.0.1", port=0)
    store = ConsoleStateRepository(path)
    await store.init_db()

    async def board() -> list[dict[str, Any]]:
        return [_item("t1"), _item("t2", "running", running=True)]

    async def requests() -> list[dict[str, Any]]:
        return []

    console = MagicMock()
    console.night_desk = NightDesk(
        store,
        board=board,
        requests=requests,
        clock=lambda: datetime(2026, 11, 14, 12, tzinfo=UTC),
    )
    client = TestClient(TestServer(server.app))
    await client.start_server()
    yield client, server, console, store
    await client.close()
    os.unlink(path)


async def test_morning_brief_and_night_settings_on_the_control_plane(api) -> None:
    """The brief reads the board; the night settings are off until set."""
    client, server, console, _ = api
    assert (await client.get("/api/secretary/morning")).status == 503
    server._console = console
    resp = await client.get("/api/secretary/morning?hours=12&base=https://c.example")
    brief = await resp.json()
    assert resp.status == 200 and brief["hours"] == 12
    assert {"done", "decide", "continuing", "markdown"} <= brief.keys()
    assert brief["continuing"][0]["link"] == "https://c.example/#focus/t2"
    md = await client.get("/api/secretary/morning?format=markdown&lang=en")
    assert md.content_type == "text/markdown" and "Morning brief" in await md.text()
    bad = await client.get("/api/secretary/morning?base=javascript:alert(1)")
    assert bad.status == 400

    night = await (await client.get("/api/secretary/night")).json()
    assert night["quiet_enabled"] is False
    put = await client.put(
        "/api/secretary/night",
        json={"quiet_start": "23:00", "quiet_end": "07:00", "urgent_statuses": ["error"]},
    )
    assert put.status == 200 and (await put.json())["quiet_enabled"] is True
    refused = await client.put("/api/secretary/night", json={"quiet_start": "7pm"})
    assert refused.status == 400 and (await refused.json())["problems"]
    kept = await (await client.get("/api/secretary/night")).json()
    assert kept["quiet_start"] == "23:00" and kept["urgent_statuses"] == ["error"]
    bad_json = await client.put(
        "/api/secretary/night", data=b"{", headers={"Content-Type": "application/json"}
    )
    assert bad_json.status == 400


async def test_night_settings_written_by_canvas_mode_are_still_read(api) -> None:
    """The settings stayed in the old canvas_state table: retiring canvas mode loses nothing."""
    client, server, console, store = api
    await store.set_state(STATE_NIGHT, '{"quiet_start": "22:00", "quiet_end": "06:00"}')
    await store.set_state("secretary_thread", "123")  # a canvas-mode row, ignored now
    server._console = console
    night = await (await client.get("/api/secretary/night")).json()
    assert night["quiet_start"] == "22:00" and night["quiet_enabled"] is True
    assert await store.get_state("secretary_thread") == "123", "nothing stored is deleted"
