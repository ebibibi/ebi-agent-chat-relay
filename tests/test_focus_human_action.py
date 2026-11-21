"""Generic conversations without a present human obligation are not next moves."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from claude_discord.console.guide import build_guide
from claude_discord.console.triage import (
    TriageRepository,
    TriageResult,
    _basis,
    apply_triage,
    parse_reply,
)

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)


def item(**over):
    return {
        "id": "t1",
        "thread_id": "1",
        "title": "Ongoing discussion",
        "status": "idle",
        "bucket": "idle",
        "priority": 2,
        "last_activity_at": "2026-11-18T11:00:00Z",
        **over,
    }


@pytest.fixture
async def repo(tmp_path):
    r = TriageRepository(str(tmp_path / "triage.db"))
    await r.init_db()
    return r


@pytest.mark.parametrize("variant", ["unknown", "settled", "stale", "legacy", "empty", "queued"])
async def test_no_recommendation_without_a_current_specific_human_action(repo, variant):
    source = item(pinned_at="2026-11-18T10:00:00Z")
    if variant != "unknown":
        guess = TriageResult(1, None, "Outstanding work", "Review the result", needs_human=True)
        if variant == "settled":
            guess = replace(guess, needs_human=False)
        if variant == "legacy":
            guess = replace(guess, needs_human=None)
        if variant == "empty":
            guess = replace(guess, next_step="")
        await repo.save("t1", guess, basis=_basis(source), model="test")
    if variant == "stale":
        source = {**source, "last_activity_at": "2026-11-18T11:01:00Z"}
    if variant == "queued":
        source = {**source, "queued": True}
    board = apply_triage([source], await repo.all(), now=NOW)
    assert board[0]["focus"] is None
    assert build_guide(board, now=NOW)["next"] == []
    assert build_guide(board, now=NOW)["calm"] is True
    assert board[0]["status"] == "idle"  # keep the continuing conversation
    assert board[0]["pinned_at"] == source["pinned_at"]


async def test_an_actual_current_action_is_recommended_with_its_reason(repo):
    source = item(status="review", bucket="me")
    await repo.save(
        "t1",
        TriageResult(1, None, "A draft needs checking", "Review the draft", needs_human=True),
        basis=_basis(source),
        model="test",
    )
    guide = build_guide(apply_triage([source], await repo.all(), now=NOW), now=NOW)
    assert guide["next"][0]["next_step"] == "Review the draft"
    assert "review" in guide["next"][0]["reasons"]


def test_a_manually_captured_task_still_works_without_a_model():
    board = apply_triage([item(thread_id=None, status="todo", bucket="todo")], {}, now=NOW)
    assert build_guide(board, now=NOW)["next"]


@pytest.mark.parametrize("flag", [None, False, "true", 1])
def test_model_must_explicitly_confirm_a_human_action(flag):
    import json

    guess = parse_reply(
        json.dumps(
            {"priority": 1, "next": "Invent a follow-up", "ready": True, "needs_human": flag}
        ),
        now=NOW,
    )
    assert guess is not None
    assert guess.needs_human is not True


def test_no_human_action_cannot_become_ready_when_a_timer_expires():
    guess = parse_reply(
        '{"priority":1,"next":"Invent a follow-up","ready":true,"needs_human":false,'
        '"ready_at":"2026-11-18T10:00"}',
        now=NOW,
    )
    assert guess is not None and guess.next_step == "" and guess.ready is False
    assert guess.ready_at is None
