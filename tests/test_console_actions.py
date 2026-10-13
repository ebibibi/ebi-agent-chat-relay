"""Pressing a Discord message's buttons from the Ebi AI Console."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import discord
import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.console.auth import ConsoleAuthConfig, ConsoleAuthenticator
from claude_discord.console.messages import serialize_message
from claude_discord.console.server import CSRF_HEADER, ConsoleServer
from claude_discord.console.work_repo import WorkItemRepository
from claude_discord.message_actions import MessageAction, message_actions

TOKEN = "k" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}
WRITE = {**AUTH, CSRF_HEADER: "1"}
CHANNEL = 100
THREAD = 200
MESSAGE = 300


def _message(content: str = "⏳ Still waiting", mid: int = MESSAGE) -> SimpleNamespace:
    return SimpleNamespace(
        id=mid,
        content=content,
        author=SimpleNamespace(bot=True, display_name="ccdb"),
        type=SimpleNamespace(name="default"),
        embeds=[],
        attachments=[],
        created_at=None,
        jump_url=None,
    )


@pytest.fixture
async def client(tmp_path):
    repo = WorkItemRepository(str(tmp_path / "sessions.db"))
    await repo.init_db()
    thread = MagicMock(spec=discord.Thread)
    thread.id = THREAD
    thread.parent_id = CHANNEL

    async def history(limit: int):
        for m in [_message()]:
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
        yield c
    message_actions.clear(MESSAGE)


def _offer() -> list[str]:
    pressed: list[str] = []

    async def approve(who: str) -> None:
        pressed.append(who)

    message_actions.register(THREAD, MESSAGE, [MessageAction("✅ Approve", approve, "success")])
    return pressed


async def test_messages_carry_the_actions_and_pressing_runs_them(client) -> None:
    pressed = _offer()
    body = await (await client.get(f"/console/api/items/t{THREAD}/messages", headers=AUTH)).json()
    [msg] = body["messages"]
    assert msg["actions"] == [{"id": "0", "label": "✅ Approve", "style": "success"}]

    url = f"/console/api/items/t{THREAD}/messages/{MESSAGE}/actions/0"
    assert (await client.post(url, headers=WRITE)).status == 200
    assert len(pressed) == 1

    # Answered: the button is gone and a second press is refused.
    assert (await client.post(url, headers=WRITE)).status == 404
    body = await (await client.get(f"/console/api/items/t{THREAD}/messages", headers=AUTH)).json()
    assert body["messages"][0]["actions"] == []


async def test_pressing_requires_csrf_and_a_watched_thread(client) -> None:
    pressed = _offer()
    url = f"/console/api/items/t{THREAD}/messages/{MESSAGE}/actions/0"
    assert (await client.post(url, headers=AUTH)).status == 403
    other = f"/console/api/items/t999/messages/{MESSAGE}/actions/0"
    assert (await client.post(other, headers=WRITE)).status == 404
    bad = f"/console/api/items/t{THREAD}/messages/abc/actions/0"
    assert (await client.post(bad, headers=WRITE)).status == 400
    assert pressed == []


async def test_a_failing_action_reports_an_error(client) -> None:
    async def boom(who: str) -> None:
        raise RuntimeError("x")

    message_actions.register(THREAD, MESSAGE, [MessageAction("Go", boom)])
    url = f"/console/api/items/t{THREAD}/messages/{MESSAGE}/actions/0"
    assert (await client.post(url, headers=WRITE)).status == 500


def test_a_message_with_actions_is_never_folded_away() -> None:
    # Subtext would normally be activity; a pending button keeps it visible.
    msg = _message("-# waiting")
    assert serialize_message(msg)["kind"] == "activity"
    out = serialize_message(msg, actions=[{"id": "0", "label": "Yes", "style": "success"}])
    assert out["kind"] == "agent"
    assert out["actions"][0]["label"] == "Yes"
