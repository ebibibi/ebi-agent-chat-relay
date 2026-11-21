"""The Console loop that proceeds with a due card's recommendation (#1478).

It acts only on a card that names an approved scope, whose time has passed, that nobody moved
to Later, and whose thread is idle and unfinished. Everything else keeps waiting for a person.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from claude_discord.console.decision_defaults import DecisionDefaulter, notice
from claude_discord.database.decision_repo import DecisionRequestRepository
from claude_discord.database.models import init_db
from claude_discord.decision_default import DefaultPolicy
from claude_discord.decision_request import parse_request
from tests.test_decision_requests import GOOD

POLICY = DefaultPolicy(scopes={"drafts": "Write drafts only; publish or send nothing"})
NOW = datetime(2026, 11, 23, 9, 0, tzinfo=UTC)
THREAD = 4242


def _spec(after: datetime, *, scope: str = "drafts", key: str = ""):
    body = copy.deepcopy(GOOD)
    body["key"] = key
    body["if_no_answer"] = {"after": after.isoformat(), "scope": scope}
    return parse_request(body, kind="waiting", scopes={**POLICY.scopes, "other": "x"})


def _item(**over) -> dict:
    return {"id": f"t{THREAD}", "thread_id": str(THREAD), "status": "waiting", **over}


@pytest.fixture
async def repo(tmp_path):
    db = str(tmp_path / "db.sqlite")
    await init_db(db)
    return DecisionRequestRepository(db)


def _defaulter(repo, deliver=None, policy=POLICY):
    deliver = deliver or AsyncMock(return_value=True)
    return DecisionDefaulter(repo, policy=lambda: policy, deliver=deliver), deliver


async def test_due_card_proceeds_with_the_recommendation_and_closes(repo):
    stored = await repo.put(
        THREAD, _spec(NOW - timedelta(minutes=1)), origin="mark", source_thread_id=THREAD
    )
    defaulter, deliver = _defaulter(repo)
    assert await defaulter.run_once([_item()], now=NOW) == 1
    item_id, text = deliver.await_args.args
    assert item_id == f"t{THREAD}"
    assert "A で消す" in text  # the recommended option of GOOD
    assert "automatic" in text and "not the person's answer" in text
    assert await repo.list_open() == []
    assert stored.if_no_answer == {
        "after": (NOW - timedelta(minutes=1)).isoformat(),
        "scope": "drafts",
    }


async def test_card_before_its_time_waits(repo):
    await repo.put(
        THREAD, _spec(NOW + timedelta(minutes=1)), origin="mark", source_thread_id=THREAD
    )
    defaulter, deliver = _defaulter(repo)
    assert await defaulter.run_once([_item()], now=NOW) == 0
    deliver.assert_not_awaited()


async def test_scope_the_owner_withdrew_since_waits(repo):
    await repo.put(
        THREAD,
        _spec(NOW - timedelta(hours=1), scope="other"),
        origin="mark",
        source_thread_id=THREAD,
    )
    defaulter, deliver = _defaulter(repo)
    assert await defaulter.run_once([_item()], now=NOW) == 0
    deliver.assert_not_awaited()
    assert len(await repo.list_open()) == 1


async def test_feature_turned_off_since_waits(repo):
    await repo.put(THREAD, _spec(NOW - timedelta(hours=1)), origin="mark", source_thread_id=THREAD)
    defaulter, deliver = _defaulter(repo, policy=None)
    assert await defaulter.run_once([_item()], now=NOW) == 0
    deliver.assert_not_awaited()


@pytest.mark.parametrize(
    "item",
    [
        _item(status="running"),
        _item(running=True),
        _item(queued=True),
        _item(status="done"),
        _item(status="candidate"),
        _item(archived=True),
    ],
)
async def test_busy_or_finished_threads_are_left_alone(repo, item):
    await repo.put(THREAD, _spec(NOW - timedelta(hours=1)), origin="mark", source_thread_id=THREAD)
    defaulter, deliver = _defaulter(repo)
    assert await defaulter.run_once([item], now=NOW) == 0
    deliver.assert_not_awaited()


async def test_unknown_thread_is_left_alone(repo):
    await repo.put(THREAD, _spec(NOW - timedelta(hours=1)), origin="mark", source_thread_id=THREAD)
    defaulter, deliver = _defaulter(repo)
    assert await defaulter.run_once([], now=NOW) == 0
    deliver.assert_not_awaited()


async def test_card_moved_to_later_waits(repo):
    stored = await repo.put(
        THREAD, _spec(NOW - timedelta(hours=1)), origin="mark", source_thread_id=THREAD
    )
    await repo.defer(THREAD, stored.id, deferred=True)
    defaulter, deliver = _defaulter(repo)
    assert await defaulter.run_once([_item()], now=NOW) == 0
    deliver.assert_not_awaited()


async def test_card_without_if_no_answer_waits(repo):
    await repo.put(
        THREAD,
        parse_request(copy.deepcopy(GOOD), kind="waiting"),
        origin="mark",
        source_thread_id=THREAD,
    )
    defaulter, deliver = _defaulter(repo)
    assert await defaulter.run_once([_item()], now=NOW) == 0
    deliver.assert_not_awaited()


async def test_failed_delivery_keeps_the_card(repo):
    await repo.put(THREAD, _spec(NOW - timedelta(hours=1)), origin="mark", source_thread_id=THREAD)
    defaulter, _ = _defaulter(repo, deliver=AsyncMock(return_value=False))
    assert await defaulter.run_once([_item()], now=NOW) == 0
    assert len(await repo.list_open()) == 1


async def test_delivery_error_keeps_the_card_and_other_threads_go_on(repo):
    other = THREAD + 1
    await repo.put(THREAD, _spec(NOW - timedelta(hours=1)), origin="mark", source_thread_id=THREAD)
    await repo.put(other, _spec(NOW - timedelta(hours=1)), origin="mark", source_thread_id=other)
    deliver = AsyncMock(side_effect=[RuntimeError("boom"), True])
    defaulter, _ = _defaulter(repo, deliver=deliver)
    items = [_item(), {"id": f"t{other}", "thread_id": str(other), "status": "waiting"}]
    assert await defaulter.run_once(items, now=NOW) == 1
    assert [r.thread_id for r in await repo.list_open()] == [THREAD]


async def test_one_card_per_thread_per_pass(repo):
    await repo.put(
        THREAD, _spec(NOW - timedelta(hours=1), key="a"), origin="mark", source_thread_id=THREAD
    )
    await repo.put(
        THREAD, _spec(NOW - timedelta(hours=1), key="b"), origin="mark", source_thread_id=THREAD
    )
    defaulter, deliver = _defaulter(repo)
    assert await defaulter.run_once([_item()], now=NOW) == 1
    assert deliver.await_count == 1


def test_notice_names_the_scope_and_the_option():
    text = notice(
        title="Write the summary?",
        label="Write it",
        effect="A draft only",
        scope_label="Drafts only",
        after="2026-11-23T09:00:00+00:00",
    )
    assert "Write it" in text and "Drafts only" in text and "Write the summary?" in text
