"""Deferring is per request, persistent and reversible; it is never an answer."""

from __future__ import annotations

import pytest

from claude_discord.console.secretary import apply_deferrals, build_secretary
from claude_discord.console.triage import focus_score
from claude_discord.database.decision_repo import DecisionRequestRepository
from claude_discord.database.models import init_db
from claude_discord.decision_request import parse_request
from tests.test_console_secretary import NOW, _item
from tests.test_decision_requests import _good


@pytest.fixture
async def repo(tmp_path):
    db = str(tmp_path / "sessions.db")
    await init_db(db)
    return DecisionRequestRepository(db)


async def test_defer_one_card_restore_and_persist_across_repository_instances(repo):
    a = await repo.put(
        1, parse_request(_good(key="a"), kind="waiting"), origin="mark", source_thread_id=1
    )
    b = await repo.put(
        1, parse_request(_good(key="b"), kind="waiting"), origin="mark", source_thread_id=1
    )
    assert await repo.defer(1, a.id, deferred=True) == 1
    assert await repo.defer(2, b.id, deferred=True) == 0
    again = DecisionRequestRepository(repo._db_path)
    cards = [r.as_dict() for r in await again.list_open()]
    summary = build_secretary([_item("t1")], cards, now=NOW)
    assert [c["request_id"] for c in summary["decide"]] == [b.id]
    assert [c["request_id"] for c in summary["deferred"]] == [a.id]
    assert summary["unexplained"] == []
    [visible] = apply_deferrals([_item("t1")], cards)
    assert visible["decision_deferred"] is False
    assert await again.defer(1, a.id, deferred=False) == 1
    assert all(r.deferred_at is None for r in await repo.list_open())


async def test_all_deferred_cards_do_not_resurface_as_next_move_or_unexplained(repo):
    a = await repo.put(1, parse_request(_good(), kind="waiting"), origin="mark", source_thread_id=1)
    await repo.defer(1, a.id, deferred=True)
    requests = [r.as_dict() for r in await repo.list_open()]
    source = _item("t1", actionable=True)
    [item] = apply_deferrals([source], requests)
    assert item["decision_deferred"] is True
    assert focus_score(item, now=NOW) is None
    summary = build_secretary([item], requests, now=NOW)
    assert summary["decide"] == [] and summary["unexplained"] == [] and summary["calm"]
    assert source.get("decision_deferred") is None
    # A new question with the same key is a new request, and is immediately active.
    fresh = await repo.put(
        1, parse_request(_good(), kind="waiting"), origin="mark", source_thread_id=1
    )
    assert fresh.deferred_at is None
    [item] = apply_deferrals([source], [r.as_dict() for r in await repo.list_open()])
    assert item["decision_deferred"] is False
    assert focus_score(item, now=NOW) is not None


async def test_deferring_a_closed_request_does_not_reopen_it(repo):
    a = await repo.put(1, parse_request(_good(), kind="waiting"), origin="mark", source_thread_id=1)
    await repo.close(1, reason="answered", request_id=a.id)
    assert await repo.defer(1, a.id, deferred=True) == 0
    assert await repo.list_open() == []


async def test_existing_database_gains_nullable_deferral_without_losing_requests(tmp_path):
    import json

    import aiosqlite

    from claude_discord.database.models import SCHEMA

    db = str(tmp_path / "old.db")
    spec = parse_request(_good(), kind="waiting")
    async with aiosqlite.connect(db) as conn:
        await conn.executescript(SCHEMA.replace(",\n    deferred_at TEXT", ""))
        await conn.execute(
            "INSERT INTO decision_requests(thread_id,req_key,kind,origin,source_thread_id,"
            "card,detail,created_at) VALUES (1,'','waiting','mark',1,?,?,?)",
            (json.dumps(spec.card()), json.dumps(spec.detail), "2026-11-19T01:00:00Z"),
        )
        await conn.commit()
    await init_db(db)
    await init_db(db)
    repo = DecisionRequestRepository(db)
    [req] = await repo.list_open()
    assert req.title == spec.title and req.deferred_at is None
    assert await repo.defer(1, req.id, deferred=True) == 1
    assert (await repo.list_open())[0].deferred_at is not None
