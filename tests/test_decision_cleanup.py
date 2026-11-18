"""Only later, evidenced human answers settle cards; unrelated chat must keep them."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from claude_discord.console.decision_cleanup import DecisionReviewer
from claude_discord.database.decision_repo import DecisionRequestRepository
from claude_discord.database.models import init_db
from claude_discord.decision_request import parse_request
from tests.test_decision_requests import _good


@pytest.fixture
async def repo(tmp_path):
    db = str(tmp_path / "db.sqlite")
    await init_db(db)
    return DecisionRequestRepository(db)


async def test_only_the_resolved_card_closes_and_history_is_cached(repo):
    first = await repo.put(
        123, parse_request(_good(key="a"), kind="waiting"), origin="mark", source_thread_id=123
    )
    second = await repo.put(
        123, parse_request(_good(key="b"), kind="waiting"), origin="mark", source_thread_id=123
    )
    stamp = (datetime.now(UTC) + timedelta(seconds=5)).isoformat()
    read = AsyncMock(
        return_value=[{"kind": "human", "content": "Aで消して大丈夫です", "created_at": stamp}]
    )
    ask = AsyncMock(
        return_value=json.dumps({"resolved": [{"id": first.id, "evidence": "Aで消して大丈夫です"}]})
    )
    review = DecisionReviewer(repo, ask=ask, read_messages=read)
    item = {"id": "t123", "thread_id": "123", "last_activity_at": stamp, "running": False}
    assert await review.run_once([item]) == 1
    assert [r.id for r in await repo.list_open()] == [second.id]
    ask.return_value = '{"resolved":[]}'
    assert await review.run_once([item]) == 0
    assert await review.run_once([item]) == 0
    assert ask.await_count == 2


@pytest.mark.parametrize(
    "kind, offset, evidence",
    [("agent", 5, "解決しました"), ("human", -5, "解決しました"), ("human", 5, "存在しない引用")],
)
async def test_no_closure_for_old_agent_or_invented_evidence(repo, kind, offset, evidence):
    req = await repo.put(
        123, parse_request(_good(), kind="waiting"), origin="mark", source_thread_id=123
    )
    stamp = (datetime.now(UTC) + timedelta(seconds=offset)).isoformat()
    read = AsyncMock(return_value=[{"kind": kind, "content": "解決しました", "created_at": stamp}])
    ask = AsyncMock(return_value=json.dumps({"resolved": [{"id": req.id, "evidence": evidence}]}))
    review = DecisionReviewer(repo, ask=ask, read_messages=read)
    assert (
        await review.run_once([{"id": "t123", "thread_id": "123", "last_activity_at": stamp}]) == 0
    )
    assert len(await repo.list_open()) == 1


async def test_running_unknown_and_other_threads_are_untouched(repo):
    req = await repo.put(
        123, parse_request(_good(), kind="waiting"), origin="mark", source_thread_id=123
    )
    ask = AsyncMock(return_value=json.dumps({"resolved": [{"id": req.id, "evidence": "完了"}]}))
    review = DecisionReviewer(repo, ask=ask, read_messages=AsyncMock())
    assert await review.run_once([{"id": "t123", "thread_id": "123", "running": True}]) == 0
    assert await review.run_once([{"id": "t999", "thread_id": "999"}]) == 0
    ask.assert_not_called()
    assert len(await repo.list_open()) == 1


async def test_replaced_request_is_not_closed_by_old_model_response(repo):
    old = await repo.put(
        123, parse_request(_good(), kind="waiting"), origin="mark", source_thread_id=123
    )
    stamp = (datetime.now(UTC) + timedelta(seconds=5)).isoformat()

    async def answer(prompt):
        await repo.put(
            123, parse_request(_good(), kind="waiting"), origin="mark", source_thread_id=123
        )
        return json.dumps({"resolved": [{"id": old.id, "evidence": "完了しました"}]})

    review = DecisionReviewer(
        repo,
        ask=answer,
        read_messages=AsyncMock(
            return_value=[{"kind": "human", "content": "完了しました", "created_at": stamp}]
        ),
    )
    assert (
        await review.run_once([{"id": "t123", "thread_id": "123", "last_activity_at": stamp}]) == 0
    )
    assert len(await repo.list_open()) == 1


@pytest.mark.parametrize(
    "reply",
    [
        '{"resolved":[]}',
        "not json",
        '{"resolved":true}',
        '{"resolved":[{"id":999,"evidence":"完了しました"}]}',
        '{"resolved":[{"id":true,"evidence":"完了しました"}]}',
    ],
)
async def test_uncertain_invalid_or_out_of_scope_results_keep_cards(repo, reply):
    await repo.put(123, parse_request(_good(), kind="waiting"), origin="mark", source_thread_id=123)
    stamp = (datetime.now(UTC) + timedelta(seconds=5)).isoformat()
    ask = AsyncMock(return_value=reply)
    review = DecisionReviewer(
        repo,
        ask=ask,
        read_messages=AsyncMock(
            return_value=[{"kind": "human", "content": "完了しました", "created_at": stamp}]
        ),
    )
    assert (
        await review.run_once([{"id": "t123", "thread_id": "123", "last_activity_at": stamp}]) == 0
    )
    assert len(await repo.list_open()) == 1


async def test_ordinary_unrelated_chat_is_explicitly_kept(repo):
    await repo.put(123, parse_request(_good(), kind="waiting"), origin="mark", source_thread_id=123)
    stamp = (datetime.now(UTC) + timedelta(seconds=5)).isoformat()
    ask = AsyncMock(return_value='{"resolved":[]}')
    review = DecisionReviewer(
        repo,
        ask=ask,
        read_messages=AsyncMock(
            return_value=[{"kind": "human", "content": "別件の質問です", "created_at": stamp}]
        ),
    )
    assert (
        await review.run_once([{"id": "t123", "thread_id": "123", "last_activity_at": stamp}]) == 0
    )
    assert len(await repo.list_open()) == 1
    assert "Unrelated replies" in ask.call_args.args[0]
