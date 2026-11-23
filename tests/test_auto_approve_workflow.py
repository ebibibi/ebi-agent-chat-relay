"""Regression checks for the owner auto-merge control-plane workflow."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _job_block(workflow: str, job: str, next_job: str) -> str:
    """Return one top-level job block without adding a YAML test dependency."""
    start = workflow.index(f"  {job}:")
    end = workflow.index(f"  {next_job}:", start)
    return workflow[start:end]


def _step_block(workflow: str, step: str) -> str:
    """Return one workflow step without bringing in a YAML dependency."""
    start = workflow.index(f"      - name: {step}")
    next_step = workflow.find("      - name: ", start + 1)
    return workflow[start:next_step] if next_step != -1 else workflow[start:]


def test_owner_auto_merge_does_not_use_the_self_hosted_ci_pool() -> None:
    """The owner merge control-plane job must leave the only CI runner free."""
    workflow = (REPO_ROOT / ".github/workflows/auto-approve.yml").read_text(encoding="utf-8")
    owner_job = _job_block(workflow, "auto-approve-and-merge", "auto-merge-dependabot")

    # Asserted against the runs-on lines rather than the whole block: comments
    # may legitimately describe the self-hosted pool without running there.
    runs_on = [
        line.strip() for line in owner_job.splitlines() if line.strip().startswith("runs-on:")
    ]

    assert runs_on == ["runs-on: ubuntu-latest"]


def test_owner_auto_merge_is_enabled_by_the_real_user_token() -> None:
    """The merge must emit a push event so event-driven post-merge work can run."""
    workflow = (REPO_ROOT / ".github/workflows/auto-approve.yml").read_text(encoding="utf-8")
    owner_job = _job_block(workflow, "auto-approve-and-merge", "auto-merge-dependabot")
    merge_step = owner_job[owner_job.index("- name: Enable auto-merge") :]

    assert "GH_TOKEN: ${{ secrets.ADMIN_PAT }}" in merge_step
    assert "gh pr merge" in merge_step


def test_owner_auto_merge_does_not_poll_for_completion() -> None:
    """Post-merge work belongs to the main push event, not a bounded polling loop."""
    workflow = (REPO_ROOT / ".github/workflows/auto-approve.yml").read_text(encoding="utf-8")
    owner_job = _job_block(workflow, "auto-approve-and-merge", "auto-merge-dependabot")

    assert "Wait for merge" not in owner_job
    assert "POLL_ATTEMPTS" not in owner_job
    assert "gh issue close" not in owner_job


def test_post_merge_workflow_reacts_to_main_push_without_self_hosted_runner() -> None:
    """Every user-attributed merge must hand off post-merge work immediately."""
    workflow = (REPO_ROOT / ".github/workflows/post-merge.yml").read_text(encoding="utf-8")

    assert "push:" in workflow
    assert "branches: [main]" in workflow
    assert "runs-on: ubuntu-latest" in workflow
    assert "commits/$GITHUB_SHA/pulls" in workflow
    assert '"pr-merged"' in workflow
    assert "🔄 ebibot-upgrade" in workflow
    assert "🔄 docs-sync" in workflow


def test_post_merge_retries_pr_association_before_using_verified_commit_fallback() -> None:
    """The push webhook can precede GitHub's commit-to-PR association index."""
    workflow = (REPO_ROOT / ".github/workflows/post-merge.yml").read_text(encoding="utf-8")

    assert "MAX_PR_LOOKUP_ATTEMPTS" in workflow
    assert "commits/$GITHUB_SHA/pulls" in workflow
    assert "sleep" in workflow
    assert 'gh pr view "$PR_NUMBER"' in workflow
    assert ".base.ref == \"main\"" in workflow
    assert '.mergedAt != null' in workflow
    assert '.mergeCommit.oid == $sha' in workflow


def test_unresolved_pr_reference_can_only_trigger_upgrade() -> None:
    """A referenced but unconfirmed PR must not enter any PR classification path."""
    workflow = (REPO_ROOT / ".github/workflows/post-merge.yml").read_text(encoding="utf-8")
    classify_step = workflow[
        workflow.index("- name: Classify the merged pull request") :
        workflow.index("- name: Trigger EbiBot upgrade")
    ]
    upgrade_step = workflow[
        workflow.index("- name: Trigger EbiBot upgrade") :
        workflow.index("- name: Check whether docs-sync is already active")
    ]
    dispatch_step = workflow[
        workflow.index("- name: Dispatch the patch version bump or release") :
    ]

    assert "steps.pr.outputs.verified == 'true'" in classify_step
    assert "steps.pr.outputs.unresolved == 'true'" in upgrade_step
    assert "steps.pr.outputs.unresolved" not in dispatch_step


def test_direct_main_push_without_a_pull_request_reference_is_a_noop() -> None:
    """A non-PR main push must not be mistaken for any post-merge work."""
    workflow = (REPO_ROOT / ".github/workflows/post-merge.yml").read_text(encoding="utf-8")

    assert "No PR reference in commit subject" in workflow
    assert "upgrade_only=false" in workflow


def test_post_merge_pull_request_lookup_retries_for_a_bounded_period() -> None:
    workflow = (REPO_ROOT / ".github/workflows/post-merge.yml").read_text(encoding="utf-8")
    resolver = _step_block(workflow, "Resolve the pull request that produced this commit")

    assert "MAX_PR_LOOKUP_ATTEMPTS=12" in resolver
    assert 'for attempt in $(seq 1 "$MAX_PR_LOOKUP_ATTEMPTS")' in resolver
    assert 'sleep 10' in resolver
    assert 'attempt" -lt "$MAX_PR_LOOKUP_ATTEMPTS"' in resolver
    assert 'commits/$GITHUB_SHA/pulls' in resolver


def test_post_merge_subject_fallback_confirms_the_current_merged_commit() -> None:
    workflow = (REPO_ROOT / ".github/workflows/post-merge.yml").read_text(encoding="utf-8")
    resolver = _step_block(workflow, "Resolve the pull request that produced this commit")

    assert ".head_commit.message //" in resolver
    assert r"\(#([0-9]+)\)" in resolver
    assert 'gh pr view "$PR_NUMBER"' in resolver
    assert '.baseRefName == "main" and .mergedAt != null' in resolver
    assert ".mergeCommit.oid == $sha" in resolver


def test_unresolved_pr_reference_triggers_only_the_idempotent_upgrade_path() -> None:
    workflow = (REPO_ROOT / ".github/workflows/post-merge.yml").read_text(encoding="utf-8")
    upgrade = _step_block(workflow, "Trigger EbiBot upgrade")
    docs = _step_block(workflow, "Trigger docs-sync")
    release = _step_block(workflow, "Dispatch the patch version bump or release")

    assert "steps.pr.outputs.unresolved == 'true'" in upgrade
    assert "steps.pr.outputs.unresolved" not in docs
    assert "steps.pr.outputs.unresolved" not in release


def test_expensive_checks_do_not_repeat_after_a_checked_pr_merges() -> None:
    """User-attributed merges must not double the single-capacity runner load."""
    for filename in ("ci.yml", "codeql.yml"):
        workflow = (REPO_ROOT / f".github/workflows/{filename}").read_text(encoding="utf-8")
        trigger = workflow[: workflow.index("jobs:")]

        assert "pull_request:" in trigger
        assert "push:" not in trigger

