"""Managing places: add, rename and delete, carried to every item and guess."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.console.auth import ConsoleAuthConfig, ConsoleAuthenticator
from claude_discord.console.places import PlaceError, PlaceRepository, clean_name
from claude_discord.console.server import ConsoleServer
from claude_discord.console.triage import TriageRepository, TriageResult
from claude_discord.console.work_repo import WorkItemRepository

TOKEN = "t" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}
WRITE = {**AUTH, "X-Console-Request": "1"}


@pytest.mark.parametrize("bad", [None, 3, "", "   ", "x" * 41])
def test_a_bad_name_is_refused(bad) -> None:
    with pytest.raises(PlaceError):
        clean_name(bad)


def test_a_name_is_trimmed() -> None:
    assert clean_name("  会社  PC ") == "会社 PC"


@pytest.fixture
async def env(tmp_path):
    db = str(tmp_path / "sessions.db")
    work = WorkItemRepository(db)
    await work.init_db()
    triage = TriageRepository(db)
    await triage.init_db()
    places = PlaceRepository(db)
    await places.init_db()
    api = MagicMock()
    api.default_channel_id = None
    api.lineage_repo = None
    api.session_repo = None
    api.bot.get_channel.return_value = None
    api.bot.cogs = {}
    api._running_thread_ids.return_value = set()
    console = ConsoleServer(
        api,
        work,
        ConsoleAuthenticator(ConsoleAuthConfig(token=TOKEN)),
        port=0,
        triage=triage,
        places=places,
    )
    async with TestClient(TestServer(console.app)) as client:
        yield client, triage, places


async def _board(client):
    return await (await client.get("/console/api/board", headers=AUTH)).json()


async def test_add_rename_and_delete_reach_every_item(env) -> None:
    client, triage, places = env
    added = await client.post("/console/api/places", json={"name": "事務所"}, headers=WRITE)
    assert added.status == 200
    assert (await added.json())["places"] == [{"name": "事務所", "count": 0, "registered": True}]

    tagged = await client.post(
        "/console/api/items", json={"title": "Print", "place": "自宅"}, headers=WRITE
    )
    tagged_id = (await tagged.json())["item"]["id"]
    guessed = await client.post("/console/api/items", json={"title": "Scan"}, headers=WRITE)
    guessed_id = (await guessed.json())["item"]["id"]
    await triage.save(
        guessed_id, TriageResult(2, None, "r", "n", place="自宅"), basis="b", model="m"
    )
    # Tagging through PATCH registers the label.
    await client.patch(f"/console/api/items/{tagged_id}", json={"place": "自宅"}, headers=WRITE)
    board = await _board(client)
    assert {p["name"]: p["count"] for p in board["places"]} == {"事務所": 0, "自宅": 2}

    renamed = await client.patch(
        "/console/api/places", json={"from": "自宅", "name": "家"}, headers=WRITE
    )
    assert renamed.status == 200
    board = await _board(client)
    assert {i["id"]: i["place"] for i in board["items"]} == {tagged_id: "家", guessed_id: "家"}
    assert [p["name"] for p in board["places"]] == ["事務所", "家"]

    gone = await client.delete("/console/api/places", json={"name": "家"}, headers=WRITE)
    assert gone.status == 200
    board = await _board(client)
    assert {i["id"]: i["place"] for i in board["items"]} == {tagged_id: None, guessed_id: None}
    assert [p["name"] for p in board["places"]] == ["事務所"]


async def test_bad_place_changes_are_400(env) -> None:
    client, _, _ = env
    for method, body in (("post", {"name": ""}), ("patch", {"from": "", "name": "x"})):
        res = await getattr(client, method)("/console/api/places", json=body, headers=WRITE)
        assert res.status == 400


async def test_the_model_is_offered_managed_places_first(tmp_path) -> None:
    from claude_discord.console.triage import Triager

    db = str(tmp_path / "s.db")
    triage = TriageRepository(db)
    await triage.init_db()
    prompts: list[str] = []

    async def ask(prompt: str) -> str | None:
        prompts.append(prompt)
        return '{"priority": 2}'

    async def read(item):
        return []

    async def managed() -> list[str]:
        return ["事務所"]

    triager = Triager(triage, ask=ask, read_messages=read, registered_places=managed)
    await triager.run_once(
        [{"id": "t1", "title": "x", "status": "idle", "place": "自宅", "last_activity_at": "z"}]
    )
    assert "Places this person already uses: 事務所, 自宅" in prompts[0]
