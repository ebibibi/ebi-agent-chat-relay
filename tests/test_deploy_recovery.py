"""Rollback must preserve main and allow the next upstream fix to deploy."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "deploy-checkout.sh"


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()


def shell(repo: Path, command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", "-c", 'source "$1"; ' + command, "test", str(SCRIPT)],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def setup_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.test")
    commit(repo, "good")
    return repo


def commit(repo: Path, text: str) -> str:
    (repo / "version").write_text(text)
    git(repo, "add", "version")
    git(repo, "commit", "-qm", text)
    return git(repo, "rev-parse", "HEAD")


def test_rollback_preserves_main_and_next_update_path(tmp_path: Path) -> None:
    repo = setup_repo(tmp_path)
    good = git(repo, "rev-parse", "HEAD")
    assert shell(repo, "ccdb_record_good").returncode == 0
    bad = commit(repo, "bad")
    result = shell(repo, "ccdb_rollback_checkout")
    assert result.returncode == 0, result.stderr
    assert git(repo, "rev-parse", "HEAD") == bad
    assert git(repo, "rev-parse", "main") == bad
    runtime = Path((repo / ".git" / "ccdb-runtime-root").read_text().strip())
    assert git(runtime, "rev-parse", "HEAD") == good
    assert (runtime / "version").read_text() == "good"
    assert shell(repo, "ccdb_resume_updates").returncode == 0
    assert not (repo / ".git" / "ccdb-runtime-root").exists()
    assert git(repo, "branch", "--show-current") == "main"
    fixed = commit(repo, "fixed")
    assert shell(repo, "ccdb_record_good").returncode == 0
    assert shell(repo, "ccdb_rollback_checkout").returncode == 0
    assert git(repo, "rev-parse", "HEAD") == fixed


def test_rollback_refuses_unverified_fallback_and_preserves_edits(tmp_path: Path) -> None:
    repo = setup_repo(tmp_path)
    assert shell(repo, "ccdb_rollback_checkout").returncode != 0
    assert shell(repo, "ccdb_record_good").returncode == 0
    bad = commit(repo, "bad")
    (repo / "version").write_text("uncommitted work")
    assert shell(repo, "ccdb_rollback_checkout").returncode != 0
    assert git(repo, "rev-parse", "HEAD") == bad
    assert (repo / "version").read_text() == "uncommitted work"


def test_unrelated_detached_checkout_is_not_changed(tmp_path: Path) -> None:
    repo = setup_repo(tmp_path)
    git(repo, "checkout", "--detach")
    assert shell(repo, "ccdb_resume_updates").returncode == 0
    assert git(repo, "branch", "--show-current") == ""


def test_dirty_import_success_cannot_replace_verified_checkpoint(tmp_path: Path) -> None:
    repo = setup_repo(tmp_path)
    good = git(repo, "rev-parse", "HEAD")
    assert shell(repo, "ccdb_record_good").returncode == 0
    commit(repo, "broken without local edits")
    (repo / "version").write_text("local repair, not committed")
    assert shell(repo, "ccdb_record_good").returncode == 0
    assert (repo / ".git" / "ccdb-last-good").read_text().strip() == good


def test_unreadable_index_never_replaces_checkpoint_or_allows_rollback(tmp_path: Path) -> None:
    repo = setup_repo(tmp_path)
    good = git(repo, "rev-parse", "HEAD")
    assert shell(repo, "ccdb_record_good").returncode == 0
    commit(repo, "new version")
    (repo / ".git" / "index").write_bytes(b"invalid test index")
    assert shell(repo, "ccdb_record_good").returncode != 0
    assert (repo / ".git" / "ccdb-last-good").read_text().strip() == good
    assert shell(repo, "ccdb_rollback_checkout").returncode != 0
    assert not (repo / ".git" / "ccdb-runtime-root").exists()


def test_unreadable_fallback_index_is_not_selected(tmp_path: Path) -> None:
    repo = setup_repo(tmp_path)
    assert shell(repo, "ccdb_record_good").returncode == 0
    commit(repo, "new version")
    assert shell(repo, "ccdb_rollback_checkout").returncode == 0
    marker = repo / ".git" / "ccdb-runtime-root"
    fallback = Path(marker.read_text().strip())
    index = Path(git(fallback, "rev-parse", "--git-path", "index"))
    index.write_bytes(b"invalid fallback index")
    assert shell(repo, "ccdb_resume_updates").returncode == 0
    assert shell(repo, "ccdb_rollback_checkout").returncode != 0
    assert not marker.exists()


def test_runtime_hook_loads_fallback_then_returns_to_main(tmp_path: Path) -> None:
    repo = setup_repo(tmp_path)
    package = repo / "claude_discord"
    package.mkdir()
    (package / "__init__.py").write_text('VERSION = "good"\n')
    git(repo, "add", "claude_discord")
    git(repo, "commit", "-qm", "working package")
    assert shell(repo, "ccdb_record_good").returncode == 0
    (package / "__init__.py").write_text('VERSION = "bad"\n')
    git(repo, "add", "claude_discord")
    git(repo, "commit", "-qm", "broken package")
    assert shell(repo, "ccdb_rollback_checkout").returncode == 0

    # Execute the exact installed hook with a main checkout first on sys.path.
    # No real user settings, model process, or third-party API is consulted.
    source = SCRIPT.with_name("pre-start.sh").read_text()
    hook = source.split("<< 'HOOK_EOF'\n", 1)[1].split("\nHOOK_EOF", 1)[0]
    site = tmp_path / "site"
    site.mkdir()
    (site / "_ccdb_dev_hook.py").write_text(hook)
    (site / "_ccdb_runtime_pointer").write_text(str(repo / ".git" / "ccdb-runtime-root"))
    command = (
        "import os,sys; "
        "os.path.expanduser=lambda _:sys.argv[3]; "
        "sys.path[:0]=[sys.argv[1],sys.argv[2]]; "
        "import _ccdb_dev_hook; import claude_discord; print(claude_discord.VERSION)"
    )
    args = [sys.executable, "-I", "-c", command, str(repo), str(site), str(tmp_path / "no-dev")]
    assert subprocess.check_output(args, text=True).strip() == "good"
    assert shell(repo, "ccdb_resume_updates").returncode == 0
    assert subprocess.check_output(args, text=True).strip() == "bad"
