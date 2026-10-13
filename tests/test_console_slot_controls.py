"""Session limit, stop, pause, resume and queue order from the Ebi AI Console."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.console.auth import ConsoleAuthConfig, ConsoleAuthenticator
from claude_discord.console.server import RESUME_PROMPT, SLOTS_SETTING, ConsoleServer
from claude_discord.console.work_repo import WorkItemRepository
from claude_discord.session_slots import SessionSlotScheduler, set_session_slots

TOKEN = "t" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}
WRITE = {**AUTH, "X-Console-Request": "1"}


class FakeRepo:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key, default=None):
        return self.values.get(key, default)

    async def set(self, key, value):
        self.values[key] = value


@pytest.fixture
async def env(tmp_path):
    work = WorkItemRepository(str(tmp_path / "sessions.db"))
    await work.init_db()
    slots = SessionSlotScheduler(1)
    set_session_slots(slots)
    api = MagicMock()
    api.default_channel_id = None
    api.lineage_repo = None
    api.session_repo = None
    api.bot.get_channel.return_value = None
    api.bot.cogs = {}
    api._running_thread_ids.return_value = set()
    settings = MagicMock()
    settings.repo = FakeRepo()
    api.components.backend_settings = settings
    console = ConsoleServer(api, work, ConsoleAuthenticator(ConsoleAuthConfig(token=TOKEN)), port=0)
    delivered: list[tuple[str, str]] = []

    async def deliver(item_id, text, who):
        from aiohttp import web

        delivered.append((item_id, text))
        return web.json_response({"status": "delivered"}, status=202)

    console._deliver = deliver  # type: ignore[method-assign]
    async with TestClient(TestServer(console.app)) as client:
        yield client, slots, work, settings.repo, delivered
    set_session_slots(None)


async def test_the_limit_changes_now_and_is_kept(env) -> None:
    client, slots, _, repo, _ = env
    res = await client.post("/console/api/slots", json={"max": 3}, headers=WRITE)
    assert res.status == 200
    assert (await res.json())["slots"]["max"] == 3
    assert slots.max_slots == 3 and repo.values[SLOTS_SETTING] == "3"
    for bad in (0, 99, "4", True, None):
        assert (
            await client.post("/console/api/slots", json={"max": bad}, headers=WRITE)
        ).status == 400
    assert slots.max_slots == 3


async def test_stop_and_pause_interrupt_a_chat_thread_run(env) -> None:
    client, slots, work, _, _ = env
    entry = await slots.acquire(42, label="t42")
    entry.on_pause = AsyncMock()
    assert (await client.post("/console/api/items/t42/stop", json={}, headers=WRITE)).status == 202
    assert entry.on_pause.await_count == 1
    assert (await client.post("/console/api/items/t42/pause", json={}, headers=WRITE)).status == 202
    assert entry.on_pause.await_count == 2
    assert (await work.get("t42")).paused_at is not None
    slots.release(entry)
    nothing = await client.post("/console/api/items/t42/stop", json={}, headers=WRITE)
    assert nothing.status == 409


async def test_resume_clears_the_mark_and_asks_the_agent_to_continue(env) -> None:
    client, _, work, _, delivered = env
    await work.update("t7", {"paused": True})
    res = await client.post("/console/api/items/t7/resume", json={}, headers=WRITE)
    assert res.status == 202
    assert (await work.get("t7")).paused_at is None
    assert delivered == [("t7", RESUME_PROMPT)]


async def test_a_waiting_thread_moves_first_or_later(env) -> None:
    client, slots, _, _, _ = env
    running = await slots.acquire(1, label="t1")
    second = asyncio.ensure_future(slots.acquire(2, label="t2"))
    third = asyncio.ensure_future(slots.acquire(3, label="t3"))
    await asyncio.sleep(0)
    res = await client.post("/console/api/items/t3/queue", json={"action": "first"}, headers=WRITE)
    assert (await res.json())["position"] == 1
    res = await client.post("/console/api/items/t3/queue", json={"action": "later"}, headers=WRITE)
    assert (await res.json())["position"] == 2
    bad = await client.post("/console/api/items/t3/queue", json={"action": "x"}, headers=WRITE)
    assert bad.status == 400
    not_waiting = await client.post(
        "/console/api/items/t1/queue", json={"action": "first"}, headers=WRITE
    )
    assert not_waiting.status == 409
    slots.release(running)
    await asyncio.sleep(0)
    for fut in (second, third):
        if fut.done():
            slots.release(fut.result())
        else:
            fut.cancel()
