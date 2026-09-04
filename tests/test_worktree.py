"""Tests for WorktreeManager and related helpers."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from claude_discord.worktree import (
    WorktreeInfo,
    WorktreeManager,
    _find_main_repo,
    _is_clean,
)

# ---------------------------------------------------------------------------
# WorktreeInfo — derived field computation
# ---------------------------------------------------------------------------


class TestWorktreeInfo:
    def test_session_branch_parsed(self) -> None:
        wt = WorktreeInfo(
            path="/home/ebi/wt-12345",
            branch="session/12345",
            commit="abc1234",
            main_repo="/home/ebi/some-repo",
        )
        assert wt.is_session_worktree is True
        assert wt.thread_id == 12345

    def test_labelled_session_branch_parsed(self) -> None:
        """A session's second worktree labels both names and is still ours.

        Regression: anchoring the branch pattern at the end made every
        ``session/{id}-{label}`` worktree invisible to cleanup, so they
        accumulated indefinitely while the unlabelled ones were collected.
        """
        wt = WorktreeInfo(
            path="/home/ebi/wt-12345-obsidian",
            branch="session/12345-obsidian",
            commit="abc1234",
            main_repo="/home/ebi/some-repo",
        )
        assert wt.is_session_worktree is True
        assert wt.thread_id == 12345

    def test_non_session_branch(self) -> None:
        wt = WorktreeInfo(
            path="/home/ebi/wt-feat-foo",
            branch="feat/my-feature",
            commit="abc1234",
            main_repo="/home/ebi/some-repo",
        )
        assert wt.is_session_worktree is False
        assert wt.thread_id is None

    def test_feature_branch_in_a_session_worktree_is_not_session(self) -> None:
        """A worktree the session renamed onto a real feature branch is left alone.

        The branch — not the directory name — decides. ``wt-12345-ebi-study``
        checked out on ``fix/issue-45-...`` holds named work that belongs to a
        PR, so automatic cleanup must not claim it.
        """
        wt = WorktreeInfo(
            path="/home/ebi/wt-12345-ebi-study",
            branch="fix/issue-45-ga-production-only",
            commit="abc1234",
            main_repo="/home/ebi/some-repo",
        )
        assert wt.is_session_worktree is False
        assert wt.thread_id is None

    def test_main_branch_not_session(self) -> None:
        wt = WorktreeInfo(
            path="/home/ebi/some-repo",
            branch="main",
            commit="abc1234",
            main_repo="/home/ebi/some-repo",
        )
        assert wt.is_session_worktree is False
        assert wt.thread_id is None

    def test_frozen(self) -> None:
        wt = WorktreeInfo(
            path="/home/ebi/wt-99",
            branch="session/99",
            commit="aaa",
            main_repo="/home/ebi/repo",
        )
        import dataclasses

        with pytest.raises(dataclasses.FrozenInstanceError):
            wt.path = "/other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# _find_main_repo
# ---------------------------------------------------------------------------


class TestFindMainRepo:
    def test_parses_gitdir_file(self, tmp_path: Path) -> None:
        """A .git file with a standard gitdir: line should resolve the main repo."""
        worktree_dir = tmp_path / "wt-999"
        worktree_dir.mkdir()
        main_git = tmp_path / "main-repo" / ".git" / "worktrees" / "wt-999"
        main_git.mkdir(parents=True)

        git_file = worktree_dir / ".git"
        git_file.write_text(f"gitdir: {main_git}\n")

        result = _find_main_repo(str(worktree_dir))
        assert result == str(tmp_path / "main-repo")

    def test_missing_git_file_returns_none(self, tmp_path: Path) -> None:
        d = tmp_path / "not-a-worktree"
        d.mkdir()
        assert _find_main_repo(str(d)) is None

    def test_non_gitdir_content_returns_none(self, tmp_path: Path) -> None:
        d = tmp_path / "weird"
        d.mkdir()
        (d / ".git").write_text("not a gitdir line\n")
        assert _find_main_repo(str(d)) is None


# ---------------------------------------------------------------------------
# _is_clean (subprocess mock)
# ---------------------------------------------------------------------------


class TestIsClean:
    def test_clean_worktree(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        with patch("claude_discord.worktree._run", return_value=mock_result):
            assert _is_clean("/some/path") is True

    def test_dirty_worktree(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = " M some_file.py\n"
        with patch("claude_discord.worktree._run", return_value=mock_result):
            assert _is_clean("/some/path") is False

    def test_git_error_treated_as_dirty(self) -> None:
        mock_result = MagicMock()
        mock_result.returncode = 128
        mock_result.stdout = ""
        with patch("claude_discord.worktree._run", return_value=mock_result):
            assert _is_clean("/some/path") is False


# ---------------------------------------------------------------------------
# WorktreeManager.find_session_worktrees
# ---------------------------------------------------------------------------


class TestFindSessionWorktrees:
    def test_finds_session_worktrees(self, tmp_path: Path) -> None:
        """Only wt-{digits} directories with session/{digits} branches should be returned."""
        # Create a fake session worktree directory
        session_wt = tmp_path / "wt-12345"
        session_wt.mkdir()
        (session_wt / ".git").write_text("gitdir: /fake/repo/.git/worktrees/wt-12345\n")

        # Non-session worktree (feature branch) — should be excluded
        feature_wt = tmp_path / "wt-feat"
        feature_wt.mkdir()
        (feature_wt / ".git").write_text("gitdir: /fake/repo/.git/worktrees/wt-feat\n")

        # Not a worktree (no .git file)
        plain_dir = tmp_path / "wt-notgit"
        plain_dir.mkdir()

        with (
            patch("claude_discord.worktree._get_branch") as mock_branch,
            patch("claude_discord.worktree._get_commit", return_value="abc1234"),
            patch("claude_discord.worktree._find_main_repo", return_value="/fake/repo"),
        ):
            # Return session branch for wt-12345, feature branch for wt-feat
            def branch_side_effect(path: str) -> str:
                if "wt-12345" in path:
                    return "session/12345"
                return "feat/my-feature"

            mock_branch.side_effect = branch_side_effect

            wm = WorktreeManager(base_dir=str(tmp_path))
            worktrees = wm.find_session_worktrees()

        assert len(worktrees) == 1
        assert worktrees[0].thread_id == 12345
        assert worktrees[0].is_session_worktree is True

    def test_finds_labelled_session_worktrees(self, tmp_path: Path) -> None:
        """``wt-{id}-{label}`` directories are scanned, not skipped by the path filter.

        Regression: the directory pattern was anchored at the end, so a session's
        labelled second worktree was never even opened — the branch check that
        would have claimed it never ran.
        """
        for name in ("wt-12345-obsidian", "wt-obsidian-12345"):
            wt = tmp_path / name
            wt.mkdir()
            (wt / ".git").write_text(f"gitdir: /fake/repo/.git/worktrees/{name}\n")

        with (
            patch("claude_discord.worktree._get_branch", return_value="session/12345-obsidian"),
            patch("claude_discord.worktree._get_commit", return_value="abc1234"),
            patch("claude_discord.worktree._find_main_repo", return_value="/fake/repo"),
        ):
            wm = WorktreeManager(base_dir=str(tmp_path))
            worktrees = wm.find_session_worktrees()

        # The label sits on either side of the thread id in practice, so the
        # directory name must not be the gate — the branch is.
        assert sorted(w.path for w in worktrees) == [
            str(tmp_path / "wt-12345-obsidian"),
            str(tmp_path / "wt-obsidian-12345"),
        ]
        assert {w.thread_id for w in worktrees} == {12345}

    def test_empty_when_no_session_worktrees(self, tmp_path: Path) -> None:
        wm = WorktreeManager(base_dir=str(tmp_path))
        assert wm.find_session_worktrees() == []


# ---------------------------------------------------------------------------
# WorktreeManager.cleanup_for_thread
# ---------------------------------------------------------------------------


class TestCleanupForThread:
    def test_removes_clean_worktree(self, tmp_path: Path) -> None:
        wt_path = tmp_path / "wt-999"
        wt_path.mkdir()
        (wt_path / ".git").write_text("gitdir: /repo/.git/worktrees/wt-999\n")

        with (
            patch("claude_discord.worktree._is_clean", return_value=True),
            patch("claude_discord.worktree._find_main_repo", return_value="/repo"),
            patch("claude_discord.worktree._run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            wm = WorktreeManager(base_dir=str(tmp_path))
            result = wm.cleanup_for_thread(999)

        assert result.removed is True
        assert result.thread_id == 999

    def test_skips_dirty_worktree(self, tmp_path: Path) -> None:
        wt_path = tmp_path / "wt-999"
        wt_path.mkdir()
        (wt_path / ".git").write_text("gitdir: /repo/.git/worktrees/wt-999\n")

        with patch("claude_discord.worktree._is_clean", return_value=False):
            wm = WorktreeManager(base_dir=str(tmp_path))
            result = wm.cleanup_for_thread(999)

        assert result.removed is False
        assert "uncommitted changes" in result.reason

    def test_noop_when_worktree_missing(self, tmp_path: Path) -> None:
        wm = WorktreeManager(base_dir=str(tmp_path))
        result = wm.cleanup_for_thread(42)
        assert result.removed is False
        assert "does not exist" in result.reason

    def test_handles_git_remove_failure(self, tmp_path: Path) -> None:
        wt_path = tmp_path / "wt-123"
        wt_path.mkdir()
        (wt_path / ".git").write_text("gitdir: /repo/.git/worktrees/wt-123\n")

        with (
            patch("claude_discord.worktree._is_clean", return_value=True),
            patch("claude_discord.worktree._find_main_repo", return_value="/repo"),
            patch("claude_discord.worktree._run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=128, stderr="locked worktree")
            wm = WorktreeManager(base_dir=str(tmp_path))
            result = wm.cleanup_for_thread(123)

        assert result.removed is False
        assert "git worktree remove failed" in result.reason


# ---------------------------------------------------------------------------
# WorktreeManager.cleanup_orphaned
# ---------------------------------------------------------------------------


class TestCleanupOrphaned:
    def test_skips_active_sessions(self, tmp_path: Path) -> None:
        wt_path = tmp_path / "wt-555"
        wt_path.mkdir()
        (wt_path / ".git").write_text("gitdir: /repo/.git/worktrees/wt-555\n")

        with (
            patch("claude_discord.worktree._get_branch", return_value="session/555"),
            patch("claude_discord.worktree._get_commit", return_value="abc"),
            patch("claude_discord.worktree._find_main_repo", return_value="/repo"),
        ):
            wm = WorktreeManager(base_dir=str(tmp_path))
            results = wm.cleanup_orphaned(active_thread_ids={555})

        assert len(results) == 1
        assert results[0].removed is False
        assert "active" in results[0].reason

    def test_removes_orphaned_clean_worktree(self, tmp_path: Path) -> None:
        wt_path = tmp_path / "wt-777"
        wt_path.mkdir()
        (wt_path / ".git").write_text("gitdir: /repo/.git/worktrees/wt-777\n")

        with (
            patch("claude_discord.worktree._get_branch", return_value="session/777"),
            patch("claude_discord.worktree._get_commit", return_value="abc"),
            patch("claude_discord.worktree._find_main_repo", return_value="/repo"),
            patch("claude_discord.worktree._is_clean", return_value=True),
            patch("claude_discord.worktree._run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stderr="")
            wm = WorktreeManager(base_dir=str(tmp_path))
            results = wm.cleanup_orphaned(active_thread_ids=set())

        assert len(results) == 1
        assert results[0].removed is True
