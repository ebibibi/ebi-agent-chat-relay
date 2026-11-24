"""Tests for POST /api/teams/reply/done — the extension's "I answered" button."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.database.ingest_repo import IngestResultRepository
from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.ext.api_server import ApiServer

TOKEN = "test-ingest-token"  # noqa: S105 - test fixture, not a real credential
AUTH = {"Authorization": f"Bearer {TOKEN}"}
TEAM = "3a2b1c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"
THREAD = {"team": TEAM, "root_mid": "1784110000000", "title": "対象スレッド"}
OTHER = {"team": TEAM, "root_mid": "1784110000555", "title": "別のスレッド"}


@pytest.fixture
async def api(tmp_path: Path) -> ApiServer:
    db = str(tmp_path / "t.db")
    repo = NotificationRepository(db)
    await repo.init_db()
    ingest_repo = IngestResultRepository(db)
    await ingest_repo.init_db()
    bot = MagicMock()
    bot.get_channel.return_value = MagicMock(spec=discord.TextChannel)
    server = ApiServer(
        repo=repo,
        bot=bot,
        default_channel_id=12345,
        ingest_token=TOKEN,
        ingest_repo=ingest_repo,
        teams_vault_root=str(tmp_path / "vault"),
        working_dir=str(tmp_path),
    )
    server._console = MagicMock()
    server._console.complete_item = AsyncMock(return_value=(MagicMock(), None))
    return server


@pytest.fixture
async def client(api: ApiServer) -> AsyncIterator[TestClient]:
    async with TestClient(TestServer(api.app)) as c:
        yield c


async def link_run(api: ApiServer, thread: dict, thread_id: str) -> None:
    from claude_discord.ext import teams_reply, teams_sync

    key = teams_reply.thread_key(teams_sync.parse_thread_ref(thread))
    await api.ingest_repo.create(  # type: ignore[union-attr]
        result_id=f"r-{thread_id}", thread_id=thread_id, teams_thread_key=key
    )


async def done(client: TestClient, thread: dict = THREAD) -> tuple[int, dict]:
    resp = await client.post(
        "/api/teams/reply/done", json={"thread": thread, "messages": []}, headers=AUTH
    )
    return resp.status, await resp.json()


def closed_items(api: ApiServer) -> list[str]:
    return [c.args[0] for c in api._console.complete_item.await_args_list]


async def test_needs_the_ingest_token(client: TestClient) -> None:
    resp = await client.post("/api/teams/reply/done", json={"thread": THREAD, "messages": []})
    assert resp.status == 401


async def test_no_reply_thread_is_404(client: TestClient, api: ApiServer) -> None:
    status, _ = await done(client)
    assert status == 404
    assert closed_items(api) == []


async def test_closes_every_reply_thread_of_that_teams_thread(
    client: TestClient, api: ApiServer
) -> None:
    await link_run(api, THREAD, "111")
    await link_run(api, THREAD, "222")
    await link_run(api, OTHER, "999")
    status, body = await done(client)
    assert status == 200
    assert body["status"] == "done"
    assert sorted(body["closed"]) == ["111", "222"]
    assert sorted(closed_items(api)) == ["t111", "t222"]


async def test_a_running_reply_closes_nothing(client: TestClient, api: ApiServer) -> None:
    await link_run(api, THREAD, "111")
    await link_run(api, THREAD, "222")
    api._running_thread_ids = lambda: {222}  # type: ignore[method-assign]
    status, body = await done(client)
    assert status == 409
    assert body["running"] == ["222"]
    assert closed_items(api) == []


async def test_status_advertises_the_capability(client: TestClient) -> None:
    resp = await client.post(
        "/api/teams/reply/status", json={"thread": THREAD, "messages": []}, headers=AUTH
    )
    assert (await resp.json())["capabilities"]["reply_done"] is True


async def test_reachable_on_the_external_listener(api: ApiServer) -> None:
    app = api._build_external_app()
    paths = {r.resource.canonical for r in app.router.routes() if r.resource is not None}
    assert "/api/teams/reply/done" in paths
