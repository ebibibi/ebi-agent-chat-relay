"""Turns cut off by SIGKILL/OOM are resumed on startup — but not forever."""

from __future__ import annotations

import asyncio
import os
import tempfile
from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import discord
import pytest

from claude_discord.cogs.claude_chat import ClaudeChatCog
from claude_discord.crash_recovery import (
    CRASH_RESUME_REASON,
    InflightTracker,
    crash_notice_text,
    crash_resume_prompt,
    plan_crash_recovery,
)
from claude_discord.database.inflight_repo import InflightTurnRepository
from claude_discord.database.models import init_db
from claude_discord.database.resume_repo import PendingResumeRepository

TTL = 300.0


@pytest.fixture
async def db_path() -> AsyncIterator[str]:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(path)
    yield path
    os.unlink(path)


def _sessions(session_id: str | None = None) -> MagicMock:
    sessions = MagicMock()
    record = SimpleNamespace(session_id=session_id) if session_id else None
    sessions.get = AsyncMock(return_value=record)
    return sessions


async def _age_heartbeats(path: str, minutes: int) -> None:
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "UPDATE inflight_turns SET heartbeat_at = datetime('now', 'localtime', ?)",
            (f"-{minutes} minutes",),
        )
        await db.commit()


class TestInflightTurnRepository:
    async def test_end_removes_only_this_process_row(self, db_path: str) -> None:
        repo = InflightTurnRepository(db_path)
        await repo.begin(1, boot_id="a", session_id="s", prompt="p")
        await repo.end(1, boot_id="other")
        assert [t.thread_id for t in await repo.take_orphans("other")] == [1]

    async def test_take_orphans_skips_live_process_and_is_single_fire(self, db_path: str) -> None:
        repo = InflightTurnRepository(db_path)
        await repo.begin(1, boot_id="dead", session_id="s1", prompt="hello")
        await repo.begin(2, boot_id="live", session_id="s2", prompt="mine")

        orphans = await repo.take_orphans("live")

        assert [(t.thread_id, t.session_id, t.prompt) for t in orphans] == [(1, "s1", "hello")]
        assert orphans[0].silent_seconds < 60
        assert await repo.take_orphans("live") == []
        # The live process's own turn is untouched.
        assert [t.thread_id for t in await repo.take_orphans("next")] == [2]

    async def test_heartbeat_refreshes_silence(self, db_path: str) -> None:
        repo = InflightTurnRepository(db_path)
        await repo.begin(1, boot_id="a", session_id=None, prompt=None)
        await _age_heartbeats(db_path, 30)
        await repo.heartbeat("a")
        [turn] = await repo.take_orphans("b")
        assert turn.silent_seconds < 60


class TestPlanCrashRecovery:
    async def test_recent_crash_becomes_pending_resume(self, db_path: str) -> None:
        inflight = InflightTurnRepository(db_path)
        resumes = PendingResumeRepository(db_path)
        await inflight.begin(10, boot_id="dead", session_id=None, prompt="build the thing")

        plan = await plan_crash_recovery(
            inflight=inflight,
            resumes=resumes,
            sessions=_sessions("sess-saved-later"),
            boot_id="new",
            ttl_seconds=TTL,
        )

        assert plan.resumed == {10: 1}
        assert plan.notices == []
        [pending] = await resumes.get_pending()
        assert pending.thread_id == 10
        assert pending.reason == CRASH_RESUME_REASON
        # The session id saved while the turn streamed wins over the empty one it started with.
        assert pending.session_id == "sess-saved-later"
        assert "build the thing" in (pending.resume_prompt or "")

    async def test_clean_shutdown_resume_wins(self, db_path: str) -> None:
        inflight = InflightTurnRepository(db_path)
        resumes = PendingResumeRepository(db_path)
        await inflight.begin(10, boot_id="dead", session_id="s", prompt="x")
        await resumes.mark(10, session_id="s", reason="bot_shutdown", resume_prompt="clean")

        plan = await plan_crash_recovery(
            inflight=inflight, resumes=resumes, sessions=_sessions(), boot_id="new", ttl_seconds=TTL
        )

        assert plan.resumed == {}
        assert plan.notices == []
        [pending] = await resumes.get_pending()
        assert pending.reason == "bot_shutdown"

    async def test_long_outage_is_reported_not_resumed(self, db_path: str) -> None:
        inflight = InflightTurnRepository(db_path)
        resumes = PendingResumeRepository(db_path)
        await inflight.begin(10, boot_id="dead", session_id="s", prompt="x")
        await _age_heartbeats(db_path, 30)

        plan = await plan_crash_recovery(
            inflight=inflight, resumes=resumes, sessions=_sessions(), boot_id="new", ttl_seconds=TTL
        )

        assert plan.resumed == {}
        assert [(n.thread_id, n.reason) for n in plan.notices] == [(10, "stale")]
        assert await resumes.get_pending() == []


class TestCrashLoopLimit:
    async def _crash_and_restart(self, db_path: str, previous: InflightTracker) -> InflightTracker:
        """Simulate a process dying mid-turn and the next one starting."""
        await previous.begin(10, session_id="s", prompt="the turn that kills the bot")
        tracker = InflightTracker(InflightTurnRepository(db_path))
        resumes = PendingResumeRepository(db_path)
        self.plan = await tracker.recover_once(
            resumes=resumes, sessions=_sessions(), ttl_seconds=TTL
        )
        # Startup consumes the pending row before resuming (on_ready deletes first).
        for row in await resumes.get_pending():
            await resumes.delete(row.id)
        return tracker

    async def test_resumes_twice_then_stops_and_tells_the_thread(self, db_path: str) -> None:
        tracker = InflightTracker(InflightTurnRepository(db_path))

        tracker = await self._crash_and_restart(db_path, tracker)
        assert self.plan.resumed == {10: 1}
        tracker = await self._crash_and_restart(db_path, tracker)
        assert self.plan.resumed == {10: 2}
        tracker = await self._crash_and_restart(db_path, tracker)

        assert self.plan.resumed == {}
        [notice] = self.plan.notices
        assert (notice.thread_id, notice.reason, notice.crash_count) == (10, "limit", 2)
        assert "2 times in a row" in crash_notice_text(notice)

    async def test_a_turn_that_finishes_resets_the_count(self, db_path: str) -> None:
        tracker = InflightTracker(InflightTurnRepository(db_path))
        tracker = await self._crash_and_restart(db_path, tracker)
        tracker = await self._crash_and_restart(db_path, tracker)
        assert self.plan.resumed == {10: 2}

        # The resumed turn completes; a later, unrelated crash starts from zero.
        await tracker.begin(10, session_id="s", prompt="resumed")
        await tracker.end(10)
        tracker = await self._crash_and_restart(db_path, tracker)
        assert self.plan.resumed == {10: 1}

    async def test_recovery_runs_once_per_process(self, db_path: str) -> None:
        dead = InflightTracker(InflightTurnRepository(db_path))
        await dead.begin(10, session_id="s", prompt="x")
        tracker = InflightTracker(InflightTurnRepository(db_path))
        resumes = PendingResumeRepository(db_path)

        first = await tracker.recover_once(resumes=resumes, sessions=_sessions(), ttl_seconds=TTL)
        await dead.begin(11, session_id="s", prompt="y")  # appears after startup
        second = await tracker.recover_once(resumes=resumes, sessions=_sessions(), ttl_seconds=TTL)

        assert first.resumed == {10: 1}
        assert second.resumed == {}


class TestTrackerNeverRaises:
    async def test_database_errors_are_swallowed(self) -> None:
        repo = MagicMock(spec=InflightTurnRepository)
        repo.begin = AsyncMock(side_effect=RuntimeError("disk full"))
        repo.end = AsyncMock(side_effect=RuntimeError("disk full"))
        repo.take_orphans = AsyncMock(side_effect=RuntimeError("disk full"))
        tracker = InflightTracker(repo)

        await tracker.begin(1, session_id=None, prompt=None)
        await tracker.end(1)
        plan = await tracker.recover_once(resumes=MagicMock(), sessions=MagicMock(), ttl_seconds=1)
        assert plan.resumed == {}


def test_resume_prompt_quotes_and_truncates_the_original_message() -> None:
    prompt = crash_resume_prompt("line one\nline two" + "x" * 2000, 1, 2)
    assert "> line one\n> line two" in prompt
    assert "resume 1 of at most 2" in prompt
    assert len(prompt) < 1800  # the seed message must fit in one Discord message


class TestCogStartup:
    async def test_crashed_turn_is_resumed_in_its_thread(self, db_path: str) -> None:
        await InflightTurnRepository(db_path).begin(
            555, boot_id="dead", session_id="sess-1", prompt="deploy it"
        )
        resumes = PendingResumeRepository(db_path)

        thread = MagicMock(spec=discord.Thread)
        thread.id = 555
        thread.parent_id = 1
        thread.send = AsyncMock(return_value=MagicMock())
        thread.parent = MagicMock(spec=discord.TextChannel)
        bot = MagicMock()
        bot.headless = False
        bot.console_sessions = None
        bot.get_channel.return_value = thread
        sessions = MagicMock()
        sessions.get = AsyncMock(return_value=None)

        cog = ClaudeChatCog(bot=bot, repo=sessions, runner=MagicMock(), resume_repo=resumes)
        assert cog._inflight is not None

        calls: list[dict] = []

        async def fake_run_claude(*args, **kwargs):
            calls.append({"prompt": args[2], **kwargs})

        with patch.object(cog, "_run_claude", side_effect=fake_run_claude):
            await cog.on_ready()
            await asyncio.sleep(0)
            await cog.on_ready()  # a Discord reconnect must not recover again
            await asyncio.sleep(0)

        assert len(calls) == 1
        assert calls[0]["session_id"] == "sess-1"
        assert "deploy it" in calls[0]["prompt"]
        assert await resumes.get_pending() == []
        for task in list(cog._background_tasks):
            task.cancel()

    async def test_stale_crash_posts_a_notice_instead(self, db_path: str) -> None:
        await InflightTurnRepository(db_path).begin(
            555, boot_id="dead", session_id="sess-1", prompt="deploy it"
        )
        await _age_heartbeats(db_path, 60)

        thread = MagicMock(spec=discord.Thread)
        thread.send = AsyncMock()
        bot = MagicMock()
        bot.headless = False
        bot.get_channel.return_value = thread
        cog = ClaudeChatCog(
            bot=bot,
            repo=MagicMock(),
            runner=MagicMock(),
            resume_repo=PendingResumeRepository(db_path),
        )

        with patch.object(cog, "_run_claude") as run:
            await cog.on_ready()
        run.assert_not_called()
        thread.send.assert_awaited_once()
        assert "stopped unexpectedly" in thread.send.await_args.args[0]
        for task in list(cog._background_tasks):
            task.cancel()


class TestRunRecordsTheTurn:
    async def test_row_exists_during_the_run_and_is_gone_after(
        self, db_path: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import claude_discord.cogs.claude_chat as chat_mod

        seen_during: list[list[int]] = []

        async def fake_run(config: object) -> None:
            orphans = await InflightTurnRepository(db_path).take_orphans("someone-else")
            seen_during.append([t.thread_id for t in orphans])
            # Put it back the way the cog wrote it so the run can clear it.
            await InflightTurnRepository(db_path).begin(
                77,
                boot_id=cog._inflight.boot_id,  # type: ignore[union-attr]
                session_id=None,
                prompt="do it",
            )

        class _Status:
            _stall_hard = 300

            async def set_thinking(self) -> None:
                return None

        class _StopView:
            def set_message(self, message: object) -> None:
                return None

            async def disable(self, message: object | None = None) -> None:
                return None

        monkeypatch.setattr(chat_mod, "run_claude_with_config", fake_run)
        monkeypatch.setattr(chat_mod, "StatusManager", lambda *a, **k: _Status())
        monkeypatch.setattr(chat_mod, "StopView", lambda *a, **k: _StopView())
        repo = MagicMock()
        repo.get = AsyncMock(return_value=None)
        cog = ClaudeChatCog(
            bot=MagicMock(),
            repo=repo,
            runner=MagicMock(),
            resume_repo=PendingResumeRepository(db_path),
        )
        cog._get_dashboard = lambda: None  # type: ignore[method-assign]

        async def build_runner(**kwargs: object) -> MagicMock:
            runner = MagicMock()
            runner.command = "claude"
            return runner

        cog._build_runner_for_thread = build_runner  # type: ignore[method-assign]
        cog._get_current_model = AsyncMock(return_value=None)  # type: ignore[method-assign]
        cog._get_allowed_tools = AsyncMock(return_value=None)  # type: ignore[method-assign]
        cog._get_current_effort = AsyncMock(return_value=None)  # type: ignore[method-assign]
        thread = MagicMock(spec=discord.Thread)
        thread.id = 77
        thread.send = AsyncMock()

        await cog._run_claude(MagicMock(), thread, "do it", None)

        assert seen_during == [[77]]
        assert await InflightTurnRepository(db_path).take_orphans("someone-else") == []
