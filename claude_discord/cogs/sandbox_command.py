"""``/sandbox`` — show or choose the execution environment for this thread.

Only modes on the operator's allowlist (``CCDB_EXECUTION_ALLOWED_MODES``) can
be chosen; the default and the allowlist themselves are deployment-scoped and
environment-only, so no Discord user can reach a mode the operator did not
enable. See docs/execution-environments.md.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands

from claude_code_core.execution import MODES

from ..execution_settings import ExecutionModeNotAllowedError, ExecutionSettings

if TYPE_CHECKING:
    from ..database.settings_repo import SettingsRepository

logger = logging.getLogger(__name__)

RESET = "default"

MODE_DESCRIPTIONS: dict[str, str] = {
    "host": "runs as the relay's user on this machine (no OS sandbox)",
    "native": "the agent's own sandbox (Claude Code sandbox / Codex workspace-write)",
    "bwrap": "bubblewrap: read-only host, writable working dir, no sudo",
    "container": "inside the operator's container image",
    "ssh": "on the operator's remote host",
}


def describe_modes(settings: ExecutionSettings) -> str:
    config = settings.config()
    lines = []
    for mode in config.allowed_modes:
        tag = " (default)" if mode == config.default_mode else ""
        lines.append(f"• `{mode}`{tag} — {MODE_DESCRIPTIONS[mode]}")
    return "\n".join(lines)


class SandboxCommandCog(commands.Cog):
    """The ``/sandbox`` slash command."""

    def __init__(
        self,
        bot: commands.Bot,
        *,
        settings_repo: SettingsRepository,
        allowed_user_ids: set[int] | None = None,
    ) -> None:
        self.bot = bot
        self._settings = ExecutionSettings(settings_repo)
        # Same rule as /skill: ``None`` means every user who can reach the bot.
        self._allowed_user_ids = allowed_user_ids

    def _is_authorized(self, user_id: int | None) -> bool:
        if self._allowed_user_ids is None:
            return True
        return user_id is not None and user_id in self._allowed_user_ids

    @app_commands.command(
        name="sandbox",
        description="Show or choose where this thread's agent runs (execution environment)",
    )
    @app_commands.choices(
        mode=[Choice(name=mode, value=mode) for mode in MODES]
        + [Choice(name="default (deployment default)", value=RESET)],
    )
    @app_commands.describe(
        mode="Execution environment for this thread. Omit to show the current one."
    )
    async def sandbox_command(
        self,
        interaction: discord.Interaction,
        mode: str | None = None,
    ) -> None:
        channel = interaction.channel
        thread_id = channel.id if isinstance(channel, discord.Thread) else None
        message, ephemeral = await self.handle(thread_id, mode, user_id=interaction.user.id)
        await interaction.response.send_message(message, ephemeral=ephemeral)

    async def handle(
        self, thread_id: int | None, mode: str | None, *, user_id: int | None = None
    ) -> tuple[str, bool]:
        """Return (reply, ephemeral). Separated from Discord for testing."""
        if not self._is_authorized(user_id):
            return "You don't have permission to use this command.", True
        config = self._settings.config()
        if config.error:
            return f"⚠️ {config.error}", True

        if mode is None:
            lines = [f"🧱 **Deployment default**: `{config.default_mode}`"]
            if thread_id is not None:
                current = await self._settings.effective_mode(thread_id)
                stored = await self._settings.stored_mode(thread_id)
                tag = " (thread choice)" if stored is not None and stored == current else ""
                lines.append(f"🧵 **This thread**: `{current}`{tag}")
                if stored is not None and stored != current:
                    lines.append(
                        f"-# `{stored}` was chosen here but is no longer allowed; "
                        "the default applies."
                    )
            lines.append("**Allowed here:**")
            lines.append(describe_modes(self._settings))
            return "\n".join(lines), True

        if thread_id is None:
            return "`/sandbox` chooses an environment per thread; run it inside a thread.", True

        if mode == RESET:
            await self._settings.clear_thread_mode(thread_id)
            return (
                f"🧱 This thread now uses the deployment default `{config.default_mode}` "
                "from the next turn.",
                False,
            )

        try:
            await self._settings.set_thread_mode(thread_id, mode)
        except ExecutionModeNotAllowedError as exc:
            return f"🚫 {exc}. The operator controls which environments are allowed.", True
        return (
            f"🧱 Execution environment set to `{mode}` for this thread from the next turn.",
            False,
        )
