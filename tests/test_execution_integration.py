"""Execution environments wired into runners, thread settings, /sandbox, and status."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import aiosqlite
import pytest

from claude_code_core.codex_runner import CodexRunner
from claude_code_core.local_backend import LocalCodexRunner
from claude_code_core.pi_runner import PiRunner
from claude_code_core.runner import ClaudeRunner
from claude_code_core.types import MessageType, StreamEvent
from claude_discord.cogs.event_processor import _completion_fields
from claude_discord.cogs.sandbox_command import SandboxCommandCog
from claude_discord.database.settings_repo import SettingsRepository
from claude_discord.execution_settings import (
    ExecutionModeNotAllowedError,
    ExecutionSettings,
    apply_thread_execution_mode,
)


async def _repo() -> SettingsRepository:
    path = Path(tempfile.mkdtemp()) / "settings.db"
    async with aiosqlite.connect(str(path)) as db:
        await db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        await db.commit()
    return SettingsRepository(str(path))


@pytest.fixture
def allow_bwrap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CCDB_EXECUTION_ALLOWED_MODES", "host,bwrap")


# ---------------------------------------------------------------- runners


class TestRunnersRefuseWithoutSpawning:
    @pytest.mark.parametrize(
        "runner",
        [
            ClaudeRunner(command="claude", working_dir="/tmp"),
            CodexRunner(command="codex", working_dir="/tmp"),
        ],
    )
    async def test_disallowed_mode_yields_one_error_and_no_process(self, runner) -> None:
        runner.execution_mode = "bwrap"  # not on the (default) allowlist
        with patch("asyncio.create_subprocess_exec", new=AsyncMock()) as spawn:
            events = [event async for event in runner.run("hi")]
        spawn.assert_not_awaited()
        assert len(events) == 1
        assert events[0].is_complete and "not allowed" in (events[0].error or "")

    async def test_pi_native_refuses(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CCDB_PI_ALLOW_UNSANDBOXED", "1")
        monkeypatch.setenv("CCDB_EXECUTION_MODE", "native")
        runner = PiRunner(command="pi", working_dir="/tmp")
        with patch("asyncio.create_subprocess_exec", new=AsyncMock()) as spawn:
            events = [event async for event in runner.run("hi")]
        spawn.assert_not_awaited()
        assert "no native sandbox" in (events[0].error or "")

    async def test_misconfigured_mode_refuses_rather_than_running_on_host(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CCDB_EXECUTION_MODE", "bwarp")
        runner = ClaudeRunner(command="claude", working_dir="/tmp")
        with patch("asyncio.create_subprocess_exec", new=AsyncMock()) as spawn:
            events = [event async for event in runner.run("hi")]
        spawn.assert_not_awaited()
        assert "misconfigured" in (events[0].error or "")

    async def test_native_codex_spawns_with_sandbox_and_records_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CCDB_EXECUTION_MODE", "native")
        runner = CodexRunner(command="codex", working_dir="/tmp", dangerously_skip_permissions=True)
        process = MagicMock()
        process.stdin = None
        process.stdout.readline = AsyncMock(return_value=b"")
        process.returncode = 0
        process.wait = AsyncMock(return_value=0)
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)) as spawn:
            _ = [event async for event in runner.run("hi")]
        argv = spawn.await_args.args
        assert argv[:4] == ("codex", "exec", "--sandbox", "workspace-write")
        assert "--dangerously-bypass-approvals-and-sandbox" not in argv
        assert runner.execution_served == "native"

    async def test_host_spawn_is_unchanged(self) -> None:
        runner = ClaudeRunner(command="claude", working_dir="/tmp")
        expected = runner._build_args("hi", None)
        process = MagicMock()
        process.stdin = None
        process.stdout.readline = AsyncMock(return_value=b"")
        process.returncode = 0
        process.wait = AsyncMock(return_value=0)
        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=process)) as spawn:
            _ = [event async for event in runner.run("hi")]
        assert list(spawn.await_args.args) == expected
        assert spawn.await_args.kwargs["cwd"] == "/tmp"
        assert runner.execution_served == "host"


class TestCloneCarriesMode:
    @pytest.mark.parametrize(
        "runner",
        [
            ClaudeRunner(command="claude"),
            CodexRunner(command="codex"),
            PiRunner(command="pi"),
            LocalCodexRunner(command="codex"),
        ],
    )
    def test_clone(self, runner) -> None:
        assert runner.clone().execution_mode is None
        runner.execution_mode = "bwrap"
        assert runner.clone().execution_mode == "bwrap"
        assert runner.clone(model="x").execution_mode == "bwrap"


# ---------------------------------------------------------------- settings


class TestExecutionSettings:
    async def test_refuses_mode_outside_allowlist(self) -> None:
        settings = ExecutionSettings(await _repo())
        with pytest.raises(ExecutionModeNotAllowedError, match="not enabled"):
            await settings.set_thread_mode(1, "bwrap")
        assert await settings.stored_mode(1) is None

    async def test_set_and_resolve(self, allow_bwrap: None) -> None:
        settings = ExecutionSettings(await _repo())
        await settings.set_thread_mode(1, "bwrap")
        assert await settings.effective_mode(1) == "bwrap"
        assert await settings.effective_mode(2) == "host"

    async def test_choice_removed_from_allowlist_falls_back_to_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CCDB_EXECUTION_ALLOWED_MODES", "bwrap")
        settings = ExecutionSettings(await _repo())
        await settings.set_thread_mode(1, "bwrap")
        monkeypatch.delenv("CCDB_EXECUTION_ALLOWED_MODES")
        assert await settings.thread_mode(1) is None
        assert await settings.effective_mode(1) == "host"

    async def test_apply_sets_runner_mode(self, allow_bwrap: None) -> None:
        repo = await _repo()
        await ExecutionSettings(repo).set_thread_mode(5, "bwrap")
        runner = ClaudeRunner()
        await apply_thread_execution_mode(runner, repo, 5)
        assert runner.execution_mode == "bwrap"
        other = ClaudeRunner()
        await apply_thread_execution_mode(other, repo, 6)
        assert other.execution_mode is None

    async def test_apply_ignores_non_async_stand_ins(self) -> None:
        runner = ClaudeRunner()
        await apply_thread_execution_mode(runner, MagicMock(), 5)
        assert runner.execution_mode is None


# ---------------------------------------------------------------- /sandbox


class TestSandboxCommand:
    async def _cog(self) -> SandboxCommandCog:
        return SandboxCommandCog(MagicMock(), settings_repo=await _repo())

    async def test_refusal_path(self) -> None:
        cog = await self._cog()
        reply, ephemeral = await cog.handle(10, "ssh")
        assert reply.startswith("🚫") and "not enabled" in reply
        assert ephemeral
        assert await cog._settings.stored_mode(10) is None

    async def test_choose_allowed_mode(self, allow_bwrap: None) -> None:
        cog = await self._cog()
        reply, ephemeral = await cog.handle(10, "bwrap")
        assert "`bwrap`" in reply and not ephemeral
        assert await cog._settings.effective_mode(10) == "bwrap"

    async def test_reset(self, allow_bwrap: None) -> None:
        cog = await self._cog()
        await cog.handle(10, "bwrap")
        await cog.handle(10, "default")
        assert await cog._settings.stored_mode(10) is None

    async def test_needs_a_thread(self, allow_bwrap: None) -> None:
        cog = await self._cog()
        reply, _ = await cog.handle(None, "bwrap")
        assert "inside a thread" in reply

    async def test_show(self, allow_bwrap: None) -> None:
        cog = await self._cog()
        reply, ephemeral = await cog.handle(10, None)
        assert "Deployment default" in reply and "`bwrap`" in reply and ephemeral

    async def test_misconfiguration_is_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CCDB_EXECUTION_MODE", "nope")
        cog = await self._cog()
        reply, _ = await cog.handle(10, None)
        assert "misconfigured" in reply


# ---------------------------------------------------------------- status


class TestCompletionFields:
    EVENT = StreamEvent(raw={}, message_type=MessageType.RESULT, is_complete=True)

    def test_host_only_deployment_is_unlabelled(self) -> None:
        runner = ClaudeRunner(model="sonnet")
        runner.execution_served = "host"
        assert [name for name, _ in _completion_fields(self.EVENT, runner)] == ["Backend"]

    def test_non_host_environment_is_shown(self) -> None:
        runner = ClaudeRunner(model="sonnet")
        runner.execution_served = "bwrap"
        assert ("Environment", "bwrap") in _completion_fields(self.EVENT, runner)

    def test_host_is_shown_when_there_is_a_choice(self, allow_bwrap: None) -> None:
        runner = ClaudeRunner(model="sonnet")
        runner.execution_served = "host"
        assert ("Environment", "host") in _completion_fields(self.EVENT, runner)


class TestSkillCommandHonoursSandbox:
    async def test_skill_in_thread_runs_in_thread_mode(self, allow_bwrap: None) -> None:
        import discord

        from claude_discord.cogs.skill_command import SkillCommandCog

        repo = await _repo()
        await ExecutionSettings(repo).set_thread_mode(77, "bwrap")
        cog = SkillCommandCog(
            MagicMock(),
            repo=MagicMock(get=AsyncMock(return_value=None)),
            runner=ClaudeRunner(command="claude"),
            claude_channel_id=1,
            settings_repo=repo,
        )
        cog._skills = [{"name": "demo", "description": ""}]
        cog._maybe_reload_skills = lambda: None  # type: ignore[method-assign]
        thread = MagicMock(spec=discord.Thread)
        thread.id = 77
        thread.parent_id = 1
        interaction = MagicMock()
        interaction.channel = thread
        interaction.user.id = 5
        interaction.response.defer = AsyncMock()
        interaction.followup.send = AsyncMock()
        captured: dict[str, object] = {}

        async def fake_run(config) -> None:
            captured["runner"] = config.runner

        with patch("claude_discord.cogs.skill_command.run_claude_with_config", fake_run):
            await cog.run_skill.callback(cog, interaction, name="demo", args=None)
        assert captured["runner"].execution_mode == "bwrap"  # type: ignore[attr-defined]
