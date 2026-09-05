"""Regression tests for scripts/cleanup_worktrees.sh.

These exercise the actual shell script (not a Python port of it) against a
throwaway git repo, so they double as a portability check for the repo-root
auto-detection logic (see PR discussion: BSD `readlink` has no `-f`).
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "cleanup_worktrees.sh"


def _init_repo_with_worktree(base: Path) -> tuple[Path, Path]:
    """Create a throwaway git repo with one extra worktree on its own branch.

    Returns (repo_dir, worktree_dir).
    """
    repo_dir = base / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    (repo_dir / "README.md").write_text("hello\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo_dir, check=True)

    (repo_dir / "scripts").mkdir()
    shutil.copy(SCRIPT, repo_dir / "scripts" / SCRIPT.name)

    worktree_dir = base / "wt-feature"
    subprocess.run(
        ["git", "worktree", "add", str(worktree_dir), "-b", "feature/thing"],
        cwd=repo_dir,
        check=True,
    )
    return repo_dir, worktree_dir


def _fake_gh_bin(base: Path, pr_state: str) -> Path:
    """A fake `gh` on PATH that reports a fixed PR state for any branch.

    Lets the test drive the script's "would remove" branch deterministically
    without depending on network access or a real GitHub PR.
    """
    bin_dir = base / "fakebin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/bin/bash\n"
        'if [[ "$1" == "pr" && "$2" == "list" ]]; then\n'
        f'  echo \'[{{"state":"{pr_state}"}}]\'\n'
        "  exit 0\n"
        "fi\n"
        'echo "unexpected gh invocation: $*" >&2\n'
        "exit 1\n"
    )
    gh.chmod(0o755)
    return bin_dir


def _run_script(
    repo_dir: Path, cwd: Path, path_prefix: Path | None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if path_prefix is not None:
        env["PATH"] = f"{path_prefix}:{env['PATH']}"
    return subprocess.run(
        [str(repo_dir / "scripts" / SCRIPT.name), "--dry-run"],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestDryRunNeverRemoves:
    def test_dry_run_leaves_removable_worktree_on_disk(self, tmp_path: Path) -> None:
        """A worktree whose PR is MERGED would normally be removed — but not in --dry-run."""
        repo_dir, worktree_dir = _init_repo_with_worktree(tmp_path)
        fake_gh = _fake_gh_bin(tmp_path, pr_state="MERGED")

        result = _run_script(repo_dir, cwd=tmp_path, path_prefix=fake_gh)

        assert result.returncode == 0, result.stderr
        assert worktree_dir.exists(), "dry-run must not delete the worktree directory"

        worktrees = subprocess.run(
            ["git", "worktree", "list"], cwd=repo_dir, check=True, capture_output=True, text=True
        ).stdout
        assert "feature/thing" in worktrees, "dry-run must not delete the branch/worktree entry"

        branches = subprocess.run(
            ["git", "branch", "--list", "feature/thing"],
            cwd=repo_dir,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert "feature/thing" in branches, "dry-run must not delete the branch"

        assert "[DRY RUN] Would remove worktree" in result.stdout

    def test_dry_run_works_when_invoked_from_unrelated_cwd(self, tmp_path: Path) -> None:
        """Repo-root auto-detection must not depend on the caller's cwd."""
        repo_dir, _worktree_dir = _init_repo_with_worktree(tmp_path)
        fake_gh = _fake_gh_bin(tmp_path, pr_state="OPEN")

        unrelated_cwd = tmp_path / "somewhere-else"
        unrelated_cwd.mkdir()

        result = _run_script(repo_dir, cwd=unrelated_cwd, path_prefix=fake_gh)

        assert result.returncode == 0, result.stderr
        assert f"Main worktree: {repo_dir}" in result.stdout


@pytest.mark.skipif(shutil.which("bash") is None, reason="requires bash")
def test_script_is_executable_bash() -> None:
    assert SCRIPT.exists()
    first_line = SCRIPT.read_text().splitlines()[0]
    assert first_line == "#!/bin/bash"


@pytest.mark.parametrize("kind", ["dirty", "untracked", "ignored", "closed", "unmerged", "clean"])
def test_live_cleanup_preserves_work_and_unmerged_branches(tmp_path: Path, kind: str) -> None:
    repo, worktree = _init_repo_with_worktree(tmp_path)
    fake = _fake_gh_bin(tmp_path, pr_state="CLOSED" if kind == "closed" else "MERGED")
    if kind == "dirty":
        (worktree / "README.md").write_text("unfinished edit")
    elif kind == "untracked":
        (worktree / "draft.txt").write_text("unfinished draft")
    elif kind == "ignored":
        (repo / ".git" / "info" / "exclude").write_text("scratch.txt\n")
        (worktree / "scratch.txt").write_text("retained output")
    elif kind == "unmerged":
        (worktree / "README.md").write_text("unmerged commit")
        subprocess.run(["git", "add", "README.md"], cwd=worktree, check=True)
        subprocess.run(["git", "commit", "-qm", "not integrated"], cwd=worktree, check=True)

    result = subprocess.run(
        [str(repo / "scripts" / SCRIPT.name)],
        cwd=repo,
        env={**os.environ, "PATH": f"{fake}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert worktree.exists() is (kind != "clean")
    branch = subprocess.check_output(
        ["git", "branch", "--list", "feature/thing"], cwd=repo, text=True
    )
    assert bool(branch.strip()) is (kind != "clean")


def test_startup_cleanup_only_requests_a_dry_run(tmp_path: Path) -> None:
    """Execute the startup call site against a stub, not a real service."""
    stub = tmp_path / "cleanup"
    stub.write_text('#!/bin/bash\nprintf "%s\\n" "$@"\n')
    stub.chmod(0o755)
    source = SCRIPT.with_name("pre-start.sh").read_text()
    condition = 'if [ -x "$CLEANUP_SCRIPT" ]; then'
    block = condition + source.split(condition, 1)[1].split("\nfi", 1)[0] + "\nfi"
    result = subprocess.run(
        ["bash", "-c", block],
        env={**os.environ, "CLEANUP_SCRIPT": str(stub)},
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "--dry-run"
