"""/backend, /model, /effort, /engine-status and /ollama pull|rm|use honour allowed_user_ids."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_discord.cogs.backend_command import BackendCommandCog
from claude_discord.cogs.ollama_command import OllamaCommandCog

REFUSAL = "You don't have permission to use this command."


def _interaction(user_id: int) -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = user_id
    interaction.channel = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _settings() -> MagicMock:
    settings = MagicMock()
    for name in (
        "set_backend",
        "set_model",
        "set_effort",
        "clear_effort",
        "set_codex_status_mode",
        "current_backend",
        "current_model",
        "current_effort",
        "codex_status_mode",
        "explicit_model",
    ):
        setattr(settings, name, AsyncMock(return_value="claude"))
    return settings


def _backend_cog(settings: MagicMock, allowed: set[int] | None) -> BackendCommandCog:
    return BackendCommandCog(
        MagicMock(),
        settings=settings,
        factory=MagicMock(),
        chat_cog=MagicMock(),
        allowed_user_ids=allowed,
    )


BACKEND_CALLS = [
    ("backend_command", {"name": "codex", "scope": "global"}),
    ("model_show_command", {}),
    ("model_set_command", {"name": "opus", "scope": "global"}),
    ("model_install_command", {"name": "x", "scope": "global"}),
    ("effort_command", {"level": "max", "scope": "global"}),
    ("engine_status_command", {"mode": "on", "scope": "global"}),
]


class TestBackendCommands:
    @pytest.mark.parametrize(("method", "kwargs"), BACKEND_CALLS)
    async def test_unauthorized_user_is_refused_and_nothing_changes(
        self, method: str, kwargs: dict[str, object]
    ) -> None:
        settings = _settings()
        cog = _backend_cog(settings, {1})
        interaction = _interaction(2)
        await getattr(cog, method).callback(cog, interaction, **kwargs)
        interaction.response.send_message.assert_awaited_once_with(REFUSAL, ephemeral=True)
        for name in ("set_backend", "set_model", "set_effort", "set_codex_status_mode"):
            getattr(settings, name).assert_not_awaited()

    async def test_authorized_user_reaches_the_command(self) -> None:
        settings = _settings()
        cog = _backend_cog(settings, {1})
        await cog.backend_command.callback(cog, _interaction(1), name="codex", scope="global")
        settings.set_backend.assert_awaited_once()

    async def test_no_allowlist_means_everyone(self) -> None:
        settings = _settings()
        cog = _backend_cog(settings, None)
        await cog.backend_command.callback(cog, _interaction(99), name="codex", scope="global")
        settings.set_backend.assert_awaited_once()


class TestOllamaCommands:
    @pytest.mark.parametrize(
        ("method", "kwargs"),
        [("pull", {"model": "qwen3:8b"}), ("rm", {"model": "qwen3:8b"}), ("use", {"model": "x"})],
    )
    async def test_unauthorized_user_is_refused(
        self, method: str, kwargs: dict[str, object]
    ) -> None:
        settings = _settings()
        cog = OllamaCommandCog(MagicMock(), settings=settings, allowed_user_ids={1})
        interaction = _interaction(2)
        await getattr(cog, method).callback(cog, interaction, **kwargs)
        interaction.response.send_message.assert_awaited_once_with(REFUSAL, ephemeral=True)
        interaction.response.defer.assert_not_awaited()
        settings.set_model.assert_not_awaited()
