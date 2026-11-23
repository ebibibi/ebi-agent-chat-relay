"""The 🌙 night shift: one tap hands the agents' own next moves over for the night (#1511)."""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from claude_discord.console.console_state import ConsoleStateRepository
from claude_discord.console.morning import build_morning_brief, render_markdown
from claude_discord.console.night import NightSettingsError, parse_night_settings
from claude_discord.console.night_shift import (
    GRACE_SECONDS,
    NightShift,
    NightShiftError,
    instruction,
    pick,
    scope_text,
)

NOW = datetime(2026, 11, 23, 22, 0, tzinfo=UTC)


def _item(n: int, status: str = "idle", **extra: Any) -> dict[str, Any]:
    return {
        "id": f"t{n}",
        "thread_id": n,
        "title": f"Thread {n}",
        "status": status,
        "running": status == "running",
        "last_activity_at": (NOW - timedelta(hours=n)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        **extra,
    }


# -- what it picks ----------------------------------------------------------------


def test_only_the_agents_own_next_moves_are_picked() -> None:
    items = [
        _item(1),
        _item(2, "waiting"),
        _item(3, "running"),
        _item(4, "candidate"),
        _item(5),  # has an open card
        _item(6),  # has an active wait
        _item(7, archived=True),
        _item(8, bucket="snoozed"),
        _item(9, last_activity_at="2026-09-01T00:00:00Z"),  # stale
        {"id": "c1", "thread_id": None, "status": "todo"},
        _item(10, queued=True),
        _item(11),
    ]
    chosen = pick(items, [{"thread_id": 5}], waiting=[6], limit=8, now=NOW)
    assert [i["id"] for i in chosen] == ["t1", "t11"]


def test_a_deferred_card_does_not_hold_the_thread_back_and_the_limit_holds() -> None:
    items = [_item(n) for n in range(1, 12)]
    chosen = pick(items, [{"thread_id": 1, "deferred_at": "x"}], limit=3, now=NOW)
    assert [i["id"] for i in chosen] == ["t1", "t2", "t3"]


def test_pinned_and_focused_come_first() -> None:
    items = [_item(1), _item(2, focus=5), _item(3, pinned_at="2026-11-01")]
    assert [i["id"] for i in pick(items, [], limit=8, now=NOW)] == ["t3", "t2", "t1"]


def test_the_instruction_forbids_merge_send_publish_and_puts_the_note_first() -> None:
    text = instruction("先にPR #5", scope_text([]), "ja")
    lines = text.splitlines()
    assert "本人の返事ではありません" in lines[0]
    assert lines[1] == "本人からの最優先の指示: 先にPR #5"
    assert "マージ・送信・公開・課金・削除・本番変更" in text
    assert "マージはしない" in text  # the default scope
    assert "drafts only" in instruction(None, scope_text(["drafts only"], "en"), "en")
    assert "Top priority" not in instruction(None, "x", "en")


def test_shift_limit_is_a_setting_with_a_default() -> None:
    assert parse_night_settings({}).shift_limit == 8
    assert parse_night_settings({"shift_limit": 3}).shift_limit == 3
    with pytest.raises(NightSettingsError):
        parse_night_settings({"shift_limit": 0})
    with pytest.raises(NightSettingsError):
        parse_night_settings({"shift_limit": True})


# -- the shift ------------------------------------------------------------------


@pytest.fixture
async def repo():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = ConsoleStateRepository(path)
    await store.init_db()
    yield store
    os.unlink(path)


class World:
    def __init__(self) -> None:
        self.items = [_item(1), _item(2), _item(3, "waiting")]
        self.requests: list[dict[str, Any]] = []
        self.sent: list[tuple[str, str]] = []
        self.clock = [NOW]
        self.fail: set[str] = set()

    async def board(self) -> list[dict[str, Any]]:
        return [dict(i) for i in self.items]

    async def open_requests(self) -> list[dict[str, Any]]:
        return list(self.requests)

    async def deliver(self, item_id: str, text: str) -> bool:
        if item_id in self.fail:
            return False
        self.sent.append((item_id, text))
        return True

    async def waiting(self) -> set[int]:
        return set()

    async def limit(self) -> int:
        return 8


def _shift(repo: ConsoleStateRepository, world: World) -> NightShift:
    return NightShift(
        repo,
        board=world.board,
        requests=world.open_requests,
        deliver=world.deliver,
        waiting=world.waiting,
        scopes=lambda: ["drafts and read-only checks"],
        limit=world.limit,
        clock=lambda: world.clock[0],
    )


async def test_a_tap_plans_waits_for_the_grace_period_then_sends(repo) -> None:
    world = World()
    night = _shift(repo, world)
    shift = await night.plan("  PRを優先  ", "ebi")
    assert [e["id"] for e in shift["items"]] == ["t1", "t2"]
    assert shift["note"] == "PRを優先" and shift["by"] == "ebi"
    assert await night.tick() == 0 and not world.sent  # still in the grace period
    world.clock[0] = NOW + timedelta(seconds=GRACE_SECONDS)
    assert await night.tick() == 2
    assert {i for i, _ in world.sent} == {"t1", "t2"}
    text = world.sent[0][1]
    assert "PRを優先" in text and "drafts and read-only checks" in text
    assert await night.tick() == 0  # nothing is sent twice


async def test_items_can_be_taken_out_before_they_are_sent(repo) -> None:
    world = World()
    night = _shift(repo, world)
    await night.plan(None, "ebi")
    await night.cancel("t1")
    world.clock[0] = NOW + timedelta(seconds=GRACE_SECONDS)
    await night.tick()
    assert [i for i, _ in world.sent] == ["t2"]
    await night.plan(None, "ebi")
    await night.cancel()
    world.clock[0] += timedelta(seconds=GRACE_SECONDS)
    await night.tick()
    assert [i for i, _ in world.sent] == ["t2"]


async def test_sending_re_checks_and_skips_what_changed(repo) -> None:
    world = World()
    night = _shift(repo, world)
    await night.plan(None, "ebi")
    world.requests = [{"thread_id": 1}]  # the agent asked something meanwhile
    world.fail = {"t2"}
    world.clock[0] = NOW + timedelta(seconds=GRACE_SECONDS)
    assert await night.tick() == 0
    states = {e["id"]: e["state"] for e in (await night.current())["items"]}
    assert states == {"t1": "skipped", "t2": "skipped"}


async def test_a_second_tap_does_not_hand_the_same_thread_over_again(repo) -> None:
    world = World()
    night = _shift(repo, world)
    await night.plan(None, "ebi")
    world.clock[0] = NOW + timedelta(seconds=GRACE_SECONDS)
    await night.tick()
    again = await night.plan(None, "ebi")
    assert [e["state"] for e in again["items"]] == ["sent", "sent"]


async def test_a_long_note_is_refused(repo) -> None:
    night = _shift(repo, World())
    with pytest.raises(NightShiftError):
        await night.plan("x" * 1001, "ebi")
    with pytest.raises(NightShiftError):
        await night.plan(3, "ebi")


async def test_the_morning_brief_shows_what_the_night_shift_handed_over() -> None:
    shift = {
        "items": [
            {"id": "t1", "title": "Thread 1", "state": "sent", "sent_at": "2026-11-23T22:02:00Z"},
            {"id": "t2", "title": "Thread 2", "state": "cancelled", "sent_at": None},
        ]
    }
    morning = datetime(2026, 11, 24, 7, 0, tzinfo=UTC)
    brief = await build_morning_brief([_item(1, "candidate")], [], now=morning, shift=shift)
    assert brief["night"] == [
        {"id": "t1", "title": "Thread 1", "status": "candidate", "link": "/#focus/t1"}
    ]
    assert "🌙 夜番が任せたもの" in render_markdown(brief) and "🏁" in render_markdown(brief)
    assert "夜番" not in render_markdown(await build_morning_brief([], [], now=morning))


async def test_the_console_routes_plan_show_and_cancel(tmp_path) -> None:
    from unittest.mock import MagicMock

    from aiohttp.test_utils import TestClient, TestServer

    from claude_discord.console.auth import ConsoleAuthConfig, ConsoleAuthenticator
    from claude_discord.console.server import ConsoleServer
    from claude_discord.console.work_repo import WorkItemRepository

    db = str(tmp_path / "sessions.db")
    work = WorkItemRepository(db)
    await work.init_db()
    state_repo = ConsoleStateRepository(db)
    await state_repo.init_db()
    api = MagicMock()
    api.default_channel_id = None
    api.lineage_repo = None
    api.session_repo = None
    api.wait_repo = None
    api.bot.get_channel.return_value = None
    api.bot.cogs = {}
    api._running_thread_ids.return_value = set()
    console = ConsoleServer(
        api,
        work,
        ConsoleAuthenticator(ConsoleAuthConfig(token="tok")),
        port=0,
        state_repo=state_repo,
    )
    client = TestClient(TestServer(console.app))
    await client.start_server()
    headers = {"Authorization": "Bearer tok", "X-Console-Request": "1"}
    try:
        assert (await client.post("/console/api/night/shift", json={})).status in (401, 403)
        made = await client.post("/console/api/night/shift", json={"note": "PR先"}, headers=headers)
        body = await made.json()
        assert made.status == 201 and body["shift"]["note"] == "PR先"
        assert body["grace_seconds"] == GRACE_SECONDS
        bad = await client.post("/console/api/night/shift", json={"note": 5}, headers=headers)
        assert bad.status == 400
        shown = await (await client.get("/console/api/night/shift", headers=headers)).json()
        assert shown["shift"]["by"]
        gone = await client.delete("/console/api/night/shift", headers=headers)
        assert gone.status == 200
    finally:
        await client.close()
