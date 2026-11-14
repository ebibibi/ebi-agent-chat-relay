"""Secretary mode: the decision fields triage adds, the summary built from the board, and the
trial metrics (docs/console.md, ADR-0024)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.console.auth import ConsoleAuthConfig, ConsoleAuthenticator
from claude_discord.console.secretary import (
    MAX_DECIDE,
    SecretaryEventRepository,
    build_secretary,
)
from claude_discord.console.server import ConsoleServer
from claude_discord.console.triage import (
    Triager,
    TriageRepository,
    TriageResult,
    apply_triage,
    build_prompt,
    parse_reply,
)
from claude_discord.console.work_repo import WorkItemRepository

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


# -- triage: the same call answers "what is being asked" --------------------


def test_the_prompt_asks_for_the_decision_only_when_the_move_is_the_persons() -> None:
    asked = build_prompt(title="t", status="waiting", messages=[], now=NOW)
    assert '"ask"' in asked and '"suggest"' in asked and '"options"' in asked
    for status in ("running", "idle", "todo"):
        plain = build_prompt(title="t", status=status, messages=[], now=NOW)
        assert '"ask"' not in plain


def test_the_decision_fields_are_parsed_and_clamped() -> None:
    raw = (
        '{"priority": 1, "ask": "  Merge   PR 12 now?  ", "suggest": "Yes, merge it",'
        ' "options": ["Yes", "No", "Yes", 3, "", "Later", "Ask again"]}'
    )
    result = parse_reply(raw, now=NOW)
    assert result is not None
    assert result.ask == "Merge PR 12 now?"
    assert result.suggest == "Yes, merge it"
    assert result.options == ("Yes", "No", "Later")


def test_a_reply_without_decision_fields_leaves_them_empty() -> None:
    result = parse_reply('{"priority": 2, "options": "Yes"}', now=NOW)
    assert result is not None
    assert (result.ask, result.suggest, result.options) == ("", "", ())


@pytest.fixture
async def repo(tmp_path) -> TriageRepository:
    r = TriageRepository(str(tmp_path / "sessions.db"))
    await r.init_db()
    return r


async def test_the_decision_is_stored_and_reported_on_the_board(repo) -> None:
    result = TriageResult(1, None, "r", "n", ask="Merge?", suggest="Yes", options=("Yes", "No"))
    await repo.save("t1", result, basis="b", model="haiku", decision=True)
    (item,) = apply_triage([_item("t1")], await repo.all(), now=NOW)
    assert item["ai"]["ask"] == "Merge?"
    assert item["ai"]["suggest"] == "Yes"
    assert item["ai"]["options"] == ["Yes", "No"]


async def test_rows_from_before_the_decision_fields_are_filled_once_for_the_persons_items(
    repo,
) -> None:
    """No prompt-version bump: only items waiting on the person are asked again, once."""
    asked: list[str] = []

    async def ask(prompt: str) -> str | None:
        asked.append(prompt.split("Item title: ")[1].split("\n")[0])
        return '{"priority": 2, "ask": "Which one?"}'

    async def read(item):
        return [("person", "hi")]

    triager = Triager(repo, ask=ask, read_messages=read, per_pass=10)
    waiting, working = _item("t1", status="waiting"), _item("t2", status="idle")
    from claude_discord.console.triage import _basis

    # Rows written by the previous release: same basis, no decision recorded.
    for item in (waiting, working):
        await repo.save(item["id"], TriageResult(2, None, "", ""), basis=_basis(item), model="m")
    assert await triager.run_once([waiting, working]) == 1
    assert asked == ["Item t1"]
    assert (await repo.all())["t1"].ask == "Which one?"

    asked.clear()
    assert await triager.run_once([waiting, working]) == 0
    assert asked == []


async def test_a_person_turn_with_no_question_is_not_asked_again(repo) -> None:
    async def ask(prompt: str) -> str | None:
        return '{"priority": 2}'

    async def read(item):
        return []

    triager = Triager(repo, ask=ask, read_messages=read)
    items = [_item("t1", status="review")]
    assert await triager.run_once(items) == 1
    assert (await repo.all())["t1"].ask == ""
    assert triager.due(items, await repo.all()) == []


# -- the summary: deterministic, no model ----------------------------------


def test_decide_now_holds_at_most_three_ranked_questions() -> None:
    items = [
        _item("t1", focus=120, ai={"ask": "Pick A or B?", "suggest": "A", "options": ["A", "B"]}),
        _item("t2", focus=320, status="action", ai={"next_step": "Sign in", "reason": "MFA"}),
        _item("t3", focus=220, status="error"),
        _item("t4", focus=120, last_activity_at=_ts(-timedelta(hours=5))),
        _item("t5", focus=120, actionable=False, ai={"waiting_for": "tomorrow"}),
        _item("t6", status="review", bucket="me"),
    ]
    out = build_secretary(items, now=NOW)
    decide = out["decide"]
    assert MAX_DECIDE == 3
    assert [d["id"] for d in decide] == ["t2", "t3", "t4"]
    assert decide[0]["ask"] == "Sign in"  # no question recorded: the next step stands in
    assert decide[0]["why"] == "MFA"
    assert decide[1]["ask"] == "Item t3"  # nothing at all: the title
    assert out["more"] == 1  # t1 is beyond the three
    assert out["later"] == 1  # t5 waits for something else
    assert [r["id"] for r in out["review"]] == ["t6"]


def test_a_decision_carries_its_suggestion_options_and_how_long_it_has_waited() -> None:
    items = [_item("t1", ai={"ask": "Pick?", "suggest": "A", "options": ["A", "B"]})]
    (card,) = build_secretary(items, now=NOW)["decide"]
    assert card["suggest"] == "A"
    assert card["options"] == ["A", "B"]
    assert card["since"] == _ts(-timedelta(hours=1))
    assert card["status"] == "waiting"
    assert card["thread_id"] == "1"


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
    assert build_secretary(items, now=NOW) == build_secretary(items, now=NOW)


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
