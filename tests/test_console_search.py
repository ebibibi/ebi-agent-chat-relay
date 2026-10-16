"""Search older message bodies and return only resumable Console board entries."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from claude_code_core.thread_search import ThreadSearchResult
from tests.test_console_conversations import AUTH, _start
from tests.test_console_conversations import env as conversation_env

env = conversation_env


async def test_search_finds_old_body_literal_and_requires_auth(env):
    item = await _start(env, "Unrelated title")
    key = int(item[1:])
    await env.conversations.append(
        key, author="AI", is_bot=True, content="Earlier: 日本語 100%_complete migration"
    )
    for _ in range(110):
        await env.conversations.append(
            key, author="Me", is_bot=False, content="Recent unrelated note"
        )
    assert (await env.client.get("/console/api/search?q=migration")).status == 401
    result = await env.client.get(
        "/console/api/search", params={"q": "100%_complete"}, headers=AUTH
    )
    assert result.status == 200
    [hit] = (await result.json())["results"]
    assert hit["item"]["id"] == item
    assert hit["source"] == "body"
    assert "日本語 100%_complete migration" in hit["snippet"]
    result = await env.client.get(
        "/console/api/search", params={"q": "100zzcomplete"}, headers=AUTH
    )
    assert (await result.json())["results"] == []


@pytest.mark.parametrize("query", ["", " ", "x" * 501])
async def test_invalid_queries(env, query):
    assert (
        await env.client.get("/console/api/search", params={"q": query}, headers=AUTH)
    ).status == 400


async def test_transcript_hits_are_scoped_to_board_threads(env, monkeypatch):
    item = await _start(env, "Existing conversation")
    key = int(item[1:])
    env.console.api.session_repo = SimpleNamespace()
    env.console.api.transcripts_path = "/test/transcripts"
    board = [{"id": item, "thread_id": key, "title": "Existing conversation"}]
    monkeypatch.setattr(env.console, "_board_items", AsyncMock(return_value=board))
    search = AsyncMock(
        return_value=[
            ThreadSearchResult(
                key, "sid", None, None, "console", None, "A migration match", "body"
            ),
            ThreadSearchResult(999, "private", None, None, "cli", None, "Hidden migration", "body"),
        ]
    )
    monkeypatch.setattr("claude_discord.console.search.run_thread_search", search)
    result = await env.client.get("/console/api/search?q=migration", headers=AUTH)
    [hit] = (await result.json())["results"]
    assert hit["item"]["id"] == item
    assert hit["snippet"] == "A migration match"


async def test_search_reopens_an_older_archived_watched_thread(env, monkeypatch):
    from unittest.mock import MagicMock

    import discord

    thread = MagicMock(spec=discord.Thread)
    thread.id = 1234
    thread.name = "Old archived work"
    thread.parent_id = 10
    thread.archived = True
    thread.created_at = None
    thread.last_message_id = None
    thread.archive_timestamp = None
    thread.jump_url = "https://discord.com/channels/1/1234"
    env.console.api.bot.get_channel.side_effect = lambda key: thread if key == 1234 else None
    monkeypatch.setattr(env.console, "_watched_channel_ids", lambda: {10})
    env.console.api.session_repo = SimpleNamespace(list_all=AsyncMock(return_value=[]))
    env.console.api.transcripts_path = "/test/transcripts"
    monkeypatch.setattr(
        "claude_discord.console.search.run_thread_search",
        AsyncMock(
            return_value=[
                ThreadSearchResult(
                    1234, "sid", None, None, "discord", None, "Earlier migration work", "body"
                )
            ]
        ),
    )
    result = await env.client.get("/console/api/search?q=migration", headers=AUTH)
    [hit] = (await result.json())["results"]
    assert hit["item"]["id"] == "t1234"
    board = await env.client.get("/console/api/board", headers=AUTH)
    assert any(i["id"] == "t1234" for i in (await board.json())["items"])


def _export(root, thread_id, title, messages):
    lines = [{"_meta": {"id": str(thread_id), "name": title}}]
    for i, (bot, content) in enumerate(messages):
        lines.append(
            {
                "type": 0,
                "id": str(i),
                "content": content,
                "author": {"username": "ai" if bot else "me", "bot": bot},
                "timestamp": f"2026-10-0{i + 1}T00:00:00+00:00",
                "attachments": [],
            }
        )
    import json

    (root / f"{thread_id}.jsonl").write_text(
        "\n".join(json.dumps(line, ensure_ascii=False) for line in lines), encoding="utf-8"
    )


async def test_deleted_threads_are_found_in_exports_and_read_only(env, tmp_path, monkeypatch):
    from claude_discord.console.thread_archive import ThreadArchive

    _export(
        tmp_path,
        555,
        "FortiGate redundancy",
        [(False, "Second FortiGate arrived"), (True, "-# status"), (True, "Use FGCP heartbeat")],
    )
    _export(tmp_path, 556, "Unrelated", [(False, "nothing here")])
    (tmp_path / "notes.txt").write_text("FGCP", encoding="utf-8")
    route = env.console.app.router
    search = next(
        r.handler.__self__ for r in route.routes() if r.resource.canonical == "/console/api/search"
    )
    monkeypatch.setattr(search, "archive", ThreadArchive(str(tmp_path)))

    result = await env.client.get("/console/api/search?q=fgcp", headers=AUTH)
    [hit] = (await result.json())["results"]
    assert hit["archived"] is True
    assert hit["item"] == {
        "id": "a555",
        "title": "FortiGate redundancy",
        "bucket": "done",
        "last_activity_at": "2026-10-03T00:00:00+00:00",
    }
    assert "FGCP heartbeat" in hit["snippet"]

    assert (await env.client.get("/console/api/archive/555")).status == 401
    read = await (await env.client.get("/console/api/archive/555", headers=AUTH)).json()
    assert read["title"] == "FortiGate redundancy"
    assert [(m["kind"], m["content"]) for m in read["messages"]] == [
        ("human", "Second FortiGate arrived"),
        ("agent", "Use FGCP heartbeat"),
    ]
    for bad in ("999", "notes", "..%2Fnotes.txt"):
        assert (await env.client.get(f"/console/api/archive/{bad}", headers=AUTH)).status == 404


async def test_transcript_hit_on_deleted_thread_uses_its_export(env, tmp_path, monkeypatch):
    from claude_discord.console.thread_archive import ThreadArchive

    _export(tmp_path, 777, "Old work", [(False, "hello")])
    route = env.console.app.router
    search = next(
        r.handler.__self__ for r in route.routes() if r.resource.canonical == "/console/api/search"
    )
    monkeypatch.setattr(search, "archive", ThreadArchive(str(tmp_path)))
    env.console.api.session_repo = SimpleNamespace()
    env.console.api.transcripts_path = "/test/transcripts"
    monkeypatch.setattr(env.console, "_board_items", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        env.console, "_thread_for", AsyncMock(return_value=(None, "unknown thread"))
    )
    monkeypatch.setattr(
        "claude_discord.console.search.run_thread_search",
        AsyncMock(
            return_value=[
                ThreadSearchResult(777, "sid", None, None, "discord", None, "a zebra", "body"),
                ThreadSearchResult(778, "gone", None, None, "discord", None, "zebra", "body"),
            ]
        ),
    )
    result = await env.client.get("/console/api/search?q=zebra", headers=AUTH)
    [hit] = (await result.json())["results"]
    assert hit["item"]["id"] == "a777"
    assert hit["snippet"] == "a zebra"
