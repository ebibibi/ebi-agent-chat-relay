"""Git worktree lifecycle management for Claude Code sessions.

Each Claude Code session may create a git worktree (wt-{thread_id}) to work
in isolation from other concurrent sessions.  These worktrees accumulate over
time because Claude has no built-in mechanism to remove them.

This module provides WorktreeManager to:
  - Identify session worktrees (branches matching ``session/\\d+``)
  - Clean up worktrees safely (never remove if uncommitted changes exist)
  - Distinguish session worktrees from manually-created feature worktrees

Cleanup is triggered at three points:
  1. Session end — remove wt-{thread_id} if it's clean (see _run_helper.py)
  2. Bot startup — remove all orphaned clean session worktrees
  3. Manual — via /worktree-list and /worktree-cleanup Discord commands

Safety invariant: a worktree with uncommitted changes is NEVER auto-removed.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Branch pattern created by the concurrency notice template:
# git worktree add ../wt-{thread_id} -b session/{thread_id}
#
# A session that needs a second worktree appends a label to both names
# (``wt-{thread_id}-obsidian`` on ``session/{thread_id}-obsidian``), so the
# thread id is a prefix rather than the whole name.  Anchoring these patterns
# at the end made every labelled worktree invisible to cleanup — the branch
# still carries the thread id, which is what identifies it as ours.
_SESSION_BRANCH_RE = re.compile(r"^session/(\d+)(?:-.*)?$")

#: Directory-name prefilter, so scanning ``base_dir`` only shells out to git for
#: plausible candidates.  It is deliberately *not* an identity test: sessions
#: label the directory freely and put the label on either side of the thread id
#: (``wt-{id}-obsidian`` and ``wt-obsidian-{id}`` both occur).  Matching the
#: directory name against the thread id made every labelled worktree invisible
#: to cleanup.  The branch is the only reliable identifier, and it decides.
_SESSION_WORKTREE_DIR_PREFIX = "wt-"


@dataclass(frozen=True)
class WorktreeInfo:
    """Snapshot of a single git worktree."""

    path: str
    branch: str  # e.g. "session/1474394070012137593" or "feat/foo"
    commit: str
    main_repo: str  # absolute path of the main git repository

    # Derived fields computed in __post_init__
    thread_id: int | None = field(default=None, init=False)
    is_session_worktree: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        m = _SESSION_BRANCH_RE.match(self.branch)
        # frozen=True requires object.__setattr__ for post-init assignment
        object.__setattr__(self, "thread_id", int(m.group(1)) if m else None)
        object.__setattr__(self, "is_session_worktree", m is not None)


@dataclass(frozen=True)
class CleanupResult:
    """Result of a single worktree cleanup attempt."""

    path: str
    thread_id: int | None
    removed: bool
    reason: str  # human-readable explanation


def _run(args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    """Run a subprocess and return the result (never raises on non-zero exit)."""
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def _find_main_repo(worktree_path: str) -> str | None:
    """Return the absolute path of the main git repo for a worktree.

    Inside a worktree, ``.git`` is a *file* (not a directory) containing::

        gitdir: /path/to/main-repo/.git/worktrees/<name>

    We parse that file to locate the main repo's ``.git`` directory, then
    return its parent.
    """
    git_file = Path(worktree_path) / ".git"
    if not git_file.is_file():
        return None
    try:
        content = git_file.read_text().strip()
    except OSError:
        return None

    if not content.startswith("gitdir:"):
        return None

    # gitdir: /home/ebi/some-repo/.git/worktrees/wt-xxx
    gitdir_value = content[len("gitdir:") :].strip()
    # Navigate up: .git/worktrees/<name> → .git → repo
    git_common = Path(gitdir_value)
    # Remove ".git/worktrees/<name>" suffix to get the main repo root
    # Expected: git_common ends with .git/worktrees/<name>
    if git_common.parent.name == "worktrees" and git_common.parent.parent.name == ".git":
        main_repo = str(git_common.parent.parent.parent)
        return main_repo

    # Fallback: ask git itself
    result = _run(
        ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=worktree_path
    )
    if result.returncode == 0:
        git_common_dir = result.stdout.strip()
        return str(Path(git_common_dir).parent)
    return None


def _is_clean(worktree_path: str) -> bool:
    """Return True if the worktree has no uncommitted changes."""
    result = _run(["git", "status", "--porcelain"], cwd=worktree_path)
    if result.returncode != 0:
        # Can't determine status — treat as dirty to be safe
        return False
    return result.stdout.strip() == ""


def _get_branch(worktree_path: str) -> str:
    """Return the current branch name, or empty string on error."""
    result = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=worktree_path)
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _get_commit(worktree_path: str) -> str:
    """Return the short commit hash, or empty string on error."""
    result = _run(["git", "rev-parse", "--short", "HEAD"], cwd=worktree_path)
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


class WorktreeManager:
    """Manages Claude Code session git worktrees.

    Scans ``base_dir`` for directories matching ``wt-{digits}`` and
    determines whether they are session worktrees (branch ``session/{digits}``).

    Args:
        base_dir: Directory to scan for session worktrees.
                  Typically the parent of all repos (e.g. ``/home/ebi``).
    """

    def __init__(self, base_dir: str = "/home/ebi") -> None:
        self._base_dir = base_dir

    def find_session_worktrees(self) -> list[WorktreeInfo]:
        """Return all session worktrees (branch matching ``session/\\d+``).

        Scans ``base_dir`` for directories whose basename matches ``wt-\\d+``,
        then filters to those with a ``session/{id}`` branch.
        """
        results: list[WorktreeInfo] = []
        base = Path(self._base_dir)

        try:
            entries = list(base.iterdir())
        except OSError as exc:
            logger.error("Cannot scan base_dir %s: %s", self._base_dir, exc)
            return results

        for entry in entries:
            if not entry.is_dir():
                continue
            if not entry.name.startswith(_SESSION_WORKTREE_DIR_PREFIX):
                continue
            if not (entry / ".git").exists():
                continue

            path = str(entry)
            branch = _get_branch(path)
            commit = _get_commit(path)
            main_repo = _find_main_repo(path) or ""

            info = WorktreeInfo(path=path, branch=branch, commit=commit, main_repo=main_repo)
            if info.is_session_worktree:
                results.append(info)

        return results

    def cleanup_for_thread(self, thread_id: int) -> CleanupResult:
        """Remove the worktree for ``thread_id`` if it is clean.

        The worktree path is expected to be ``{base_dir}/wt-{thread_id}``.
        If the path does not exist this is a no-op (returns removed=False).

        Returns:
            CleanupResult describing what happened.
        """
        path = str(Path(self._base_dir) / f"wt-{thread_id}")
        if not Path(path).is_dir():
            return CleanupResult(
                path=path,
                thread_id=thread_id,
                removed=False,
                reason="worktree directory does not exist",
            )

        return self._try_remove(path, thread_id)

    def cleanup_orphaned(self, active_thread_ids: set[int]) -> list[CleanupResult]:
        """Remove clean session worktrees whose sessions are no longer active.

        Call this at bot startup (``active_thread_ids=set()``) to sweep up
        leftover worktrees from previous runs, or periodically with the current
        active session IDs to keep things tidy.

        Args:
            active_thread_ids: Thread IDs that are currently running. Worktrees
                               for these sessions are skipped.

        Returns:
            List of CleanupResult for every worktree that was examined.
        """
        results: list[CleanupResult] = []
        for info in self.find_session_worktrees():
            if info.thread_id in active_thread_ids:
                results.append(
                    CleanupResult(
                        path=info.path,
                        thread_id=info.thread_id,
                        removed=False,
                        reason="session is still active",
                    )
                )
                continue

            result = self._try_remove(info.path, info.thread_id)
            results.append(result)

        return results

    def _try_remove(self, path: str, thread_id: int | None) -> CleanupResult:
        """Check cleanliness and remove the worktree if safe."""
        if not _is_clean(path):
            logger.warning(
                "Skipping worktree removal (dirty): %s (thread_id=%s)",
                path,
                thread_id,
            )
            return CleanupResult(
                path=path,
                thread_id=thread_id,
                removed=False,
                reason="worktree has uncommitted changes — skipped to prevent data loss",
            )

        main_repo = _find_main_repo(path)
        if not main_repo:
            logger.warning("Cannot determine main repo for worktree %s", path)
            return CleanupResult(
                path=path,
                thread_id=thread_id,
                removed=False,
                reason="cannot determine main git repository",
            )

        result = _run(["git", "worktree", "remove", path], cwd=main_repo)
        if result.returncode == 0:
            logger.info("Removed session worktree: %s (thread_id=%s)", path, thread_id)
            return CleanupResult(path=path, thread_id=thread_id, removed=True, reason="clean")
        else:
            stderr = result.stderr.strip()
            logger.warning(
                "Failed to remove worktree %s: %s",
                path,
                stderr,
            )
            return CleanupResult(
                path=path,
                thread_id=thread_id,
                removed=False,
                reason=f"git worktree remove failed: {stderr}",
            )
