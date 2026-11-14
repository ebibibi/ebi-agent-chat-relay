"""Secretary mode: the home built from the board and the agents' decision requests, and the
trial metrics (docs/console.md, ADR-0024)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from aiohttp.test_utils import TestClient, TestServer

from claude_discord.console.auth import ConsoleAuthConfig, ConsoleAuthenticator
from claude_discord.console.secretary import (
    MAX_CARDS,
    SecretaryEventRepository,
    build_secretary,
)
from claude_discord.console.server import ConsoleServer
from claude_discord.console.surface import apply_outcome
from claude_discord.console.work_repo import WorkItemRepository
from claude_discord.database.decision_repo import DecisionRequestRepository
from claude_discord.decision_request import parse_request
from claude_discord.thread_marker import OUTCOME_WAITING
from tests.test_console_conversations import AUTH, WRITE, _settle, _start
from tests.test_console_conversations import env as conversation_env
from tests.test_decision_requests import _good

env = conversation_env

# 12:00 in Tokyo, so "today" is the same date in UTC and in local time for the fixtures.
NOW = datetime(2026, 11, 14, 3, 0, tzinfo=UTC)
TOKEN = "t" * 40


def _ts(delta: timedelta) -> str:
    return (NOW + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def _item(item_id: str, **over):
    base = {
        "id": item_id,
        "title": f"Item {item_id}",
        "thread_id": item_id.lstrip("t") or "1",
        "status": "waiting",
        "bucket": "me",
        "priority": 2,
        "due_at": None,
        "overdue": False,
        "parent_id": None,
        "last_activity_at": _ts(-timedelta(hours=1)),
        "focus": 120,
        "actionable": True,
        "running": False,
    }
    base.update(over)
    return base


def _request(thread: int, *, rid: int = 1, kind: str = "waiting", origin: str = "mark", **over):
    """A stored request as the board reads it (DecisionRequest.as_dict())."""
    base = {
        "id": rid,
        "thread_id": str(thread),
        "source_thread_id": str(thread),
        "key": "",
        "kind": kind,
        "origin": origin,
        "card": {
            "title": f"Decide {thread}",
            "context": "What this is about, from zero.",
            "options": [
                {"label": "A", "effect": "Does A", "recommended": True},
                {"label": "B", "effect": "Does B", "recommended": False},
            ],
            "deadline": "Today",
        },
        "detail": {"about": "x"},
        "created_at": _ts(-timedelta(minutes=30)),
    }
    base.update(over)
    return base


# -- the summary: deterministic, no model ----------------------------------


def test_only_written_requests_become_cards_ranked_like_focus() -> None:
    items = [
        _item("t1", focus=120),
        _item("t2", focus=320, status="action"),
        _item("t3", focus=220, status="error"),
        _item("t4", focus=120, last_activity_at=_ts(-timedelta(hours=5))),
        _item("t6", status="review", bucket="me"),
    ]
    requests = [
        _request(1, rid=1),
        _request(2, rid=2, kind="action"),
        _request(4, rid=4),
        _request(6, rid=6, kind="review"),
    ]
    out = build_secretary(items, requests, now=NOW)
    assert [c["id"] for c in out["decide"]] == ["t2", "t4", "t1"]
    card = out["decide"][0]
    assert card["request_id"] == 2
    assert card["card"]["title"] == "Decide 2"
    assert card["detail"] == {"about": "x"}
    assert card["since"] == _ts(-timedelta(minutes=30))
    assert card["thread_id"] == "2"
    assert [c["id"] for c in out["review"]] == ["t6"]
    # t3 failed and nobody wrote a request: listed apart, never summarised by a model.
    assert [u["id"] for u in out["unexplained"]] == ["t3"]
    assert out["unexplained"][0]["status"] == "error"
    assert out["calm"] is False


def test_an_item_waiting_with_no_request_is_shown_as_unexplained() -> None:
    items = [_item("t1", ai={"next_step": "Sign in", "reason": "MFA"})]
    out = build_secretary(items, [], now=NOW)
    assert out["decide"] == []
    (entry,) = out["unexplained"]
    assert entry == {
        "id": "t1",
        "title": "Item t1",
        "status": "waiting",
        "since": _ts(-timedelta(hours=1)),
    }


def test_a_request_is_hidden_once_its_thread_moves_on() -> None:
    items = [
        _item("t1", status="running", running=True, focus=None),  # the person replied
        _item("t2", status="candidate", bucket="candidate", focus=None),
        _item("t3", status="idle", focus=None),  # marker cleared, agent not marking again
        _item("t4", archived=True),
    ]
    requests = [_request(n, rid=n) for n in (1, 2, 3, 4, 9)]  # 9: no such item
    out = build_secretary(items, requests, now=NOW)
    assert out["decide"] == []
    assert out["unexplained"] == []


def test_one_thread_may_carry_two_questions() -> None:
    requests = [_request(1, rid=5, key="b"), _request(1, rid=3, key="a")]
    out = build_secretary([_item("t1")], requests, now=NOW)
    assert [c["request_id"] for c in out["decide"]] == [3, 5]
    assert out["unexplained"] == []


def test_cards_are_capped() -> None:
    total = MAX_CARDS + 5
    items = [_item(f"t{n}", focus=100 + n) for n in range(1, total + 1)]
    requests = [_request(n, rid=n) for n in range(1, total + 1)]
    out = build_secretary(items, requests, now=NOW)
    assert len(out["decide"]) == MAX_CARDS
    assert out["decide_more"] == 5


def test_agents_at_work_and_todays_finished_work_are_listed() -> None:
    items = [
        _item("t1", status="running", bucket="ai", running=True, focus=None),
        _item("t2", status="done", bucket="done", focus=None),
        _item("t3", status="candidate", bucket="candidate", focus=None),
        _item(
            "t4",
            status="done",
            bucket="done",
            focus=None,
            last_activity_at=_ts(-timedelta(days=2)),
        ),
    ]
    out = build_secretary(items, now=NOW)
    assert [r["id"] for r in out["working"]] == ["t1"]
    assert {r["id"] for r in out["done_today"]} == {"t2", "t3"}
    assert next(r for r in out["done_today"] if r["id"] == "t3")["candidate"] is True
    assert out["calm"] is True


def test_the_summary_is_the_same_twice() -> None:
    items = [_item(f"t{i}", focus=100 + i) for i in range(6)]
    requests = [_request(i, rid=i) for i in range(3)]
    assert build_secretary(items, requests, now=NOW) == build_secretary(items, requests, now=NOW)


# -- trial metrics ---------------------------------------------------------


async def test_events_are_kept_as_metadata_and_counted_per_day(tmp_path) -> None:
    events = SecretaryEventRepository(str(tmp_path / "sessions.db"))
    await events.init_db()
    await events.record("open", "t1", mode="classic", at=NOW)
    await events.record("open", "t1", mode="classic", at=NOW + timedelta(minutes=5))
    await events.record("open", "t2", mode="secretary", at=NOW)
    await events.record("answer", "t2", mode="secretary", waited_seconds=3600, at=NOW)
    stats = await events.stats(days=7, now=NOW)
    (today,) = stats["days"]
    assert today["opened_threads"] == 2
    assert today["card_answers"] == 1
    assert today["answer_wait_seconds_median"] == 3600
    assert today["by_mode"] == {"classic": 1, "secretary": 1}


async def _client(tmp_path) -> TestClient:
    db = str(tmp_path / "sessions.db")
    work = WorkItemRepository(db)
    await work.init_db()
    events = SecretaryEventRepository(db)
    await events.init_db()
    api = MagicMock()
    api.default_channel_id = None
    api.lineage_repo = None
    api.session_repo = None
    api.bot.get_channel.return_value = None
    api.bot.cogs = {}
    api._running_thread_ids.return_value = set()
    console = ConsoleServer(
        api,
        work,
        ConsoleAuthenticator(ConsoleAuthConfig(token=TOKEN)),
        port=0,
        secretary_events=events,
    )
    client = TestClient(TestServer(console.app))
    await client.start_server()
    return client


async def test_the_events_endpoint_validates_and_records(tmp_path) -> None:
    client = await _client(tmp_path)
    headers = {"Authorization": f"Bearer {TOKEN}", "X-Console-Request": "1"}
    try:
        anonymous = await client.post(
            "/console/api/secretary/events",
            json={"kind": "open", "item_id": "t1"},
            headers={"X-Console-Request": "1"},
        )
        assert anonymous.status == 401
        ok = await client.post(
            "/console/api/secretary/events",
            json={"kind": "open", "item_id": "t1", "mode": "secretary"},
            headers=headers,
        )
        assert ok.status == 204
        for bad in (
            {"kind": "delete", "item_id": "t1"},
            {"kind": "open", "item_id": ""},
            {"kind": "open", "item_id": "x" * 300},
            {"kind": "open", "item_id": "t1", "mode": "other"},
            {"kind": "answer", "item_id": "t1", "waited_seconds": -1},
        ):
            res = await client.post("/console/api/secretary/events", json=bad, headers=headers)
            assert res.status == 400, bad
        stats = await client.get("/console/api/secretary/stats", headers=headers)
        assert stats.status == 200
        data = await stats.json()
        assert data["days"][-1]["opened_threads"] == 1
    finally:
        await client.close()


# -- the Console: cards on the board, closed by an answer --------------------


async def test_a_card_is_on_the_board_and_an_answer_closes_it(env, tmp_path) -> None:
    decisions = DecisionRequestRepository(str(tmp_path / "sessions.db"))
    env.console.api.decision_repo = decisions
    item_id = await _start(env)
    key = int(item_id[1:])
    await apply_outcome(env.conversations, key, OUTCOME_WAITING)
    spec = parse_request(_good(), kind="waiting")
    stored = await decisions.put(key, spec, origin="mark", source_thread_id=key)
    second = parse_request(_good(key="b"), kind="waiting")
    await decisions.put(key, second, origin="mark", source_thread_id=key)

    board = await (await env.client.get("/console/api/board", headers=AUTH)).json()
    cards = board["secretary"]["decide"]
    assert [c["request_id"] for c in cards] == [stored.id, stored.id + 1]
    assert cards[0]["card"]["title"] == spec.title

    replied = await env.client.post(
        f"/console/api/items/{item_id}/reply",
        json={"text": "A で消す", "request_id": stored.id},
        headers=WRITE,
    )
    assert replied.status == 202
    await _settle(env.host)
    assert [r.key for r in await decisions.list_open()] == ["b"]

    # Typing in the thread answers whatever it still asks.
    await env.client.post(
        f"/console/api/items/{item_id}/reply", json={"text": "both done"}, headers=WRITE
    )
    await _settle(env.host)
    assert await decisions.list_open() == []
