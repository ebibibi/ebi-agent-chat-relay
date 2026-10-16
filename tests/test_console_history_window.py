"""The console's history window counts conversation, not hidden activity.

In a long thread most Discord messages are activity (tool-call logs, footers,
"Session running"), which the console folds away. A window of the latest N raw
messages then held only a handful of real ones, and a file the agent delivered
a few turns earlier vanished from the console although it was in the thread.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import discord
import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.console.auth import ConsoleAuthConfig, ConsoleAuthenticator
from claude_discord.console.server import ConsoleServer
from claude_discord.console.work_repo import WorkItemRepository

TOKEN = "k" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}
CHANNEL = 100
THREAD = 200


def _msg(mid: int, content: str, *, files: tuple[str, ...] = ()) -> SimpleNamespace:
    return SimpleNamespace(
        id=mid,
        content=content,
        author=SimpleNamespace(bot=True, display_name="ccdb"),
        type=SimpleNamespace(name="default"),
        embeds=[],
        attachments=[
            SimpleNamespace(
                filename=f, url=f"https://cdn.discordapp.com/{f}", size=1, content_type=None
            )
            for f in files
        ],
        created_at=None,
        jump_url=None,
    )


def _thread_messages() -> list[SimpleNamespace]:
    """Oldest first: a delivered file, then far more activity than one window."""
    out = [_msg(1, "Here is the article", files=("article.md",))]
    mid = 2
    for turn in range(30):
        for _ in range(8):
            out.append(_msg(mid, "-# ⏺ Session running"))
            mid += 1
        out.append(_msg(mid, f"answer {turn}"))
        mid += 1
    return out


@pytest.fixture
async def client(tmp_path):
    repo = WorkItemRepository(str(tmp_path / "sessions.db"))
    await repo.init_db()
    thread = MagicMock(spec=discord.Thread)
    thread.id = THREAD
    thread.parent_id = CHANNEL
    stored = _thread_messages()
    asked: list[int | None] = []

    async def history(limit: int | None = 100, before=None):
        asked.append(limit)
        newest_first = [m for m in reversed(stored) if before is None or m.id < before.id]
        for m in newest_first[:limit]:
            yield m

    thread.history = history
    api = MagicMock()
    api.default_channel_id = CHANNEL
    api.lineage_repo = None
    api.session_repo = None
    api.bot.get_channel.side_effect = lambda cid: thread if cid == THREAD else None
    api.bot.cogs = {}
    api._running_thread_ids.return_value = set()
    console = ConsoleServer(api, repo, ConsoleAuthenticator(ConsoleAuthConfig(token=TOKEN)), port=0)
    async with TestClient(TestServer(console.app)) as c:
        c.asked = asked  # type: ignore[attr-defined]
        c.stored = stored  # type: ignore[attr-defined]
        yield c


async def _get(client, limit: int) -> list[dict]:
    resp = await client.get(f"/console/api/items/t{THREAD}/messages?limit={limit}", headers=AUTH)
    assert resp.status == 200
    return (await resp.json())["messages"]


async def test_a_delivered_file_stays_visible_behind_activity(client) -> None:
    messages = await _get(client, 100)
    shown = [m for m in messages if m["kind"] != "activity"]
    # 31 conversation messages exist; all fit in a window of 100 of them.
    assert len(shown) == 31
    assert shown[0]["attachments"][0]["filename"] == "article.md"
    # Oldest first, and the activity between them is still there for the toggle.
    assert [m["id"] for m in messages] == sorted(m["id"] for m in messages)
    assert sum(m["kind"] == "activity" for m in messages) == 240


async def test_the_window_is_counted_in_conversation_messages(client) -> None:
    messages = await _get(client, 5)
    shown = [m["content"] for m in messages if m["kind"] != "activity"]
    assert shown == [f"answer {t}" for t in range(25, 30)]
    # It stops reading once the window is full: the oldest kept message is a
    # conversation one, not a run of activity before it.
    assert messages[0]["kind"] != "activity"


async def test_reading_older_history_is_bounded(client) -> None:
    await _get(client, 100)
    assert all(limit is not None and limit <= 500 for limit in client.asked)


async def test_polling_a_long_thread_reads_one_page_after_the_first(client) -> None:
    await _get(client, 100)
    client.asked.clear()
    # A new answer arrives while the client polls a running thread.
    client.stored.append(_msg(10_000, "fresh answer"))
    messages = await _get(client, 100)
    assert client.asked == [100]
    shown = [m["content"] for m in messages if m["kind"] != "activity"]
    assert shown[-1] == "fresh answer"
    # Nothing between the cached span and the new page went missing.
    assert shown[0] == "Here is the article" and len(shown) == 32
    assert sum(m["kind"] == "activity" for m in messages) == 240
