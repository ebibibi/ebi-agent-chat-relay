"""Regression tests for scripts/check-deploy-drift.sh.

The script exists because a forgotten `make dev-on` kept a side branch in
production while every merged PR appeared to deploy. These exercise the real
shell script against throwaway git repos so the exit codes stay meaningful —
a detector that cannot fail is worth nothing.
"""

from __future__ import annotations

import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check-deploy-drift.sh"


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _ago(days: float) -> str:
    """An absolute timestamp N days back — git rejects relative committer dates."""
    when = datetime.now(UTC) - timedelta(days=days)
    return when.isoformat()


def _commit(repo: Path, name: str, *, days_ago: float = 0) -> None:
    """One commit, optionally backdated — the age is what the check reports."""
    (repo / name).write_text("x = 1\n")
    _git(repo, "add", "-A")
    when = _ago(days_ago)
    subprocess.run(
        ["git", "commit", "-q", "-m", name],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when},
    )


def _init_origin(base: Path) -> Path:
    """A bare 'origin' plus a clone with one commit on main."""
    origin = base / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)

    repo = base / "ccdb"
    subprocess.run(["git", "clone", "-q", str(origin), str(repo)], check=True)
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    (repo / "claude_discord").mkdir()
    (repo / "claude_discord" / "__init__.py").write_text("")
    # Backdated: the freshness check counts commits newer than the bot's start
    # time, so a history that begins "now" would make every test look stale.
    _commit(repo, "init.py", days_ago=60)
    _git(repo, "push", "-q", "origin", "main")
    return repo


def _stub_systemctl(base: Path, timestamp: str) -> Path:
    """A fake ``systemctl`` reporting a fixed unit start time.

    Without it these tests read the *host's* real discord-bot unit, so the
    result would depend on when the developer last restarted their bot.
    """
    bin_dir = base / "stub-bin"
    bin_dir.mkdir(exist_ok=True)
    stub = bin_dir / "systemctl"
    stub.write_text(f'#!/bin/bash\nprintf "%s\\n" {timestamp!r}\n')
    stub.chmod(0o755)
    return bin_dir


def _run(
    script_home: Path,
    repo: Path,
    *,
    started: str | None = None,
    stale_days: str | None = None,
) -> subprocess.CompletedProcess[str]:
    path = "/usr/bin:/bin:/usr/local/bin"
    if started is not None:
        path = f"{_stub_systemctl(script_home, started)}:{path}"
    env = {
        "HOME": str(script_home),
        "CCDB_HOME": str(repo),
        "PATH": path,
    }
    if stale_days is not None:
        env["CCDB_STALE_DAYS"] = stale_days
    return subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True)


def test_no_marker_is_main_tree_mode(tmp_path: Path) -> None:
    repo = _init_origin(tmp_path)
    result = _run(tmp_path, repo, started=_ago(-1))

    assert result.returncode == 0
    assert "main-tree mode" in result.stdout


def test_main_tree_mode_is_clean_when_the_bot_started_after_the_last_commit(
    tmp_path: Path,
) -> None:
    repo = _init_origin(tmp_path)

    result = _run(tmp_path, repo, started=_ago(-1))

    assert result.returncode == 0
    assert "running the newest code" in result.stdout


def test_commits_merged_since_the_bot_started_are_reported_before_they_are_drift(
    tmp_path: Path,
) -> None:
    """Reported from day one, but a fresh merge must not page anyone."""
    repo = _init_origin(tmp_path)
    _commit(repo, "merged.py")
    _git(repo, "push", "-q", "origin", "main")

    result = _run(tmp_path, repo, started=_ago(2))

    assert result.returncode == 0
    assert "1 commit(s) merged since the bot started" in result.stdout
    assert "next restart" in result.stdout


def test_undeployed_commits_older_than_the_threshold_are_drift(tmp_path: Path) -> None:
    """A merge nobody restarted for is the same failure as a stale worktree."""
    repo = _init_origin(tmp_path)
    _commit(repo, "merged.py", days_ago=9)
    _git(repo, "push", "-q", "origin", "main")

    result = _run(tmp_path, repo, started=_ago(20))

    assert result.returncode == 1
    assert "waiting 9 days for a restart" in result.stdout
    assert "missing  : 1 commit(s)" in result.stdout


def test_the_threshold_is_configurable(tmp_path: Path) -> None:
    repo = _init_origin(tmp_path)
    _commit(repo, "merged.py", days_ago=2)
    _git(repo, "push", "-q", "origin", "main")

    assert _run(tmp_path, repo, started=_ago(5)).returncode == 0
    assert _run(tmp_path, repo, started=_ago(5), stale_days="1").returncode == 1


def test_an_unreadable_service_says_so_instead_of_passing_silently(
    tmp_path: Path,
) -> None:
    """`systemctl` answers 'n/a' for a unit it does not know — not a clean bot."""
    repo = _init_origin(tmp_path)
    _commit(repo, "merged.py", days_ago=9)
    _git(repo, "push", "-q", "origin", "main")

    result = _run(tmp_path, repo, started="n/a")

    assert result.returncode == 0
    assert "skipped the freshness check" in result.stdout


def test_marker_pointing_nowhere_is_reported_not_silently_ignored(tmp_path: Path) -> None:
    """The import hook falls back to the main tree here — say so, don't pass."""
    repo = _init_origin(tmp_path)
    (tmp_path / ".ccdb-dev-worktree").write_text(str(tmp_path / "gone"))

    result = _run(tmp_path, repo)

    assert result.returncode == 2
    assert "BROKEN" in result.stdout


def test_dev_mode_on_unmerged_branch_is_drift(tmp_path: Path) -> None:
    repo = _init_origin(tmp_path)
    worktree = tmp_path / "wt-dev"
    _git(repo, "worktree", "add", "-q", str(worktree), "-b", "feat/side")
    (worktree / "claude_discord" / "extra.py").write_text("x = 1\n")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-q", "-m", "side work")
    (tmp_path / ".ccdb-dev-worktree").write_text(str(worktree))

    result = _run(tmp_path, repo)

    assert result.returncode == 1
    assert "DRIFT" in result.stdout
    assert "feat/side" in result.stdout


def test_dev_mode_on_merged_branch_is_not_drift(tmp_path: Path) -> None:
    """Dev mode is only a problem when the code isn't on origin/main."""
    repo = _init_origin(tmp_path)
    worktree = tmp_path / "wt-dev"
    _git(repo, "worktree", "add", "-q", str(worktree), "-b", "feat/merged")
    (tmp_path / ".ccdb-dev-worktree").write_text(str(worktree))

    result = _run(tmp_path, repo)

    assert result.returncode == 0
    assert "already merged" in result.stdout


def test_drift_report_counts_commits_left_behind(tmp_path: Path) -> None:
    """The count is the point: it says how many merges never actually shipped."""
    repo = _init_origin(tmp_path)
    worktree = tmp_path / "wt-dev"
    _git(repo, "worktree", "add", "-q", str(worktree), "-b", "feat/side")
    (worktree / "claude_discord" / "extra.py").write_text("x = 1\n")
    _git(worktree, "add", "-A")
    _git(worktree, "commit", "-q", "-m", "side work")
    (tmp_path / ".ccdb-dev-worktree").write_text(str(worktree))

    for n in range(2):
        (repo / f"merged{n}.py").write_text("y = 1\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", f"merged pr {n}")
    _git(repo, "push", "-q", "origin", "main")

    result = _run(tmp_path, repo)

    assert result.returncode == 1
    assert "behind   : 2 commit(s)" in result.stdout


def test_script_is_executable() -> None:
    assert SCRIPT.stat().st_mode & 0o111
