"""Detect owner PRs that a Discord session left open."""

from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess  # nosec B404
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_OWNER_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
_GRAPHQL_QUERY = """
query($q: String!) {
  search(query: $q, type: ISSUE, first: 20) {
    nodes {
      ... on PullRequest {
        number
        title
        url
        isDraft
        headRefName
        mergeable
        reviewDecision
        repository { nameWithOwner owner { login } }
        commits(last: 1) {
          nodes { commit { statusCheckRollup { state } } }
        }
      }
    }
  }
}
""".strip()


@dataclass(frozen=True)
class PullRequestStatus:
    """The PR state needed to decide whether a session is actually complete."""

    repository: str
    number: int
    title: str
    url: str
    head_ref: str
    mergeable: str
    review_decision: str | None
    checks_state: str | None


QueryRunner = Callable[[str], str]


def _run_graphql(search_query: str) -> str:
    """Run one fixed GitHub GraphQL query without a shell."""
    gh_path = shutil.which("gh")
    if gh_path is None:
        raise FileNotFoundError("GitHub CLI executable was not found")
    completed = subprocess.run(  # nosec B603
        [
            gh_path,
            "api",
            "graphql",
            "-f",
            f"query={_GRAPHQL_QUERY}",
            "-f",
            f"q={search_query}",
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
    )
    return completed.stdout


def _checks_state(node: dict[str, Any]) -> str | None:
    commits = node.get("commits")
    if not isinstance(commits, dict):
        return None
    nodes = commits.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        return None
    latest = nodes[-1]
    if not isinstance(latest, dict):
        return None
    commit = latest.get("commit")
    if not isinstance(commit, dict):
        return None
    rollup = commit.get("statusCheckRollup")
    if not isinstance(rollup, dict):
        return None
    state = rollup.get("state")
    return state if isinstance(state, str) else None


class GitHubPrCompletionGate:
    """Find non-draft PRs created from a thread's conventional session branch."""

    def __init__(self, owner: str, query_runner: QueryRunner | None = None) -> None:
        owner = owner.strip()
        if not _OWNER_PATTERN.fullmatch(owner):
            raise ValueError("owner must be a valid GitHub login")
        self._owner = owner
        self._query_runner = query_runner or _run_graphql

    @property
    def owner(self) -> str:
        return self._owner

    async def find_for_thread(self, thread_id: int) -> tuple[PullRequestStatus, ...]:
        """Return actionable owner PRs whose head is ``session/<thread_id>``."""
        expected_head = f"session/{thread_id}"
        search_query = f"is:pr is:open user:{self._owner} head:{expected_head}"
        raw = await asyncio.to_thread(self._query_runner, search_query)
        payload = json.loads(raw)
        search = payload.get("data", {}).get("search", {})
        nodes = search.get("nodes", [])
        if not isinstance(nodes, list):
            raise ValueError("GitHub response did not contain search nodes")

        results: list[PullRequestStatus] = []
        for node in nodes:
            if not isinstance(node, dict) or node.get("isDraft") is True:
                continue
            repository = node.get("repository")
            if not isinstance(repository, dict):
                continue
            owner = repository.get("owner")
            owner_login = owner.get("login") if isinstance(owner, dict) else None
            head_ref = node.get("headRefName")
            if (
                not isinstance(head_ref, str)
                or not isinstance(owner_login, str)
                or owner_login.casefold() != self._owner.casefold()
                or head_ref != expected_head
            ):
                continue
            name_with_owner = repository.get("nameWithOwner")
            if not isinstance(name_with_owner, str):
                continue
            results.append(
                PullRequestStatus(
                    repository=name_with_owner,
                    number=int(node["number"]),
                    title=str(node.get("title", "")),
                    url=str(node.get("url", "")),
                    head_ref=head_ref,
                    mergeable=str(node.get("mergeable", "UNKNOWN")),
                    review_decision=(
                        str(node["reviewDecision"])
                        if node.get("reviewDecision") is not None
                        else None
                    ),
                    checks_state=_checks_state(node),
                )
            )
        return tuple(results)


def build_completion_prompt(
    prs: tuple[PullRequestStatus, ...], *, waits_available: bool = False
) -> str:
    """Build the single automatic continuation used to finish owner PRs.

    With *waits_available* the agent is told to end the turn while checks run
    (``/api/waits`` resumes it); without, nothing would resume it, so it waits
    in the turn as before.
    """
    if waits_available:
        waiting = [
            "For each PR: inspect it, fix failures when in scope, merge it once checks pass,",
            "then verify the PR is closed and perform any required post-merge deployment or",
            "installed-consumer update. Do not claim completion merely because a PR exists.",
            "If checks are still running, do not watch them in this turn: register a wait",
            "(POST $CCDB_API_URL/api/waits, see the system instructions), say what you are",
            "waiting for, and end the turn — you will be resumed when the checks finish.",
        ]
    else:
        waiting = [
            "For each PR: inspect it, wait for all checks, fix failures when in scope, merge it,",
            "then verify the PR is closed and perform any required post-merge deployment or",
            "installed-consumer update. Do not claim completion merely because a PR exists.",
        ]
    lines = [
        "[PR COMPLETION GATE — automatic continuation]",
        "This session created or owns non-draft PRs that are still open:",
    ]
    for pr in prs:
        lines.append(
            f"- {pr.repository} PR #{pr.number}: {pr.title} "
            f"(checks={pr.checks_state or 'NONE'}, mergeable={pr.mergeable}) {pr.url}"
        )
    lines.extend(
        [
            "",
            "The previous answer is not a terminal completion while these owner PRs remain open.",
            *waiting,
            "If a PR is genuinely blocked by a decision or authority outside the current request,",
            "report the exact blocker and evidence instead of looping or broadening scope.",
        ]
    )
    return "\n".join(lines)
