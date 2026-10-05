"""Tests for TaskRepository — scheduled task CRUD."""

from __future__ import annotations

import time

import pytest

from claude_discord.database.task_repo import TaskRepository


@pytest.fixture
async def repo(tmp_path) -> TaskRepository:
    r = TaskRepository(str(tmp_path / "tasks.db"))
    await r.init_db()
    return r


class TestTaskRepoCreate:
    async def test_create_returns_id(self, repo: TaskRepository) -> None:
        task_id = await repo.create(
            name="test-task",
            prompt="Do something",
            interval_seconds=3600,
            channel_id=123,
        )
        assert isinstance(task_id, int)
        assert task_id > 0

    async def test_create_sets_next_run_at(self, repo: TaskRepository) -> None:
        before = time.time()
        task_id = await repo.create(
            name="test-task",
            prompt="Do something",
            interval_seconds=3600,
            channel_id=123,
        )
        task = await repo.get(task_id)
        assert task is not None
        assert task["next_run_at"] >= before

    async def test_create_defaults(self, repo: TaskRepository) -> None:
        task_id = await repo.create(
            name="minimal",
            prompt="Minimal prompt",
            interval_seconds=60,
            channel_id=999,
        )
        task = await repo.get(task_id)
        assert task is not None
        assert task["enabled"] is True
        assert task["working_dir"] is None
        assert task["last_run_at"] is None

    async def test_create_with_working_dir(self, repo: TaskRepository) -> None:
        task_id = await repo.create(
            name="with-dir",
            prompt="prompt",
            interval_seconds=60,
            channel_id=1,
            working_dir="/home/user/project",
        )
        task = await repo.get(task_id)
        assert task is not None
        assert task["working_dir"] == "/home/user/project"

    async def test_create_with_anchor_time(self, repo: TaskRepository) -> None:
        task_id = await repo.create(
            name="anchored",
            prompt="prompt",
            interval_seconds=86400,
            channel_id=1,
            anchor_hour=18,
            anchor_minute=30,
        )
        task = await repo.get(task_id)
        assert task is not None
        assert task["anchor_hour"] == 18
        assert task["anchor_minute"] == 30

    async def test_create_without_anchor_has_none(self, repo: TaskRepository) -> None:
        task_id = await repo.create(
            name="no-anchor",
            prompt="prompt",
            interval_seconds=3600,
            channel_id=1,
        )
        task = await repo.get(task_id)
        assert task is not None
        assert task["anchor_hour"] is None
        assert task["anchor_minute"] is None

    async def test_create_with_anchor_sets_next_run_to_anchor_time(
        self, repo: TaskRepository
    ) -> None:
        """When anchor is set and run_immediately=False, next_run_at should be
        the next occurrence of anchor time."""
        from datetime import datetime

        task_id = await repo.create(
            name="anchor-schedule",
            prompt="prompt",
            interval_seconds=86400,
            channel_id=1,
            anchor_hour=18,
            anchor_minute=0,
            run_immediately=False,
        )
        task = await repo.get(task_id)
        assert task is not None
        next_dt = datetime.fromtimestamp(task["next_run_at"])
        assert next_dt.hour == 18
        assert next_dt.minute == 0

    async def test_duplicate_name_raises(self, repo: TaskRepository) -> None:
        import aiosqlite

        await repo.create(name="dup", prompt="p", interval_seconds=60, channel_id=1)
        with pytest.raises(aiosqlite.IntegrityError):
            await repo.create(name="dup", prompt="p2", interval_seconds=60, channel_id=2)


class TestTaskRepoGetAll:
    async def test_get_all_empty(self, repo: TaskRepository) -> None:
        tasks = await repo.get_all()
        assert tasks == []

    async def test_get_all_returns_all(self, repo: TaskRepository) -> None:
        await repo.create(name="a", prompt="p", interval_seconds=60, channel_id=1)
        await repo.create(name="b", prompt="p", interval_seconds=120, channel_id=2)
        tasks = await repo.get_all()
        assert len(tasks) == 2
        names = {t["name"] for t in tasks}
        assert names == {"a", "b"}

    async def test_get_all_includes_disabled(self, repo: TaskRepository) -> None:
        task_id = await repo.create(name="x", prompt="p", interval_seconds=60, channel_id=1)
        await repo.set_enabled(task_id, enabled=False)
        tasks = await repo.get_all()
        assert len(tasks) == 1
        assert tasks[0]["enabled"] is False


class TestTaskRepoDue:
    async def test_get_due_empty_when_all_future(self, repo: TaskRepository) -> None:
        await repo.create(name="future", prompt="p", interval_seconds=3600, channel_id=1)
        # Push next_run_at far into the future
        await repo._db_execute(
            "UPDATE scheduled_tasks SET next_run_at = ? WHERE name = 'future'",
            (time.time() + 9999,),
        )
        due = await repo.get_due()
        assert due == []

    async def test_get_due_returns_overdue_tasks(self, repo: TaskRepository) -> None:
        task_id = await repo.create(name="overdue", prompt="p", interval_seconds=60, channel_id=1)
        # Set next_run_at to the past
        await repo._db_execute(
            "UPDATE scheduled_tasks SET next_run_at = ? WHERE id = ?",
            (time.time() - 100, task_id),
        )
        due = await repo.get_due()
        assert len(due) == 1
        assert due[0]["name"] == "overdue"

    async def test_get_due_excludes_disabled(self, repo: TaskRepository) -> None:
        task_id = await repo.create(name="disabled", prompt="p", interval_seconds=60, channel_id=1)
        await repo._db_execute(
            "UPDATE scheduled_tasks SET next_run_at = ? WHERE id = ?",
            (time.time() - 100, task_id),
        )
        await repo.set_enabled(task_id, enabled=False)
        due = await repo.get_due()
        assert due == []


class TestTaskRepoUpdateNextRun:
    async def test_update_next_run_advances_time(self, repo: TaskRepository) -> None:
        before = time.time()
        task_id = await repo.create(name="t", prompt="p", interval_seconds=3600, channel_id=1)
        await repo.update_next_run(task_id, interval_seconds=3600)
        task = await repo.get(task_id)
        assert task is not None
        assert task["next_run_at"] >= before + 3600 - 1  # allow 1s skew
        assert task["last_run_at"] is not None

    async def test_update_next_run_with_anchor_snaps_to_wall_clock(
        self, repo: TaskRepository
    ) -> None:
        """When anchor_hour/anchor_minute are set, next_run_at should snap to
        the next occurrence of that wall-clock time instead of now + interval."""
        task_id = await repo.create(
            name="anchored",
            prompt="p",
            interval_seconds=86400,  # daily
            channel_id=1,
            anchor_hour=18,
            anchor_minute=0,
        )
        await repo.update_next_run(task_id, interval_seconds=86400)
        task = await repo.get(task_id)
        assert task is not None

        from datetime import datetime

        next_dt = datetime.fromtimestamp(task["next_run_at"])
        assert next_dt.hour == 18
        assert next_dt.minute == 0
        assert next_dt.second == 0
        # Must be in the future
        assert task["next_run_at"] > time.time()

    async def test_update_next_run_without_anchor_uses_relative(self, repo: TaskRepository) -> None:
        """Without anchor, update_next_run still uses now + interval (backward compat)."""
        before = time.time()
        task_id = await repo.create(
            name="no-anchor", prompt="p", interval_seconds=600, channel_id=1
        )
        await repo.update_next_run(task_id, interval_seconds=600)
        task = await repo.get(task_id)
        assert task is not None
        assert task["next_run_at"] >= before + 600 - 1
        assert task["anchor_hour"] is None
        assert task["anchor_minute"] is None


class TestTaskRepoDelete:
    async def test_delete_existing(self, repo: TaskRepository) -> None:
        task_id = await repo.create(name="del", prompt="p", interval_seconds=60, channel_id=1)
        deleted = await repo.delete(task_id)
        assert deleted is True
        assert await repo.get(task_id) is None

    async def test_delete_nonexistent_returns_false(self, repo: TaskRepository) -> None:
        deleted = await repo.delete(99999)
        assert deleted is False


class TestTaskRepoSetEnabled:
    async def test_disable_task(self, repo: TaskRepository) -> None:
        task_id = await repo.create(name="e", prompt="p", interval_seconds=60, channel_id=1)
        result = await repo.set_enabled(task_id, enabled=False)
        assert result is True
        task = await repo.get(task_id)
        assert task is not None
        assert task["enabled"] is False

    async def test_enable_task(self, repo: TaskRepository) -> None:
        task_id = await repo.create(name="e2", prompt="p", interval_seconds=60, channel_id=1)
        await repo.set_enabled(task_id, enabled=False)
        result = await repo.set_enabled(task_id, enabled=True)
        assert result is True
        task = await repo.get(task_id)
        assert task is not None
        assert task["enabled"] is True

    async def test_enable_nonexistent_returns_false(self, repo: TaskRepository) -> None:
        result = await repo.set_enabled(99999, enabled=True)
        assert result is False


class TestTaskRepoUpdate:
    async def test_update_prompt(self, repo: TaskRepository) -> None:
        task_id = await repo.create(name="u", prompt="old", interval_seconds=60, channel_id=1)
        result = await repo.update(task_id, prompt="new prompt")
        assert result is True
        task = await repo.get(task_id)
        assert task is not None
        assert task["prompt"] == "new prompt"

    async def test_update_interval(self, repo: TaskRepository) -> None:
        task_id = await repo.create(name="u2", prompt="p", interval_seconds=60, channel_id=1)
        await repo.update(task_id, interval_seconds=7200)
        task = await repo.get(task_id)
        assert task is not None
        assert task["interval_seconds"] == 7200

    async def test_update_anchor_time(self, repo: TaskRepository) -> None:
        task_id = await repo.create(
            name="u-anchor", prompt="p", interval_seconds=86400, channel_id=1
        )
        result = await repo.update(task_id, anchor_hour=9, anchor_minute=30)
        assert result is True
        task = await repo.get(task_id)
        assert task is not None
        assert task["anchor_hour"] == 9
        assert task["anchor_minute"] == 30

    async def test_update_clear_anchor(self, repo: TaskRepository) -> None:
        task_id = await repo.create(
            name="u-clear",
            prompt="p",
            interval_seconds=86400,
            channel_id=1,
            anchor_hour=18,
            anchor_minute=0,
        )
        # Clear anchor by setting to -1 (sentinel for None)
        result = await repo.update(task_id, anchor_hour=-1)
        assert result is True
        task = await repo.get(task_id)
        assert task is not None
        assert task["anchor_hour"] is None
        assert task["anchor_minute"] is None

    async def test_update_nonexistent_returns_false(self, repo: TaskRepository) -> None:
        result = await repo.update(99999, prompt="x")
        assert result is False


class TestTaskRepoThreadId:
    """Tests for thread_id column — follow-up in existing threads."""

    async def test_create_without_thread_id_defaults_to_none(self, repo: TaskRepository) -> None:
        task_id = await repo.create(name="no-thread", prompt="p", interval_seconds=60, channel_id=1)
        task = await repo.get(task_id)
        assert task is not None
        assert task["thread_id"] is None

    async def test_create_with_thread_id(self, repo: TaskRepository) -> None:
        task_id = await repo.create(
            name="follow-up",
            prompt="Check results",
            interval_seconds=86400,
            channel_id=1,
            thread_id=1234567890,
        )
        task = await repo.get(task_id)
        assert task is not None
        assert task["thread_id"] == 1234567890

    async def test_update_thread_id(self, repo: TaskRepository) -> None:
        task_id = await repo.create(name="t", prompt="p", interval_seconds=60, channel_id=1)
        result = await repo.update(task_id, thread_id=9876543210)
        assert result is True
        task = await repo.get(task_id)
        assert task is not None
        assert task["thread_id"] == 9876543210

    async def test_clear_thread_id(self, repo: TaskRepository) -> None:
        task_id = await repo.create(
            name="t", prompt="p", interval_seconds=60, channel_id=1, thread_id=111
        )
        result = await repo.update(task_id, thread_id=-1)
        assert result is True
        task = await repo.get(task_id)
        assert task is not None
        assert task["thread_id"] is None


class TestTaskRepoOneShot:
    """Tests for one_shot column — auto-disable after execution."""

    async def test_create_without_one_shot_defaults_to_false(self, repo: TaskRepository) -> None:
        task_id = await repo.create(name="recurring", prompt="p", interval_seconds=60, channel_id=1)
        task = await repo.get(task_id)
        assert task is not None
        assert task["one_shot"] is False

    async def test_create_with_one_shot(self, repo: TaskRepository) -> None:
        task_id = await repo.create(
            name="once",
            prompt="Check once",
            interval_seconds=86400,
            channel_id=1,
            one_shot=True,
        )
        task = await repo.get(task_id)
        assert task is not None
        assert task["one_shot"] is True


class TestTaskRepoBackendPin:
    """A task may pin the backend (and model) it runs on, or pin neither."""

    async def test_create_without_pin_leaves_both_null(self, repo: TaskRepository) -> None:
        """The existing behaviour — follow /backend at run time — is the default."""
        task_id = await repo.create(name="unpinned", prompt="p", interval_seconds=60, channel_id=1)
        task = await repo.get(task_id)
        assert task is not None
        assert task["backend"] is None
        assert task["model"] is None

    async def test_create_persists_backend_and_model(self, repo: TaskRepository) -> None:
        task_id = await repo.create(
            name="pinned",
            prompt="p",
            interval_seconds=60,
            channel_id=1,
            backend="codex",
            model="gpt-6-astra",
        )
        task = await repo.get(task_id)
        assert task is not None
        assert task["backend"] == "codex"
        assert task["model"] == "gpt-6-astra"

    async def test_get_due_carries_the_pin(self, repo: TaskRepository) -> None:
        """The master loop reads tasks through get_due(), not get()."""
        await repo.create(
            name="due-pinned",
            prompt="p",
            interval_seconds=60,
            channel_id=1,
            backend="pi",
            model="anthropic/claude-opus-5",
        )
        due = await repo.get_due(now=time.time() + 1)
        assert len(due) == 1
        assert due[0]["backend"] == "pi"
        assert due[0]["model"] == "anthropic/claude-opus-5"

    async def test_update_sets_the_pin(self, repo: TaskRepository) -> None:
        task_id = await repo.create(name="to-pin", prompt="p", interval_seconds=60, channel_id=1)
        assert await repo.update(task_id, backend="local", model="qwen3.5:35b") is True
        task = await repo.get(task_id)
        assert task is not None
        assert task["backend"] == "local"
        assert task["model"] == "qwen3.5:35b"

    async def test_update_clears_the_pin_with_none(self, repo: TaskRepository) -> None:
        """None means "clear", which is why update() needs a separate sentinel."""
        task_id = await repo.create(
            name="to-unpin",
            prompt="p",
            interval_seconds=60,
            channel_id=1,
            backend="codex",
            model="gpt-6-astra",
        )
        assert await repo.update(task_id, backend=None, model=None) is True
        task = await repo.get(task_id)
        assert task is not None
        assert task["backend"] is None
        assert task["model"] is None

    async def test_update_without_mentioning_the_pin_leaves_it_alone(
        self, repo: TaskRepository
    ) -> None:
        """Omitting the field must not clear it — the sentinel's whole purpose."""
        task_id = await repo.create(
            name="keep-pin",
            prompt="p",
            interval_seconds=60,
            channel_id=1,
            backend="codex",
            model="gpt-6-astra",
        )
        assert await repo.update(task_id, prompt="changed") is True
        task = await repo.get(task_id)
        assert task is not None
        assert task["prompt"] == "changed"
        assert task["backend"] == "codex"
        assert task["model"] == "gpt-6-astra"

    async def test_migration_adds_columns_to_a_pre_existing_db(self, tmp_path) -> None:
        """A database written before this change must gain the columns, not fail."""
        import aiosqlite

        db_path = str(tmp_path / "legacy.db")
        async with aiosqlite.connect(db_path) as db:
            await db.execute(
                """CREATE TABLE scheduled_tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    prompt TEXT NOT NULL,
                    interval_seconds INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    working_dir TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    next_run_at REAL NOT NULL,
                    last_run_at REAL,
                    created_at REAL NOT NULL
                )"""
            )
            await db.execute(
                "INSERT INTO scheduled_tasks (name, prompt, interval_seconds, channel_id,"
                " next_run_at, created_at) VALUES ('old', 'p', 60, 1, 0, 0)"
            )
            await db.commit()

        repo = TaskRepository(db_path)
        await repo.init_db()
        tasks = await repo.get_all()
        assert len(tasks) == 1
        assert tasks[0]["backend"] is None
        assert tasks[0]["model"] is None
