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
    """A merge-polling job must not occupy the only runner required by CI."""
    workflow = (REPO_ROOT / ".github/workflows/auto-approve.yml").read_text()
    owner_job = _job_block(workflow, "auto-approve-and-merge", "auto-merge-dependabot")

    # Asserted against the runs-on lines rather than the whole block: the job
    # explains in a comment why it waits as long as it does, and the reason is
    # the self-hosted pool, so a substring search over the block fails on prose
    # that says nothing about where this job runs.
    runs_on = [
        line.strip() for line in owner_job.splitlines() if line.strip().startswith("runs-on:")
    ]

    assert runs_on == ["runs-on: ubuntu-latest"]
