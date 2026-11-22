"""Evidence-based auto-completion: the pure rules (#1477).

A thread may close itself only when evidence says the work is finished — never
because time passed, and never on the agent's word alone where ccdb can measure.
"""

from __future__ import annotations

import pytest

from claude_discord.auto_complete import (
    MODE_DRY_RUN,
    MODE_OFF,
    MODE_ON,
    SCOPE_ALL,
    SCOPE_CHILDREN,
    AutoCompletePolicy,
    ChildFacts,
    Evidence,
    EvidenceError,
    ThreadFacts,
    evaluate,
    parse_evidence,
)

GOOD = {
    "deliverables": ["https://github.com/o/r/pull/12"],
    "verification": ["PR #12 checks green"],
    "within_scope": True,
    "external_effects": [],
}


def _facts(**overrides: object) -> ThreadFacts:
    base: dict[str, object] = {
        "is_child": True,
        "teams_reply_draft": False,
        "active_waits": 0,
        "open_requests": 0,
        "children": (),
    }
    base.update(overrides)
    return ThreadFacts(**base)  # type: ignore[arg-type]


def _on(**overrides: object) -> AutoCompletePolicy:
    base: dict[str, object] = {
        "mode": MODE_ON,
        "scope": SCOPE_CHILDREN,
        "require_verification": True,
    }
    base.update(overrides)
    return AutoCompletePolicy(**base)  # type: ignore[arg-type]


class TestPolicyFromEnv:
    def test_default_is_off(self) -> None:
        policy = AutoCompletePolicy.from_env({})
        assert policy.mode == MODE_OFF
        assert policy.scope == SCOPE_CHILDREN
        assert policy.require_verification is True

    def test_reads_every_setting(self) -> None:
        policy = AutoCompletePolicy.from_env(
            {
                "CCDB_AUTO_COMPLETE": "ON",
                "CCDB_AUTO_COMPLETE_SCOPE": "all",
                "CCDB_AUTO_COMPLETE_REQUIRE_VERIFICATION": "0",
            }
        )
        assert policy == AutoCompletePolicy(
            mode=MODE_ON, scope=SCOPE_ALL, require_verification=False
        )

    def test_dry_run(self) -> None:
        assert AutoCompletePolicy.from_env({"CCDB_AUTO_COMPLETE": "dry-run"}).mode == MODE_DRY_RUN

    @pytest.mark.parametrize(
        "env",
        [
            {"CCDB_AUTO_COMPLETE": "yes-please"},
            {"CCDB_AUTO_COMPLETE": "on", "CCDB_AUTO_COMPLETE_SCOPE": "everything"},
        ],
    )
    def test_an_unknown_value_falls_back_to_off(self, env: dict[str, str]) -> None:
        # A typo must never widen what closes on its own.
        assert AutoCompletePolicy.from_env(env).mode == MODE_OFF


class TestParseEvidence:
    def test_none_without_evidence(self) -> None:
        assert parse_evidence(None) is None
        assert parse_evidence({}) is None
        assert parse_evidence({"title": "a card"}) is None

    def test_reads_a_complete_body(self) -> None:
        evidence = parse_evidence({"evidence": GOOD})
        assert evidence == Evidence(
            deliverables=("https://github.com/o/r/pull/12",),
            verification=("PR #12 checks green",),
            within_scope=True,
            external_effects=(),
        )

    def test_accepts_a_single_string(self) -> None:
        evidence = parse_evidence({"evidence": {**GOOD, "deliverables": "PR #12"}})
        assert evidence is not None
        assert evidence.deliverables == ("PR #12",)

    def test_drops_blank_entries(self) -> None:
        evidence = parse_evidence({"evidence": {**GOOD, "verification": ["", "  ", "ok"]}})
        assert evidence is not None
        assert evidence.verification == ("ok",)

    @pytest.mark.parametrize(
        "raw",
        [
            "not an object",
            {**GOOD, "deliverables": 12},
            {**GOOD, "within_scope": "yes"},
            {**GOOD, "deliverables": ["x" * 2001]},
            {**GOOD, "verification": ["v"] * 51},
        ],
    )
    def test_refuses_malformed_evidence(self, raw: object) -> None:
        with pytest.raises(EvidenceError):
            parse_evidence({"evidence": raw})


class TestEvaluate:
    def test_off_never_completes(self) -> None:
        verdict = evaluate(_on(mode=MODE_OFF), parse_evidence({"evidence": GOOD}), _facts())
        assert verdict.complete is False
        assert verdict.eligible is False

    def test_all_evidence_completes_a_child(self) -> None:
        verdict = evaluate(_on(), parse_evidence({"evidence": GOOD}), _facts())
        assert verdict.eligible is True
        assert verdict.complete is True
        assert verdict.reasons == ()

    def test_dry_run_reports_without_completing(self) -> None:
        verdict = evaluate(_on(mode=MODE_DRY_RUN), parse_evidence({"evidence": GOOD}), _facts())
        assert verdict.eligible is True
        assert verdict.complete is False

    def test_no_evidence_stays_a_candidate(self) -> None:
        verdict = evaluate(_on(), None, _facts())
        assert verdict.complete is False
        assert "no evidence" in verdict.reasons[0]

    def test_children_scope_skips_a_root_thread(self) -> None:
        verdict = evaluate(_on(), parse_evidence({"evidence": GOOD}), _facts(is_child=False))
        assert verdict.complete is False
        assert any("child" in r for r in verdict.reasons)

    def test_all_scope_takes_a_root_thread(self) -> None:
        verdict = evaluate(
            _on(scope=SCOPE_ALL), parse_evidence({"evidence": GOOD}), _facts(is_child=False)
        )
        assert verdict.complete is True

    def test_never_a_teams_reply_draft(self) -> None:
        verdict = evaluate(
            _on(scope=SCOPE_ALL),
            parse_evidence({"evidence": GOOD}),
            _facts(teams_reply_draft=True),
        )
        assert verdict.complete is False
        assert any("Teams" in r for r in verdict.reasons)

    def test_missing_deliverables(self) -> None:
        evidence = parse_evidence({"evidence": {**GOOD, "deliverables": []}})
        assert evaluate(_on(), evidence, _facts()).complete is False

    def test_missing_verification_blocks_when_required(self) -> None:
        evidence = parse_evidence({"evidence": {**GOOD, "verification": []}})
        assert evaluate(_on(), evidence, _facts()).complete is False
        assert evaluate(_on(require_verification=False), evidence, _facts()).complete is True

    def test_outside_scope_blocks(self) -> None:
        evidence = parse_evidence({"evidence": {**GOOD, "within_scope": False}})
        assert evaluate(_on(), evidence, _facts()).complete is False

    def test_within_scope_must_be_said(self) -> None:
        raw = {k: v for k, v in GOOD.items() if k != "within_scope"}
        evidence = parse_evidence({"evidence": raw})
        assert evaluate(_on(), evidence, _facts()).complete is False

    def test_an_external_effect_needs_a_human(self) -> None:
        # Sending, publishing or paying is never approved by this mechanism.
        evidence = parse_evidence({"evidence": {**GOOD, "external_effects": ["sent the email"]}})
        verdict = evaluate(_on(), evidence, _facts())
        assert verdict.complete is False
        assert any("external" in r for r in verdict.reasons)

    def test_active_wait_blocks(self) -> None:
        verdict = evaluate(_on(), parse_evidence({"evidence": GOOD}), _facts(active_waits=1))
        assert verdict.complete is False

    def test_open_request_blocks(self) -> None:
        verdict = evaluate(_on(), parse_evidence({"evidence": GOOD}), _facts(open_requests=2))
        assert verdict.complete is False

    def test_finished_children_do_not_block(self) -> None:
        children = (ChildFacts(thread_id=5, finished=True),)
        verdict = evaluate(_on(), parse_evidence({"evidence": GOOD}), _facts(children=children))
        assert verdict.complete is True

    @pytest.mark.parametrize(
        "child",
        [
            ChildFacts(thread_id=5, finished=False),
            ChildFacts(thread_id=5, finished=True, running=True),
            ChildFacts(thread_id=5, finished=True, active_waits=1),
            ChildFacts(thread_id=5, finished=True, open_requests=1),
        ],
    )
    def test_an_unfinished_child_blocks(self, child: ChildFacts) -> None:
        verdict = evaluate(_on(), parse_evidence({"evidence": GOOD}), _facts(children=(child,)))
        assert verdict.complete is False
        assert any("5" in r for r in verdict.reasons)

    def test_reports_every_reason(self) -> None:
        verdict = evaluate(_on(), None, _facts(is_child=False, active_waits=1, open_requests=1))
        assert len(verdict.reasons) >= 3

    def test_as_dict(self) -> None:
        verdict = evaluate(_on(mode=MODE_DRY_RUN), parse_evidence({"evidence": GOOD}), _facts())
        assert verdict.as_dict() == {
            "mode": "dry-run",
            "eligible": True,
            "complete": False,
            "reasons": [],
        }
