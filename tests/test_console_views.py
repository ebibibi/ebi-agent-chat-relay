"""Saved views: named rules over the board, stored for every device, plus item tags."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.console.auth import ConsoleAuthConfig, ConsoleAuthenticator
from claude_discord.console.board import SessionInfo, ThreadSnapshot, build_board
from claude_discord.console.server import ConsoleServer
from claude_discord.console.views import MAX_VIEWS, SavedViewRepository, ViewError
from claude_discord.console.work_repo import WorkItemRepository, thread_item_id

TOKEN = "t" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}
WRITE = {**AUTH, "X-Console-Request": "1"}


@pytest.fixture
async def env(tmp_path):
    db = str(tmp_path / "sessions.db")
    work = WorkItemRepository(db)
    await work.init_db()
    views = SavedViewRepository(db)
    await views.init_db()
    api = MagicMock()
    api.default_channel_id = None
    api.lineage_repo = None
    api.session_repo = None
    api.bot.get_channel.return_value = None
    api.bot.cogs = {}
    api._running_thread_ids.return_value = set()
    console = ConsoleServer(
        api, work, ConsoleAuthenticator(ConsoleAuthConfig(token=TOKEN)), port=0, views=views
    )
    async with TestClient(TestServer(console.app)) as client:
        yield client, views


async def test_a_saved_view_round_trips_and_comes_with_the_board(env) -> None:
    client, _ = env
    res = await client.post(
        "/console/api/views",
        json={"name": " Teams  返信 ", "query": "tag:teams-exporter -is:done"},
        headers=WRITE,
    )
    assert res.status == 200
    [view] = (await res.json())["views"]
    assert view["name"] == "Teams 返信"
    assert view["query"] == "tag:teams-exporter -is:done"

    board = await (await client.get("/console/api/board", headers=AUTH)).json()
    assert board["views"] == [view]

    res = await client.patch(
        f"/console/api/views/{view['id']}", json={"query": "tag:teams-exporter"}, headers=WRITE
    )
    assert (await res.json())["views"][0]["query"] == "tag:teams-exporter"

    res = await client.delete(f"/console/api/views/{view['id']}", headers=WRITE)
    assert res.status == 200
    assert (await res.json())["views"] == []


async def test_bad_view_changes_are_refused(env) -> None:
    client, _ = env
    for body in ({"name": "", "query": "x"}, {"name": "n", "query": ""}, {"name": "n"}):
        res = await client.post("/console/api/views", json=body, headers=WRITE)
        assert res.status == 400
    assert (await client.patch("/console/api/views/vnope", json={}, headers=WRITE)).status == 404
    assert (await client.delete("/console/api/views/vnope", headers=WRITE)).status == 404


async def test_writes_need_the_console_request_header(env) -> None:
    client, _ = env
    res = await client.post("/console/api/views", json={"name": "n", "query": "q"}, headers=AUTH)
    assert res.status in (400, 403)


async def test_the_number_of_views_is_capped(tmp_path) -> None:
    repo = SavedViewRepository(str(tmp_path / "v.db"))
    await repo.init_db()
    for n in range(MAX_VIEWS):
        await repo.add(f"v{n}", "tag:x")
    with pytest.raises(ViewError):
        await repo.add("one more", "tag:x")
    assert [v.position for v in await repo.list()] == list(range(MAX_VIEWS))


async def test_a_tag_set_through_patch_reaches_the_board(env) -> None:
    client, _ = env
    res = await client.post("/console/api/items", json={"title": "Invoice"}, headers=WRITE)
    item_id = (await res.json())["item"]["id"]
    res = await client.patch(
        f"/console/api/items/{item_id}", json={"tags": ["Billing"]}, headers=WRITE
    )
    assert res.status == 200
    board = await (await client.get("/console/api/board", headers=AUTH)).json()
    assert [i["tags"] for i in board["items"]] == [["billing"]]
    res = await client.patch(f"/console/api/items/{item_id}", json={"tags": ["a b"]}, headers=WRITE)
    assert res.status == 400


def test_the_board_merges_own_and_derived_tags_and_reports_origin() -> None:
    from claude_discord.console.work_repo import WorkItem

    now = datetime(2026, 10, 30, tzinfo=UTC)
    overlay = WorkItem(
        id=thread_item_id(5),
        thread_id=5,
        title=None,
        note=None,
        parent_id=None,
        priority=0,
        due_at=None,
        snoozed_until=None,
        project=None,
        state="open",
        created_at="2026-10-30T00:00:00Z",
        updated_at="2026-10-30T00:00:00Z",
        tags=("teams-exporter", "ops"),
    )
    items = build_board(
        threads=[
            ThreadSnapshot(
                thread_id=5,
                name="reply",
                created_at=None,
                last_activity_at=None,
                url=None,
                archived=False,
            ),
            ThreadSnapshot(
                thread_id=6,
                name="plain",
                created_at=None,
                last_activity_at=None,
                url=None,
                archived=False,
            ),
        ],
        overlays=[overlay],
        lineage={},
        running=set(),
        sessions={
            5: SessionInfo(origin="discord", tags=("teams-exporter",)),
            6: SessionInfo(origin="console"),
        },
        now=now,
    )
    by_id = {i["id"]: i for i in items}
    assert by_id["t5"]["tags"] == ["ops", "teams-exporter"]
    assert by_id["t5"]["origin"] == "discord"
    assert by_id["t6"]["tags"] == []
    assert by_id["t6"]["origin"] == "console"
