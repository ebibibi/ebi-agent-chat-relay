"""Tests for SchedulerCog."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_discord.cogs.scheduler import SchedulerCog
from claude_discord.database.repository import SessionRepository
from claude_discord.database.task_repo import TaskRepository


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.loop = MagicMock()
    return bot


def _make_runner() -> MagicMock:
    runner = MagicMock()
    runner.clone.return_value = runner
    return runner


@pytest.fixture
async def repo(tmp_path) -> TaskRepository:
    r = TaskRepository(str(tmp_path / "tasks.db"))
    await r.init_db()
    return r


@pytest.fixture
def cog(repo: TaskRepository) -> SchedulerCog:
    return SchedulerCog(_make_bot(), _make_runner(), repo=repo)


class TestSchedulerCogInit:
    def test_cog_created(self, repo: TaskRepository) -> None:
        cog = SchedulerCog(_make_bot(), _make_runner(), repo=repo)
        assert cog is not None

    def test_master_loop_not_running_at_init(self, repo: TaskRepository) -> None:
        cog = SchedulerCog(_make_bot(), _make_runner(), repo=repo)
        # loop should not be running before cog_load is called
        assert not cog._master_loop.is_running()


class TestSchedulerCogMasterLoop:
    async def test_no_tasks_does_nothing(self, cog: SchedulerCog) -> None:
        """Master loop with empty DB should complete without errors."""
        with patch(
            "claude_discord.cogs.scheduler.run_claude_with_config", new_callable=AsyncMock
        ) as mock_run:
            await cog._master_loop()
        mock_run.assert_not_called()

    async def test_future_task_not_run(self, cog: SchedulerCog, repo: TaskRepository) -> None:
        """Tasks with next_run_at in the future should not fire."""
        task_id = await repo.create(name="future", prompt="p", interval_seconds=3600, channel_id=1)
        await repo._db_execute(
            "UPDATE scheduled_tasks SET next_run_at = ? WHERE id = ?",
            (time.time() + 9999, task_id),
        )
        with patch(
            "claude_discord.cogs.scheduler.run_claude_with_config", new_callable=AsyncMock
        ) as mock_run:
            await cog._master_loop()
        mock_run.assert_not_called()

    async def test_due_task_triggers_run(self, cog: SchedulerCog, repo: TaskRepository) -> None:
        """Due tasks should cause _run_task to be called via create_task."""
        task_id = await repo.create(
            name="due", prompt="check stuff", interval_seconds=60, channel_id=42
        )
        await repo._db_execute(
            "UPDATE scheduled_tasks SET next_run_at = ? WHERE id = ?",
            (time.time() - 1, task_id),
        )
        # Patch _run_task directly — create_task wraps a coroutine, so we need
        # to intercept at this level (not run_claude_in_thread) and then yield
        # control so the event loop can execute the spawned task.
        cog._run_task = AsyncMock()
        await cog._master_loop()
        await asyncio.sleep(0)  # yield to let create_task execute

        cog._run_task.assert_called_once()
        called_task = cog._run_task.call_args[0][0]
        assert called_task["prompt"] == "check stuff"

    async def test_due_task_updates_next_run(self, cog: SchedulerCog, repo: TaskRepository) -> None:
        """After firing, next_run_at should be advanced by interval_seconds."""
        task_id = await repo.create(name="tick", prompt="p", interval_seconds=300, channel_id=1)
        before = time.time()
        await repo._db_execute(
            "UPDATE scheduled_tasks SET next_run_at = ? WHERE id = ?",
            (time.time() - 1, task_id),
        )
        cog._run_task = AsyncMock()
        await cog._master_loop()

        task = await repo.get(task_id)
        assert task is not None
        assert task["next_run_at"] >= before + 300 - 1

    async def test_run_task_creates_starter_message_then_thread(
        self, cog: SchedulerCog, repo: TaskRepository
    ) -> None:
        """_run_task should post a starter message then attach a thread to it.

        This ensures the thread appears in the channel list (left sidebar)
        rather than only in the Threads panel (🧵).
        """
        import discord

        task_id = await repo.create(
            name="my-task", prompt="do stuff", interval_seconds=60, channel_id=99
        )
        task = await repo.get(task_id)

        # Build mock channel → starter message → thread chain
        mock_thread = AsyncMock(spec=discord.Thread)
        mock_starter_msg = AsyncMock()
        mock_starter_msg.create_thread = AsyncMock(return_value=mock_thread)
        mock_channel = AsyncMock(spec=discord.TextChannel)
        mock_channel.send = AsyncMock(return_value=mock_starter_msg)

        cog.bot.get_channel = MagicMock(return_value=mock_channel)

        with patch(
            "claude_discord.cogs.scheduler.run_claude_with_config", new_callable=AsyncMock
        ) as mock_run:
            await cog._run_task(task)

        # Starter message posted to channel
        mock_channel.send.assert_called_once()
        sent_content = mock_channel.send.call_args[0][0]
        assert "my-task" in sent_content

        # Thread created from the starter message (not from the channel)
        mock_starter_msg.create_thread.assert_called_once()
        thread_name = mock_starter_msg.create_thread.call_args[1]["name"]
        assert "my-task" in thread_name

        # Claude ran inside that thread. The RunConfig now carries a surface
        # rather than a raw thread, so identity is checked by key.
        mock_run.assert_called_once()
        assert mock_run.call_args[0][0].surface.thread_key == mock_thread.id

    async def test_run_task_passes_session_repo_to_run_config(
        self, repo: TaskRepository, tmp_path
    ) -> None:
        """When session_repo is provided, RunConfig.repo should be set (not None).

        This enables session persistence so follow-up messages in the scheduled
        task's thread resume the original session (fixes GitHub issue #264).
        """
        import discord

        from claude_discord.database.models import init_db

        session_db = str(tmp_path / "sessions.db")
        await init_db(session_db)
        session_repo = SessionRepository(session_db)

        cog_with_session_repo = SchedulerCog(
            _make_bot(), _make_runner(), repo=repo, session_repo=session_repo
        )

        task_id = await repo.create(
            name="resumable", prompt="do stuff", interval_seconds=60, channel_id=99
        )
        task = await repo.get(task_id)

        mock_thread = AsyncMock(spec=discord.Thread)
        mock_starter_msg = AsyncMock()
        mock_starter_msg.create_thread = AsyncMock(return_value=mock_thread)
        mock_channel = AsyncMock(spec=discord.TextChannel)
        mock_channel.send = AsyncMock(return_value=mock_starter_msg)
        cog_with_session_repo.bot.get_channel = MagicMock(return_value=mock_channel)

        with patch(
            "claude_discord.cogs.scheduler.run_claude_with_config", new_callable=AsyncMock
        ) as mock_run:
            await cog_with_session_repo._run_task(task)

        mock_run.assert_called_once()
        run_config = mock_run.call_args[0][0]
        assert run_config.repo is session_repo

    async def test_run_task_repo_none_when_session_repo_not_set(
        self, cog: SchedulerCog, repo: TaskRepository
    ) -> None:
        """Without session_repo, RunConfig.repo stays None (backward compatible)."""
        import discord

        task_id = await repo.create(
            name="no-session", prompt="p", interval_seconds=60, channel_id=99
        )
        task = await repo.get(task_id)

        mock_thread = AsyncMock(spec=discord.Thread)
        mock_starter_msg = AsyncMock()
        mock_starter_msg.create_thread = AsyncMock(return_value=mock_thread)
        mock_channel = AsyncMock(spec=discord.TextChannel)
        mock_channel.send = AsyncMock(return_value=mock_starter_msg)
        cog.bot.get_channel = MagicMock(return_value=mock_channel)

        with patch(
            "claude_discord.cogs.scheduler.run_claude_with_config", new_callable=AsyncMock
        ) as mock_run:
            await cog._run_task(task)

        run_config = mock_run.call_args[0][0]
        assert run_config.repo is None

    async def test_run_task_uses_current_backend_from_settings(self, repo: TaskRepository) -> None:
        """Scheduled tasks should follow the runtime global backend setting."""
        import discord

        base_runner = _make_runner()
        codex_runner = MagicMock()
        factory = MagicMock()
        factory.build.return_value = codex_runner
        settings = MagicMock()
        settings.current_backend = AsyncMock(return_value="codex")
        settings.current_model = AsyncMock(return_value=None)
        settings.current_effort = AsyncMock(return_value="high")
        cog = SchedulerCog(
            _make_bot(),
            base_runner,
            repo=repo,
            backend_factory=factory,
            backend_settings=settings,
        )

        task_id = await repo.create(
            name="codex-task", prompt="p", interval_seconds=60, channel_id=99
        )
        task = await repo.get(task_id)

        mock_thread = AsyncMock(spec=discord.Thread)
        mock_thread.id = 1234
        mock_starter_msg = AsyncMock()
        mock_starter_msg.create_thread = AsyncMock(return_value=mock_thread)
        mock_channel = AsyncMock(spec=discord.TextChannel)
        mock_channel.send = AsyncMock(return_value=mock_starter_msg)
        cog.bot.get_channel = MagicMock(return_value=mock_channel)

        with patch(
            "claude_discord.cogs.scheduler.run_claude_with_config", new_callable=AsyncMock
        ) as mock_run:
            await cog._run_task(task)

        base_runner.clone.assert_not_called()
        factory.build.assert_called_once_with(backend="codex", model=None, thread_id=1234)
        assert codex_runner.effort == "high"
        run_config = mock_run.call_args[0][0]
        assert run_config.runner is codex_runner
        assert run_config.backend_settings is settings

    async def test_run_task_pin_wins_over_the_current_setting(self, repo: TaskRepository) -> None:
        """A pinned task runs on its own backend/model, not the active ones."""
        import discord

        base_runner = _make_runner()
        pinned_runner = MagicMock()
        factory = MagicMock()
        factory.build.return_value = pinned_runner
        settings = MagicMock()
        settings.current_backend = AsyncMock(return_value="claude")
        settings.current_model = AsyncMock(return_value="sonnet")
        settings.current_effort = AsyncMock(return_value="high")
        cog = SchedulerCog(
            _make_bot(),
            base_runner,
            repo=repo,
            backend_factory=factory,
            backend_settings=settings,
        )

        task_id = await repo.create(
            name="pinned-task",
            prompt="p",
            interval_seconds=60,
            channel_id=99,
            backend="codex",
            model="gpt-6-astra",
        )
        task = await repo.get(task_id)

        mock_thread = AsyncMock(spec=discord.Thread)
        mock_thread.id = 1234
        mock_starter_msg = AsyncMock()
        mock_starter_msg.create_thread = AsyncMock(return_value=mock_thread)
        mock_channel = AsyncMock(spec=discord.TextChannel)
        mock_channel.send = AsyncMock(return_value=mock_starter_msg)
        cog.bot.get_channel = MagicMock(return_value=mock_channel)

        with patch("claude_discord.cogs.scheduler.run_claude_with_config", new_callable=AsyncMock):
            await cog._run_task(task)

        factory.build.assert_called_once_with(backend="codex", model="gpt-6-astra", thread_id=1234)
        # The pin replaces both lookups rather than being merged with them.
        settings.current_backend.assert_not_called()
        settings.current_model.assert_not_called()
        # Effort still follows the setting for the pinned backend.
        settings.current_effort.assert_awaited_once_with("codex", 1234)
        assert pinned_runner.effort == "high"

    async def test_disabled_task_not_run(self, cog: SchedulerCog, repo: TaskRepository) -> None:
        """Disabled tasks should not fire even if overdue."""
        task_id = await repo.create(name="dis", prompt="p", interval_seconds=60, channel_id=1)
        await repo._db_execute(
            "UPDATE scheduled_tasks SET next_run_at = ?, enabled = 0 WHERE id = ?",
            (time.time() - 1, task_id),
        )
        with patch(
            "claude_discord.cogs.scheduler.run_claude_with_config", new_callable=AsyncMock
        ) as mock_run:
            await cog._master_loop()
        mock_run.assert_not_called()


class TestSchedulerCogFollowUp:
    """Tests for follow-up in existing threads (thread_id) and one-shot tasks."""

    async def test_run_task_with_thread_id_posts_to_existing_thread(
        self, repo: TaskRepository
    ) -> None:
        """When thread_id is set, _run_task should fetch the existing thread
        and post there instead of creating a new one."""
        import discord

        task_id = await repo.create(
            name="followup",
            prompt="Check pipeline",
            interval_seconds=86400,
            channel_id=99,
            thread_id=555555,
        )
        task = await repo.get(task_id)

        mock_thread = AsyncMock(spec=discord.Thread)
        mock_thread.send = AsyncMock()

        bot = _make_bot()
        bot.get_channel = MagicMock(
            side_effect=lambda cid: {
                99: MagicMock(spec=discord.TextChannel),
                555555: mock_thread,
            }.get(cid)
        )

        cog = SchedulerCog(bot, _make_runner(), repo=repo)

        with patch(
            "claude_discord.cogs.scheduler.run_claude_with_config", new_callable=AsyncMock
        ) as mock_run:
            await cog._run_task(task)

        # Should use the existing thread, not create a new one
        mock_run.assert_called_once()
        run_config = mock_run.call_args[0][0]
        assert run_config.surface.thread_key == mock_thread.id

        # Should have sent a starter message in the thread
        mock_thread.send.assert_called_once()

    async def test_one_shot_task_disabled_after_run(self, repo: TaskRepository) -> None:
        """Tasks with one_shot=True should be disabled after execution."""
        import discord

        task_id = await repo.create(
            name="one-time",
            prompt="Check once",
            interval_seconds=86400,
            channel_id=99,
            one_shot=True,
        )
        task = await repo.get(task_id)

        mock_thread = AsyncMock(spec=discord.Thread)
        mock_starter = AsyncMock()
        mock_starter.create_thread = AsyncMock(return_value=mock_thread)
        mock_channel = AsyncMock(spec=discord.TextChannel)
        mock_channel.send = AsyncMock(return_value=mock_starter)

        bot = _make_bot()
        bot.get_channel = MagicMock(return_value=mock_channel)

        cog = SchedulerCog(bot, _make_runner(), repo=repo)

        with patch("claude_discord.cogs.scheduler.run_claude_with_config", new_callable=AsyncMock):
            await cog._run_task(task)

        # Task should be disabled after execution
        updated = await repo.get(task_id)
        assert updated is not None
        assert updated["enabled"] is False

    async def test_recurring_task_not_disabled_after_run(self, repo: TaskRepository) -> None:
        """Regular tasks (one_shot=False) should remain enabled after execution."""
        import discord

        task_id = await repo.create(
            name="recurring",
            prompt="Regular check",
            interval_seconds=3600,
            channel_id=99,
        )
        task = await repo.get(task_id)

        mock_thread = AsyncMock(spec=discord.Thread)
        mock_starter = AsyncMock()
        mock_starter.create_thread = AsyncMock(return_value=mock_thread)
        mock_channel = AsyncMock(spec=discord.TextChannel)
        mock_channel.send = AsyncMock(return_value=mock_starter)

        bot = _make_bot()
        bot.get_channel = MagicMock(return_value=mock_channel)

        cog = SchedulerCog(bot, _make_runner(), repo=repo)

        with patch("claude_discord.cogs.scheduler.run_claude_with_config", new_callable=AsyncMock):
            await cog._run_task(task)

        updated = await repo.get(task_id)
        assert updated is not None
        assert updated["enabled"] is True

    async def test_followup_with_thread_id_resumes_session(self, repo: TaskRepository) -> None:
        """When thread_id is set and session_repo has a record, session_id should be passed."""
        # Set up session DB with an existing session for thread 555555
        import tempfile

        import discord

        from claude_discord.database.models import init_db
        from claude_discord.database.repository import SessionRepository

        fd, session_db_path = tempfile.mkstemp(suffix=".db")
        import os

        os.close(fd)
        await init_db(session_db_path)
        session_repo = SessionRepository(session_db_path)
        await session_repo.save(555555, "existing-session-id-abc", "/home/ebi")

        task_id = await repo.create(
            name="resume-followup",
            prompt="Continue analysis",
            interval_seconds=86400,
            channel_id=99,
            thread_id=555555,
        )
        task = await repo.get(task_id)

        mock_thread = AsyncMock(spec=discord.Thread)
        mock_thread.send = AsyncMock()

        bot = _make_bot()
        bot.get_channel = MagicMock(
            side_effect=lambda cid: {
                99: MagicMock(spec=discord.TextChannel),
                555555: mock_thread,
            }.get(cid)
        )

        cog = SchedulerCog(bot, _make_runner(), repo=repo, session_repo=session_repo)

        with patch(
            "claude_discord.cogs.scheduler.run_claude_with_config", new_callable=AsyncMock
        ) as mock_run:
            await cog._run_task(task)

        run_config = mock_run.call_args[0][0]
        assert run_config.session_id == "existing-session-id-abc"

        os.unlink(session_db_path)
