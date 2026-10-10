from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from claude_discord.cogs import _run_helper
from claude_discord.pr_completion_gate import (
    GitHubPrCompletionGate,
    PullRequestStatus,
    build_completion_prompt,
)


def _graphql_payload(nodes: list[dict[str, object]]) -> str:
    return json.dumps({"data": {"search": {"nodes": nodes}}})


@pytest.mark.asyncio
async def test_finds_owned_non_draft_pr_for_session_branch() -> None:
    captured: list[str] = []

    def query_runner(query: str) -> str:
        captured.append(query)
        return _graphql_payload(
            [
                {
                    "number": 9,
                    "title": "Release the fix",
                    "url": "https://github.com/ebibibi/example/pull/9",
                    "isDraft": False,
                    "headRefName": "session/12345",
                    "mergeable": "MERGEABLE",
                    "reviewDecision": None,
                    "repository": {
                        "nameWithOwner": "ebibibi/example",
                        "owner": {"login": "ebibibi"},
                    },
                    "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}]},
                }
            ]
        )

    gate = GitHubPrCompletionGate(owner="ebibibi", query_runner=query_runner)

    prs = await gate.find_for_thread(12345)

    assert len(prs) == 1
    assert prs[0].repository == "ebibibi/example"
    assert prs[0].checks_state == "SUCCESS"
    assert "head:session/12345" in captured[0]


@pytest.mark.asyncio
async def test_filters_drafts_and_defensively_rejects_wrong_branch_or_owner() -> None:
    nodes = [
        {
            "number": number,
            "title": "ignored",
            "url": f"https://example.test/{number}",
            "isDraft": draft,
            "headRefName": branch,
            "mergeable": "MERGEABLE",
            "reviewDecision": None,
            "repository": {
                "nameWithOwner": f"{owner}/repo",
                "owner": {"login": owner},
            },
            "commits": {"nodes": []},
        }
        for number, draft, branch, owner in (
            (1, True, "session/77", "ebibibi"),
            (2, False, "session/other", "ebibibi"),
            (3, False, "session/77", "someone-else"),
        )
    ]
    gate = GitHubPrCompletionGate(
        owner="ebibibi", query_runner=lambda _query: _graphql_payload(nodes)
    )

    assert await gate.find_for_thread(77) == ()


def test_completion_prompt_requires_merge_and_post_merge_verification() -> None:
    prompt = build_completion_prompt(
        (
            PullRequestStatus(
                repository="ebibibi/example",
                number=42,
                title="Finish release",
                url="https://github.com/ebibibi/example/pull/42",
                head_ref="session/123",
                mergeable="MERGEABLE",
                review_decision=None,
                checks_state="SUCCESS",
            ),
        )
    )

    assert "PR #42" in prompt
    assert "merge" in prompt.lower()
    assert "post-merge" in prompt.lower()
    assert "do not claim completion" in prompt.lower()


def test_owner_must_be_a_safe_github_login() -> None:
    with pytest.raises(ValueError, match="owner"):
        GitHubPrCompletionGate(owner="bad owner; rm -rf")


@pytest.mark.asyncio
async def test_run_helper_requests_one_completion_continuation(monkeypatch) -> None:
    pr = PullRequestStatus(
        repository="ebibibi/example",
        number=7,
        title="Ready release",
        url="https://github.com/ebibibi/example/pull/7",
        head_ref="session/123",
        mergeable="MERGEABLE",
        review_decision=None,
        checks_state="SUCCESS",
    )
    gate = SimpleNamespace(find_for_thread=AsyncMock(return_value=(pr,)))
    monkeypatch.setattr(_run_helper, "_pr_completion_gate", gate)
    surface = SimpleNamespace(thread_key=123, send_notice=AsyncMock())
    config = SimpleNamespace(pr_completion_gate_rerun=False, surface=surface)

    prompt = await _run_helper._get_pr_completion_prompt(
        config, session_id="session-id", final_error=None
    )

    assert prompt is not None
    assert "PR #7" in prompt
    surface.send_notice.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_helper_does_not_repeat_completion_gate(monkeypatch) -> None:
    gate = SimpleNamespace(find_for_thread=AsyncMock())
    monkeypatch.setattr(_run_helper, "_pr_completion_gate", gate)
    config = SimpleNamespace(
        pr_completion_gate_rerun=True,
        surface=SimpleNamespace(thread_key=123, send_notice=AsyncMock()),
    )

    prompt = await _run_helper._get_pr_completion_prompt(
        config, session_id="session-id", final_error=None
    )

    assert prompt is None
    gate.find_for_thread.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_helper_fails_open_with_visible_warning(monkeypatch) -> None:
    gate = SimpleNamespace(
        find_for_thread=AsyncMock(side_effect=RuntimeError("GitHub unavailable"))
    )
    monkeypatch.setattr(_run_helper, "_pr_completion_gate", gate)
    surface = SimpleNamespace(thread_key=123, send_notice=AsyncMock())
    config = SimpleNamespace(pr_completion_gate_rerun=False, surface=surface)

    prompt = await _run_helper._get_pr_completion_prompt(
        config, session_id="session-id", final_error=None
    )

    assert prompt is None
    notice = surface.send_notice.await_args.args[0]
    assert "unavailable" in notice.title.lower()


def _pr(number: int = 7) -> PullRequestStatus:
    return PullRequestStatus(
        repository="ebibibi/example",
        number=number,
        title="Ready release",
        url=f"https://github.com/ebibibi/example/pull/{number}",
        head_ref="session/123",
        mergeable="MERGEABLE",
        review_decision=None,
        checks_state="PENDING",
    )


def test_completion_prompt_only_offers_waits_when_they_work() -> None:
    without = build_completion_prompt((_pr(),))
    assert "wait for all checks" in without
    assert "/api/waits" not in without
    with_waits = build_completion_prompt((_pr(),), waits_available=True)
    assert "/api/waits" in with_waits
    assert "end the turn" in with_waits


@pytest.mark.asyncio
async def test_gate_mentions_waits_only_with_a_watcher_and_api(monkeypatch) -> None:
    from claude_discord import waits

    gate = SimpleNamespace(find_for_thread=AsyncMock(return_value=(_pr(),)))
    monkeypatch.setattr(_run_helper, "_pr_completion_gate", gate)
    surface = SimpleNamespace(thread_key=123, send_notice=AsyncMock())
    config = SimpleNamespace(
        pr_completion_gate_rerun=False, surface=surface, runner=SimpleNamespace(api_port=None)
    )
    repo = SimpleNamespace(active_thread_ids=AsyncMock(return_value=set()))
    waits.register_watcher(repo)  # type: ignore[arg-type]
    try:
        prompt = await _run_helper._get_pr_completion_prompt(
            config, session_id="s", final_error=None
        )
        assert prompt is not None and "/api/waits" not in prompt
        config.runner.api_port = 8099
        prompt = await _run_helper._get_pr_completion_prompt(
            config, session_id="s", final_error=None
        )
        assert prompt is not None and "/api/waits" in prompt
    finally:
        waits.register_watcher(None)


@pytest.mark.asyncio
async def test_gate_stays_quiet_while_the_thread_waits(monkeypatch) -> None:
    from claude_discord import waits

    gate = SimpleNamespace(find_for_thread=AsyncMock(return_value=(_pr(),)))
    monkeypatch.setattr(_run_helper, "_pr_completion_gate", gate)
    surface = SimpleNamespace(thread_key=123, send_notice=AsyncMock())
    config = SimpleNamespace(
        pr_completion_gate_rerun=False, surface=surface, runner=SimpleNamespace(api_port=8099)
    )
    repo = SimpleNamespace(active_thread_ids=AsyncMock(return_value={123}))
    waits.register_watcher(repo)  # type: ignore[arg-type]
    try:
        prompt = await _run_helper._get_pr_completion_prompt(
            config, session_id="s", final_error=None
        )
    finally:
        waits.register_watcher(None)
    assert prompt is None
    gate.find_for_thread.assert_not_awaited()
