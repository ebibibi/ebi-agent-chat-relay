"""Tests for POST/GET/DELETE /api/waits."""

from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.database.models import init_db
from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.database.wait_repo import WaitRepository
from claude_discord.ext.api_server import ApiServer
from claude_discord.waits import MAX_WAITS_PER_THREAD


@pytest.fixture
async def db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(path)
    yield path
    os.unlink(path)


async def _client(db_path: str, *, wait_repo: WaitRepository | None, session_repo=None):
    notif_repo = NotificationRepository(db_path)
    await notif_repo.init_db()
    bot = MagicMock()
    bot.cogs = {}
    api = ApiServer(repo=notif_repo, bot=bot, host="127.0.0.1", port=0, session_repo=session_repo)
    api.wait_repo = wait_repo
    client = TestClient(TestServer(api.app))
    await client.start_server()
    return client


@pytest.fixture
async def client(db_path: str) -> TestClient:
    c = await _client(db_path, wait_repo=WaitRepository(db_path))
    yield c
    await c.close()


def _body(**overrides: object) -> dict:
    body: dict = {
        "thread_id": 42,
        "argv": ["gh", "pr", "checks", "5"],
        "pending_exit_codes": [8],
        "label": "PR #5 checks",
    }
    body.update(overrides)
    return body


async def test_create_returns_201_with_the_wait(client: TestClient) -> None:
    resp = await client.post("/api/waits", json=_body())
    assert resp.status == 201
    data = await resp.json()
    assert data["wait"]["thread_id"] == 42
    assert data["wait"]["status"] == "active"
    assert data["wait"]["argv"] == ["gh", "pr", "checks", "5"]


@pytest.mark.parametrize(
    "body",
    [
        _body(argv="gh pr checks 5"),
        _body(pending_exit_codes=None),
        _body(thread_id=None),
        _body(done_pattern="(bad"),
    ],
)
async def test_create_rejects_bad_specs(client: TestClient, body: dict) -> None:
    resp = await client.post("/api/waits", json=body)
    assert resp.status == 400
    assert "error" in await resp.json()


async def test_create_rejects_invalid_json(client: TestClient) -> None:
    resp = await client.post("/api/waits", data="{nope")
    assert resp.status == 400


async def test_create_reports_limit_with_429(client: TestClient) -> None:
    for _ in range(MAX_WAITS_PER_THREAD):
        assert (await client.post("/api/waits", json=_body())).status == 201
    resp = await client.post("/api/waits", json=_body())
    assert resp.status == 429


async def test_cwd_defaults_to_the_sessions_working_dir(db_path: str, tmp_path) -> None:
    session_repo = MagicMock()
    session_repo.get = AsyncMock(return_value=SimpleNamespace(working_dir=str(tmp_path)))
    c = await _client(db_path, wait_repo=WaitRepository(db_path), session_repo=session_repo)
    try:
        resp = await c.post("/api/waits", json=_body())
        assert (await resp.json())["wait"]["cwd"] == str(tmp_path)
    finally:
        await c.close()


async def test_missing_session_dir_leaves_cwd_unset(db_path: str, tmp_path) -> None:
    session_repo = MagicMock()
    session_repo.get = AsyncMock(
        return_value=SimpleNamespace(working_dir=str(tmp_path / "removed-worktree"))
    )
    c = await _client(db_path, wait_repo=WaitRepository(db_path), session_repo=session_repo)
    try:
        resp = await c.post("/api/waits", json=_body())
        assert (await resp.json())["wait"]["cwd"] is None
    finally:
        await c.close()


async def test_list_filters_by_thread(client: TestClient) -> None:
    await client.post("/api/waits", json=_body(thread_id=1))
    await client.post("/api/waits", json=_body(thread_id=2))
    resp = await client.get("/api/waits?thread_id=1")
    assert resp.status == 200
    waits = (await resp.json())["waits"]
    assert [w["thread_id"] for w in waits] == [1]
    all_waits = (await (await client.get("/api/waits")).json())["waits"]
    assert len(all_waits) == 2


async def test_list_rejects_bad_thread_id(client: TestClient) -> None:
    assert (await client.get("/api/waits?thread_id=abc")).status == 400


async def test_delete_cancels_an_active_wait(client: TestClient) -> None:
    wait_id = (await (await client.post("/api/waits", json=_body())).json())["wait"]["id"]
    assert (await client.delete(f"/api/waits/{wait_id}?thread_id=999")).status == 404
    assert (await client.delete(f"/api/waits/{wait_id}?thread_id=42")).status == 200
    assert (await client.delete(f"/api/waits/{wait_id}")).status == 404
    assert (await (await client.get("/api/waits")).json())["waits"] == []


async def test_delete_rejects_bad_id(client: TestClient) -> None:
    assert (await client.delete("/api/waits/abc")).status == 400


async def test_503_when_waits_are_not_configured(db_path: str) -> None:
    c = await _client(db_path, wait_repo=None)
    try:
        assert (await c.post("/api/waits", json=_body())).status == 503
        assert (await c.get("/api/waits")).status == 503
        assert (await c.delete("/api/waits/1")).status == 503
    finally:
        await c.close()
