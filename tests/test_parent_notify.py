"""Tests for child→parent reports (Issue #820).

The contract from the Issue: a spawned child that opted in produces exactly one
queued message in its parent when it marks itself finished; a child spawned
without the opt-in produces none. Around that: the opt-in is stored only on a
link the relay recorded, repeats and loops are bounded, and a failed report
never fails the child's mark.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.database.lineage_repo import ThreadLineageRepository
from claude_discord.database.models import init_db
from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.ext.api_server import ApiServer
from claude_discord.ext.parent_notify import (
    DEDUPE_SECONDS,
    DEFAULT_ENV_VAR,
    MAX_PER_CHILD_ENV_VAR,
    ParentNotifier,
    build_report_prompt,
    parse_opt_in,
)
from claude_discord.relay import RelayGuard
from claude_discord.thread_marker import (
    OUTCOME_CANDIDATE,
    OUTCOME_DONE,
    OUTCOME_WAITING,
    family_code,
)

PARENT = 1550121254361768021
CHILD = 1550121254361768999


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DEFAULT_ENV_VAR, raising=False)
    monkeypatch.delenv(MAX_PER_CHILD_ENV_VAR, raising=False)


@pytest.fixture
async def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(path)
    yield path
    os.unlink(path)


@pytest.fixture
async def lineage(db_path: str) -> ThreadLineageRepository:
    return ThreadLineageRepository(db_path)


class _Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


def _notifier(lineage: ThreadLineageRepository, dispatch: AsyncMock, clock: _Clock):
    return ParentNotifier(lineage, RelayGuard(), dispatch, wall_clock=clock, monotonic=clock)


async def _opted_in_child(lineage: ThreadLineageRepository) -> None:
    await lineage.record(CHILD, PARENT, family_code(PARENT))
    assert await lineage.set_notify_parent(CHILD, True) is True


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


async def test_opt_in_needs_a_recorded_link(lineage: ThreadLineageRepository) -> None:
    assert await lineage.set_notify_parent(CHILD, True) is False
    assert await lineage.notify_target(CHILD) is None


async def test_link_without_opt_in_has_no_target(lineage: ThreadLineageRepository) -> None:
    await lineage.record(CHILD, PARENT, family_code(PARENT))
    assert await lineage.notify_target(CHILD) is None


async def test_record_notified_counts(lineage: ThreadLineageRepository) -> None:
    await _opted_in_child(lineage)
    await lineage.record_notified(CHILD, OUTCOME_CANDIDATE, 5.0)
    target = await lineage.notify_target(CHILD)
    assert target is not None
    assert (target.parent_thread_id, target.last_outcome, target.last_at, target.count) == (
        PARENT,
        OUTCOME_CANDIDATE,
        5.0,
        1,
    )


# ---------------------------------------------------------------------------
# Notifier rules
# ---------------------------------------------------------------------------


async def test_opted_in_child_reports_once(lineage: ThreadLineageRepository) -> None:
    await _opted_in_child(lineage)
    dispatch = AsyncMock(return_value=True)
    notifier = _notifier(lineage, dispatch, _Clock())

    result = await notifier.on_outcome(
        CHILD, OUTCOME_CANDIDATE, thread_name="🏁 Audit DNS", changed=True
    )

    assert result == "sent"
    dispatch.assert_awaited_once()
    parent, prompt, child = dispatch.await_args.args
    assert (parent, child) == (PARENT, CHILD)
    assert "Audit DNS" in prompt and "NOT from your human" in prompt


async def test_child_without_opt_in_reports_nothing(lineage: ThreadLineageRepository) -> None:
    await lineage.record(CHILD, PARENT, family_code(PARENT))
    dispatch = AsyncMock(return_value=True)

    result = await _notifier(lineage, dispatch, _Clock()).on_outcome(
        CHILD, OUTCOME_CANDIDATE, thread_name="x", changed=True
    )

    assert result == "not_opted_in"
    dispatch.assert_not_awaited()


async def test_unchanged_mark_is_not_reported(lineage: ThreadLineageRepository) -> None:
    await _opted_in_child(lineage)
    dispatch = AsyncMock(return_value=True)
    result = await _notifier(lineage, dispatch, _Clock()).on_outcome(
        CHILD, OUTCOME_CANDIDATE, thread_name="x", changed=False
    )
    assert result == "unchanged"
    dispatch.assert_not_awaited()


async def test_same_outcome_soon_after_is_a_duplicate(lineage: ThreadLineageRepository) -> None:
    await _opted_in_child(lineage)
    dispatch = AsyncMock(return_value=True)
    clock = _Clock()
    notifier = _notifier(lineage, dispatch, clock)

    assert await notifier.on_outcome(CHILD, OUTCOME_DONE, thread_name="x", changed=True) == "sent"
    clock.now += 5
    assert (
        await notifier.on_outcome(CHILD, OUTCOME_DONE, thread_name="x", changed=True) == "duplicate"
    )
    clock.now += DEDUPE_SECONDS
    assert await notifier.on_outcome(CHILD, OUTCOME_DONE, thread_name="x", changed=True) == "sent"
    assert dispatch.await_count == 2


async def test_a_new_state_is_reported(lineage: ThreadLineageRepository) -> None:
    await _opted_in_child(lineage)
    dispatch = AsyncMock(return_value=True)
    clock = _Clock()
    notifier = _notifier(lineage, dispatch, clock)
    await notifier.on_outcome(CHILD, OUTCOME_WAITING, thread_name="x", changed=True)
    clock.now += 61  # outside the relay guard's duplicate window
    result = await notifier.on_outcome(CHILD, OUTCOME_CANDIDATE, thread_name="x", changed=True)
    assert result == "sent"


async def test_reports_per_child_are_capped(
    lineage: ThreadLineageRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A parent and child handing work back and forth cannot wake each other forever."""
    monkeypatch.setenv(MAX_PER_CHILD_ENV_VAR, "2")
    await _opted_in_child(lineage)
    dispatch = AsyncMock(return_value=True)
    clock = _Clock()
    notifier = _notifier(lineage, dispatch, clock)
    results = []
    for _ in range(3):
        results.append(
            await notifier.on_outcome(CHILD, OUTCOME_CANDIDATE, thread_name="x", changed=True)
        )
        clock.now += DEDUPE_SECONDS + 1
    assert results == ["sent", "sent", "cap_reached"]


async def test_failed_delivery_is_retryable(lineage: ThreadLineageRepository) -> None:
    await _opted_in_child(lineage)
    dispatch = AsyncMock(side_effect=[False, True])
    notifier = _notifier(lineage, dispatch, _Clock())
    first = await notifier.on_outcome(CHILD, OUTCOME_CANDIDATE, thread_name="x", changed=True)
    second = await notifier.on_outcome(CHILD, OUTCOME_CANDIDATE, thread_name="x", changed=True)
    assert (first, second) == ("not_delivered", "sent")


async def test_dispatch_error_never_raises(lineage: ThreadLineageRepository) -> None:
    await _opted_in_child(lineage)
    dispatch = AsyncMock(side_effect=RuntimeError("boom"))
    result = await _notifier(lineage, dispatch, _Clock()).on_outcome(
        CHILD, OUTCOME_CANDIDATE, thread_name="x", changed=True
    )
    assert result == "error"


def test_parse_opt_in() -> None:
    assert parse_opt_in(None) is None
    assert parse_opt_in(True) is True
    assert parse_opt_in(False) is False
    with pytest.raises(ValueError):
        parse_opt_in("yes")


def test_report_prompt_points_at_the_child() -> None:
    prompt = build_report_prompt(child_thread_id=CHILD, child_title="Fix", outcome=OUTCOME_WAITING)
    assert f"/api/threads/{CHILD}/messages" in prompt
    assert "waiting" in prompt
    assert "one combined report" in prompt


# ---------------------------------------------------------------------------
# HTTP: marks and spawns
# ---------------------------------------------------------------------------


@pytest.fixture
def bot() -> MagicMock:
    b = MagicMock()
    b.cogs = {}
    b.headless = False
    b.get_channel.return_value = None
    b.fetch_channel = AsyncMock(side_effect=RuntimeError("Unknown Channel"))
    return b


@pytest.fixture
async def api(bot: MagicMock, db_path: str, lineage: ThreadLineageRepository):
    repo = NotificationRepository(db_path)
    await repo.init_db()
    server = ApiServer(
        repo=repo,
        bot=bot,
        default_channel_id=1,
        host="127.0.0.1",
        port=0,
        lineage_repo=lineage,
    )
    server._dispatch_relay = AsyncMock(  # type: ignore[method-assign]
        return_value=web.json_response({"status": "delivered"}, status=202)
    )
    client = TestClient(TestServer(server.app))
    await client.start_server()
    yield server, client
    await client.close()


def _thread(name: str) -> MagicMock:
    thread = MagicMock(spec=discord.Thread)
    thread.name = name
    thread.edit = AsyncMock()
    return thread


async def test_mark_queues_one_report_in_the_parent(api, bot, lineage) -> None:
    server, client = api
    await _opted_in_child(lineage)
    bot.get_channel.return_value = _thread("Audit DNS")

    resp = await client.post(f"/api/threads/{CHILD}/candidate")

    assert resp.status == 200
    server._dispatch_relay.assert_awaited_once()
    call = server._dispatch_relay.await_args
    assert call.args == (PARENT,)
    assert call.kwargs["mode"] == "queue"
    assert call.kwargs["from_thread"] == CHILD
    assert "CHILD THREAD REPORT" in call.kwargs["prompt"]


async def test_mark_without_opt_in_queues_nothing(api, bot, lineage) -> None:
    server, client = api
    await lineage.record(CHILD, PARENT, family_code(PARENT))
    bot.get_channel.return_value = _thread("Audit DNS")

    resp = await client.post(f"/api/threads/{CHILD}/candidate")

    assert resp.status == 200
    server._dispatch_relay.assert_not_awaited()


async def test_spawn_rejects_a_non_boolean_opt_in(api) -> None:
    _, client = api
    resp = await client.post(
        "/api/spawn", json={"prompt": "x", "parent_thread_id": PARENT, "notify_parent": "yes"}
    )
    assert resp.status == 400


async def test_spawn_opt_in_needs_a_parent(api) -> None:
    _, client = api
    resp = await client.post("/api/spawn", json={"prompt": "x", "notify_parent": True})
    assert resp.status == 400


def _spawnable(bot: MagicMock) -> MagicMock:
    channel = MagicMock(spec=discord.TextChannel)
    bot.get_channel.side_effect = lambda cid: channel if cid == 1 else None
    child = MagicMock()
    child.id = CHILD
    child.name = "child"
    cog = MagicMock()
    cog.spawn_session = AsyncMock(return_value=child)
    bot.cogs = {"ClaudeChatCog": cog}
    return cog


@pytest.mark.parametrize(
    ("body_flag", "env", "expected"),
    [
        (True, None, True),
        (None, None, False),
        (None, "1", True),
        (False, "1", False),
    ],
)
async def test_spawn_stores_the_opt_in(
    api, bot, lineage, monkeypatch: pytest.MonkeyPatch, body_flag, env, expected
) -> None:
    _, client = api
    _spawnable(bot)
    if env is not None:
        monkeypatch.setenv(DEFAULT_ENV_VAR, env)
    body: dict[str, object] = {"prompt": "x", "parent_thread_id": PARENT}
    if body_flag is not None:
        body["notify_parent"] = body_flag

    resp = await client.post("/api/spawn", json=body)

    assert resp.status == 201
    assert (await resp.json())["notify_parent"] is expected
    assert (await lineage.notify_target(CHILD) is not None) is expected
