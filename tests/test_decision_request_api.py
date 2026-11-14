"""Decision requests through the control plane: with a mark, and in a parent's ``asks``."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from aiohttp.test_utils import TestClient, TestServer
from aiohttp.web import Application

from claude_discord.database.decision_repo import DecisionRequestRepository
from claude_discord.database.models import init_db
from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.database.situation_repo import ThreadSituationRepository
from claude_discord.ext.api_server import ApiServer
from claude_discord.ext.situation_api import handle_put_situation
from claude_discord.thread_marker import DEFAULT_WAITING_MARKER
from tests.test_decision_requests import THREAD, _good


def _thread(name: str) -> MagicMock:
    thread = MagicMock(spec=discord.Thread)
    thread.name = name
    thread.edit = AsyncMock()
    return thread


@pytest.fixture
async def setup():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(path)
    notifications = NotificationRepository(path)
    await notifications.init_db()
    bot = MagicMock()
    bot.cogs = {}
    bot.get_channel.return_value = _thread("Fix the build")
    decisions = DecisionRequestRepository(path)
    api = ApiServer(
        repo=notifications,
        bot=bot,
        default_channel_id=1,
        host="127.0.0.1",
        port=0,
        decision_repo=decisions,
    )
    client = TestClient(TestServer(api.app))
    await client.start_server()
    yield client, decisions, bot
    await client.close()
    os.unlink(path)


async def test_a_mark_with_a_request_keeps_it(setup) -> None:
    client, decisions, _ = setup
    resp = await client.post(f"/api/threads/{THREAD}/waiting", json=_good())
    assert resp.status == 200
    body = await resp.json()
    assert body["thread_name"].startswith(DEFAULT_WAITING_MARKER)
    (stored,) = await decisions.list_open()
    assert body["request_id"] == stored.id
    assert (stored.kind, stored.origin, stored.thread_id) == ("waiting", "mark", THREAD)


async def test_a_bad_request_is_refused_before_the_mark(setup) -> None:
    client, decisions, bot = setup
    resp = await client.post(f"/api/threads/{THREAD}/waiting", json=_good(title=""))
    assert resp.status == 400
    body = await resp.json()
    assert any("title" in p for p in body["problems"])
    assert "forgotten" in body["format"]
    bot.get_channel.return_value.edit.assert_not_called()
    assert await decisions.list_open() == []


async def test_a_bare_mark_still_works_and_supersedes_the_old_request(setup) -> None:
    client, decisions, bot = setup
    await client.post(f"/api/threads/{THREAD}/waiting", json=_good())
    bot.get_channel.return_value = _thread(f"{DEFAULT_WAITING_MARKER} Fix the build")
    resp = await client.post(f"/api/threads/{THREAD}/action")
    assert resp.status == 200
    assert "request_id" not in await resp.json()
    assert await decisions.list_open() == []


async def test_finishing_the_thread_closes_every_request_aimed_at_it(setup) -> None:
    client, decisions, _ = setup
    await client.post(f"/api/threads/{THREAD}/waiting", json=_good())
    await client.post(f"/api/threads/{THREAD}/waiting", json=_good(key="own"))
    assert len(await decisions.list_open()) == 2
    resp = await client.post(f"/api/threads/{THREAD}/candidate")
    assert resp.status == 200
    assert await decisions.list_open() == []


async def test_a_request_on_a_finishing_mark_is_refused(setup) -> None:
    client, _, _ = setup
    resp = await client.post(f"/api/threads/{THREAD}/candidate", json=_good())
    assert resp.status == 400


async def test_a_review_needs_only_one_answer(setup) -> None:
    client, decisions, _ = setup
    body = _good(options=[{"label": "OK で出す", "effect": "このまま公開する"}])
    resp = await client.post(f"/api/threads/{THREAD}/review", json=body)
    assert resp.status == 200
    (stored,) = await decisions.list_open()
    assert stored.kind == "review"


# -- situation: asks stays plain text; requests ride on markers ---------------


async def test_a_request_written_into_asks_is_pointed_at_the_markers(tmp_path) -> None:
    path = str(tmp_path / "s.db")
    await init_db(path)
    situations = ThreadSituationRepository(path)
    app = Application()
    app.router.add_put(
        "/api/threads/{thread_id}/situation",
        lambda request: handle_put_situation(request, situations),
    )
    async with TestClient(TestServer(app)) as client:
        url = f"/api/threads/{THREAD}/situation"
        resp = await client.put(url, json={"asks": [_good()]})
        assert resp.status == 400
        assert "/waiting" in (await resp.json())["error"]
        # Plain strings keep working for the agents that still write them.
        assert (await client.put(url, json={"asks": ["older plain ask"]})).status == 200
