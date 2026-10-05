"""``/ollama`` — inspect and manage the runtime behind the ``local`` backend.

``/backend local`` and ``/model`` answer "which model do I want". They cannot
answer the questions that actually come up when the cloud backends are gone:
what is installed, what will fit, what is resident in memory right now, why the
answers are bad. Those live in Ollama's native API, and until now the only way
to reach it was to SSH to the box.

This group is deliberately a thin, typed mirror of the handful of ``ollama``
subcommands that matter (``list``, ``ps``, ``show``, ``pull``, ``rm``), plus two
that only make sense here: ``status``, which diagnoses the whole path from
Discord to the GPU, and ``use``, which is ``/model`` scoped to what is actually
installed. Nothing takes free-form shell input — every model name goes through
the same strict grammar the pull path already used.

Guidance is built in rather than documented: every model argument is
autocompleted from live server state (or, for ``pull``, from the curated
catalog with size and fit annotations), so the commands are usable without
knowing any Ollama syntax.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.app_commands import Choice
from discord.ext import commands

from claude_code_core.local_backend import LocalModelConfig, validate_ollama_model_name
from claude_code_core.ollama_client import (
    OllamaError,
    OllamaModel,
    RunningModel,
    delete_model,
    list_models,
    pull_model,
    running_models,
    server_version,
    show_model,
)

from ..ollama_catalog import CATALOG, CATALOG_NOTE, catalog_by_name

if TYPE_CHECKING:
    from ..backend_settings import BackendSettings
    from ..cogs.claude_chat import ClaudeChatCog

logger = logging.getLogger(__name__)

LOCAL_BACKEND = "local"
MAX_CHOICES = 25
# Discord rejects a message body over 2,000 characters. Tables are trimmed to
# stay under it with room for the surrounding prose and fence.
MAX_TABLE_CHARS = 1700


def _fence(body: str) -> str:
    return f"```\n{body}\n```"


def _trim_rows(rows: list[str]) -> tuple[list[str], int]:
    """Keep as many rows as fit in one Discord message; report what was dropped.

    Silent truncation would read as "that model is not installed" — the one
    conclusion this command must never cause by accident.
    """
    kept: list[str] = []
    used = 0
    for row in rows:
        if used + len(row) + 1 > MAX_TABLE_CHARS:
            break
        kept.append(row)
        used += len(row) + 1
    return kept, len(rows) - len(kept)


def _caps(model: OllamaModel) -> str:
    """Short capability flags; the leading marker is the one that matters."""
    flags = []
    flags.append("tools" if model.supports_tools else "NO-TOOLS")
    if "thinking" in model.capabilities:
        flags.append("think")
    if "vision" in model.capabilities:
        flags.append("vision")
    return ",".join(flags)


class OllamaCommandCog(commands.Cog):
    """``/ollama status|list|ps|show|pull|rm|use`` for the local backend."""

    ollama = app_commands.Group(
        name="ollama",
        description="Inspect and manage the local Ollama runtime",
    )

    def __init__(
        self,
        bot: commands.Bot,
        *,
        settings: BackendSettings,
        chat_cog: ClaudeChatCog | None = None,
        config: LocalModelConfig | None = None,
        allowed_user_ids: set[int] | None = None,
    ) -> None:
        self.bot = bot
        # Who may install, delete or select models: the same set as /skill.
        self._allowed_user_ids = allowed_user_ids
        self._settings = settings
        self._chat_cog = chat_cog
        # Read once at construction: the endpoint is an operator-level setting,
        # and re-reading os.environ per command would let a half-applied env
        # change split one command's calls across two servers.
        self._config = config or LocalModelConfig.from_env()

    def _is_authorized(self, user_id: int) -> bool:
        """Same rule as /skill: ``None`` means every user who can reach the bot."""
        if self._allowed_user_ids is None:
            return True
        return user_id in self._allowed_user_ids

    async def _refuse_unauthorized(self, interaction: discord.Interaction) -> bool:
        if self._is_authorized(interaction.user.id):
            return False
        await interaction.response.send_message(
            "You don't have permission to use this command.", ephemeral=True
        )
        return True

    # ── helpers ────────────────────────────────────────────────────

    @property
    def base_url(self) -> str:
        return self._config.base_url

    def _thread_id_or_none(self, interaction: discord.Interaction) -> int | None:
        channel = interaction.channel
        return channel.id if isinstance(channel, discord.Thread) else None

    async def _selected_model(self, interaction: discord.Interaction) -> str | None:
        """The model the local backend would use for this context."""
        thread_id = self._thread_id_or_none(interaction)
        try:
            selected = await self._settings.current_model(LOCAL_BACKEND, thread_id)
        except Exception:  # noqa: BLE001 - a settings hiccup must not break /ollama
            logger.exception("Could not read the current local model")
            return self._config.model
        return selected or self._config.model

    async def _installed_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[Choice[str]]:
        """Suggest models that are installed on the server right now."""
        try:
            models = await list_models(self.base_url, timeout_seconds=3.0)
        except (OllamaError, ValueError):
            # Autocomplete runs on a 3-second Discord budget; an unreachable
            # server must produce an empty list, never an exception.
            return []
        needle = current.lower()
        choices: list[Choice[str]] = []
        for model in models:
            if needle and needle not in model.name.lower():
                continue
            label = f"{model.name} — {model.size_gb:.0f}GB, {_caps(model)}"
            choices.append(Choice(name=label[:100], value=model.name))
        return choices[:MAX_CHOICES]

    async def _catalog_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[Choice[str]]:
        """Suggest installable models, marking the ones already present.

        Offering an installed model as "installable" is the fastest way to waste
        an hour re-downloading something, so the state is shown inline.
        """
        try:
            installed = {m.name for m in await list_models(self.base_url, timeout_seconds=3.0)}
        except (OllamaError, ValueError):
            installed = set()
        installed_stems = {name.split(":", 1)[0] for name in installed}

        needle = current.lower()
        choices: list[Choice[str]] = []
        for entry in CATALOG:
            if needle and needle not in entry.name.lower():
                continue
            mark = " [installed]" if entry.name.split(":", 1)[0] in installed_stems else ""
            label = f"{entry.label}{mark}"
            choices.append(Choice(name=label[:100], value=entry.name))
        if current and not choices:
            # Free-text: anything the registry serves is valid, so echo it back
            # rather than leaving the operator with an empty dropdown.
            choices.append(Choice(name=f"{current} (custom tag)"[:100], value=current))
        return choices[:MAX_CHOICES]

    # ── /ollama status ─────────────────────────────────────────────

    @ollama.command(name="status", description="Check the local runtime end to end")
    async def status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        lines: list[str] = [f"🏠 **Ollama** — `{self._config.endpoint_host}`"]
        hints: list[str] = []

        try:
            version = await server_version(self.base_url)
            lines.append(f"• Server: reachable, version `{version}`")
        except (OllamaError, ValueError) as exc:
            lines.append(f"• Server: ❌ {exc}")
            lines.append(
                "-# Set `CCDB_LOCAL_BASE_URL` to the Ollama OpenAI endpoint "
                "(e.g. `http://host:11434/v1`) and make sure the server is up."
            )
            await interaction.followup.send("\n".join(lines), ephemeral=True)
            return

        selected = await self._selected_model(interaction)
        installed: list[OllamaModel] = []
        try:
            installed = await list_models(self.base_url)
        except (OllamaError, ValueError) as exc:
            lines.append(f"• Installed: ❌ {exc}")

        if installed:
            total_gb = sum(m.size_gb for m in installed)
            lines.append(f"• Installed: {len(installed)} model(s), {total_gb:.0f}GB on disk")

        match = next((m for m in installed if m.name == selected), None)
        if selected is None:
            lines.append("• Selected model: _(none — CLI default)_")
        elif match is None and installed:
            lines.append(f"• Selected model: `{selected}` ⚠️ **not installed**")
            hints.append(
                f"`/ollama pull model:{selected}` — or pick an installed one with `/ollama use`"
            )
        elif match is not None:
            tools = "✅ tool calling" if match.supports_tools else "❌ **no tool calling**"
            lines.append(f"• Selected model: `{selected}` — {match.size_gb:.0f}GB, {tools}")
            if not match.supports_tools:
                hints.append(
                    "Codex acts only through tool calls. A model without them will "
                    "describe edits instead of making them — switch with `/ollama use`."
                )
        else:
            lines.append(f"• Selected model: `{selected}`")

        try:
            running = await running_models(self.base_url)
            if running:
                for entry in running:
                    placement = (
                        "fully on GPU"
                        if entry.fully_on_gpu
                        else f"⚠️ only {entry.gpu_percent}% on GPU (rest in system RAM — slow)"
                    )
                    ctx = f", ctx {entry.context_length:,}" if entry.context_length else ""
                    lines.append(
                        f"• Loaded: `{entry.name}` — {entry.size_gb:.0f}GB{ctx}, {placement}"
                    )
            else:
                lines.append("• Loaded: nothing resident (first turn pays the load time)")
        except (OllamaError, ValueError) as exc:
            lines.append(f"• Loaded: ❌ {exc}")

        lines.append(f"• CLI home: `{self._config.resolved_codex_home}`")

        if hints:
            lines.append("")
            lines.extend(f"👉 {hint}" for hint in hints)

        await interaction.followup.send("\n".join(lines), ephemeral=True)

    # ── /ollama list ───────────────────────────────────────────────

    @ollama.command(name="list", description="List the models installed on the server")
    async def list_command(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            models = await list_models(self.base_url)
        except (OllamaError, ValueError) as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return

        if not models:
            await interaction.followup.send(
                "No models installed. Try `/ollama pull` — the dropdown suggests "
                "ones that suit the Codex CLI.",
                ephemeral=True,
            )
            return

        selected = await self._selected_model(interaction)
        rows = [f"{'':1s} {'MODEL':34s} {'SIZE':>7s} {'PARAMS':>8s}  CAPABILITIES"]
        for model in models:
            mark = "▶" if model.name == selected else " "
            rows.append(
                f"{mark:1s} {model.name[:34]:34s} {model.size_gb:6.1f}G "
                f"{model.parameter_size:>8s}  {_caps(model)}"
            )
        kept, dropped = _trim_rows(rows)

        total = sum(m.size_gb for m in models)
        footer = [f"-# ▶ = selected · {len(models)} models · {total:.0f}GB on disk"]
        if dropped:
            footer.append(f"-# {dropped} more row(s) not shown (Discord message limit)")
        no_tools = [m.name for m in models if not m.supports_tools]
        if no_tools:
            footer.append(f"-# NO-TOOLS models cannot drive Codex: {', '.join(no_tools[:5])}")

        await interaction.followup.send(
            _fence("\n".join(kept)) + "\n" + "\n".join(footer), ephemeral=True
        )

    # ── /ollama ps ─────────────────────────────────────────────────

    @ollama.command(name="ps", description="Show which models are loaded in memory right now")
    async def ps(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            running: list[RunningModel] = await running_models(self.base_url)
        except (OllamaError, ValueError) as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return

        if not running:
            await interaction.followup.send(
                "Nothing is loaded. The next local turn will pay the model load time "
                "(seconds for a small model, a minute or more for a 100GB one).",
                ephemeral=True,
            )
            return

        rows = [f"{'MODEL':30s} {'MEMORY':>8s} {'ON GPU':>8s} {'CONTEXT':>9s}  UNTIL"]
        for entry in running:
            gpu = f"{entry.gpu_percent}%" if entry.size_bytes else "?"
            ctx = f"{entry.context_length:,}" if entry.context_length else "-"
            until = entry.expires_at[11:19] if len(entry.expires_at) >= 19 else "-"
            rows.append(f"{entry.name[:30]:30s} {entry.size_gb:7.1f}G {gpu:>8s} {ctx:>9s}  {until}")
        kept, dropped = _trim_rows(rows)

        spilled = [e.name for e in running if not e.fully_on_gpu]
        footer = ["-# ON GPU below 100% means the rest runs on CPU — expect it to be slow."]
        if spilled:
            footer.append(f"-# ⚠️ spilling to system RAM: {', '.join(spilled)}")
        if dropped:
            footer.append(f"-# {dropped} more row(s) not shown (Discord message limit)")

        await interaction.followup.send(
            _fence("\n".join(kept)) + "\n" + "\n".join(footer), ephemeral=True
        )

    # ── /ollama show ───────────────────────────────────────────────

    @ollama.command(name="show", description="Show the details of one installed model")
    @app_commands.autocomplete(model=_installed_autocomplete)
    @app_commands.describe(model="Installed model to describe")
    async def show(self, interaction: discord.Interaction, model: str) -> None:
        await interaction.response.defer(ephemeral=True)
        try:
            detail = await show_model(self.base_url, model)
        except (OllamaError, ValueError) as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return

        lines = [
            f"🔎 **`{detail.name}`**",
            f"• Family: `{detail.family or '?'}` · Params: `{detail.parameter_size or '?'}` "
            f"· Quantization: `{detail.quantization or '?'}`",
            f"• Capabilities: `{', '.join(detail.capabilities) or 'none reported'}`",
        ]
        if detail.max_context_length:
            lines.append(f"• Max context: `{detail.max_context_length:,}` tokens")
        if detail.parameters.strip():
            lines.append("• Model-file parameters:\n" + _fence(detail.parameters.strip()[:600]))
        if not detail.supports_tools:
            lines.append(
                "\n⚠️ This model does **not** advertise tool calling, which the Codex "
                "CLI needs. Selecting it will produce descriptions of work instead of work."
            )
        await interaction.followup.send("\n".join(lines)[:1900], ephemeral=True)

    # ── /ollama pull ───────────────────────────────────────────────

    @ollama.command(name="pull", description="Download a model onto the server")
    @app_commands.autocomplete(model=_catalog_autocomplete)
    @app_commands.describe(
        model="Model to download. The dropdown suggests ones suited to the Codex CLI.",
        use="Also select it for the local backend once the download finishes.",
    )
    async def pull(self, interaction: discord.Interaction, model: str, use: bool = False) -> None:
        if await self._refuse_unauthorized(interaction):
            return
        try:
            name = validate_ollama_model_name(model)
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return

        entry = catalog_by_name(name)
        size_note = f" (~{entry.approx_gb:.0f}GB)" if entry else ""
        await interaction.response.send_message(
            f"📦 Pulling `{name}`{size_note}. Large models take a while; "
            f"I'll post here when it finishes.\n-# {CATALOG_NOTE}",
        )

        channel: Any = interaction.channel
        try:
            await pull_model(self.base_url, name)
        except (OllamaError, ValueError) as exc:
            logger.exception("Ollama pull failed for %s", name)
            detail = str(exc).strip()[:500] or type(exc).__name__
            await self._post(channel, interaction, f"❌ Pull failed for `{name}`: {detail}")
            return

        message = f"✅ Pulled `{name}`."
        # Verify against the server rather than trusting the pull's own "success":
        # the tag that lands can differ from the tag requested (`qwen3.6:35b-a3b`
        # resolving to a quantized variant), and selecting a name that does not
        # exist fails later, in a thread, with a worse error.
        try:
            installed = await list_models(self.base_url)
            landed = next((m for m in installed if m.name == name), None)
            if landed is None:
                stem = name.split(":", 1)[0]
                near = [m.name for m in installed if m.name.split(":", 1)[0] == stem]
                if near:
                    message += f" It is registered as `{near[0]}`."
                    name = near[0]
                    landed = next((m for m in installed if m.name == name), None)
            if landed is not None:
                message += f" {landed.size_gb:.0f}GB, capabilities: {_caps(landed)}."
                if not landed.supports_tools:
                    message += (
                        "\n⚠️ It does not advertise tool calling, so the Codex CLI "
                        "cannot drive it properly."
                    )
        except (OllamaError, ValueError):
            logger.debug("Post-pull verification failed", exc_info=True)

        if use:
            await self._settings.set_model(LOCAL_BACKEND, name, thread_id=None)
            self._apply_to_runner(name)
            message += f"\n🧠 Selected `{name}` for the `local` backend (global)."
        else:
            message += f"\n-# Select it with `/ollama use model:{name}`."

        await self._post(channel, interaction, message)

    # ── /ollama rm ─────────────────────────────────────────────────

    @ollama.command(name="rm", description="Delete an installed model and free its disk space")
    @app_commands.autocomplete(model=_installed_autocomplete)
    @app_commands.describe(model="Installed model to delete")
    async def rm(self, interaction: discord.Interaction, model: str) -> None:
        if await self._refuse_unauthorized(interaction):
            return
        await interaction.response.defer(ephemeral=True)
        try:
            name = validate_ollama_model_name(model)
        except ValueError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return

        selected = await self._selected_model(interaction)
        if name == selected:
            # Deleting the selected model leaves the backend pointing at
            # nothing, and the failure would surface mid-thread.
            await interaction.followup.send(
                f"❌ `{name}` is the model the `local` backend is set to use. "
                "Pick a different one with `/ollama use` first.",
                ephemeral=True,
            )
            return

        try:
            await delete_model(self.base_url, name)
        except (OllamaError, ValueError) as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return

        await interaction.followup.send(f"🗑️ Deleted `{name}`.", ephemeral=True)

    # ── /ollama use ────────────────────────────────────────────────

    @ollama.command(name="use", description="Select an installed model for the local backend")
    @app_commands.autocomplete(model=_installed_autocomplete)
    @app_commands.describe(
        model="Installed model to use",
        thread_only="Apply to this thread only instead of server-wide",
    )
    async def use(
        self, interaction: discord.Interaction, model: str, thread_only: bool = False
    ) -> None:
        if await self._refuse_unauthorized(interaction):
            return
        await interaction.response.defer()
        try:
            name = validate_ollama_model_name(model)
        except ValueError as exc:
            await interaction.followup.send(f"❌ {exc}", ephemeral=True)
            return

        thread_id = self._thread_id_or_none(interaction)
        if thread_only and thread_id is None:
            await interaction.followup.send(
                "`thread_only` requires the command to be run inside a thread.",
                ephemeral=True,
            )
            return

        warning = ""
        try:
            installed = await list_models(self.base_url)
            match = next((m for m in installed if m.name == name), None)
            if match is None:
                await interaction.followup.send(
                    f"❌ `{name}` is not installed. Pull it first: `/ollama pull model:{name}`",
                    ephemeral=True,
                )
                return
            if not match.supports_tools:
                warning = (
                    "\n⚠️ This model does not advertise tool calling. The Codex CLI "
                    "drives everything through tools, so expect it to describe work "
                    "rather than do it."
                )
        except (OllamaError, ValueError) as exc:
            # An unreachable server should not block a selection the operator
            # knows is right; say so instead of guessing.
            warning = f"\n-# Could not verify against the server ({exc})."

        target_thread = thread_id if thread_only else None
        await self._settings.set_model(LOCAL_BACKEND, name, thread_id=target_thread)
        if target_thread is None:
            self._apply_to_runner(name)

        scope = f"<#{target_thread}>" if target_thread is not None else "**globally**"
        await interaction.followup.send(
            f"🧠 Local backend model set to `{name}` {scope}. Next session will use it.{warning}"
        )

    # ── plumbing ───────────────────────────────────────────────────

    def _apply_to_runner(self, name: str) -> None:
        """Point the shared runner at ``name`` when the local backend is active.

        Mirrors ``/model``: without this the setting is stored but the already
        built runner keeps the old model until the bot restarts.
        """
        chat_cog = self._chat_cog
        if chat_cog is None or getattr(chat_cog, "runner", None) is None:
            return
        try:
            chat_cog.runner.model = name  # type: ignore[assignment]
        except Exception:  # noqa: BLE001 - a runner swap failure is not fatal
            logger.exception("Could not apply the new local model to the shared runner")

    @staticmethod
    async def _post(channel: Any, interaction: discord.Interaction, message: str) -> None:
        """Post a follow-up after the initial interaction response was consumed."""
        if channel is not None:
            await channel.send(message)
        else:
            await interaction.followup.send(message)
