"""The console listener: authentication, CSRF, the board and item changes."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.console.auth import ConsoleAuthConfig, ConsoleAuthenticator
from claude_discord.console.server import CSRF_HEADER, ConsoleServer, maybe_start_console
from claude_discord.console.work_repo import WorkItemRepository

TOKEN = "k" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}
WRITE = {**AUTH, CSRF_HEADER: "1"}


@pytest.fixture
async def client(tmp_path):
    repo = WorkItemRepository(str(tmp_path / "sessions.db"))
    await repo.init_db()
    api = MagicMock()
    api.default_channel_id = None
    api.lineage_repo = None
    api.session_repo = None
    api.bot.get_channel.return_value = None
    api.bot.cogs = {}
    api._running_thread_ids.return_value = set()
    console = ConsoleServer(api, repo, ConsoleAuthenticator(ConsoleAuthConfig(token=TOKEN)), port=0)
    async with TestClient(TestServer(console.app)) as c:
        yield c


async def test_the_api_requires_authentication(client) -> None:
    assert (await client.get("/console/api/board")).status == 401
    assert (
        await client.get("/console/api/board", headers={"Authorization": "Bearer x"})
    ).status == 401


async def test_the_shell_is_served_with_security_headers(client) -> None:
    response = await client.get("/")
    assert response.status == 200
    assert "Relay Console" in await response.text()
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert (await client.get("/static/app.js")).status == 200


async def test_static_files_cannot_escape_the_directory(client) -> None:
    assert (await client.get("/static/..%2Fserver.py")).status == 404
    assert (await client.get("/static/server.py")).status == 404


async def test_mutations_require_the_csrf_header(client) -> None:
    response = await client.post("/console/api/items", json={"title": "x"}, headers=AUTH)
    assert response.status == 403


async def test_capture_then_board_then_patch(client) -> None:
    created = await client.post("/console/api/items", json={"title": "Plan Sunday"}, headers=WRITE)
    assert created.status == 201
    item_id = (await created.json())["item"]["id"]

    board = await (await client.get("/console/api/board", headers=AUTH)).json()
    [item] = board["items"]
    assert (item["id"], item["bucket"]) == (item_id, "todo")

    patched = await client.patch(
        f"/console/api/items/{item_id}", json={"priority": 0, "project": "relay"}, headers=WRITE
    )
    assert patched.status == 200
    assert (await patched.json())["item"]["priority"] == 0

    bad = await client.patch(f"/console/api/items/{item_id}", json={"priority": 9}, headers=WRITE)
    assert bad.status == 400

    done = await client.post(f"/console/api/items/{item_id}/done", json={}, headers=WRITE)
    assert (await done.json())["item"]["state"] == "done"


async def test_reply_is_refused_for_threads_not_on_the_board(client) -> None:
    response = await client.post(
        "/console/api/items/t123/reply", json={"text": "hi"}, headers=WRITE
    )
    assert response.status == 404


async def test_reply_needs_text(client) -> None:
    response = await client.post("/console/api/items/t123/reply", json={}, headers=WRITE)
    assert response.status == 400


async def test_the_body_must_be_an_object(client) -> None:
    response = await client.post("/console/api/items", json=["x"], headers=WRITE)
    assert response.status == 400


async def test_console_is_not_started_without_a_port(monkeypatch) -> None:
    monkeypatch.delenv("CCDB_CONSOLE_PORT", raising=False)
    assert await maybe_start_console(MagicMock()) is None


async def test_console_refuses_to_start_unauthenticated(monkeypatch) -> None:
    monkeypatch.setenv("CCDB_CONSOLE_PORT", "0")
    for name in (
        "CCDB_CONSOLE_TOKEN",
        "CCDB_CONSOLE_ACCESS_TEAM_DOMAIN",
        "CCDB_CONSOLE_ACCESS_AUD",
    ):
        monkeypatch.delenv(name, raising=False)
    assert await maybe_start_console(MagicMock()) is None


async def test_a_bad_port_does_not_take_the_bot_down(monkeypatch) -> None:
    monkeypatch.setenv("CCDB_CONSOLE_PORT", "not-a-port")
    monkeypatch.setenv("CCDB_CONSOLE_TOKEN", TOKEN)
    api = MagicMock()
    api.session_repo.db_path = ":memory:"
    assert await maybe_start_console(api) is None


async def test_starting_twice_at_once_is_refused(client, monkeypatch) -> None:
    import asyncio

    from claude_discord.console import server as srv

    created = await client.post("/console/api/items", json={"title": "x"}, headers=WRITE)
    item_id = (await created.json())["item"]["id"]
    gate = asyncio.Event()

    async def slow(self, item_id, body):
        await gate.wait()
        return srv.web.json_response({}, status=201)

    monkeypatch.setattr(srv.ConsoleServer, "_start_locked", slow)
    first = asyncio.ensure_future(
        client.post(f"/console/api/items/{item_id}/start", json={}, headers=WRITE)
    )
    await asyncio.sleep(0.05)
    second = await client.post(f"/console/api/items/{item_id}/start", json={}, headers=WRITE)
    gate.set()
    assert second.status == 409
    assert (await first).status == 201
