"""``POST /api/threads/{id}/complete`` — closing a thread on the user's explicit request (#1343).

The guard rails are the point: a quoted request, the caller's own family only,
and never a thread that is still running.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.console.work_repo import WorkItemError
from claude_discord.database.lineage_repo import ThreadLineageRepository
from claude_discord.database.models import init_db
from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.ext.api_server import ApiServer
from claude_discord.ext.thread_complete_api import is_descendant

PARENT = 1001
CHILD = 1002
GRANDCHILD = 1003
STRANGER = 2001
REQUEST = "終わった子スレッドを完了にして"


@pytest.fixture
async def setup():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(path)
    repo = NotificationRepository(path)
    await repo.init_db()
    lineage = ThreadLineageRepository(path)
    await lineage.record(CHILD, PARENT, "AB")
    await lineage.record(GRANDCHILD, CHILD, "AB")

    bot = MagicMock()
    bot.cogs = {"ClaudeChatCog": MagicMock(_active_runners={})}
    bot.session_registry = None
    api = ApiServer(
        repo=repo, bot=bot, default_channel_id=1, host="127.0.0.1", port=0, lineage_repo=lineage
    )
    item = MagicMock()
    item.as_dict.return_value = {"id": "t0", "state": "done"}
    console = MagicMock()
    console.complete_item = AsyncMock(return_value=(item, None))
    api._console = console

    client = TestClient(TestServer(api.app))
    await client.start_server()
    yield client, console, bot, lineage, api
    await client.close()
    os.unlink(path)


async def _complete(client: TestClient, target: int, body: object):
    return await client.post(f"/api/threads/{target}/complete", json=body)


async def test_parent_closes_its_child(setup) -> None:
    client, console, _, _, _ = setup
    resp = await _complete(client, CHILD, {"from_thread": PARENT, "user_request": REQUEST})
    assert resp.status == 200
    assert (await resp.json())["status"] == "done"
    console.complete_item.assert_awaited_once_with(f"t{CHILD}")


async def test_grandparent_closes_a_grandchild(setup) -> None:
    client, console, _, _, _ = setup
    resp = await _complete(client, GRANDCHILD, {"from_thread": PARENT, "user_request": REQUEST})
    assert resp.status == 200
    console.complete_item.assert_awaited_once_with(f"t{GRANDCHILD}")


async def test_a_thread_closes_itself_while_its_turn_runs(setup) -> None:
    client, console, bot, _, _ = setup
    bot.cogs["ClaudeChatCog"]._active_runners = {PARENT: object()}
    resp = await _complete(client, PARENT, {"from_thread": PARENT, "user_request": REQUEST})
    assert resp.status == 200
    console.complete_item.assert_awaited_once_with(f"t{PARENT}")


async def test_requires_the_users_words(setup) -> None:
    client, console, _, _, _ = setup
    for body in ({"from_thread": PARENT}, {"from_thread": PARENT, "user_request": "  "}):
        resp = await _complete(client, CHILD, body)
        assert resp.status == 400
        assert "/candidate" in (await resp.json())["error"]
    console.complete_item.assert_not_awaited()


async def test_rejects_an_overlong_request(setup) -> None:
    client, console, _, _, _ = setup
    resp = await _complete(client, CHILD, {"from_thread": PARENT, "user_request": "x" * 501})
    assert resp.status == 400
    console.complete_item.assert_not_awaited()


@pytest.mark.parametrize("caller", [None, "abc", 0, -5])
async def test_requires_a_valid_calling_thread(setup, caller: object) -> None:
    client, console, _, _, _ = setup
    resp = await _complete(client, CHILD, {"from_thread": caller, "user_request": REQUEST})
    assert resp.status == 400
    console.complete_item.assert_not_awaited()


async def test_rejects_a_non_object_body(setup) -> None:
    client, _, _, _, _ = setup
    resp = await _complete(client, CHILD, ["not", "an", "object"])
    assert resp.status == 400


async def test_rejects_a_bad_thread_id(setup) -> None:
    client, _, _, _, _ = setup
    resp = await client.post(
        "/api/threads/abc/complete", json={"from_thread": PARENT, "user_request": REQUEST}
    )
    assert resp.status == 400


@pytest.mark.parametrize(
    ("target", "caller"),
    [(STRANGER, PARENT), (PARENT, CHILD), (CHILD, GRANDCHILD), (CHILD, STRANGER)],
)
async def test_refuses_outside_the_callers_family(setup, target: int, caller: int) -> None:
    """No unrelated thread, and no climbing up: a child cannot close its parent."""
    client, console, _, _, _ = setup
    resp = await _complete(client, target, {"from_thread": caller, "user_request": REQUEST})
    assert resp.status == 403
    console.complete_item.assert_not_awaited()


async def test_refuses_a_child_that_is_still_running(setup) -> None:
    client, console, bot, _, _ = setup
    bot.cogs["ClaudeChatCog"]._active_runners = {CHILD: object()}
    resp = await _complete(client, CHILD, {"from_thread": PARENT, "user_request": REQUEST})
    assert resp.status == 409
    console.complete_item.assert_not_awaited()


async def test_reports_an_item_that_cannot_move(setup) -> None:
    client, console, _, _, _ = setup
    console.complete_item.side_effect = WorkItemError("no such item")
    resp = await _complete(client, CHILD, {"from_thread": PARENT, "user_request": REQUEST})
    assert resp.status == 400
    assert "no such item" in (await resp.json())["error"]


async def test_without_a_console_marks_the_discord_thread_done(setup) -> None:
    client, _, bot, _, api = setup
    api._console = None
    thread = MagicMock(spec=discord.Thread)
    thread.id = CHILD
    thread.name = "child work"
    thread.edit = AsyncMock()
    bot.get_channel.return_value = thread
    resp = await _complete(client, CHILD, {"from_thread": PARENT, "user_request": REQUEST})
    assert resp.status == 200
    assert (await resp.json())["status"] == "marked"
    thread.edit.assert_awaited_once()
    assert thread.edit.await_args.kwargs["name"].startswith("✅")


async def test_is_descendant_stops_on_a_cycle(setup) -> None:
    _, _, _, lineage, _ = setup
    await lineage.record(PARENT, GRANDCHILD, "AB")  # a corrupt loop
    assert not await is_descendant(lineage, CHILD, STRANGER)


async def test_is_descendant_without_lineage() -> None:
    assert not await is_descendant(None, CHILD, PARENT)
