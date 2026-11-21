"""One card for the same question across a thread family (#1493)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.console.auth import ConsoleAuthConfig, ConsoleAuthenticator
from claude_discord.console.server import ConsoleServer
from claude_discord.console.work_repo import WorkItemRepository
from claude_discord.database.decision_repo import DecisionRequestRepository
from claude_discord.database.lineage_repo import ThreadLineageRepository
from claude_discord.database.models import init_db
from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.decision_request import parse_request
from claude_discord.ext.api_server import ApiServer
from claude_discord.parent_mode import build_parent_section
from tests.test_decision_requests import _good

PARENT = 1567765910595440100
CHILD_A = 1567765910595440201
CHILD_B = 1567765910595440202
GRANDCHILD = 1567765910595440301
STRANGER = 1567765910595440999


@pytest.fixture
async def db(tmp_path):
    path = str(tmp_path / "s.db")
    await init_db(path)
    lineage = ThreadLineageRepository(path)
    await lineage.record(CHILD_A, PARENT, "K1")
    await lineage.record(CHILD_B, PARENT, "K1")
    await lineage.record(GRANDCHILD, CHILD_A, "K1")
    return path, DecisionRequestRepository(path), lineage


async def _card(repo, thread: int, key: str = "", title: str | None = None):
    over = {"key": key} if key else {}
    if title:
        over["title"] = title
    spec = parse_request(_good(**over), kind="waiting")
    return await repo.put(thread, spec, origin="mark", source_thread_id=thread)


# -- repository ---------------------------------------------------------------


async def test_bundle_closes_and_links(db):
    _, repo, _ = db
    a = await _card(repo, CHILD_A)
    b = await _card(repo, CHILD_B)
    mine = await _card(repo, PARENT, "same")
    assert await repo.bundle(mine.id, [a.id, b.id]) == 2
    assert [r.id for r in await repo.list_open()] == [mine.id]
    assert [r.thread_id for r in await repo.covered_by(mine.id)] == [CHILD_A, CHILD_B]


async def test_links_follow_a_rewritten_card(db):
    _, repo, _ = db
    a = await _card(repo, CHILD_A)
    mine = await _card(repo, PARENT, "same")
    await repo.bundle(mine.id, [a.id])
    rewritten = await _card(repo, PARENT, "same", title="書き直した同じ質問のタイトルです")
    assert await repo.covered_by(mine.id) == []
    assert [r.id for r in await repo.covered_by(rewritten.id)] == [a.id]


# -- control plane ------------------------------------------------------------


@pytest.fixture
async def api(db):
    path, repo, lineage = db
    notifications = NotificationRepository(path)
    await notifications.init_db()
    bot = MagicMock()
    bot.cogs = {}
    server = ApiServer(
        repo=notifications,
        bot=bot,
        default_channel_id=1,
        host="127.0.0.1",
        port=0,
        decision_repo=repo,
        lineage_repo=lineage,
    )
    client = TestClient(TestServer(server.app))
    await client.start_server()
    yield client, repo
    await client.close()


async def test_family_listing(api):
    client, repo = api
    a = await _card(repo, CHILD_A)
    g = await _card(repo, GRANDCHILD)
    await _card(repo, STRANGER)
    mine = await _card(repo, PARENT, "same")
    own = await (await client.get(f"/api/threads/{PARENT}/decisions")).json()
    assert [r["id"] for r in own["requests"]] == [mine.id]
    family = await (await client.get(f"/api/threads/{PARENT}/decisions?family=1")).json()
    assert sorted(r["id"] for r in family["requests"]) == sorted([a.id, g.id, mine.id])
    assert all(isinstance(r["thread_id"], str) for r in family["requests"])


async def test_cover_descendants(api):
    client, repo = api
    a = await _card(repo, CHILD_A)
    g = await _card(repo, GRANDCHILD)
    mine = await _card(repo, PARENT, "same")
    resp = await client.post(
        f"/api/threads/{PARENT}/decisions/same/covers", json={"request_ids": [a.id, g.id]}
    )
    assert resp.status == 200
    assert (await resp.json())["covered"] == 2
    assert [r.id for r in await repo.list_open()] == [mine.id]


async def test_cover_refuses_outsiders_and_covers_nothing(api):
    client, repo = api
    a = await _card(repo, CHILD_A)
    s = await _card(repo, STRANGER)
    await _card(repo, PARENT, "same")
    resp = await client.post(
        f"/api/threads/{PARENT}/decisions/same/covers", json={"request_ids": [a.id, s.id]}
    )
    assert resp.status == 400
    body = await resp.json()
    assert any("not from a thread you spawned" in p for p in body["problems"])
    assert {r.id for r in await repo.list_open()} >= {a.id, s.id}


async def test_a_child_cannot_cover_its_parent(api):
    client, repo = api
    p = await _card(repo, PARENT)
    await _card(repo, CHILD_A, "same")
    resp = await client.post(
        f"/api/threads/{CHILD_A}/decisions/same/covers", json={"request_ids": [p.id]}
    )
    assert resp.status == 400


async def test_cover_needs_the_parents_card_first(api):
    client, repo = api
    a = await _card(repo, CHILD_A)
    resp = await client.post(
        f"/api/threads/{PARENT}/decisions/same/covers", json={"request_ids": [a.id]}
    )
    assert resp.status == 404


@pytest.mark.parametrize(
    "body", [{}, {"request_ids": []}, {"request_ids": ["1"]}, {"request_ids": [True]}, []]
)
async def test_cover_validates_the_body(api, body):
    client, repo = api
    await _card(repo, PARENT, "same")
    resp = await client.post(f"/api/threads/{PARENT}/decisions/same/covers", json=body)
    assert resp.status == 400


# -- Console: the answer reaches every covered child -------------------------


async def test_answer_is_relayed_to_covered_children(db, tmp_path):
    path, repo, _ = db
    a = await _card(repo, CHILD_A)
    b = await _card(repo, CHILD_B, title="子Bの同じ質問をここに書いておきます")
    mine = await _card(repo, PARENT, "same")
    await repo.bundle(mine.id, [a.id, b.id])
    work = WorkItemRepository(path)
    await work.init_db()
    api = MagicMock()
    api.decision_repo = repo
    console = ConsoleServer(
        api, work, ConsoleAuthenticator(ConsoleAuthConfig(token="t" * 40)), port=0
    )
    console._deliver = AsyncMock(return_value=web.json_response({}, status=202))  # type: ignore[method-assign]

    await console._answer_covered(f"t{PARENT}", mine.id, "A で消す")

    targets = [c.args[0] for c in console._deliver.await_args_list]
    assert targets == [f"t{CHILD_A}", f"t{CHILD_B}"]
    second = console._deliver.await_args_list[1].args[1]
    assert "A で消す" in second and str(PARENT) in second and "子Bの同じ質問" in second


async def test_answer_without_covers_relays_nothing(db, tmp_path):
    path, repo, _ = db
    mine = await _card(repo, PARENT, "same")
    work = WorkItemRepository(path)
    await work.init_db()
    api = MagicMock()
    api.decision_repo = repo
    console = ConsoleServer(
        api, work, ConsoleAuthenticator(ConsoleAuthConfig(token="t" * 40)), port=0
    )
    console._deliver = AsyncMock()  # type: ignore[method-assign]
    await console._answer_covered(f"t{PARENT}", mine.id, "A")
    await console._answer_covered(f"t{PARENT}", None, "A")
    console._deliver.assert_not_awaited()


def test_parent_prompt_explains_combining():
    text = build_parent_section(PARENT, 5)
    assert f"/api/threads/{PARENT}/decisions?family=1" in text
    assert "/covers" in text
