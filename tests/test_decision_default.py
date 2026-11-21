"""Proceeding with the recommendation when nobody answers — only inside approved scopes (#1478).

Silence is consent only for kinds of reversible work the owner listed in advance. Without the
operator's scope file nothing changes: every card waits for a person, as before.
"""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta

import pytest

from claude_discord.decision_default import (
    ENV_FILE,
    DefaultPolicyError,
    load_policy,
    policy_from_env,
)
from claude_discord.decision_request import (
    DecisionRequestError,
    agent_guide,
    parse_request,
)
from tests.test_decision_requests import GOOD

SCOPES = {"drafts": "Write drafts only; publish or send nothing"}
FUTURE = (datetime.now(UTC) + timedelta(days=1)).isoformat()


def _body(**over) -> dict:
    body = copy.deepcopy(GOOD)
    body["if_no_answer"] = {"after": FUTURE, "scope": "drafts"}
    body.update(over)
    return body


# -- the policy file ----------------------------------------------------------


def _write(tmp_path, data) -> str:
    path = tmp_path / "defaults.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


def test_policy_loads_listed_scopes(tmp_path):
    policy = load_policy(_write(tmp_path, {"scopes": {"drafts": {"label": "Drafts only"}}}))
    assert policy.scopes == {"drafts": "Drafts only"}


@pytest.mark.parametrize(
    "key", ["publish_article", "send-mail", "payment", "delete.branch", "prod_change", "purchase"]
)
def test_policy_refuses_scopes_that_silence_must_never_approve(tmp_path, key):
    with pytest.raises(DefaultPolicyError, match=key):
        load_policy(_write(tmp_path, {"scopes": {key: {"label": "x"}}}))


@pytest.mark.parametrize(
    "data",
    [[], {"scopes": []}, {"scopes": {"bad key!": {"label": "x"}}}, {"scopes": {"a": {}}}],
)
def test_policy_refuses_malformed_files(tmp_path, data):
    with pytest.raises(DefaultPolicyError):
        load_policy(_write(tmp_path, data))


def test_no_env_means_feature_off(monkeypatch):
    monkeypatch.delenv(ENV_FILE, raising=False)
    assert policy_from_env() is None


def test_broken_file_turns_the_feature_off_rather_than_on(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv(ENV_FILE, _write(tmp_path, {"scopes": {"send_mail": {"label": "x"}}}))
    assert policy_from_env() is None
    assert "send_mail" in caplog.text


def test_missing_file_turns_the_feature_off(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_FILE, str(tmp_path / "absent.json"))
    assert policy_from_env() is None


# -- the request field ----------------------------------------------------------


def test_request_keeps_if_no_answer_on_the_card():
    spec = parse_request(_body(), kind="waiting", scopes=SCOPES)
    assert spec.if_no_answer is not None
    assert spec.if_no_answer.scope == "drafts"
    assert spec.card()["if_no_answer"]["scope"] == "drafts"


def test_request_without_if_no_answer_is_unchanged():
    spec = parse_request(copy.deepcopy(GOOD), kind="waiting", scopes=SCOPES)
    assert spec.if_no_answer is None
    assert "if_no_answer" not in spec.card()


def _problems(body: dict, kind: str = "waiting", scopes=SCOPES) -> str:
    with pytest.raises(DecisionRequestError) as caught:
        parse_request(body, kind=kind, scopes=scopes)
    return " ".join(caught.value.problems)


def test_scope_must_be_one_the_owner_approved():
    text = _problems(_body(if_no_answer={"after": FUTURE, "scope": "anything"}))
    assert "drafts" in text  # the refusal lists what is allowed


def test_feature_off_refuses_if_no_answer_instead_of_storing_a_dead_promise():
    text = _problems(_body(), scopes=None)
    assert "not enabled" in text


@pytest.mark.parametrize("after", ["tomorrow", "2026-11-23T09:00:00", 5, ""])
def test_after_must_be_a_timestamp_with_offset(after):
    assert "after" in _problems(_body(if_no_answer={"after": after, "scope": "drafts"}))


def test_action_cards_cannot_default():
    body = _body()
    body["options"] = [{"label": "Did it", "effect": "AI continues", "recommended": True}]
    assert "action" in _problems(body, kind="action")


def test_a_recommended_option_is_required():
    body = _body()
    body["options"] = [{"label": "Look", "effect": "AI fixes what you point out"}]
    assert "recommended" in _problems(body, kind="review")


def test_unknown_fields_inside_if_no_answer_are_refused():
    body = _body(if_no_answer={"after": FUTURE, "scope": "drafts", "force": True})
    assert "force" in _problems(body)


# -- the agent guide -------------------------------------------------------------


def test_guide_tells_agents_to_keep_working_while_a_card_waits():
    assert "independent" in agent_guide()


def test_guide_mentions_if_no_answer_only_when_scopes_exist():
    assert "if_no_answer" not in agent_guide()
    guide = agent_guide(SCOPES)
    assert "if_no_answer" in guide
    assert "drafts" in guide
