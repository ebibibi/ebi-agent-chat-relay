"""The "waiting on a scheduled task" thread marker.

A thread that a scheduled task will post into later (a follow-up task, or a
``ScheduleWakeup`` bridged to one) looks idle in the channel list.  The
scheduler keeps a marker on its title while such a task is pending.
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_discord.cogs.scheduler import SchedulerCog
from claude_discord.database.task_repo import TaskRepository
from claude_discord.scheduled_marker import ScheduledThreadMarker
from claude_discord.thread_marker import (
    DEFAULT_DONE_MARKER,
    DEFAULT_SCHEDULED_MARKER,
    DEFAULT_SPAWN_MARKER,
    DONE_MARKER_ENV_VAR,
    MAX_THREAD_NAME_LENGTH,
    SCHEDULED_MARKER_ENV_VAR,
    family_code,
    mark_done_thread_name,
    mark_scheduled_thread_name,
    retag_thread_name,
    unmark_done_thread_name,
    unmark_scheduled_thread_name,
)

DONE = DEFAULT_DONE_MARKER
SCHED = DEFAULT_SCHEDULED_MARKER


@pytest.fixture(autouse=True)
def _default_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DONE_MARKER_ENV_VAR, raising=False)
    monkeypatch.delenv(SCHEDULED_MARKER_ENV_VAR, raising=False)


# ---------------------------------------------------------------------------
# name helpers
# ---------------------------------------------------------------------------


class TestMarkScheduled:
    def test_prefixes_the_marker(self) -> None:
        assert mark_scheduled_thread_name("Merge PR") == f"{SCHED} Merge PR"

    def test_is_idempotent(self) -> None:
        once = mark_scheduled_thread_name("Merge PR")
        assert mark_scheduled_thread_name(once) == once

    def test_goes_behind_the_done_marker(self) -> None:
        assert mark_scheduled_thread_name(f"{DONE} Merge PR") == f"{DONE} {SCHED} Merge PR"

    def test_goes_in_front_of_lineage_tags(self) -> None:
        name = f"{DEFAULT_SPAWN_MARKER}{family_code(1)} Merge PR"
        assert mark_scheduled_thread_name(name) == f"{SCHED} {name}"

    def test_keeps_the_marker_when_truncating(self) -> None:
        marked = mark_scheduled_thread_name("x" * 200)
        assert marked.startswith(SCHED)
        assert len(marked) == MAX_THREAD_NAME_LENGTH

    def test_empty_env_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(SCHEDULED_MARKER_ENV_VAR, "")
        assert mark_scheduled_thread_name("Merge PR") == "Merge PR"

    def test_custom_marker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(SCHEDULED_MARKER_ENV_VAR, "[later]")
        assert mark_scheduled_thread_name("Merge PR") == "[later] Merge PR"


class TestUnmarkScheduled:
    def test_removes_the_marker(self) -> None:
        assert unmark_scheduled_thread_name(f"{SCHED} Merge PR") == "Merge PR"

    def test_keeps_the_done_marker(self) -> None:
        assert unmark_scheduled_thread_name(f"{DONE} {SCHED} Merge PR") == f"{DONE} Merge PR"

    def test_unmarked_name_is_unchanged(self) -> None:
        assert unmark_scheduled_thread_name("Merge PR") == "Merge PR"


class TestDoneAndScheduledTogether:
    def test_done_goes_in_front_of_scheduled(self) -> None:
        assert mark_done_thread_name(f"{SCHED} Merge PR") == f"{DONE} {SCHED} Merge PR"

    def test_unmark_done_keeps_scheduled(self) -> None:
        assert unmark_done_thread_name(f"{DONE} {SCHED} Merge PR") == f"{SCHED} Merge PR"

    def test_either_order_is_normalised(self) -> None:
        assert mark_done_thread_name(f"{SCHED} {DONE} Merge PR") == f"{DONE} {SCHED} Merge PR"


class TestRetitleKeepsScheduled:
    def test_retitle_keeps_the_scheduled_marker(self) -> None:
        assert retag_thread_name(f"{SCHED} Old", "New") == f"{SCHED} New"

    def test_retitle_drops_done_but_keeps_scheduled_and_lineage(self) -> None:
        tag = f"{DEFAULT_SPAWN_MARKER}{family_code(1)}"
        old = f"{DONE} {SCHED} {tag} Old"
        assert retag_thread_name(old, "New") == f"{SCHED} {tag} New"

    def test_a_suggested_title_copying_the_marker_is_cleaned(self) -> None:
        assert retag_thread_name("Old", f"{SCHED} New") == "New"


# ---------------------------------------------------------------------------
# ScheduledThreadMarker — reconcile titles with the set of pending threads
# ---------------------------------------------------------------------------


def _thread(name: str) -> MagicMock:
    thread = MagicMock(spec=discord.Thread)
    thread.name = name
    thread.edit = AsyncMock()
    return thread


def _bot(channels: dict[int, object]) -> MagicMock:
    bot = MagicMock()
    bot.get_channel.side_effect = channels.get
    bot.fetch_channel = AsyncMock(side_effect=RuntimeError("Unknown Channel"))
    return bot


class TestScheduledThreadMarker:
    async def test_marks_a_pending_thread(self) -> None:
        thread = _thread("Merge PR")
        marker = ScheduledThreadMarker(_bot({1: thread}))

        await marker.reconcile({1})

        thread.edit.assert_awaited_once_with(name=f"{SCHED} Merge PR")

    async def test_already_marked_thread_is_not_renamed(self) -> None:
        thread = _thread(f"{SCHED} Merge PR")
        marker = ScheduledThreadMarker(_bot({1: thread}))

        await marker.reconcile({1})

        thread.edit.assert_not_awaited()

    async def test_unchanged_pending_set_touches_nothing(self) -> None:
        thread = _thread("Merge PR")
        bot = _bot({1: thread})
        marker = ScheduledThreadMarker(bot)
        await marker.reconcile({1})
        bot.get_channel.reset_mock()

        await marker.reconcile({1})

        bot.get_channel.assert_not_called()

    async def test_unmarks_a_thread_that_is_no_longer_pending(self) -> None:
        thread = _thread("Merge PR")
        marker = ScheduledThreadMarker(_bot({1: thread}))
        await marker.reconcile({1})
        thread.name = f"{SCHED} Merge PR"
        thread.edit.reset_mock()

        await marker.reconcile(set())

        thread.edit.assert_awaited_once_with(name="Merge PR")

    async def test_a_non_thread_channel_is_skipped(self) -> None:
        channel = MagicMock(spec=discord.TextChannel)
        channel.edit = AsyncMock()
        marker = ScheduledThreadMarker(_bot({1: channel}))

        await marker.reconcile({1})

        channel.edit.assert_not_awaited()

    async def test_an_unknown_channel_does_not_raise(self) -> None:
        marker = ScheduledThreadMarker(_bot({}))
        await marker.reconcile({99})

    async def test_rename_failure_is_retried_next_time(self) -> None:
        thread = _thread("Merge PR")
        thread.edit.side_effect = [RuntimeError("rate limited"), None]
        marker = ScheduledThreadMarker(_bot({1: thread}))

        await marker.reconcile({1})
        await marker.reconcile({1})

        assert thread.edit.await_count == 2

    async def test_disabled_marker_renames_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(SCHEDULED_MARKER_ENV_VAR, "")
        thread = _thread("Merge PR")
        marker = ScheduledThreadMarker(_bot({1: thread}))

        await marker.reconcile({1})

        thread.edit.assert_not_awaited()


# ---------------------------------------------------------------------------
# SchedulerCog — which threads count as pending
# ---------------------------------------------------------------------------


@pytest.fixture
async def repo(tmp_path) -> TaskRepository:
    r = TaskRepository(str(tmp_path / "tasks.db"))
    await r.init_db()
    return r


def _cog(repo: TaskRepository) -> SchedulerCog:
    runner = MagicMock()
    runner.clone.return_value = runner
    return SchedulerCog(MagicMock(), runner, repo=repo)


async def _future(repo: TaskRepository, **kwargs: object) -> int:
    task_id = await repo.create(
        prompt="p", interval_seconds=3600, channel_id=1, run_immediately=False, **kwargs
    )
    await repo._db_execute(
        "UPDATE scheduled_tasks SET next_run_at = ? WHERE id = ?",
        (time.time() + 9999, task_id),
    )
    return task_id


class TestPendingThreads:
    async def test_enabled_follow_up_task_is_pending(self, repo: TaskRepository) -> None:
        await _future(repo, name="merge", thread_id=555, one_shot=True)
        assert await _cog(repo)._pending_thread_ids() == {555}

    async def test_task_without_thread_is_ignored(self, repo: TaskRepository) -> None:
        await _future(repo, name="nightly")
        assert await _cog(repo)._pending_thread_ids() == set()

    async def test_disabled_task_is_not_pending(self, repo: TaskRepository) -> None:
        task_id = await _future(repo, name="merge", thread_id=555, one_shot=True)
        await repo.set_enabled(task_id, enabled=False)
        assert await _cog(repo)._pending_thread_ids() == set()

    async def test_running_one_shot_is_no_longer_waiting(self, repo: TaskRepository) -> None:
        task_id = await _future(repo, name="merge", thread_id=555, one_shot=True)
        cog = _cog(repo)
        cog._running.add(task_id)
        assert await cog._pending_thread_ids() == set()

    async def test_running_recurring_task_still_waits(self, repo: TaskRepository) -> None:
        task_id = await _future(repo, name="poll", thread_id=555)
        cog = _cog(repo)
        cog._running.add(task_id)
        assert await cog._pending_thread_ids() == {555}

    async def test_master_loop_reconciles_even_with_nothing_due(self, repo: TaskRepository) -> None:
        await _future(repo, name="merge", thread_id=555, one_shot=True)
        cog = _cog(repo)
        cog.thread_marker = MagicMock()
        cog.thread_marker.reconcile = AsyncMock()

        await cog._master_loop()
        assert cog._marker_task is not None
        await cog._marker_task

        cog.thread_marker.reconcile.assert_awaited_once_with({555})

    async def test_fired_one_shot_is_unmarked_in_the_same_tick(self, repo: TaskRepository) -> None:
        task_id = await _future(repo, name="merge", thread_id=555, one_shot=True)
        await repo._db_execute(
            "UPDATE scheduled_tasks SET next_run_at = ? WHERE id = ?", (time.time() - 1, task_id)
        )
        cog = _cog(repo)
        cog._run_task = AsyncMock()  # type: ignore[method-assign]
        cog.thread_marker = MagicMock()
        cog.thread_marker.reconcile = AsyncMock()

        await cog._master_loop()
        assert cog._marker_task is not None
        await cog._marker_task

        cog.thread_marker.reconcile.assert_awaited_once_with(set())
