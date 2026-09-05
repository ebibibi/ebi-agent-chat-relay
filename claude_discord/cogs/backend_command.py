"""Runtime slash commands for selecting backends, models, and effort."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands

from claude_code_core.codex_runner import VALID_CODEX_EFFORTS
from claude_code_core.local_backend import pull_ollama_model, validate_ollama_model_name

from ..backend_settings import (
    ALL_BACKENDS,
    CODEX_STATUS_DEFAULT,
    CODEX_STATUS_MODES,
    BackendSettings,
)
from ..model_catalog import claude_model_choices, codex_model_choices

if TYPE_CHECKING:
    from ..backend_factory import BackendFactory
    from ..cogs.claude_chat import ClaudeChatCog

logger = logging.getLogger(__name__)

SCOPE_THREAD = "thread"
SCOPE_GLOBAL = "global"

VALID_EFFORTS: dict[str, frozenset[str]] = {
    "claude": frozenset({"low", "medium", "high", "max"}),
    "codex": VALID_CODEX_EFFORTS,
    "local": VALID_CODEX_EFFORTS,
}

EFFORT_ORDER: dict[str, list[str]] = {
    "claude": ["low", "medium", "high", "max"],
    "codex": ["minimal", "low", "medium", "high", "xhigh", "max", "ultra"],
    "local": ["minimal", "low", "medium", "high", "xhigh", "max", "ultra"],
}

# Suggestions only: the model fields remain free text.
SUGGESTED_MODELS: dict[str, list[tuple[str, str]]] = {
    "claude": [
        ("haiku", "fastest, cheapest (alias — newest Haiku)"),
        ("sonnet", "balanced (alias — newest Sonnet)"),
        ("opus", "most capable (alias — newest Opus)"),
        ("fable", "token-efficient frontier (alias — newest Fable)"),
    ],
    # Fallback only — the live list comes from the Codex CLI's own catalog
    # (codex_model_choices). Kept short and generation-current so a host that
    # has never run the CLI still sees something selectable.
    "codex": [
        ("gpt-6-astra", "GPT-6-Astra — most capable"),
        ("gpt-5.6-sol", "GPT-5.6-Sol"),
        ("gpt-5.5", "GPT-5.5"),
    ],
    "local": [
        ("gpt-oss:120b", "gpt-oss 120B (tool use, needs real VRAM)"),
        ("qwen3.5:35b", "Qwen3.5 35B"),
    ],
}


def _model_label(model: str | None) -> str:
    """Human-readable model label; ``None`` means the backend CLI default."""
    return f"`{model}`" if model else "_(CLI default)_"


class BackendCommandCog(commands.Cog):
    """Backend, model, effort, and engine-status slash commands."""

    model_group = app_commands.Group(
        name="model",
        description="Show, select, or install a model for the current backend",
    )

    def __init__(
        self,
        bot: commands.Bot,
        *,
        settings: BackendSettings,
        factory: BackendFactory,
        chat_cog: ClaudeChatCog,
    ) -> None:
        self.bot = bot
        self._settings = settings
        self._factory = factory
        self._chat_cog = chat_cog

    def _thread_id_or_none(self, interaction: discord.Interaction) -> int | None:
        channel = interaction.channel
        if isinstance(channel, discord.Thread):
            return channel.id
        return None

    def _resolve_scope(
        self, interaction: discord.Interaction, requested: str | None
    ) -> tuple[str, int | None]:
        """Resolve an explicit scope, or default to thread when in a thread."""
        thread_id = self._thread_id_or_none(interaction)
        if requested == SCOPE_GLOBAL:
            return SCOPE_GLOBAL, None
        if requested == SCOPE_THREAD:
            return SCOPE_THREAD, thread_id
        if thread_id is not None:
            return SCOPE_THREAD, thread_id
        return SCOPE_GLOBAL, None

    async def _send_install_result(
        self,
        interaction: discord.Interaction,
        message: str,
    ) -> None:
        """Post the result after the initial interaction response was consumed."""
        channel: Any = interaction.channel
        if channel is not None:
            await channel.send(message)
        else:
            await interaction.followup.send(message)

    async def _set_model_selection(
        self,
        *,
        backend: str,
        name: str,
        resolved_scope: str,
        target_thread_id: int | None,
    ) -> None:
        """Persist a model and update the global default runner when applicable."""
        await self._settings.set_model(backend, name, thread_id=target_thread_id)
        if resolved_scope == SCOPE_GLOBAL and self._chat_cog.runner is not None:
            try:
                self._chat_cog.runner.model = name  # type: ignore[assignment]
                logger.info("ClaudeChatCog default runner.model swapped to %s", name)
            except Exception:
                logger.exception("Failed to update ClaudeChatCog.runner.model")

    # ── /backend ───────────────────────────────────────────────────

    @app_commands.command(
        name="backend",
        description="Show or switch the AI backend",
    )
    @app_commands.choices(
        name=[Choice(name=backend, value=backend) for backend in ALL_BACKENDS],
        scope=[
            Choice(name="thread", value=SCOPE_THREAD),
            Choice(name="global", value=SCOPE_GLOBAL),
        ],
    )
    @app_commands.describe(
        name="claude, codex, local, or agui. Omit to show current setting.",
        scope=(
            "thread: only this thread; global: server-wide default. "
            "Default: thread when invoked in a thread, otherwise global."
        ),
    )
    async def backend_command(
        self,
        interaction: discord.Interaction,
        name: str | None = None,
        scope: str | None = None,
    ) -> None:
        thread_id_now = self._thread_id_or_none(interaction)

        if name is None:
            current_thread = (
                await self._settings.current_backend(thread_id_now)
                if thread_id_now is not None
                else None
            )
            current_global = await self._settings.current_backend(None)
            lines = [f"🧠 **Global backend**: `{current_global}`"]
            if thread_id_now is not None and current_thread is not None:
                tag = " (thread override)" if current_thread != current_global else ""
                lines.append(f"🧵 **This thread**: `{current_thread}`{tag}")
            await interaction.response.send_message("\n".join(lines), ephemeral=True)
            return

        if name not in ALL_BACKENDS:
            await interaction.response.send_message(
                f"Unknown backend `{name}`. Choose: {', '.join(ALL_BACKENDS)}.",
                ephemeral=True,
            )
            return

        resolved_scope, target_thread_id = self._resolve_scope(interaction, scope)
        if resolved_scope == SCOPE_THREAD and target_thread_id is None:
            await interaction.response.send_message(
                "`scope:thread` requires the command to be run inside a thread.",
                ephemeral=True,
            )
            return

        await self._settings.set_backend(name, thread_id=target_thread_id)

        if resolved_scope == SCOPE_GLOBAL:
            try:
                model = await self._settings.current_model(name, None)
                new_runner = self._factory.build(backend=name, model=model)
                self._chat_cog.runner = new_runner  # type: ignore[assignment]
                logger.info(
                    "ClaudeChatCog default runner swapped: %s (model=%s)",
                    name,
                    new_runner.model,
                )
            except Exception:
                logger.exception("Failed to swap ClaudeChatCog.runner after /backend change")

        scope_label = (
            f"<#{target_thread_id}>"
            if resolved_scope == SCOPE_THREAD and target_thread_id is not None
            else "**globally**"
        )
        emoji = {"codex": "🌀", "local": "🏠", "agui": "🔌"}.get(name, "🤖")
        await interaction.response.send_message(
            f"{emoji} Backend set to `{name}` {scope_label}. Next session will use it.",
            ephemeral=False,
        )

    # ── /model show|set|install ────────────────────────────────────

    async def _backend_for_autocomplete(self, interaction: discord.Interaction) -> str:
        """Resolve the backend whose model suggestions should be displayed."""
        thread_id = self._thread_id_or_none(interaction)
        return await self._settings.current_backend(thread_id)

    async def _model_name_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[Choice[str]]:
        """Suggest models for the active backend, filtered by typed text."""
        backend = await self._backend_for_autocomplete(interaction)
        if backend == "claude":
            suggestions = await claude_model_choices(fallback=SUGGESTED_MODELS["claude"])
        elif backend == "codex":
            suggestions = codex_model_choices(fallback=SUGGESTED_MODELS["codex"])
        else:
            suggestions = SUGGESTED_MODELS.get(backend, [])
        current_lower = current.lower()
        choices: list[Choice[str]] = []
        for value, description in suggestions:
            if current_lower and current_lower not in value.lower():
                continue
            label = f"{value} — {description}"
            choices.append(Choice(name=label[:100], value=value))
        return choices[:25]

    @model_group.command(
        name="show",
        description="Show the current model selection",
    )
    async def model_show_command(self, interaction: discord.Interaction) -> None:
        thread_id_now = self._thread_id_or_none(interaction)
        backend_for_thread = (
            await self._settings.current_backend(thread_id_now)
            if thread_id_now is not None
            else await self._settings.current_backend(None)
        )
        current_thread = (
            await self._settings.current_model(backend_for_thread, thread_id_now)
            if thread_id_now is not None
            else None
        )
        backend_for_global = await self._settings.current_backend(None)
        current_global = await self._settings.current_model(
            backend_for_global, None
        ) or self._factory.default_model_for(backend_for_global)
        lines = [
            f"🧠 **Global model**: {_model_label(current_global)} (for `{backend_for_global}`)",
        ]
        if thread_id_now is not None:
            resolved_thread = current_thread or self._factory.default_model_for(backend_for_thread)
            lines.append(
                f"🧵 **This thread**: {_model_label(resolved_thread)} (for `{backend_for_thread}`)"
            )
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @model_group.command(
        name="set",
        description="Select a model for the current backend",
    )
    @app_commands.choices(
        scope=[
            Choice(name="thread", value=SCOPE_THREAD),
            Choice(name="global", value=SCOPE_GLOBAL),
        ],
    )
    @app_commands.autocomplete(name=_model_name_autocomplete)
    @app_commands.describe(
        name="Model id to select.",
        scope=(
            "thread: only this thread; global: server-wide. "
            "Default: thread when in thread, else global."
        ),
    )
    async def model_set_command(
        self,
        interaction: discord.Interaction,
        name: str,
        scope: str | None = None,
    ) -> None:
        resolved_scope, target_thread_id = self._resolve_scope(interaction, scope)
        if resolved_scope == SCOPE_THREAD and target_thread_id is None:
            await interaction.response.send_message(
                "`scope:thread` requires the command to be run inside a thread.",
                ephemeral=True,
            )
            return

        backend = await self._settings.current_backend(
            target_thread_id if resolved_scope == SCOPE_THREAD else None
        )
        await self._set_model_selection(
            backend=backend,
            name=name,
            resolved_scope=resolved_scope,
            target_thread_id=target_thread_id,
        )

        scope_label = (
            f"<#{target_thread_id}>"
            if resolved_scope == SCOPE_THREAD and target_thread_id is not None
            else "**globally**"
        )
        await interaction.response.send_message(
            f"🧠 Model set to `{name}` for `{backend}` {scope_label}. Next session will use it.",
            ephemeral=False,
        )

    @model_group.command(
        name="install",
        description="Install and select an Ollama model for the local backend",
    )
    @app_commands.choices(
        scope=[
            Choice(name="thread", value=SCOPE_THREAD),
            Choice(name="global", value=SCOPE_GLOBAL),
        ],
    )
    @app_commands.describe(
        name="Ollama model id to pull and select.",
        scope=(
            "thread: only this thread; global: server-wide. "
            "Default: thread when in thread, else global."
        ),
    )
    async def model_install_command(
        self,
        interaction: discord.Interaction,
        name: str,
        scope: str | None = None,
    ) -> None:
        resolved_scope, target_thread_id = self._resolve_scope(interaction, scope)
        if resolved_scope == SCOPE_THREAD and target_thread_id is None:
            await interaction.response.send_message(
                "`scope:thread` requires the command to be run inside a thread.",
                ephemeral=True,
            )
            return

        backend = await self._settings.current_backend(
            target_thread_id if resolved_scope == SCOPE_THREAD else None
        )
        if backend != "local":
            await interaction.response.send_message(
                "Model installation is available only for the `local` backend. "
                "Run `/backend name:local` first.",
                ephemeral=True,
            )
            return

        try:
            normalized_name = validate_ollama_model_name(name)
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return

        await interaction.response.send_message(
            f"📦 Installing `{normalized_name}` from Ollama. This can take a while; "
            "I will post again when it finishes.",
            ephemeral=False,
        )
        try:
            await pull_ollama_model(normalized_name)
        except Exception as exc:
            logger.exception("Failed to install Ollama model %s", normalized_name)
            detail = str(exc).strip()[:500] or type(exc).__name__
            await self._send_install_result(
                interaction,
                f"❌ Ollama model installation failed for `{normalized_name}`: {detail}",
            )
            return

        await self._set_model_selection(
            backend=backend,
            name=normalized_name,
            resolved_scope=resolved_scope,
            target_thread_id=target_thread_id,
        )
        scope_label = (
            f"<#{target_thread_id}>"
            if resolved_scope == SCOPE_THREAD and target_thread_id is not None
            else "**globally**"
        )
        await self._send_install_result(
            interaction,
            f"✅ Installed `{normalized_name}` from Ollama and set it for `local` "
            f"{scope_label}. It is ready for the next session.",
        )

    # ── /effort ────────────────────────────────────────────────────

    async def _effort_level_autocomplete(
        self,
        interaction: discord.Interaction,
        current: str,
    ) -> list[Choice[str]]:
        """Suggest effort levels valid for the active backend, low to high."""
        backend = await self._backend_for_autocomplete(interaction)
        current_lower = current.lower()
        levels = EFFORT_ORDER.get(backend, sorted(VALID_EFFORTS.get(backend, frozenset())))
        return [
            Choice(name=level, value=level)
            for level in levels
            if not current_lower or current_lower in level.lower()
        ][:25]

    @app_commands.command(
        name="effort",
        description="Show or set the reasoning effort for the current backend",
    )
    @app_commands.choices(
        scope=[
            Choice(name="thread", value=SCOPE_THREAD),
            Choice(name="global", value=SCOPE_GLOBAL),
        ],
    )
    @app_commands.autocomplete(level=_effort_level_autocomplete)
    @app_commands.describe(
        level=(
            "Effort level. Claude: low/medium/high/max. "
            "Codex: low/medium/high/xhigh/max/ultra (model-dependent). "
            "Omit to show current."
        ),
        scope=(
            "thread: only this thread; global: server-wide. "
            "Default: thread when in thread, else global."
        ),
    )
    async def effort_command(
        self,
        interaction: discord.Interaction,
        level: str | None = None,
        scope: str | None = None,
    ) -> None:
        thread_id_now = self._thread_id_or_none(interaction)

        if level is None:
            backend_for_global = await self._settings.current_backend(None)
            current_global = await self._settings.current_effort(backend_for_global, None)
            lines = [
                f"⚡ **Global effort**: {self._effort_label(current_global)} "
                f"(for `{backend_for_global}`)",
            ]
            if thread_id_now is not None:
                backend_for_thread = await self._settings.current_backend(thread_id_now)
                current_thread = await self._settings.current_effort(
                    backend_for_thread, thread_id_now
                )
                lines.append(
                    f"🧵 **This thread**: {self._effort_label(current_thread)} "
                    f"(for `{backend_for_thread}`)"
                )
            await interaction.response.send_message("\n".join(lines), ephemeral=True)
            return

        resolved_scope, target_thread_id = self._resolve_scope(interaction, scope)
        if resolved_scope == SCOPE_THREAD and target_thread_id is None:
            await interaction.response.send_message(
                "`scope:thread` requires the command to be run inside a thread.",
                ephemeral=True,
            )
            return

        backend = await self._settings.current_backend(
            target_thread_id if resolved_scope == SCOPE_THREAD else None
        )
        valid = VALID_EFFORTS.get(backend, frozenset())
        normalized = level.strip().lower()
        if normalized not in valid:
            await interaction.response.send_message(
                f"❌ Unknown effort `{level}` for `{backend}`. Choose: {', '.join(sorted(valid))}.",
                ephemeral=True,
            )
            return

        await self._settings.set_effort(backend, normalized, thread_id=target_thread_id)
        scope_label = (
            f"<#{target_thread_id}>"
            if resolved_scope == SCOPE_THREAD and target_thread_id is not None
            else "**globally**"
        )
        await interaction.response.send_message(
            f"⚡ Effort set to `{normalized}` for `{backend}` {scope_label}. "
            "Next session will use it.",
            ephemeral=False,
        )

    @staticmethod
    def _effort_label(effort: str | None) -> str:
        """Human-readable effort label; ``None`` means the backend CLI default."""
        return f"`{effort}`" if effort else "_(CLI default)_"

    # ── /engine-status ─────────────────────────────────────────────

    @app_commands.command(
        name="engine-status",
        description="Show/set whether the Codex usage line appears after each turn",
    )
    @app_commands.choices(
        mode=[Choice(name=mode, value=mode) for mode in CODEX_STATUS_MODES],
        scope=[
            Choice(name="thread", value=SCOPE_THREAD),
            Choice(name="global", value=SCOPE_GLOBAL),
        ],
    )
    @app_commands.describe(
        mode=(
            "auto: show Codex usage only when it can be fetched; "
            "on: always; off: never. Omit to show current setting."
        ),
        scope=(
            "thread: only this thread; global: server-wide default. "
            "Default: thread when invoked in a thread, otherwise global."
        ),
    )
    async def engine_status_command(
        self,
        interaction: discord.Interaction,
        mode: str | None = None,
        scope: str | None = None,
    ) -> None:
        thread_id_now = self._thread_id_or_none(interaction)

        if mode is None:
            current_global = await self._settings.codex_status_mode(None)
            lines = [f"🧠 **Global Codex status**: `{current_global}`"]
            if thread_id_now is not None:
                current_thread = await self._settings.codex_status_mode(thread_id_now)
                tag = " (thread override)" if current_thread != current_global else ""
                lines.append(f"🧵 **This thread**: `{current_thread}`{tag}")
            lines.append(
                f"-# auto = show Codex usage only when reachable (default: "
                f"`{CODEX_STATUS_DEFAULT}`)."
            )
            await interaction.response.send_message("\n".join(lines), ephemeral=True)
            return

        if mode not in CODEX_STATUS_MODES:
            await interaction.response.send_message(
                f"Unknown mode `{mode}`. Choose: {', '.join(CODEX_STATUS_MODES)}.",
                ephemeral=True,
            )
            return

        resolved_scope, target_thread_id = self._resolve_scope(interaction, scope)
        if resolved_scope == SCOPE_THREAD and target_thread_id is None:
            await interaction.response.send_message(
                "`scope:thread` requires the command to be run inside a thread.",
                ephemeral=True,
            )
            return

        await self._settings.set_codex_status_mode(mode, thread_id=target_thread_id)
        scope_label = (
            f"<#{target_thread_id}>"
            if resolved_scope == SCOPE_THREAD and target_thread_id is not None
            else "**globally**"
        )
        await interaction.response.send_message(
            f"🌀 Codex status set to `{mode}` {scope_label}.",
            ephemeral=False,
        )
