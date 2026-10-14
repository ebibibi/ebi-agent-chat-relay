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
