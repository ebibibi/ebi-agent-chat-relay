"""Session management Cog.

Provides slash commands for viewing and managing Claude Code sessions:
- /resume-info: Show CLI resume command for the current thread's session
- /sessions: List all known sessions (Discord and CLI originated)
- /sync-sessions: Import CLI sessions as Discord threads
- /sync-settings: Configure session sync preferences (thread style)
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from claude_code_core.thread_search import ThreadSearchResult, run_thread_search
from claude_code_core.transcript_search import default_transcripts_root

from ..database.repository import SessionRepository, UsageStatsRepository
from ..database.settings_repo import SettingsRepository
from ..discord_ui.embeds import COLOR_ERROR, COLOR_INFO, COLOR_SUCCESS, COLOR_TOOL
from ..discord_ui.views import ResumeSelectView, ToolSelectView
from ..worktree import WorktreeManager
from .session_sync import sync_cli_sessions

if TYPE_CHECKING:
    from claude_code_core.account_router import AccountRouter

    from ..bot import ClaudeDiscordBot

logger = logging.getLogger(__name__)

_ORIGIN_ICON = {
    "discord": "\U0001f4ac",  # 💬
    "cli": "\U0001f5a5\ufe0f",  # 🖥️
}

_ORIGIN_CHOICES = [
    app_commands.Choice(name="All", value="all"),
    app_commands.Choice(name="Discord", value="discord"),
    app_commands.Choice(name="CLI", value="cli"),
]

SETTING_SYNC_THREAD_STYLE = "sync_thread_style"
THREAD_STYLE_CHANNEL = "channel"
THREAD_STYLE_MESSAGE = "message"
_VALID_THREAD_STYLES = {THREAD_STYLE_CHANNEL, THREAD_STYLE_MESSAGE}

_STYLE_CHOICES = [
    app_commands.Choice(name="Channel threads (hidden in panel)", value=THREAD_STYLE_CHANNEL),
    app_commands.Choice(name="Message threads (visible in channel)", value=THREAD_STYLE_MESSAGE),
]

SETTING_SYNC_SINCE_HOURS = "sync_since_hours"
_DEFAULT_SINCE_HOURS = 24
SETTING_SYNC_MIN_RESULTS = "sync_min_results"
_DEFAULT_MIN_RESULTS = 10

# Legacy model/effort setting keys.
#
# The user-facing ``/model-set`` / ``/effort-set`` commands were removed in
# favour of the backend-aware ``/model`` and ``/effort`` commands (see
# ``cogs/backend_command.py``). These two keys are retained **read-only** as a
# backward-compatibility fallback: installs that stored a value under the old
# commands still have it honoured at spawn time by ``ClaudeChatCog`` (Claude
# backend only). Nothing writes them any more.
SETTING_CLAUDE_MODEL = "claude_model"
SETTING_CLAUDE_EFFORT = "claude_effort"

# Tool permission management
SETTING_ALLOWED_TOOLS = "allowed_tools"
KNOWN_TOOLS: list[str] = [
    "Bash",
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "WebFetch",
    "WebSearch",
    "NotebookEdit",
]


_AUTOCOMPACT_THRESHOLD = 0.835  # Claude Code's default autocompact threshold

# Max results shown by /search. Kept small so the embed stays scannable on mobile.
_SEARCH_RESULT_LIMIT = 15


def build_search_embed(
    query: str,
    results: list[ThreadSearchResult],
    *,
    guild_id: int | None,
) -> discord.Embed:
    """Render /search hits as an embed with a Discord deep-link per thread.

    The deep-link reopens even an archived (sidebar-hidden) thread, which is the
    whole point: threads are never deleted, just hard to find again. Body-match
    hits (``source == "body"``) also show the matching snippet; a transcript with
    no Discord thread offers a ``claude --resume`` hint instead of a link.
    """
    if not results:
        return discord.Embed(
            title=f"\U0001f50d Search: {query}",
            description="No threads matched. Try a different keyword.",
            color=COLOR_INFO,
        )

    embed = discord.Embed(
        title=f"\U0001f50d Search: {query} ({len(results)})",
        color=COLOR_INFO,
    )
    for result in results:
        icon = _ORIGIN_ICON.get(result.origin or "", "❓")
        label = result.summary or (f"session {result.session_id[:8]}" if result.session_id else "")
        badge = " 💬" if result.source == "body" else ""
        name = f"{icon} {label.replace(chr(10), ' ')[:60]}{badge}"

        parts: list[str] = []
        if guild_id is not None and result.thread_id is not None:
            link = f"https://discord.com/channels/{guild_id}/{result.thread_id}"
            parts.append(f"[\U0001f517 open thread]({link})")
        elif result.session_id is not None:
            parts.append(f"resume: `claude --resume {result.session_id}`")
        if result.last_used_at:
            parts.append(result.last_used_at)
        if result.working_dir:
            parts.append(f"`{result.working_dir.rsplit('/', 1)[-1]}`")

        value = " · ".join(parts) if parts else "​"
        if result.snippet:
            value = f"{value}\n> {result.snippet[:180]}"
        embed.add_field(name=name, value=value, inline=False)
    return embed


def _progress_bar(ratio: float, width: int = 20) -> str:
    """Return a block-character progress bar, e.g. '████████░░░░░░░░░░░░'."""
    filled = round(ratio * width)
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


def _format_countdown(resets_at: int) -> str:
    """Return a human-readable countdown to a Unix timestamp, e.g. 'resets in 2h 14m'."""
    remaining = resets_at - int(time.time())
    if remaining <= 0:
        return "resetting now"
    hours, rem = divmod(remaining, 3600)
    minutes = rem // 60
    if hours > 0:
        return f"resets in {hours}h {minutes}m"
    return f"resets in {minutes}m"


class SessionManageCog(commands.Cog):
    """Cog for session listing, resume info, and CLI sync commands."""

    def __init__(
        self,
        bot: ClaudeDiscordBot,
        repo: SessionRepository,
        cli_sessions_path: str | None = None,
        settings_repo: SettingsRepository | None = None,
        runner: object | None = None,
        usage_repo: UsageStatsRepository | None = None,
        account_router: AccountRouter | None = None,
    ) -> None:
        self.bot = bot
        self.repo = repo
        self.cli_sessions_path = cli_sessions_path
        self.settings_repo = settings_repo
        self.usage_repo = usage_repo
        # Set when CCDB_ACCOUNT_POOLS_FILE configures account pools: /usage
        # then lists every profile instead of the single implicit login.
        self.account_router = account_router
        # Optional ClaudeRunner reference for reading the default model.
        # Resolved lazily from ClaudeChatCog if not provided directly.
        self._runner = runner

    async def _get_thread_style(self) -> str:
        """Get the configured thread style, defaulting to 'channel'."""
        if self.settings_repo is None:
            return THREAD_STYLE_CHANNEL
        style = await self.settings_repo.get(SETTING_SYNC_THREAD_STYLE)
        if style in _VALID_THREAD_STYLES:
            return style
        return THREAD_STYLE_CHANNEL

    async def _get_since_hours(self) -> int:
        """Get the configured since_hours filter, defaulting to 24."""
        if self.settings_repo is None:
            return _DEFAULT_SINCE_HOURS
        raw = await self.settings_repo.get(SETTING_SYNC_SINCE_HOURS)
        if raw is not None and raw.isdigit():
            return int(raw)
        return _DEFAULT_SINCE_HOURS

    async def _get_min_results(self) -> int:
        """Get the configured min_results fallback, defaulting to 10."""
        if self.settings_repo is None:
            return _DEFAULT_MIN_RESULTS
        raw = await self.settings_repo.get(SETTING_SYNC_MIN_RESULTS)
        if raw is not None and raw.isdigit():
            return int(raw)
        return _DEFAULT_MIN_RESULTS

    def _get_runner(self) -> object | None:
        """Return the runner, resolving it from ClaudeChatCog if not set directly."""
        if self._runner is not None:
            return self._runner
        chat_cog = self.bot.get_cog("ClaudeChatCog")
        if chat_cog is not None:
            return getattr(chat_cog, "runner", None)
        return None

    # Note: the legacy Claude-only ``/model-show`` / ``/model-set`` /
    # ``/effort-show`` / ``/effort-set`` / ``/effort-clear`` commands were
    # removed here in favour of the backend-aware ``/model`` and ``/effort``
    # commands in ``cogs/backend_command.py`` (which autocomplete per active
    # backend). The ``SETTING_CLAUDE_MODEL`` / ``SETTING_CLAUDE_EFFORT`` keys are
    # retained read-only for backward compatibility (see their definitions).

    # ── Tool permission commands ──────────────────────────────────────────────

    async def _get_effective_tools(self) -> list[str] | None:
        """Return the effective allowed tools: settings_repo override or runner default.

        Returns None when no tool restrictions are configured.
        """
        if self.settings_repo is not None:
            stored = await self.settings_repo.get(SETTING_ALLOWED_TOOLS)
            if stored is not None:
                return [t.strip() for t in stored.split(",") if t.strip()]
        runner = self._get_runner()
        if runner is not None and hasattr(runner, "allowed_tools"):
            return runner.allowed_tools  # type: ignore[return-value]
        return None

    @app_commands.command(name="tools-show", description="Show current allowed tools")
    async def tools_show(self, interaction: discord.Interaction) -> None:
        """Display the current tool whitelist."""
        tools = await self._get_effective_tools()

        embed = discord.Embed(
            title="🔧 Allowed Tools",
            color=COLOR_INFO,
        )
        if tools:
            embed.description = "\n".join(f"• `{t}`" for t in tools)
        else:
            embed.description = (
                "**No restrictions** — all tools are available.\n"
                "Use `/tools-set` to restrict tools."
            )

        # Show source (override vs default)
        stored = await self.settings_repo.get(SETTING_ALLOWED_TOOLS) if self.settings_repo else None
        runner = self._get_runner()
        runner_tools = getattr(runner, "allowed_tools", None) if runner else None
        if stored is not None:
            embed.set_footer(text="Source: /tools-set override")
        elif runner_tools:
            embed.set_footer(text="Source: .env default (CLAUDE_ALLOWED_TOOLS)")
        else:
            embed.set_footer(text="No tool restrictions configured")

        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="tools-set", description="Change allowed tools via select menu")
    async def tools_set(self, interaction: discord.Interaction) -> None:
        """Show a multi-select menu to pick which tools to enable."""
        if self.settings_repo is None:
            await interaction.response.send_message(
                "❌ Settings repository is unavailable — tools cannot be persisted.",
                ephemeral=True,
            )
            return

        current_tools = await self._get_effective_tools()
        view = ToolSelectView(
            known_tools=KNOWN_TOOLS,
            current_tools=current_tools,
            settings_repo=self.settings_repo,
            setting_key=SETTING_ALLOWED_TOOLS,
        )
        await interaction.response.send_message(
            "Select the tools to allow:", view=view, ephemeral=True
        )

    @app_commands.command(name="tools-reset", description="Reset allowed tools to .env default")
    async def tools_reset(self, interaction: discord.Interaction) -> None:
        """Remove the settings_repo override, reverting to .env default."""
        if self.settings_repo is None:
            await interaction.response.send_message(
                "❌ Settings repository is unavailable.", ephemeral=True
            )
            return

        deleted = await self.settings_repo.delete(SETTING_ALLOWED_TOOLS)
        runner = self._get_runner()
        runner_tools = getattr(runner, "allowed_tools", None) if runner else None

        if deleted:
            if runner_tools:
                desc = "Reverted to `.env` default:\n" + ", ".join(f"`{t}`" for t in runner_tools)
            else:
                desc = "Reverted to `.env` default: **no restrictions**."
            embed = discord.Embed(title="🔧 Tools Reset", description=desc, color=COLOR_SUCCESS)
        else:
            embed = discord.Embed(
                title="🔧 Tools Reset",
                description="No override was set — already using defaults.",
                color=COLOR_INFO,
            )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="sync-settings",
        description="View or change session sync settings",
    )
    @app_commands.describe(
        thread_style="How synced sessions appear in Discord",
        since_hours="Sync sessions active within the last N hours (default: 24)",
        min_results="Minimum sessions to sync even if outside time window (default: 10)",
    )
    @app_commands.choices(thread_style=_STYLE_CHOICES)
    async def sync_settings(
        self,
        interaction: discord.Interaction,
        thread_style: str | None = None,
        since_hours: int | None = None,
        min_results: int | None = None,
    ) -> None:
        """View or change sync settings. Without arguments, shows current settings."""
        current_style = await self._get_thread_style()
        current_hours = await self._get_since_hours()
        current_min = await self._get_min_results()
        updated = False

        if thread_style is not None and thread_style in _VALID_THREAD_STYLES:
            if self.settings_repo is not None:
                await self.settings_repo.set(SETTING_SYNC_THREAD_STYLE, thread_style)
            current_style = thread_style
            updated = True

        if since_hours is not None and since_hours >= 0:
            if self.settings_repo is not None:
                await self.settings_repo.set(SETTING_SYNC_SINCE_HOURS, str(since_hours))
            current_hours = since_hours
            updated = True

        if min_results is not None and min_results >= 0:
            if self.settings_repo is not None:
                await self.settings_repo.set(SETTING_SYNC_MIN_RESULTS, str(min_results))
            current_min = min_results
            updated = True

        style_desc = {
            THREAD_STYLE_CHANNEL: (
                "\U0001f4c1 **Channel threads** — threads appear in the Threads panel, "
                "keeping the main channel clean"
            ),
            THREAD_STYLE_MESSAGE: (
                "\U0001f4ac **Message threads** — each session posts a summary card "
                "in the channel with a thread attached"
            ),
        }

        hours_desc = (
            f"\U0001f552 **{current_hours}h** — sessions active within the last "
            f"{current_hours} hour(s)"
            if current_hours > 0
            else "\U0001f552 **No time filter** — all sessions considered"
        )

        min_desc = (
            f"\U0001f4ca **{current_min}** — if fewer than {current_min} sessions "
            f"match the time filter, fill up to {current_min} from most recent"
            if current_min > 0
            else "\U0001f4ca **No minimum** — strict time filter only"
        )

        embed = discord.Embed(
            title="\u2699\ufe0f Sync Settings",
            description=(
                f"**Thread style**: {current_style}\n"
                f"{style_desc.get(current_style, '')}\n\n"
                f"**Since hours**: {current_hours}\n"
                f"{hours_desc}\n\n"
                f"**Min results**: {current_min}\n"
                f"{min_desc}"
            ),
            color=COLOR_SUCCESS if updated else COLOR_INFO,
        )
        if updated:
            embed.set_footer(text="Setting updated! New syncs will use this style.")

        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="resume",
        description="Resume a previous session in a new thread",
    )
    @app_commands.describe(
        query="Search sessions by keyword (matches summary and working directory)",
        filter="Filter sessions: all (default), orphaned (deleted threads only)",
    )
    @app_commands.choices(
        filter=[
            app_commands.Choice(name="All sessions", value="all"),
            app_commands.Choice(name="Orphaned (deleted threads)", value="orphaned"),
        ]
    )
    async def resume_session(
        self,
        interaction: discord.Interaction,
        query: str | None = None,
        filter: str | None = None,  # noqa: A002
    ) -> None:
        """Show a select menu of recent sessions to resume."""
        if query or filter:
            await interaction.response.defer(ephemeral=True)
            exclude_ids: list[int] | None = None

            if filter == "orphaned":
                all_records = await self.repo.list_all(limit=200)
                live_ids: list[int] = []
                for rec in all_records:
                    try:
                        ch = await self.bot.fetch_channel(rec.thread_id)
                        if ch is not None:
                            live_ids.append(rec.thread_id)
                    except Exception:  # noqa: BLE001
                        pass
                exclude_ids = live_ids if live_ids else None

            records = await self.repo.search(
                query=query or "",
                limit=25,
                exclude_thread_ids=exclude_ids,
            )

            if not records:
                await interaction.followup.send(
                    "No sessions found matching your search.",
                    ephemeral=True,
                )
                return

            view = ResumeSelectView(records=records, bot=self.bot)
            await interaction.followup.send(
                f"\U0001f504 **Resume** — {len(records)} session(s) found:",
                view=view,
                ephemeral=True,
            )
        else:
            records = await self.repo.list_all(limit=25)
            if not records:
                await interaction.response.send_message(
                    "No sessions found. Start a conversation first!",
                    ephemeral=True,
                )
                return

            view = ResumeSelectView(records=records, bot=self.bot)
            await interaction.response.send_message(
                "\U0001f504 **Resume** — select a session to continue:",
                view=view,
                ephemeral=True,
            )

    @app_commands.command(
        name="resume-info",
        description="Show the CLI command to resume this thread's session",
    )
    async def resume_info(self, interaction: discord.Interaction) -> None:
        """Show the claude --resume command for the current thread."""
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "This command can only be used in a Claude chat thread.",
                ephemeral=True,
            )
            return

        record = await self.repo.get(interaction.channel.id)
        if not record:
            await interaction.response.send_message(
                "No session found for this thread.",
                ephemeral=True,
            )
            return

        embed = discord.Embed(
            title="\U0001f517 Resume from CLI",
            description=(
                f"```\nclaude --resume {record.session_id}\n```\n"
                f"Run this command in your terminal to continue this session."
            ),
            color=COLOR_INFO,
        )
        if record.working_dir:
            embed.add_field(name="Working Directory", value=f"`{record.working_dir}`", inline=True)
        if record.model:
            embed.add_field(name="Model", value=record.model, inline=True)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="sessions",
        description="List all known Claude Code sessions",
    )
    @app_commands.describe(origin="Filter by session origin")
    @app_commands.choices(origin=_ORIGIN_CHOICES)
    async def sessions_list(
        self,
        interaction: discord.Interaction,
        origin: str | None = None,
    ) -> None:
        """List all sessions with origin, summary, and last activity."""
        # Convert "all" to None for the repository
        origin_filter = None if origin in (None, "all") else origin
        records = await self.repo.list_all(limit=25, origin=origin_filter)

        if not records:
            embed = discord.Embed(
                title="\U0001f4cb Sessions",
                description="No sessions found.",
                color=COLOR_INFO,
            )
            await interaction.response.send_message(embed=embed)
            return

        embed = discord.Embed(
            title=f"\U0001f4cb Sessions ({len(records)})",
            color=COLOR_INFO,
        )

        for record in records:
            icon = _ORIGIN_ICON.get(record.origin, "\u2753")
            summary = record.summary or "(no summary)"
            session_short = record.session_id[:8]

            name = f"{icon} {summary[:50]}"
            value = f"`{session_short}...` | {record.last_used_at}"
            if record.working_dir:
                # Show just the last directory component
                dir_short = record.working_dir.rsplit("/", 1)[-1]
                value += f" | `{dir_short}`"

            embed.add_field(name=name, value=value, inline=False)

        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="search",
        description="Find a past thread by keyword (add body:True to grep full conversations)",
    )
    @app_commands.describe(
        query="Keyword to look for",
        origin="Filter by session origin",
        body="Also grep the full conversation transcripts (token-free, a bit slower)",
    )
    @app_commands.choices(origin=_ORIGIN_CHOICES)
    async def search_command(
        self,
        interaction: discord.Interaction,
        query: str,
        origin: str | None = None,
        body: bool = False,
    ) -> None:
        """Search past threads by keyword and return clickable deep-links.

        By default matches the persistent per-thread summary (instant). With
        ``body:True`` it also greps the local Claude transcripts so keywords that
        only appear mid-conversation are found — still zero AI tokens.
        """
        query = (query or "").strip()
        if not query:
            await interaction.response.send_message(
                "❌ Enter a keyword to search for.", ephemeral=True
            )
            return

        origin_filter = None if origin in (None, "all") else origin
        transcripts_root = self.cli_sessions_path or default_transcripts_root()

        async def _run() -> list[ThreadSearchResult]:
            return await run_thread_search(
                session_repo=self.repo,
                query=query,
                origin=origin_filter,
                limit=_SEARCH_RESULT_LIMIT,
                include_body=body,
                transcripts_root=transcripts_root,
            )

        # Body search greps files on disk, which can exceed Discord's 3s ACK
        # window, so defer first. Summary-only search is instant.
        if body:
            await interaction.response.defer()
            results = await _run()
            embed = build_search_embed(query, results, guild_id=interaction.guild_id)
            await interaction.followup.send(embed=embed)
        else:
            results = await _run()
            embed = build_search_embed(query, results, guild_id=interaction.guild_id)
            await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="sync-sessions",
        description="Import CLI sessions from Claude Code as Discord threads",
    )
    async def sync_sessions(self, interaction: discord.Interaction) -> None:
        """Scan CLI session storage and create threads for unknown sessions."""
        if not self.cli_sessions_path:
            await interaction.response.send_message(
                "\u274c CLI sessions path is not configured. "
                "Set `cli_sessions_path` when initializing SessionManageCog.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        thread_style = await self._get_thread_style()
        since_hours = await self._get_since_hours()
        min_results = await self._get_min_results()

        raw_channel = self.bot.get_channel(self.bot.channel_id)

        if not isinstance(raw_channel, discord.TextChannel):
            logger.warning("Channel %d is not a TextChannel", self.bot.channel_id)
            await interaction.followup.send(
                embed=discord.Embed(
                    title="\U0001f504 Session Sync Complete",
                    description="Found **0** CLI session(s).\nChannel not available.",
                    color=COLOR_SUCCESS,
                )
            )
            return

        result = await sync_cli_sessions(
            cli_sessions_path=self.cli_sessions_path,
            channel=raw_channel,
            repo=self.repo,
            thread_style=thread_style,
            since_hours=since_hours,
            min_results=min_results,
        )

        embed = discord.Embed(
            title="\U0001f504 Session Sync Complete",
            description=(
                f"Found **{result.total_found}** CLI session(s).\n"
                f"\u2705 Imported: **{result.imported}**\n"
                f"\u23ed\ufe0f Already synced: **{result.skipped}**"
            ),
            color=COLOR_SUCCESS,
        )
        await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------------
    # Worktree commands
    # ------------------------------------------------------------------

    def _get_worktree_manager(self) -> WorktreeManager | None:
        """Return the WorktreeManager from the bot, if configured."""
        return getattr(self.bot, "worktree_manager", None)

    @app_commands.command(
        name="worktree-list",
        description="List all active Claude Code session worktrees",
    )
    async def worktree_list(self, interaction: discord.Interaction) -> None:
        """Show all session worktrees (branch ``session/\\d+``) and their status."""
        wm = self._get_worktree_manager()
        if wm is None:
            await interaction.response.send_message(
                "❌ Worktree manager is not configured.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        import asyncio

        worktrees = await asyncio.to_thread(wm.find_session_worktrees)

        if not worktrees:
            await interaction.followup.send(
                embed=discord.Embed(
                    title="🌲 Session Worktrees",
                    description="No session worktrees found.",
                    color=COLOR_INFO,
                )
            )
            return

        from ..worktree import _is_clean  # noqa: PLC0415

        embed = discord.Embed(
            title=f"🌲 Session Worktrees ({len(worktrees)})",
            color=COLOR_INFO,
        )
        for wt in worktrees:
            clean = await asyncio.to_thread(_is_clean, wt.path)
            status = "✅ clean" if clean else "⚠️ dirty"
            name = f"`wt-{wt.thread_id}`"
            value = f"Branch: `{wt.branch}`\nRepo: `{wt.main_repo or 'unknown'}`\nStatus: {status}"
            embed.add_field(name=name, value=value, inline=False)

        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="worktree-cleanup",
        description="Remove clean orphaned session worktrees",
    )
    @app_commands.describe(
        dry_run="Preview what would be removed without actually removing anything",
    )
    async def worktree_cleanup(
        self,
        interaction: discord.Interaction,
        dry_run: bool = False,
    ) -> None:
        """Remove session worktrees that have no active session and are clean."""
        wm = self._get_worktree_manager()
        if wm is None:
            await interaction.response.send_message(
                "❌ Worktree manager is not configured.", ephemeral=True
            )
            return

        await interaction.response.defer()

        import asyncio

        # Determine active thread IDs from the session registry
        active_ids: set[int] = set()
        if hasattr(self.bot, "session_registry"):
            active_ids = {s.thread_id for s in self.bot.session_registry.list_active()}

        if dry_run:
            # Just list what would be removed
            worktrees = await asyncio.to_thread(wm.find_session_worktrees)
            from ..worktree import _is_clean  # noqa: PLC0415

            candidates = []
            skipped = []
            for wt in worktrees:
                if wt.thread_id in active_ids:
                    skipped.append((wt, "session is active"))
                    continue
                clean = await asyncio.to_thread(_is_clean, wt.path)
                if clean:
                    candidates.append(wt)
                else:
                    skipped.append((wt, "dirty"))

            embed = discord.Embed(
                title="🌲 Worktree Cleanup — Dry Run",
                color=COLOR_INFO,
            )
            if candidates:
                embed.add_field(
                    name=f"Would remove ({len(candidates)})",
                    value="\n".join(f"`{wt.path}`" for wt in candidates) or "—",
                    inline=False,
                )
            if skipped:
                embed.add_field(
                    name=f"Would skip ({len(skipped)})",
                    value="\n".join(f"`{wt.path}` — {reason}" for wt, reason in skipped) or "—",
                    inline=False,
                )
            if not candidates and not skipped:
                embed.description = "No session worktrees found."
            embed.set_footer(text="Re-run without dry_run=True to actually remove.")
            await interaction.followup.send(embed=embed)
            return

        results = await asyncio.to_thread(wm.cleanup_orphaned, active_ids)

        removed = [r for r in results if r.removed]
        dirty = [r for r in results if not r.removed and "uncommitted changes" in r.reason]
        other_skipped = [
            r
            for r in results
            if not r.removed
            and "uncommitted changes" not in r.reason
            and r.reason != "session is still active"
        ]

        color = COLOR_SUCCESS if removed else COLOR_INFO
        if dirty:
            color = COLOR_TOOL

        embed = discord.Embed(
            title="🌲 Worktree Cleanup Complete",
            color=color,
        )
        embed.add_field(
            name=f"✅ Removed ({len(removed)})",
            value="\n".join(f"`{r.path}`" for r in removed) or "—",
            inline=False,
        )
        if dirty:
            embed.add_field(
                name=f"⚠️ Dirty — not removed ({len(dirty)})",
                value="\n".join(f"`{r.path}`" for r in dirty) or "—",
                inline=False,
            )
        if other_skipped:
            embed.add_field(
                name=f"ℹ️ Skipped ({len(other_skipped)})",
                value="\n".join(f"`{r.path}` — {r.reason}" for r in other_skipped) or "—",
                inline=False,
            )

        await interaction.followup.send(embed=embed)

    # ------------------------------------------------------------------
    # Context window commands
    # ------------------------------------------------------------------

    @app_commands.command(
        name="context",
        description="Show the context window usage for this thread's session",
    )
    async def context_show(self, interaction: discord.Interaction) -> None:
        """Display context window usage (%) with a progress bar and autocompact distance."""
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "This command can only be used in a Claude chat thread.", ephemeral=True
            )
            return

        record = await self.repo.get(interaction.channel.id)
        if record is None or record.context_window is None or record.context_used is None:
            await interaction.response.send_message(
                "ℹ️ No context data yet — stats are recorded after the first session completes.",
                ephemeral=True,
            )
            return

        ratio = record.context_used / record.context_window
        pct = round(ratio * 100)
        bar = _progress_bar(ratio)
        autocompact_tokens = round(_AUTOCOMPACT_THRESHOLD * record.context_window)
        distance_to_compact = max(0, autocompact_tokens - record.context_used)

        warning = ratio >= _AUTOCOMPACT_THRESHOLD
        color = COLOR_ERROR if warning else COLOR_INFO

        lines = [
            f"`{bar}`  **{pct}%**  ({record.context_used:,} / {record.context_window:,} tokens)",
            "",
            f"⚡ autocompact threshold: {round(_AUTOCOMPACT_THRESHOLD * 100, 1)}%"
            f" ({distance_to_compact:,} tokens away)",
        ]
        if warning:
            lines.append("")
            lines.append("⚠️ Above autocompact threshold — auto-compact may run on next turn")

        lines.append("")
        lines.append("💡 Use `/rewind` to recover context headroom")

        embed = discord.Embed(
            title=f"📊 Context Window — #{interaction.channel.name}",
            description="\n".join(lines),
            color=color,
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="usage",
        description="Show Claude Code rate limit usage and weekly activity",
    )
    async def usage_show(self, interaction: discord.Interaction) -> None:
        """Display rate limit utilization (5-hour / 7-day) with reset countdown."""
        if self.account_router is not None:
            await self._usage_show_pools(interaction)
            return
        if self.usage_repo is None:
            await interaction.response.send_message(
                "ℹ️ Usage tracking is not enabled for this bot instance.", ephemeral=True
            )
            return

        rows = await self.usage_repo.get_latest()
        if not rows:
            await interaction.response.send_message(
                "ℹ️ No usage data yet — stats are recorded after the first session completes.",
                ephemeral=True,
            )
            return

        lines: list[str] = []
        has_warning = any(r.utilization >= 0.8 for r in rows)

        type_label = {
            "five_hour": "⚡ 5-hour window",
            "seven_day": "📅 7-day window",
            "seven_day_sonnet": "📅 7-day (Sonnet)",
            "seven_day_opus": "📅 7-day (Opus)",
        }

        for row in rows:
            label = type_label.get(row.rate_limit_type, f"📊 {row.rate_limit_type}")
            bar = _progress_bar(row.utilization)
            pct = round(row.utilization * 100)
            countdown = _format_countdown(row.resets_at)
            warn = " ⚠️" if row.utilization >= 0.8 else ""
            lines.append(f"**{label}**{warn}")
            lines.append(f"`{bar}`  **{pct}%**  — {countdown}")
            lines.append("")

        embed = discord.Embed(
            title="📊 Claude Code Usage",
            description="\n".join(lines).rstrip(),
            color=COLOR_ERROR if has_warning else COLOR_INFO,
        )
        await interaction.response.send_message(embed=embed)

    async def _usage_show_pools(self, interaction: discord.Interaction) -> None:
        """/usage with account pools: every profile, its windows and availability."""
        from ..account_turns import usage_lines

        assert self.account_router is not None
        statuses = await self.account_router.statuses()
        any_exhausted = any(s.unavailable_until is not None for s in statuses)
        embed = discord.Embed(
            title="📊 Account Pool Usage",
            description="\n".join(usage_lines(statuses)).rstrip()[:4000],
            color=COLOR_ERROR if any_exhausted else COLOR_INFO,
        )
        await interaction.response.send_message(embed=embed)
