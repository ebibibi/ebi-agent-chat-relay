"""Tests for BackendCommandCog.effort_command (/effort).

Covers per-backend persistence, backend-appropriate validation (Claude's four
levels vs Codex's wider set), and thread vs global scope.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import aiosqlite
import discord

from claude_discord.backend_factory import BackendFactory
from claude_discord.backend_settings import BackendSettings
from claude_discord.cogs.backend_command import BackendCommandCog
from claude_discord.database.settings_repo import SettingsRepository


async def _new_settings_repo() -> SettingsRepository:
    tmp = Path(tempfile.mkdtemp()) / "settings.db"
    async with aiosqlite.connect(str(tmp)) as db:
        await db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        await db.commit()
    return SettingsRepository(str(tmp))


def _make_cog(settings: BackendSettings) -> BackendCommandCog:
    factory = BackendFactory(
        claude_command="claude",
        codex_command="codex",
        permission_mode="acceptEdits",
        working_dir=None,
        timeout_seconds=300,
        dangerously_skip_permissions=False,
        allowed_tools=None,
        append_system_prompt=None,
        effort=None,
    )
    chat_cog = MagicMock()
    chat_cog.runner = MagicMock()
    return BackendCommandCog(MagicMock(), settings=settings, factory=factory, chat_cog=chat_cog)


def _thread_interaction(thread_id: int) -> MagicMock:
    interaction = MagicMock()
    thread = MagicMock(spec=discord.Thread)
    thread.id = thread_id
    interaction.channel = thread
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    return interaction


def _channel_interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.channel = MagicMock()  # not a discord.Thread
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    return interaction


async def _settings() -> BackendSettings:
    repo = await _new_settings_repo()
    return BackendSettings(
        repo,
        env_backend="claude",
        env_model_for_claude="sonnet",
        env_model_for_codex="",
    )


class TestEffortCommand:
    async def test_set_codex_effort_global(self) -> None:
        settings = await _settings()
        await settings.set_backend("codex")
        cog = _make_cog(settings)
        interaction = _channel_interaction()

        await cog.effort_command.callback(cog, interaction, level="xhigh", scope="global")

        assert await settings.current_effort("codex") == "xhigh"
        interaction.response.send_message.assert_awaited_once()

    async def test_codex_rejects_unknown_level(self) -> None:
        settings = await _settings()
        await settings.set_backend("codex")
        cog = _make_cog(settings)
        interaction = _channel_interaction()

        await cog.effort_command.callback(cog, interaction, level="extreme", scope="global")

        assert await settings.current_effort("codex") is None
        msg = interaction.response.send_message.await_args.args[0]
        assert "Unknown effort" in msg

    async def test_codex_accepts_the_top_levels_of_the_current_generation(self) -> None:
        """GPT-5.6 and GPT-6 added `max`/`ultra`; rejecting them hides the model's ceiling."""
        settings = await _settings()
        await settings.set_backend("codex")
        cog = _make_cog(settings)

        await cog.effort_command.callback(
            cog, _channel_interaction(), level="ultra", scope="global"
        )

        assert await settings.current_effort("codex") == "ultra"

    async def test_claude_accepts_max(self) -> None:
        settings = await _settings()  # default backend claude
        cog = _make_cog(settings)
        interaction = _channel_interaction()

        await cog.effort_command.callback(cog, interaction, level="max", scope="global")

        assert await settings.current_effort("claude") == "max"

    async def test_thread_scope_persists_per_thread(self) -> None:
        settings = await _settings()
        await settings.set_backend("codex", thread_id=77)
        cog = _make_cog(settings)
        interaction = _thread_interaction(77)

        await cog.effort_command.callback(cog, interaction, level="low", scope="thread")

        assert await settings.current_effort("codex", thread_id=77) == "low"
        assert await settings.current_effort("codex") is None  # global untouched

    async def test_level_is_normalized_lowercase(self) -> None:
        settings = await _settings()
        await settings.set_backend("codex")
        cog = _make_cog(settings)
        interaction = _channel_interaction()

        await cog.effort_command.callback(cog, interaction, level="HIGH", scope="global")

        assert await settings.current_effort("codex") == "high"

    async def test_show_current_when_level_omitted(self) -> None:
        settings = await _settings()
        await settings.set_backend("codex")
        await settings.set_effort("codex", "high")
        cog = _make_cog(settings)
        interaction = _channel_interaction()

        await cog.effort_command.callback(cog, interaction, level=None, scope=None)

        msg = interaction.response.send_message.await_args.args[0]
        assert "high" in msg
