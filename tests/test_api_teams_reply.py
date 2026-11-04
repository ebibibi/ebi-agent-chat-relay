"""Tests for the path from a linked ingest to /api/teams/reply/status."""

from __future__ import annotations

import os
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
ROOT = "1784110000000"
LATER = "1784110000999"
THREAD = {"team": TEAM, "root_mid": ROOT, "title": "対象スレッド"}
BODY = "ご連絡ありがとうございます。\n\n■ 確認結果\n\n見直したところ、原因はプロキシの設定でした。"


@pytest.fixture
async def api(tmp_path: Path) -> ApiServer:
    db = str(tmp_path / "t.db")
    repo = NotificationRepository(db)
    await repo.init_db()
    ingest_repo = IngestResultRepository(db)
    await ingest_repo.init_db()
    spawned = MagicMock()
    spawned.id = 111222333
    spawned.name = "対象スレッド"
    spawned.add_user = AsyncMock()
    spawned.send = AsyncMock()
    cog = MagicMock()
    cog.spawn_session = AsyncMock(return_value=spawned)
    bot = MagicMock()
    bot.get_channel.return_value = MagicMock(spec=discord.TextChannel)
    bot.cogs = {"ClaudeChatCog": cog}
    return ApiServer(
        repo=repo,
        bot=bot,
        default_channel_id=12345,
        ingest_token=TOKEN,
        ingest_repo=ingest_repo,
        teams_vault_root=str(tmp_path / "vault"),
        working_dir=str(tmp_path),
    )


@pytest.fixture
async def client(api: ApiServer) -> AsyncIterator[TestClient]:
    async with TestClient(TestServer(api.app)) as c:
        yield c


async def sync_root(client: TestClient) -> None:
    resp = await client.post(
        "/api/teams/sync/push",
        json={
            "thread": THREAD,
            "messages": [{"mid": ROOT, "hash": "h1", "text": "質問です", "author": "相手"}],
        },
        headers=AUTH,
    )
    assert resp.status == 200


async def ingest(client: TestClient, **over) -> dict:
    body = {"content": "返信を作ってください", "reply_target": {"thread": THREAD}}
    body.update(over)
    resp = await client.post("/api/ingest", json=body, headers=AUTH)
    assert resp.status == 201, await resp.text()
    return await resp.json()


async def status(client: TestClient, messages: list[dict] | None = None) -> dict:
    resp = await client.post(
        "/api/teams/reply/status",
        json={"thread": THREAD, "messages": messages or []},
        headers=AUTH,
    )
    assert resp.status == 200, await resp.text()
    return await resp.json()


def draft_file(api: ApiServer, result_id: str) -> Path:
    prompt = api.bot.cogs["ClaudeChatCog"].spawn_session.await_args.args[1]
    line = next(p for p in prompt.splitlines() if result_id in p and p.startswith("`"))
    return Path(line.strip("`"))


async def test_status_needs_the_ingest_token(client: TestClient) -> None:
    resp = await client.post("/api/teams/reply/status", json={"thread": THREAD, "messages": []})
    assert resp.status == 401


async def test_a_thread_with_no_run_is_none(client: TestClient) -> None:
    data = await status(client)
    assert data["state"] == "none"
    assert data["draft"] is None


async def test_ingest_without_a_reply_target_links_nothing(client: TestClient) -> None:
    await sync_root(client)
    resp = await client.post("/api/ingest", json={"content": "別の用途"}, headers=AUTH)
    assert "reply_link" not in await resp.json()
    assert (await status(client))["state"] == "none"


async def test_a_malformed_reply_target_is_refused(client: TestClient) -> None:
    resp = await client.post(
        "/api/ingest",
        json={"content": "x", "reply_target": {"thread": {"team": "../..", "root_mid": ROOT}}},
        headers=AUTH,
    )
    assert resp.status == 400


async def test_an_unsynced_thread_still_starts_but_reports_no_link(client: TestClient) -> None:
    data = await ingest(client)
    assert data["reply_link"]["linked"] is False
    assert data["reply_link"]["reason"]


async def test_the_saved_body_is_returned_and_a_new_message_makes_it_stale(
    api: ApiServer, client: TestClient
) -> None:
    await sync_root(client)
    started = await ingest(client)
    assert started["reply_link"]["linked"] is True

    path = draft_file(api, started["result_id"])
    assert path.parent.is_dir(), "the session must be able to save without creating folders"

    result_sink = api.bot.cogs["ClaudeChatCog"].spawn_session.await_args.kwargs["result_sink"]
    await result_sink("作業メモ: 添付を確認してください", None)
    # Windows text, as the customer-reply skill also produces.
    path.write_bytes(("﻿" + BODY.replace("\n", "\r\n")).encode("utf-8"))

    ready = await status(client, [{"mid": ROOT, "hash": "h1", "text": "質問です"}])
    assert ready["state"] == "ready"
    assert ready["draft"]["text"] == BODY
    assert ready["draft"]["source"] == "file"
    assert ready["thread_id"] == "111222333"
    assert ready["base_mid"] == ROOT

    stale = await status(
        client,
        [
            {"mid": ROOT, "hash": "h1", "text": "質問です"},
            {"mid": LATER, "hash": "h2", "text": "追加の連絡です", "author": "相手"},
        ],
    )
    assert stale["state"] == "stale"
    assert stale["changes"] == [
        {"mid": LATER, "kind": "new", "author": "相手", "excerpt": "追加の連絡です"}
    ]


async def test_without_a_saved_body_the_final_message_is_only_unverified(
    api: ApiServer, client: TestClient
) -> None:
    await sync_root(client)
    await ingest(client)
    result_sink = api.bot.cogs["ClaudeChatCog"].spawn_session.await_args.kwargs["result_sink"]
    await result_sink("作業メモ: 本文は添付です", None)

    data = await status(client)
    assert data["state"] == "unverified"
    assert data["draft"]["source"] == "final_message"


async def test_a_newer_run_replaces_the_older_reply(api: ApiServer, client: TestClient) -> None:
    await sync_root(client)
    first = await ingest(client)
    draft_file(api, first["result_id"]).write_text(BODY, encoding="utf-8")
    assert (await status(client))["state"] == "ready"

    await ingest(client)
    data = await status(client)
    assert data["state"] == "generating", "the old body must not be offered during a regeneration"
    assert data["draft"] is None


async def test_a_chat_is_found_again_after_its_root_mid_moves(
    api: ApiServer, client: TestClient
) -> None:
    chat = {**THREAD, "identity_scope": "conversation"}
    await client.post(
        "/api/teams/sync/push",
        json={"thread": chat, "messages": [{"mid": ROOT, "hash": "h1", "text": "質問"}]},
        headers=AUTH,
    )
    started = await ingest(client, reply_target={"thread": chat})
    draft_file(api, started["result_id"]).write_text(BODY, encoding="utf-8")

    resp = await client.post(
        "/api/teams/reply/status",
        json={"thread": {**chat, "root_mid": "1784000000000"}, "messages": []},
        headers=AUTH,
    )
    assert (await resp.json())["state"] == "ready"


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
async def test_a_symlink_out_of_the_thread_is_not_served(
    api: ApiServer, client: TestClient, tmp_path: Path
) -> None:
    await sync_root(client)
    started = await ingest(client)
    secret = tmp_path / "secret.txt"
    secret.write_text("外に出してはいけない内容", encoding="utf-8")
    draft_file(api, started["result_id"]).symlink_to(secret)

    data = await status(client)
    assert data["state"] != "ready"
    assert data["draft"] is None


async def test_an_old_row_gains_the_new_columns(tmp_path: Path) -> None:
    import aiosqlite

    db = str(tmp_path / "old.db")
    async with aiosqlite.connect(db) as conn:
        await conn.execute(
            "CREATE TABLE ingest_results (result_id TEXT PRIMARY KEY, "
            "status TEXT NOT NULL DEFAULT 'running', result TEXT, error TEXT, thread_id TEXT, "
            "thread_name TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')), "
            "updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')))"
        )
        await conn.commit()
    repo = IngestResultRepository(db)
    await repo.init_db()
    await repo.create(result_id="r1", teams_thread_key="k", teams_base_mid="1")
    row = await repo.latest_for_teams_thread("k")
    assert row is not None
    assert row["result_id"] == "r1"
