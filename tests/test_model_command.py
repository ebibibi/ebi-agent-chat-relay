"""Tests for backend-aware autocomplete on /model and /effort.

The old Claude-only ``/model-set`` / ``/effort-set`` commands (which hardcoded
Claude model choices) were removed in favour of the backend-aware ``/model`` and
``/effort`` commands. Those take free-text values; this module verifies the
autocomplete surfaces the *active backend's* suggestions so Codex users see
Codex models/efforts, not Claude ones.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import aiosqlite
import discord
import pytest

from claude_discord.backend_factory import BackendFactory
from claude_discord.backend_settings import BackendSettings
from claude_discord.cogs.backend_command import (
    SUGGESTED_MODELS,
    VALID_EFFORTS,
    BackendCommandCog,
)
from claude_discord.database.settings_repo import SettingsRepository


@pytest.fixture(autouse=True)
def _offline_model_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the suite off the network: discovery degrades to the static list.

    Live discovery has its own tests in ``test_model_catalog.py``; here we only
    care that the autocomplete surfaces whatever it is handed.
    """

    async def _fallback_only(*, fallback: list[tuple[str, str]]) -> list[tuple[str, str]]:
        return fallback

    monkeypatch.setattr("claude_discord.cogs.backend_command.claude_model_choices", _fallback_only)
    # Codex discovery reads the host's own ~/.codex catalog, so leaving it on
    # would make these assertions depend on whoever ran the suite.
    monkeypatch.setenv("CCDB_MODEL_DISCOVERY", "0")


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
    return interaction


def _channel_interaction() -> MagicMock:
    interaction = MagicMock()
    interaction.channel = MagicMock()  # not a discord.Thread
    return interaction


async def _settings() -> BackendSettings:
    repo = await _new_settings_repo()
    return BackendSettings(
        repo,
        env_backend="claude",
        env_model_for_claude="sonnet",
        env_model_for_codex="",
    )


class TestModelAutocomplete:
    async def test_claude_backend_suggests_claude_models(self) -> None:
        settings = await _settings()  # default backend claude
        cog = _make_cog(settings)
        interaction = _channel_interaction()

        choices = await cog._model_name_autocomplete(interaction, "")

        values = {c.value for c in choices}
        assert {"haiku", "sonnet", "opus", "fable"} <= values
        # No Codex models leaked in.
        assert not any(v.startswith("gpt-") for v in values)

    async def test_claude_backend_surfaces_discovered_models(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A model that shipped after this release must appear without a code change."""

        async def _discovered(*, fallback: list[tuple[str, str]]) -> list[tuple[str, str]]:
            return [("opus", "Claude Opus 5 (alias)"), ("claude-opus-5", "Claude Opus 5")]

        monkeypatch.setattr("claude_discord.cogs.backend_command.claude_model_choices", _discovered)
        settings = await _settings()
        cog = _make_cog(settings)

        choices = await cog._model_name_autocomplete(_channel_interaction(), "")

        assert [c.value for c in choices] == ["opus", "claude-opus-5"]

    async def test_codex_backend_ignores_claude_discovery(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Codex must never be handed Claude ids, discovered or otherwise."""

        async def _discovered(*, fallback: list[tuple[str, str]]) -> list[tuple[str, str]]:
            raise AssertionError("Claude discovery must not run for the Codex backend")

        monkeypatch.setattr("claude_discord.cogs.backend_command.claude_model_choices", _discovered)
        settings = await _settings()
        await settings.set_backend("codex")
        cog = _make_cog(settings)

        choices = await cog._model_name_autocomplete(_channel_interaction(), "")

        assert {c.value for c in choices} == {m for m, _ in SUGGESTED_MODELS["codex"]}

    async def test_codex_backend_suggests_codex_models(self) -> None:
        settings = await _settings()
        await settings.set_backend("codex")
        cog = _make_cog(settings)
        interaction = _channel_interaction()

        choices = await cog._model_name_autocomplete(interaction, "")

        values = {c.value for c in choices}
        expected = {m for m, _ in SUGGESTED_MODELS["codex"]}
        assert values == expected
        # No Claude models leaked in.
        assert "sonnet" not in values

    async def test_codex_backend_suggests_latest_codex_model_first(self) -> None:
        settings = await _settings()
        await settings.set_backend("codex")
        cog = _make_cog(settings)
        interaction = _channel_interaction()

        choices = await cog._model_name_autocomplete(interaction, "")

        assert choices[0].value == "gpt-6-astra"

    async def test_codex_backend_surfaces_discovered_models(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A Codex generation the CLI already knows must not need a ccdb release."""
        monkeypatch.delenv("CCDB_MODEL_DISCOVERY", raising=False)
        monkeypatch.setenv("CODEX_HOME", str(tmp_path))
        (tmp_path / "models_cache.json").write_text(
            json.dumps(
                {
                    "models": [
                        {
                            "slug": "gpt-7",
                            "display_name": "GPT-7",
                            "description": "Newer than this release",
                            "visibility": "list",
                            "priority": 1,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        settings = await _settings()
        await settings.set_backend("codex")
        cog = _make_cog(settings)

        choices = await cog._model_name_autocomplete(_channel_interaction(), "")

        assert [c.value for c in choices] == ["gpt-7"]

    async def test_filters_by_current_substring(self) -> None:
        settings = await _settings()
        cog = _make_cog(settings)
        interaction = _channel_interaction()

        choices = await cog._model_name_autocomplete(interaction, "op")

        assert [c.value for c in choices] == ["opus"]

    async def test_thread_backend_drives_suggestions(self) -> None:
        settings = await _settings()
        await settings.set_backend("codex", thread_id=42)
        cog = _make_cog(settings)
        interaction = _thread_interaction(42)

        choices = await cog._model_name_autocomplete(interaction, "")

        values = {c.value for c in choices}
        assert values == {m for m, _ in SUGGESTED_MODELS["codex"]}

    async def test_caps_at_25_choices(self) -> None:
        settings = await _settings()
        cog = _make_cog(settings)
        interaction = _channel_interaction()

        choices = await cog._model_name_autocomplete(interaction, "")

        assert len(choices) <= 25


class TestEffortAutocomplete:
    async def test_claude_backend_lists_claude_levels(self) -> None:
        settings = await _settings()
        cog = _make_cog(settings)
        interaction = _channel_interaction()

        choices = await cog._effort_level_autocomplete(interaction, "")

        assert {c.value for c in choices} == set(VALID_EFFORTS["claude"])

    async def test_codex_backend_lists_codex_levels(self) -> None:
        settings = await _settings()
        await settings.set_backend("codex")
        cog = _make_cog(settings)
        interaction = _channel_interaction()

        choices = await cog._effort_level_autocomplete(interaction, "")

        assert {c.value for c in choices} == set(VALID_EFFORTS["codex"])

    async def test_effort_filters_by_substring(self) -> None:
        settings = await _settings()
        await settings.set_backend("codex")
        cog = _make_cog(settings)
        interaction = _channel_interaction()

        choices = await cog._effort_level_autocomplete(interaction, "min")

        assert [c.value for c in choices] == ["minimal"]
