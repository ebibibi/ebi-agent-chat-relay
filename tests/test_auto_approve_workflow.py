"""Regression checks for the owner auto-merge control-plane workflow."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _job_block(workflow: str, job: str, next_job: str) -> str:
    """Return one top-level job block without adding a YAML test dependency."""
    start = workflow.index(f"  {job}:")
    end = workflow.index(f"  {next_job}:", start)
    return workflow[start:end]


def test_owner_auto_merge_does_not_use_the_self_hosted_ci_pool() -> None:
    """The owner merge control-plane job must leave the only CI runner free."""
    workflow = (REPO_ROOT / ".github/workflows/auto-approve.yml").read_text()
    owner_job = _job_block(workflow, "auto-approve-and-merge", "auto-merge-dependabot")

    # Asserted against the runs-on lines rather than the whole block: comments
    # may legitimately describe the self-hosted pool without running there.
    runs_on = [
        line.strip() for line in owner_job.splitlines() if line.strip().startswith("runs-on:")
    ]

    assert runs_on == ["runs-on: ubuntu-latest"]


def test_owner_auto_merge_is_enabled_by_the_real_user_token() -> None:
    """The merge must emit a push event so event-driven post-merge work can run."""
    workflow = (REPO_ROOT / ".github/workflows/auto-approve.yml").read_text()
    owner_job = _job_block(workflow, "auto-approve-and-merge", "auto-merge-dependabot")
    merge_step = owner_job[owner_job.index("- name: Enable auto-merge") :]

    assert "GH_TOKEN: ${{ secrets.ADMIN_PAT }}" in merge_step
    assert "gh pr merge" in merge_step


def test_owner_auto_merge_does_not_poll_for_completion() -> None:
    """Post-merge work belongs to the main push event, not a bounded polling loop."""
    workflow = (REPO_ROOT / ".github/workflows/auto-approve.yml").read_text()
    owner_job = _job_block(workflow, "auto-approve-and-merge", "auto-merge-dependabot")

    assert "Wait for merge" not in owner_job
    assert "POLL_ATTEMPTS" not in owner_job
    assert "gh issue close" not in owner_job


def test_post_merge_workflow_reacts_to_main_push_without_self_hosted_runner() -> None:
    """Every user-attributed merge must hand off post-merge work immediately."""
    workflow = (REPO_ROOT / ".github/workflows/post-merge.yml").read_text()

    assert "push:" in workflow
    assert "branches: [main]" in workflow
    assert "runs-on: ubuntu-latest" in workflow
    assert "commits/$GITHUB_SHA/pulls" in workflow
    assert '"pr-merged"' in workflow
    assert "🔄 ebibot-upgrade" in workflow
    assert "🔄 docs-sync" in workflow


def test_expensive_checks_do_not_repeat_after_a_checked_pr_merges() -> None:
    """User-attributed merges must not double the single-capacity runner load."""
    for filename in ("ci.yml", "codeql.yml"):
        workflow = (REPO_ROOT / f".github/workflows/{filename}").read_text()
        trigger = workflow[: workflow.index("jobs:")]

        assert "pull_request:" in trigger
        assert "push:" not in trigger
