"""What a parent says about where its work stands: stored, validated, shown to the Console."""

from __future__ import annotations

import pytest
from aiohttp.test_utils import TestClient, TestServer
from aiohttp.web import Application

from claude_discord.database.models import init_db
from claude_discord.database.situation_repo import (
    MAX_ENTRIES,
    MAX_ENTRY_CHARS,
    SituationError,
    ThreadSituationRepository,
    parse_situation,
)
from claude_discord.ext.situation_api import handle_get_situation, handle_put_situation
from tests.test_console_conversations import AUTH
from tests.test_console_conversations import env as conversation_env

env = conversation_env
THREAD = 1567765910595440741


@pytest.fixture
async def repo(tmp_path) -> ThreadSituationRepository:
    path = str(tmp_path / "situations.db")
    await init_db(path)
    return ThreadSituationRepository(path)


@pytest.fixture
async def client(repo):
    app = Application()
    route = "/api/threads/{thread_id}/situation"
    app.router.add_get(route, lambda request: handle_get_situation(request, repo))
    app.router.add_put(route, lambda request: handle_put_situation(request, repo))
    async with TestClient(TestServer(app)) as c:
        yield c


async def test_a_thread_nobody_wrote_about_has_no_record(repo) -> None:
    assert await repo.get(THREAD) is None


async def test_a_write_replaces_only_the_fields_it_names(repo) -> None:
    await repo.update(THREAD, {"asks": ["Pick a region"], "decided": ["Ship on Friday"]})
    stored = await repo.update(THREAD, {"asks": []})
    assert stored.asks == []
    assert stored.decided == ["Ship on Friday"]
    assert stored.doing == []


async def test_the_id_survives_a_javascript_round_trip(repo) -> None:
    stored = await repo.update(THREAD, {"doing": ["Migrating"]})
    assert stored.as_dict()["thread_id"] == str(THREAD)


@pytest.mark.parametrize(
    "body",
    [
        [],
        {},
        {"decisions": ["typo in the field name"]},
        {"asks": "not a list"},
        {"asks": [1]},
        {"asks": ["x"] * (MAX_ENTRIES + 1)},
        {"asks": ["x" * (MAX_ENTRY_CHARS + 1)]},
    ],
)
def test_a_write_that_would_mislead_the_panel_is_refused(body) -> None:
    with pytest.raises(SituationError):
        parse_situation(body)


def test_blank_entries_are_dropped_and_the_rest_trimmed() -> None:
    assert parse_situation({"asks": ["  Pick a region ", "  "]}) == {"asks": ["Pick a region"]}


async def test_the_control_plane_writes_and_reads(client) -> None:
    url = f"/api/threads/{THREAD}/situation"
    assert await (await client.get(url)).json() == {"situation": None}
    answer = await client.put(url, json={"asks": ["Approve the move?"]})
    assert answer.status == 200
    situation = (await (await client.get(url)).json())["situation"]
    assert situation["asks"] == ["Approve the move?"]
    assert situation["updated_at"]


async def test_the_control_plane_explains_a_refusal(client) -> None:
    url = f"/api/threads/{THREAD}/situation"
    answer = await client.put(url, json={"decisions": ["x"]})
    assert answer.status == 400
    assert "decided" in (await answer.json())["error"]
    assert (await client.put(url, data="{nope")).status == 400
    assert (await client.get("/api/threads/abc/situation")).status == 400


async def test_without_a_store_the_control_plane_says_so() -> None:
    app = Application()
    route = "/api/threads/{thread_id}/situation"
    app.router.add_get(route, lambda request: handle_get_situation(request, None))
    app.router.add_put(route, lambda request: handle_put_situation(request, None))
    async with TestClient(TestServer(app)) as c:
        assert (await c.get(f"/api/threads/{THREAD}/situation")).status == 503
        assert (await c.put(f"/api/threads/{THREAD}/situation", json={"asks": []})).status == 503


async def test_the_console_needs_a_sign_in_and_tolerates_no_store(env) -> None:
    url = f"/console/api/items/t{THREAD}/situation"
    assert (await env.client.get(url)).status == 401
    env.console.api.situation_repo = None
    env.console.api.wait_repo = None
    answer = await env.client.get(url, headers=AUTH)
    assert await answer.json() == {"situation": None, "waits": []}


async def test_the_console_shows_what_the_parent_wrote(env, repo) -> None:
    await repo.update(THREAD, {"asks": ["Approve the move?"]})
    env.console.api.situation_repo = repo
    env.console.api.wait_repo = None
    url = f"/console/api/items/t{THREAD}/situation"
    body = await (await env.client.get(url, headers=AUTH)).json()
    assert body["situation"]["asks"] == ["Approve the move?"]
    # A written-down item has no thread, so no record.
    capture = await env.client.get("/console/api/items/cabc/situation", headers=AUTH)
    assert (await capture.json())["situation"] is None


async def test_the_console_lists_active_waits_with_string_ids(env, repo, tmp_path) -> None:
    from claude_discord.database.wait_repo import WaitRepository
    from claude_discord.waits import parse_wait_spec

    waits = WaitRepository(repo._db_path)
    spec = parse_wait_spec(
        {"thread_id": THREAD, "label": "PR checks", "argv": ["true"], "done_values": ["x"]}
    )
    await waits.create(spec, now=1.0)
    env.console.api.situation_repo = repo
    env.console.api.wait_repo = waits
    url = f"/console/api/items/t{THREAD}/situation"
    body = await (await env.client.get(url, headers=AUTH)).json()
    assert body["waits"] == [{"thread_id": str(THREAD), "label": "PR checks"}]
