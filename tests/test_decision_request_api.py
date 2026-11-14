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
from tests.test_decision_requests import CHILD, GOOD, THREAD, _good


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
    await decisions.sync_situation(CHILD, [(THREAD, decisions_spec())])
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


def decisions_spec():
    from claude_discord.decision_request import parse_request

    return parse_request(_good(), kind="waiting")


# -- situation ---------------------------------------------------------------


@pytest.fixture
async def situation_client(tmp_path):
    path = str(tmp_path / "s.db")
    await init_db(path)
    situations = ThreadSituationRepository(path)
    decisions = DecisionRequestRepository(path)
    app = Application()
    app.router.add_put(
        "/api/threads/{thread_id}/situation",
        lambda request: handle_put_situation(request, situations, decisions),
    )
    async with TestClient(TestServer(app)) as client:
        yield client, situations, decisions


async def test_a_parent_carries_a_childs_question_as_a_request(situation_client) -> None:
    client, situations, decisions = situation_client
    ask = {**_good(), "for_thread": str(CHILD)}
    resp = await client.put(
        f"/api/threads/{THREAD}/situation", json={"asks": [ask, "older plain ask"]}
    )
    assert resp.status == 200
    assert (await resp.json())["requests"] == 1
    stored = await situations.get(THREAD)
    assert stored is not None
    assert stored.asks == [GOOD["title"], "older plain ask"]
    (request,) = await decisions.list_open()
    assert (request.thread_id, request.source_thread_id) == (CHILD, THREAD)


async def test_an_answered_ask_left_out_is_closed(situation_client) -> None:
    client, _, decisions = situation_client
    url = f"/api/threads/{THREAD}/situation"
    await client.put(url, json={"asks": [_good()]})
    await client.put(url, json={"doing": ["still working"]})
    assert len(await decisions.list_open()) == 1
    await client.put(url, json={"asks": []})
    assert await decisions.list_open() == []


async def test_a_bad_ask_is_refused_with_its_position(situation_client) -> None:
    client, situations, decisions = situation_client
    url = f"/api/threads/{THREAD}/situation"
    bad = [_good(), {**_good(), "for_thread": "x"}, {**_good(), "kind": "done"}, _good(title="")]
    resp = await client.put(url, json={"asks": bad})
    assert resp.status == 400
    problems = (await resp.json())["problems"]
    assert any(p.startswith("asks[1]") for p in problems)
    assert any(p.startswith("asks[2]") for p in problems)
    assert any(p.startswith("asks[3]") for p in problems)
    assert await situations.get(THREAD) is None
    assert await decisions.list_open() == []


async def test_two_questions_for_one_thread_need_keys(situation_client) -> None:
    client, _, _ = situation_client
    url = f"/api/threads/{THREAD}/situation"
    resp = await client.put(url, json={"asks": [_good(), _good()]})
    assert resp.status == 400
    resp = await client.put(url, json={"asks": [_good(), _good(key="second")]})
    assert resp.status == 200
