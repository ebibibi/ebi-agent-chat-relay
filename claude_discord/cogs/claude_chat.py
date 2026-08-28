"""Claude Code chat Cog.

Handles the core message flow:
1. User sends message in the configured channel
2. Bot creates a thread (or continues in existing thread)
3. Claude Code CLI is invoked with stream-json output
4. Status reactions and tool embeds are posted in real-time
5. Final response is posted to the thread
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import tempfile
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from claude_code_core.backend import SessionBackend

from ..backend_factory import BackendFactory
from ..backend_settings import BackendSettings, session_is_resumable
from ..claude.rewind import find_session_jsonl, parse_user_turns
from ..claude.types import ImageData
from ..concurrency import SessionRegistry
from ..cross_backend_handoff import ConversationHistoryReader, build_handoff_prompt
from ..database.ask_repo import PendingAskRepository
from ..database.lounge_repo import LoungeRepository
from ..database.repository import SessionRecord, SessionRepository
from ..database.resume_repo import PendingResumeRepository
from ..database.settings_repo import SettingsRepository
from ..discord_ui.chunker import chunk_message
from ..discord_ui.embeds import stopped_embed
from ..discord_ui.file_sender import send_file_blobs
from ..discord_ui.status import StatusManager
from ..discord_ui.thread_context import DEFAULT_DAYS, build_recent_transcript
from ..discord_ui.thread_dashboard import ThreadState, ThreadStatusDashboard
from ..discord_ui.thread_renamer import suggest_title
from ..discord_ui.views import RewindSelectView, StopView
from ..thread_policy import THREAD_AUTO_ARCHIVE_MINUTES
from ._run_helper import run_claude_with_config
from .prompt_builder import build_prompt_and_images, wants_file_attachment
from .run_config import RunConfig

if TYPE_CHECKING:
    from ..bot import ClaudeDiscordBot

logger = logging.getLogger(__name__)

# How many messages to scan when recovering an ``auto_start=false`` seed.
# A seed is chunked at Discord's per-message limit, so this bounds the seed at
# roughly 40 x 2,000 characters — far more than any real prompt, while still
# refusing to walk an entire thread's history on a malformed one.
SEED_CONTEXT_MESSAGE_LIMIT = 40

# ---------------------------------------------------------------------------
# /help command metadata
#
# _HELP_CATEGORY maps every slash-command name to its display section.
# Use None to exclude a command from the embed (e.g. "help" itself).
# Commands missing from this dict fall through to "🔧 Advanced" at runtime,
# but the test_help_sync.py CI test will fail — forcing explicit categorisation.
# ---------------------------------------------------------------------------
_HELP_CATEGORY: dict[str, str | None] = {
    "help": None,  # the help command doesn't list itself
    "stop": "📌 Session",
    "clear": "📌 Session",
    "rewind": "📌 Session",
    "compact": "📌 Session",
    "goal": "📌 Session",
    "fork": "📌 Session",
    "context": "📌 Session",
    "usage": "📌 Session",
    "sessions": "📌 Session",
    "search": "📌 Session",
    "resume": "📌 Session",
    "resume-info": "📌 Session",
    "sync-sessions": "📌 Session",
    "sync-settings": "📌 Session",
    "model": "🤖 Model",
    "backend": "🤖 Model",
    "engine-status": "🤖 Model",
    "ollama": "🤖 Model",  # manage the runtime behind the `local` backend
    "ask": "🤖 Model",  # one anonymized question to an external model
    "effort": "⚡ Effort",
    "tools-show": "🔧 Advanced",
    "tools-set": "🔧 Advanced",
    "tools-reset": "🔧 Advanced",
    "skill": "🔧 Advanced",
    "worktree-list": "🔧 Advanced",
    "worktree-cleanup": "🔧 Advanced",
    "upgrade": "🔧 Advanced",
}

# Section display order in the embed.
_HELP_SECTION_ORDER: list[str] = ["📌 Session", "🤖 Model", "⚡ Effort", "🔧 Advanced"]


class ClaudeChatCog(commands.Cog):
    """Cog that handles Claude Code conversations via Discord threads."""

    def __init__(
        self,
        bot: ClaudeDiscordBot,
        repo: SessionRepository,
        runner: SessionBackend,
        max_concurrent: int = 3,
        allowed_user_ids: set[int] | None = None,
        registry: SessionRegistry | None = None,
        dashboard: ThreadStatusDashboard | None = None,
        ask_repo: PendingAskRepository | None = None,
        lounge_repo: LoungeRepository | None = None,
        resume_repo: PendingResumeRepository | None = None,
        settings_repo: SettingsRepository | None = None,
        channel_ids: set[int] | None = None,
        mention_only_channel_ids: set[int] | None = None,
        inline_reply_channel_ids: set[int] | None = None,
        chat_only_channel_ids: set[int] | None = None,
        auto_rename_threads: bool = False,
        monitor_all_channels: bool = False,
        mention_anywhere: bool = True,
        thread_context_days: int = DEFAULT_DAYS,
        factory: BackendFactory | None = None,
        backend_settings: BackendSettings | None = None,
        conversation_history: ConversationHistoryReader | None = None,
    ) -> None:
        self.bot = bot
        self.repo = repo
        self.runner = runner
        # Optional backend factory + settings: when both are present,
        # session spawns consult them to honour per-thread /backend overrides.
        # When either is None, we fall back to self.runner.clone() (legacy).
        self._factory = factory
        self._backend_settings = backend_settings
        self._conversation_history = conversation_history or ConversationHistoryReader()
        self._max_concurrent = max_concurrent
        self._allowed_user_ids = allowed_user_ids
        # When True, skip channel-ID filtering and accept all guild channels.
        self._monitor_all_channels = monitor_all_channels
        # Set of channel IDs to listen on.  When provided, overrides bot.channel_id.
        # Falls back to {bot.channel_id} for backward compatibility.
        if channel_ids is not None:
            self._channel_ids = channel_ids
        else:
            bid = getattr(bot, "channel_id", None)
            self._channel_ids: set[int] = {bid} if bid else set()
        # Channels carved out of the no-mention scope above (legacy knob: with
        # mention_anywhere on, simply *not listing* a channel has the same effect).
        self._mention_only_channel_ids: set[int] = mention_only_channel_ids or set()
        # When True, an @mention reaches the bot in any guild channel or thread,
        # not just the configured ones.  This is what makes channel_ids a list of
        # places that need no mention rather than a list of places ccdb exists in.
        self._mention_anywhere = mention_anywhere
        # How many days of a foreign thread's history to feed Claude when a
        # mention wakes it there.  0 disables the transcript.
        self._thread_context_days = thread_context_days
        # Channels where the bot replies directly (no thread created).
        self._inline_reply_channel_ids: set[int] = inline_reply_channel_ids or set()
        # Channels where only text responses are shown (no tool embeds, thinking, etc.).
        self._chat_only_channel_ids: set[int] = chat_only_channel_ids or set()
        self._registry = registry or getattr(bot, "session_registry", None)
        self._active_runners: dict[int, SessionBackend] = {}
        # Tracks the asyncio.Task running _run_claude for each thread.
        # Used by _handle_thread_reply to wait for an interrupted session
        # to fully clean up before starting the replacement session.
        self._active_tasks: dict[int, asyncio.Task] = {}
        self._thread_locks: dict[int, asyncio.Lock] = {}
        # Dashboard may be None until bot is ready; resolved lazily in _get_dashboard()
        self._dashboard = dashboard
        # For AskUserQuestion persistence across restarts
        self._ask_repo = ask_repo or getattr(bot, "ask_repo", None)
        # AI Lounge repo (optional — lounge disabled when None)
        self._lounge_repo = lounge_repo or getattr(bot, "lounge_repo", None)
        # Pending resume repo (optional — startup resume disabled when None)
        self._resume_repo = resume_repo or getattr(bot, "resume_repo", None)
        # Settings repo for dynamic model lookup (optional — falls back to runner.model)
        self._settings_repo = settings_repo or getattr(bot, "settings_repo", None)
        # When True, rename the thread after creation using a claude -p title suggestion
        self._auto_rename_threads = auto_rename_threads

    @property
    def active_session_count(self) -> int:
        """Number of Claude sessions currently running in this cog."""
        return len(self._active_runners)

    @property
    def active_count(self) -> int:
        """Alias for active_session_count (satisfies DrainAware protocol)."""
        return self.active_session_count

    def _get_dashboard(self) -> ThreadStatusDashboard | None:
        """Return the dashboard, resolving it from the bot if not yet set."""
        if self._dashboard is None:
            self._dashboard = getattr(self.bot, "thread_dashboard", None)
        return self._dashboard

    async def _get_current_model(self) -> str | None:
        """Return the model override from settings_repo, or None to use runner default.

        When /model set has been used to change the global model, this returns
        the stored value. Returns None if no override is set or settings_repo
        is unavailable.
        """
        if self._settings_repo is None:
            return None
        from .session_manage import SETTING_CLAUDE_MODEL

        return await self._settings_repo.get(SETTING_CLAUDE_MODEL)

    async def _get_current_effort(self) -> str | None:
        """Return the effort override from settings_repo, or None to use runner default."""
        if self._settings_repo is None:
            return None
        from .session_manage import SETTING_CLAUDE_EFFORT

        return await self._settings_repo.get(SETTING_CLAUDE_EFFORT)

    async def _get_allowed_tools(self) -> list[str] | None:
        """Return the tool override from settings_repo, or None to use runner default.

        When /tools-set has been used to change the allowed tools, this returns
        the parsed list.  Returns None if no override is set or settings_repo
        is unavailable (meaning: inherit from the base runner).
        """
        if self._settings_repo is None:
            return None
        from .session_manage import SETTING_ALLOWED_TOOLS

        stored = await self._settings_repo.get(SETTING_ALLOWED_TOOLS)
        if stored is None:
            return None
        return [t.strip() for t in stored.split(",") if t.strip()]

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Handle incoming messages."""
        # Ignore bot messages
        if message.author.bot:
            return

        # Ignore Discord system messages (thread renames, pins, call events, etc.)
        # Only MessageType.default and MessageType.reply are genuine user text.
        if message.type not in (discord.MessageType.default, discord.MessageType.reply):
            return

        # Authorization check — if allowed_user_ids is set, only those users
        # can invoke Claude.  When unset, channel-level Discord permissions
        # are the only gate (suitable for private servers).
        if self._allowed_user_ids is not None and message.author.id not in self._allowed_user_ids:
            return

        # Inside a no-mention channel (or a thread under it) everything is for
        # Claude, and the session model applies: a channel message opens a
        # thread, a thread message continues that thread's session.
        if self._is_no_mention_scope(message.channel):
            if isinstance(message.channel, discord.Thread):
                await self._handle_thread_reply(message)
            else:
                await self._handle_new_conversation(message)
            return

        # Everywhere else: answer only when summoned, and answer *there*.
        if self._is_summoned(message):
            await self._handle_mention(message)

    def _is_no_mention_scope(self, channel: discord.abc.MessageableChannel) -> bool:
        """Return whether *channel* is one ccdb was invited to speak in freely.

        A "no-mention" scope is a configured channel (or any thread under it):
        everything posted there is for Claude, so no @mention is required.
        ``monitor_all_channels`` widens this to the whole guild; a channel
        listed in ``mention_only_channel_ids`` is carved back out of it.
        """
        root = channel.parent_id if isinstance(channel, discord.Thread) else channel.id
        if root in self._mention_only_channel_ids:
            return False
        if root in self._channel_ids:
            return True
        return self._monitor_all_channels and getattr(channel, "guild", None) is not None

    def _is_summoned(self, message: discord.Message) -> bool:
        """Return whether this message explicitly @mentions the bot.

        Outside the no-mention channels this is the *only* way in — including in
        threads ccdb created itself.  Owning a thread is not standing consent:
        people keep talking in these threads to each other, and a run they did
        not ask for is noise at best.  Every run out here is summoned by name.
        """
        if not self._mention_anywhere:
            return False
        # A mention only carries a channel policy inside a guild — stay out of DMs.
        if getattr(message, "guild", None) is None:
            return False
        return self.bot.user is not None and self.bot.user in message.mentions

    async def _build_runner_for_thread(
        self,
        *,
        thread_id: int,
        model_override: str | None,
        tools_override: list[str] | None,
        fork_session: bool,
        working_dir_override: str | None,
        effort_override: str | None,
    ) -> SessionBackend:
        """Build a runner for a session, honouring per-thread backend/model overrides.

        When the cog has a BackendFactory + BackendSettings (the 3.x path), the
        backend and model are resolved from BackendSettings — thread > global >
        env. Per-call overrides (model_override, tools_override, ...) still win
        over stored settings.

        When either dependency is missing (legacy embedded setups, tests), we
        fall back to ``self.runner.clone()`` so existing behaviour is preserved.
        """
        from ..claude.runner import _UNSET

        if self._factory is None or self._backend_settings is None:
            return self.runner.clone(
                thread_id=thread_id,
                model=model_override,
                allowed_tools=tools_override if tools_override is not None else _UNSET,
                fork_session=fork_session,
                working_dir=working_dir_override if working_dir_override is not None else _UNSET,
                effort=effort_override if effort_override is not None else _UNSET,
            )

        backend = await self._backend_settings.current_backend(thread_id)

        # Model resolution order:
        # 1. Explicit /model command value for THIS backend (thread > global).
        #    Env fallback is NOT considered yet — see step 3.
        # 2. Legacy ``claude_model`` setting value (passed in as
        #    ``model_override``) — left over from the removed /model-set command.
        #    Honoured only when the current backend is claude. Passing a
        #    Claude model id to Codex would cause `codex exec` to fail
        #    with an unknown model error.
        # 3. Env-derived per-backend default, then the factory's backend default
        #    (sonnet for claude, CLI default for codex). Returned as None here
        #    so factory.build() picks the right one.
        explicit_model = await self._backend_settings.explicit_model(backend, thread_id)
        model: str | None
        if explicit_model:
            model = explicit_model
        elif model_override and backend == "claude":
            model = model_override
        else:
            # Fall back to env-set default (current_model() exposes it),
            # or None for the factory to choose.
            model = await self._backend_settings.current_model(backend, thread_id)

        runner = self._factory.build(
            backend=backend,
            model=model,
            thread_id=thread_id,
        )

        # Apply per-call overrides that the factory does not know about.
        # Both ClaudeRunner and CodexRunner allow attribute assignment for
        # these fields; effort/fork are gated by hasattr.
        if tools_override is not None:
            runner.allowed_tools = tools_override
        if working_dir_override is not None:
            runner.working_dir = working_dir_override

        # Effort resolution is per-backend:
        #   1. BackendSettings effort for THIS backend (thread > global) — set
        #      via the backend-aware /effort command. Works for both backends.
        #   2. Legacy ``claude_effort`` setting value (``effort_override``),
        #      left over from the removed /effort-set command — Claude only;
        #      Codex effort levels differ ("max" is not a Codex level).
        effective_effort = await self._backend_settings.current_effort(backend, thread_id)
        if effective_effort is None and backend == "claude":
            effective_effort = effort_override
        if effective_effort is not None and hasattr(runner, "effort"):
            runner.effort = effective_effort  # type: ignore[attr-defined]

        if fork_session and hasattr(runner, "fork_session"):
            runner.fork_session = True  # type: ignore[attr-defined]

        return runner

    @app_commands.command(name="help", description="Show available commands and how to use the bot")
    async def help_command(self, interaction: discord.Interaction) -> None:
        """Display a categorised embed of all slash commands.

        Command names and descriptions are read dynamically from the live
        command tree so they can never drift from the actual definitions.
        Category assignments live in _HELP_CATEGORY; CI (test_help_sync.py)
        ensures every registered command is listed there.
        """
        sections: dict[str, list[str]] = {s: [] for s in _HELP_SECTION_ORDER}

        for cmd in sorted(interaction.client.tree.get_commands(), key=lambda c: c.name):  # type: ignore[attr-defined]
            section = _HELP_CATEGORY.get(cmd.name, "🔧 Advanced")
            if section is None:
                continue  # excluded (e.g. the help command itself)
            sections.setdefault(section, []).append(f"`/{cmd.name}` — {cmd.description}")

        embed = discord.Embed(
            title="🤖 Claude & Codex Bot — Help",
            description=(
                "**Getting started**: type a message in the configured channel.\n"
                "A new thread is created and your selected AI backend begins working.\n\n"
                "**In a thread**: reply to continue the conversation, "
                "or use the slash commands below."
            ),
            color=0x5865F2,  # Discord blurple
        )
        for section_name in _HELP_SECTION_ORDER:
            lines = sections.get(section_name, [])
            if lines:
                embed.add_field(name=section_name, value="\n".join(lines), inline=False)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="stop", description="Stop the active session (session is preserved)")
    async def stop_session(self, interaction: discord.Interaction) -> None:
        """Stop the active Claude run without clearing the session.

        Unlike /clear, this preserves the session ID so the user can
        resume by sending a new message.
        """
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "This command can only be used in a Claude chat thread.", ephemeral=True
            )
            return

        runner = self._active_runners.get(interaction.channel.id)
        if not runner:
            await interaction.response.send_message(
                "No active session is running in this thread.", ephemeral=True
            )
            return

        await runner.interrupt()
        # _active_runners cleanup is handled by _run_claude's finally block.
        # We intentionally do NOT delete from the session DB so the user can resume.
        await interaction.response.send_message(embed=stopped_embed())

    @app_commands.command(
        name="compact",
        description="Manually compact (summarize) the conversation to free context space",
    )
    async def compact_session(self, interaction: discord.Interaction) -> None:
        """Trigger manual context compaction via the CLI's /compact command."""
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "This command can only be used in a Claude chat thread.", ephemeral=True
            )
            return

        thread_id = interaction.channel.id
        record = await self.repo.get(thread_id)
        if record is None:
            await interaction.response.send_message(
                "No active session found for this thread.", ephemeral=True
            )
            return

        if thread_id in self._active_runners:
            await interaction.response.send_message(
                "A session is currently running. Wait for it to finish before compacting.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        seed_message = await interaction.followup.send("🗜️ Compacting conversation...", wait=True)

        await self._run_claude(
            user_message=seed_message,
            thread=interaction.channel,
            prompt="/compact",
            session_id=record.session_id,
            working_dir_override=record.working_dir,
            chat_only=True,
        )

    @app_commands.command(
        name="goal",
        description="Set a completion condition — Claude keeps working until it's met",
    )
    @app_commands.describe(condition="Goal condition (omit to check status, 'clear' to cancel)")
    async def goal_session(
        self,
        interaction: discord.Interaction,
        condition: str | None = None,
    ) -> None:
        """Set, check, or clear a goal via the CLI's /goal command."""
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "This command can only be used in a Claude chat thread.", ephemeral=True
            )
            return

        thread_id = interaction.channel.id
        record = await self.repo.get(thread_id)
        if record is None:
            await interaction.response.send_message(
                "No active session found for this thread.", ephemeral=True
            )
            return

        if thread_id in self._active_runners:
            await interaction.response.send_message(
                "A session is currently running. Use /stop to interrupt it first.",
                ephemeral=True,
            )
            return

        prompt = f"/goal {condition}" if condition else "/goal"

        await interaction.response.defer()

        if condition and condition.strip().lower() not in (
            "clear",
            "stop",
            "off",
            "reset",
            "none",
            "cancel",
        ):
            label = f"◎ Setting goal: {condition[:80]}"
        elif not condition:
            label = "◎ Checking goal status..."
        else:
            label = "◎ Clearing goal..."

        seed_message = await interaction.followup.send(label, wait=True)

        await self._run_claude(
            user_message=seed_message,
            thread=interaction.channel,
            prompt=prompt,
            session_id=record.session_id,
            working_dir_override=record.working_dir,
        )

    @app_commands.command(name="clear", description="Reset the Claude Code session for this thread")
    async def clear_session(self, interaction: discord.Interaction) -> None:
        """Reset the session for the current thread."""
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "This command can only be used in a Claude chat thread.", ephemeral=True
            )
            return

        # Kill active runner if any
        runner = self._active_runners.get(interaction.channel.id)
        if runner:
            await runner.kill()
            del self._active_runners[interaction.channel.id]

        deleted = await self.repo.delete(interaction.channel.id)
        if deleted:
            await interaction.response.send_message(
                "\U0001f504 Session cleared. Next message will start a fresh session."
            )
        else:
            await interaction.response.send_message(
                "No active session found for this thread.", ephemeral=True
            )

    @app_commands.command(
        name="rewind",
        description="Go back to an earlier point in the conversation",
    )
    async def rewind_session(self, interaction: discord.Interaction) -> None:
        """Rewind the conversation to a selected earlier turn.

        Reads the session JSONL history, shows a select menu of past user
        messages, and truncates the JSONL at the chosen point so that
        ``--resume`` picks up from just before that message.

        Unlike /clear, the session record is **kept** — only the JSONL is
        trimmed — so you can continue the conversation from the rewound state
        rather than starting a completely fresh session.

        Working files created by Claude are always preserved.
        """
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "This command can only be used in a Claude chat thread.", ephemeral=True
            )
            return

        thread_id = interaction.channel.id
        record = await self.repo.get(thread_id)
        if record is None:
            await interaction.response.send_message(
                "No active session found for this thread.", ephemeral=True
            )
            return

        # Locate the JSONL and parse user turns.
        jsonl_path = find_session_jsonl(record.session_id, record.working_dir)
        turns = parse_user_turns(jsonl_path) if jsonl_path is not None else []

        if not turns:
            # No history to rewind through — fall back to a full reset (same as /clear).
            runner = self._active_runners.pop(thread_id, None)
            if runner:
                await runner.kill()
            await self.repo.delete(thread_id)
            await interaction.response.send_message(
                "⏪ No conversation history found to rewind. "
                "Session has been reset — send a new message to start fresh."
            )
            return

        # Show the turn-selection menu.  The runner will be stopped inside the
        # view callback once the user confirms a specific turn to rewind to.
        ctx_note = ""
        if record.context_window and record.context_used is not None:
            pct = round(record.context_used / record.context_window * 100)
            ctx_note = f" (context {pct}% full)"

        assert jsonl_path is not None  # guaranteed: turns is non-empty here
        view = RewindSelectView(
            turns=turns,
            jsonl_path=jsonl_path,
            active_runners=self._active_runners,
            thread_id=thread_id,
        )
        await interaction.response.send_message(
            f"⏪ **Rewind**{ctx_note} — select a turn to go back to before:",
            view=view,
        )

    @app_commands.command(
        name="fork",
        description="Branch this conversation into a new thread",
    )
    async def fork_session(self, interaction: discord.Interaction) -> None:
        """Create a new thread that continues this conversation from the current point.

        The new thread starts a fresh Claude process that resumes the **same session**
        via ``--resume``, giving you a copy of the conversation history so you can
        explore a different direction without affecting the original thread.

        Useful when you want to try an alternative approach while keeping the current
        thread intact.
        """
        if not isinstance(interaction.channel, discord.Thread):
            await interaction.response.send_message(
                "This command can only be used in a Claude chat thread.", ephemeral=True
            )
            return

        record = await self.repo.get(interaction.channel.id)
        if record is None:
            await interaction.response.send_message(
                "No active session found for this thread. "
                "Start a conversation first, then use /fork to branch it.",
                ephemeral=True,
            )
            return

        parent_channel = getattr(interaction.channel, "parent", None)
        if not isinstance(parent_channel, discord.TextChannel):
            await interaction.response.send_message(
                "Cannot create a fork: unable to find the parent channel.", ephemeral=True
            )
            return

        # Defer so we have time to create the thread before Discord's 3-second limit.
        await interaction.response.defer(ephemeral=False)

        fork_name = f"🔀 Fork of {interaction.channel.name}"[:100]
        new_thread = await self.spawn_session(
            channel=parent_channel,
            prompt=(
                "This thread is a fork of the previous conversation. "
                "Continue from where we left off."
            ),
            thread_name=fork_name,
            session_id=record.session_id,
            fork=True,
        )

        await interaction.followup.send(
            f"🔀 Forked! Continue in {new_thread.mention} — this thread is unchanged."
        )

    async def _handle_mention(self, message: discord.Message) -> None:
        """Answer an @mention **in the place it was written** — never in a new thread.

        This is deliberately not the session flow.  A mention outside the
        no-mention channels is someone in a conversation turning to Claude for
        an answer, so spinning off a thread would move the answer away from the
        discussion that prompted it and leave a session running somewhere nobody
        is reading.  Instead ccdb reads the recent history of that exact channel
        or thread, answers there, and goes quiet again until the next mention.
        """
        channel = message.channel
        if not isinstance(channel, (discord.Thread, discord.TextChannel)):
            return  # voice/DM/forum-root: nothing sensible to reply into
        prompt, images = await self._build_prompt_and_images(message)

        if self._thread_context_days > 0:
            transcript = await build_recent_transcript(
                channel,
                days=self._thread_context_days,
                exclude_message_id=message.id,
            )
            if transcript:
                prompt = f"{transcript}\n\n---\n\n{prompt}"

        # Nothing to send — ignore silently (e.g. unsupported attachment only).
        if not prompt and not images:
            return

        # Resume whatever session already belongs to this place, so a follow-up
        # mention continues the same work rather than starting from scratch.
        record = await self.repo.get(channel.id)
        session_id = record.session_id if record else None
        if record is not None and session_id:
            session_id = await self._session_id_for_current_backend(channel, record)

        root_id = channel.parent_id if isinstance(channel, discord.Thread) else channel.id
        await self._run_claude(
            message,
            channel,
            prompt,
            session_id=session_id,
            images=images,
            working_dir_override=record.working_dir if record else None,
            chat_only=(root_id or 0) in self._chat_only_channel_ids,
            interrupt_existing=True,
        )

    async def _handle_new_conversation(self, message: discord.Message) -> None:
        """Start a Claude Code session, creating a thread unless inline-reply mode is active."""
        prompt, images = await self._build_prompt_and_images(message)
        chat_only = message.channel.id in self._chat_only_channel_ids
        if (
            isinstance(message.channel, discord.TextChannel)
            and message.channel.id in self._inline_reply_channel_ids
        ):
            # Inline-reply mode: respond directly in the channel without creating
            # a thread. Every message in such a channel lands here, so resume the
            # session stored against the channel id — otherwise the conversation
            # would restart cold on each message, which threads never do (see
            # _handle_thread_reply). The channel is the conversation; /clear ends it.
            record = await self.repo.get(message.channel.id)
            await self._run_claude(
                message,
                message.channel,
                prompt,
                session_id=record.session_id if record else None,
                images=images,
                chat_only=chat_only,
            )
        else:
            thread_name = message.content[:100] if message.content else "Claude Chat"
            thread = await message.create_thread(
                name=thread_name,
                auto_archive_duration=THREAD_AUTO_ARCHIVE_MINUTES,
            )
            if self._auto_rename_threads and message.content:
                asyncio.create_task(self._background_rename_thread(thread, message.content))
            await self._run_claude(
                message,
                thread,
                prompt,
                session_id=None,
                images=images,
                chat_only=chat_only,
            )

    async def _background_rename_thread(
        self,
        thread: discord.Thread,
        user_message: str,
    ) -> None:
        """Rename *thread* to a Claude-generated title based on the first user message.

        Runs as a background asyncio task so it does not block the main session.
        Silently no-ops on any error so the thread name is never left in a bad state.
        """
        title = await suggest_title(
            user_message,
            claude_command=self.runner.command,
            env=self.runner._build_env(),
        )
        if title:
            try:
                await thread.edit(name=title)
                logger.debug("thread %d renamed to %r", thread.id, title)
            except Exception:
                logger.warning("Failed to rename thread %d to %r", thread.id, title, exc_info=True)

    async def spawn_session(
        self,
        channel: discord.TextChannel,
        prompt: str,
        thread_name: str | None = None,
        session_id: str | None = None,
        fork: bool = False,
        auto_start: bool = True,
        result_sink: Callable[[str | None, str | None], Awaitable[None]] | None = None,
        attachments: list[tuple[str, bytes]] | None = None,
        invite_user_id: int | None = None,
    ) -> discord.Thread:
        """Create a new thread and optionally start a Claude Code session.

        This is the API-initiated equivalent of ``_handle_new_conversation``.
        It bypasses the ``on_message`` bot-author guard, enabling programmatic
        spawning of Claude sessions (e.g. from ``POST /api/spawn``).

        A seed message is posted inside the new thread so that ``StatusManager``
        has a concrete ``discord.Message`` to attach reaction-emoji status to.

        Args:
            channel: The parent text channel in which to create the thread.
            prompt: The instruction to send to Claude Code.
            thread_name: Optional thread title; defaults to the first 100 chars
                of *prompt*.
            session_id: Optional Claude session ID to resume via ``--resume``.
                        When supplied the new Claude process continues the
                        previous conversation rather than starting fresh.
            auto_start: Whether to immediately start a Claude Code session.
                        When ``False``, only the thread and seed message are
                        created — a Claude session will start when a user
                        replies in the thread.  Defaults to ``True``.
            result_sink: Optional callback invoked once with
                        ``(final_assistant_text, error)`` when the spawned
                        session reaches its terminal state. Lets an external
                        caller (e.g. /api/ingest) retrieve the final reply.
                        Only wired when ``auto_start`` is True (otherwise no
                        session runs and no result would ever be produced).
            attachments: Optional ``(filename, bytes)`` pairs to post into the
                        new thread as Discord file attachments, right after the
                        seed prompt. Lets a programmatic caller (e.g. a Forgejo
                        Issue watcher via ``/api/spawn``) surface the original
                        attachments so they're viewable in the thread.
            invite_user_id: Optional Discord user to add as a thread member, so
                        a thread nobody was watching still lands in their joined
                        list. Best-effort: a failure here is a visibility miss,
                        never a reason to fail a spawn that already succeeded.

        Returns:
            The newly created :class:`discord.Thread`.
        """
        name = (thread_name or prompt)[:100]
        thread = await channel.create_thread(
            name=name,
            type=discord.ChannelType.public_thread,
            auto_archive_duration=THREAD_AUTO_ARCHIVE_MINUTES,
        )
        # Added before the seed message so the requester sees the thread from its
        # first line, not after Claude has already been talking to itself.
        if invite_user_id:
            with contextlib.suppress(Exception):
                await thread.add_user(discord.Object(id=invite_user_id))
        # Post the prompt so StatusManager has a Message to add reactions to.
        # Long prompts (e.g. an ingested Teams thread) exceed Discord's
        # per-message limit, so chunk the seed for display. The full prompt is
        # still passed to _run_claude below (the CLI has no such limit), so
        # chunking only affects what's shown in the thread, never what Claude
        # receives. The last chunk becomes the status-reaction anchor.
        chunks = chunk_message(prompt) or [prompt]
        seed_message = await thread.send(chunks[0])
        for chunk in chunks[1:]:
            seed_message = await thread.send(chunk)
        # Surface any caller-provided attachments in the thread so they're
        # viewable alongside the prompt (e.g. files attached to a Forgejo Issue).
        if attachments:
            await send_file_blobs(thread, attachments)
        if auto_start:
            # Run Claude in the background so /api/spawn returns immediately.
            # The caller gets the thread reference without waiting for Claude to finish.
            asyncio.create_task(
                self._run_claude(
                    seed_message,
                    thread,
                    prompt,
                    session_id=session_id,
                    fork=fork,
                    result_sink=result_sink,
                )
            )
        return thread

    async def deliver_relayed_message(
        self,
        thread: discord.Thread,
        text: str,
        *,
        interrupt: bool,
    ) -> None:
        """Feed a message from another session into this thread's Claude session.

        The API-initiated equivalent of a human reply, and the sibling of
        ``spawn_session``: ``on_message`` drops anything a bot wrote, so a
        relayed message would never reach Claude through the normal path.

        The text is posted into the thread first, so the humans watching see the
        AI-to-AI exchange — a relay must never become a back channel.

        Args:
            thread: The receiving thread.
            text: Already-wrapped message (see ``relay.build_relay_prompt``).
            interrupt: When True, SIGINT the turn in flight so a "stop, I have
                this" reaches Claude within seconds. When False, wait for the
                current turn to finish — the right default, because a message
                that preempts a turn can cost the receiver uncommitted work.
        """
        chunks = chunk_message(text) or [text]
        seed_message = await thread.send(chunks[0])
        for chunk in chunks[1:]:
            seed_message = await thread.send(chunk)

        record = await self.repo.get(thread.id)
        session_id = record.session_id if record else None
        if record is not None and session_id:
            session_id = await self._session_id_for_current_backend(thread, record)

        chat_only = (thread.parent_id or 0) in self._chat_only_channel_ids
        # _run_claude serializes per thread: with interrupt=False it queues
        # behind the current turn, with interrupt=True it preempts it. Either
        # way eviction + registration is atomic under the per-thread lock, so a
        # relayed message can never spawn a second parallel process here.
        await self._run_claude(
            seed_message,
            thread,
            text,
            session_id=session_id,
            working_dir_override=record.working_dir if record else None,
            chat_only=chat_only,
            interrupt_existing=interrupt,
            interrupt_notice="-# ⚡ Interrupted by another session's message...",
        )

    async def cog_unload(self) -> None:
        """Mark all mid-run Claude sessions for auto-resume on the next bot startup.

        Called by discord.py whenever the cog is removed — including during a
        clean shutdown triggered by ``systemctl restart/stop``, ``bot.close()``,
        or any other SIGTERM-based shutdown.  This ensures that sessions which
        were actively running when the bot was killed will be automatically
        resumed (with a "bot restarted" prompt) as soon as the bot comes back.

        Idle sessions (where Claude has already replied and is waiting for the
        next human message) are NOT in ``_active_runners`` and therefore are not
        marked — they resume naturally via message-triggered resume when the user
        sends their next message.

        No-op when ``_resume_repo`` is not configured.
        """
        if not self._active_runners or self._resume_repo is None:
            return

        logger.info(
            "Shutdown detected: marking %d active session(s) for restart-resume",
            len(self._active_runners),
        )
        for thread_id in list(self._active_runners):
            try:
                session_id: str | None = None
                record = await self.repo.get(thread_id)
                if record is not None:
                    session_id = record.session_id

                await self._resume_repo.mark(
                    thread_id,
                    session_id=session_id,
                    reason="bot_shutdown",
                    resume_prompt=(
                        "The bot restarted. "
                        "Please report what you were working on before resuming. "
                        "⚠️ Context may have been compressed, which means the approval status of "
                        "planned tasks could be lost. "
                        "Before making any code changes, commits, or PRs, "
                        "re-confirm with the user that they want you to proceed."
                    ),
                )
                logger.info(
                    "Marked thread %d for restart-resume (session=%s)", thread_id, session_id
                )
            except Exception:
                logger.warning(
                    "Failed to mark thread %d for restart-resume", thread_id, exc_info=True
                )

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        """Resume any Claude sessions that marked themselves for restart-resume.

        Called each time the bot connects to Discord (including reconnects).
        Only pending resumes within the TTL window (default 5 minutes) are
        processed; older entries are silently discarded by the repository.

        Safety guarantees:
        - Each row is **deleted before** spawning Claude so that even a
          crash during spawn cannot cause a double-resume.
        - The TTL prevents stale markers from triggering after a long
          downtime or accidental second restart.
        - A resume failure (e.g. channel not found) is logged and skipped
          gracefully — it never prevents the bot from becoming ready.
        """
        if self._resume_repo is None:
            return

        pending = await self._resume_repo.get_pending()
        if not pending:
            return

        logger.info("Found %d pending session resume(s) on startup", len(pending))

        for entry in pending:
            # Delete FIRST — prevents double-resume even if spawn fails
            await self._resume_repo.delete(entry.id)

            thread_id = entry.thread_id
            try:
                raw = self.bot.get_channel(thread_id)
                if raw is None:
                    raw = await self.bot.fetch_channel(thread_id)
            except Exception:
                logger.warning(
                    "Pending resume: thread %d not found, skipping", thread_id, exc_info=True
                )
                continue

            if not isinstance(raw, discord.Thread):
                logger.warning("Pending resume: channel %d is not a Thread, skipping", thread_id)
                continue

            thread = raw
            parent = thread.parent
            if not isinstance(parent, discord.TextChannel):
                logger.warning(
                    "Pending resume: thread %d has no TextChannel parent, skipping", thread_id
                )
                continue

            resume_prompt = entry.resume_prompt or (
                "The bot restarted. "
                "Please report what you were working on before resuming. "
                "⚠️ Context may have been compressed, which means the approval status of "
                "planned tasks could be lost. "
                "Before making any code changes, commits, or PRs, "
                "re-confirm with the user that they want you to proceed."
            )

            logger.info(
                "Resuming session in thread %d (session_id=%s, reason=%s)",
                thread_id,
                entry.session_id,
                entry.reason,
            )
            try:
                # Post directly into the existing thread — no new thread needed
                seed_message = await thread.send(f"🔄 **Bot restarted.**\n{resume_prompt}")
                asyncio.create_task(
                    self._run_claude(
                        seed_message,
                        thread,
                        resume_prompt,
                        session_id=entry.session_id,
                    )
                )
            except Exception:
                logger.error("Failed to resume session in thread %d", thread_id, exc_info=True)

    async def _handle_thread_reply(self, message: discord.Message) -> None:
        """Continue a Claude Code session in an existing thread.

        If Claude is already running in this thread, sends SIGINT to the active
        session (graceful interrupt, like pressing Escape) and waits for it to
        finish cleaning up before starting the new session.  This prevents two
        Claude processes from running in parallel in the same thread.
        """
        thread = message.channel
        assert isinstance(thread, discord.Thread)

        record = await self.repo.get(thread.id)
        session_id = record.session_id if record else None
        if record is not None and session_id:
            session_id = await self._session_id_for_current_backend(thread, record)
        prompt, images = await self._build_prompt_and_images(message)

        # Threads ccdb did not create are conversations it has only partly seen:
        # a mention wakes it into the middle of a discussion whose earlier turns
        # (and any human chatter since its last run) never reached the session.
        # Prepend a bounded transcript so the answer is about what was actually
        # being discussed.  Threads the bot owns are skipped — it saw every turn
        # there, so re-sending them would only burn tokens.
        bot_owned = self.bot.user is not None and thread.owner_id == self.bot.user.id
        if not bot_owned and self._thread_context_days > 0:
            transcript = await build_recent_transcript(
                thread,
                days=self._thread_context_days,
                exclude_message_id=message.id,
            )
            if transcript:
                prompt = f"{transcript}\n\n---\n\n{prompt}"
        elif record is None:
            # First human reply in a thread created via /api/spawn with
            # auto_start=false.  The seed message (posted by the bot) carries
            # context (e.g. the goodmorning summary) that Claude needs to see.
            seed_context = await self._fetch_seed_context(thread)
            if seed_context:
                prompt = f"{seed_context}\n\n---\n\n{prompt}"

        # Nothing to send — ignore silently (e.g. unsupported attachment only).
        if not prompt and not images:
            return

        # User replied — remove this thread from the inbox immediately so the
        # dashboard no longer surfaces it as needing attention.
        # Use isinstance checks so plain MagicMock bots in tests are ignored safely.
        from ..database.inbox_repo import ThreadInboxRepository
        from ..discord_ui.thread_dashboard import ThreadStatusDashboard

        _inbox_repo = getattr(self.bot, "inbox_repo", None)
        if isinstance(_inbox_repo, ThreadInboxRepository):
            _removed = await _inbox_repo.remove(thread.id)
            if _removed:
                _dashboard = getattr(self.bot, "thread_dashboard", None)
                if isinstance(_dashboard, ThreadStatusDashboard):
                    await _dashboard.refresh_inbox(_inbox_repo)

        # Determine chat_only from the parent channel of this thread.
        chat_only = (thread.parent_id or 0) in self._chat_only_channel_ids
        # A human reply preempts whatever is running in this thread. _run_claude
        # is the single serialization point: it interrupts the in-flight run and
        # registers the replacement atomically under the per-thread lock, so two
        # fast replies can never spawn parallel CLI processes.
        await self._run_claude(
            message,
            thread,
            prompt,
            session_id=session_id,
            images=images,
            working_dir_override=record.working_dir if record else None,
            chat_only=chat_only,
            interrupt_existing=True,
        )

    async def _session_id_for_current_backend(
        self, thread: discord.Thread | discord.TextChannel, record: SessionRecord
    ) -> str | None:
        """Return the stored session ID, or ``None`` when the backend changed.

        *thread* is whatever the session is keyed on — a thread, or the channel
        itself for the in-place mention flow.

        A global ``/backend`` switch does not touch per-thread session records
        (only a thread-scoped switch does), so a thread can end up holding a
        Codex rollout ID while the active backend is Claude.  Resuming it makes
        the CLI exit instantly with "No conversation found with session ID" and
        the thread looks dead.  Detect the mismatch and start fresh instead.
        """
        if self._backend_settings is None:
            return record.session_id

        current = await self._backend_settings.current_backend(thread.id)
        if session_is_resumable(record.backend, current):
            return record.session_id

        logger.info(
            "Thread %d session %s was created by %s but the active backend is %s "
            "— starting a fresh session instead of resuming",
            thread.id,
            record.session_id,
            record.backend,
            current,
        )
        with contextlib.suppress(discord.HTTPException):
            await thread.send(
                f"-# 🔀 Backend changed (`{record.backend}` → `{current}`). "
                f"`{current}` cannot resume a `{record.backend}` session, "
                "so its file-backed conversation history will be carried into a fresh session."
            )
        return None

    async def _prepare_cross_backend_handoff(
        self,
        thread: discord.Thread | discord.TextChannel,
        prompt: str,
        session_id: str | None,
    ) -> tuple[str | None, str]:
        """Replace an incompatible native resume with a text transcript handoff."""
        if self._backend_settings is None:
            return session_id, prompt

        record = await self.repo.get(thread.id)
        if record is None or not record.backend or not record.session_id:
            return session_id, prompt
        current = await self._backend_settings.current_backend(thread.id)
        if session_is_resumable(record.backend, current):
            return session_id, prompt

        transcript = await asyncio.to_thread(
            self._conversation_history.read,
            record.backend,
            record.session_id,
        )
        if not transcript:
            logger.warning(
                "No file-backed transcript found for cross-backend handoff: "
                "thread=%d backend=%s session=%s",
                thread.id,
                record.backend,
                record.session_id,
            )
            return None, prompt

        logger.info(
            "Injecting cross-backend transcript: thread=%d %s->%s chars=%d",
            thread.id,
            record.backend,
            current,
            len(transcript),
        )
        return None, build_handoff_prompt(
            source_backend=record.backend,
            target_backend=current,
            transcript=transcript,
            current_prompt=prompt,
        )

    async def _build_prompt_and_images(
        self, message: discord.Message
    ) -> tuple[str, list[ImageData]]:
        """Delegate to the standalone prompt_builder module.

        When the message has attachments, creates a temporary directory so all
        files (including PDF, Excel, etc.) are saved to disk and their paths
        are listed in a header prepended to the prompt.
        """
        save_dir: str | None = None
        if message.attachments:
            save_dir = os.path.join(tempfile.gettempdir(), "ccdb-uploads", str(message.id))
            os.makedirs(save_dir, exist_ok=True)
        return await build_prompt_and_images(message, save_dir=save_dir)

    @staticmethod
    async def _fetch_seed_context(thread: discord.Thread) -> str | None:
        """Return the seed text of a thread, if it was posted by the bot.

        Used to recover context from ``/api/spawn`` threads with ``auto_start=false``,
        where the bot posted a seed message but did not start Claude.  Returns
        ``None`` if the seed cannot be retrieved or was not from a bot.

        Reads the *leading run* of bot messages, not just the first one: a prompt
        longer than Discord's per-message limit is chunked by ``spawn_session``, so
        taking only message #1 hands Claude a seed cut off mid-sentence — silently,
        and worse the longer the seed is. The run stops at the first human message,
        which is the reply that triggered this lookup.
        """
        try:
            chunks: list[str] = []
            async for message in thread.history(
                limit=SEED_CONTEXT_MESSAGE_LIMIT, oldest_first=True
            ):
                # Only include bot-authored seed messages (from /api/spawn).
                if not message.author.bot:
                    break
                if message.content:
                    chunks.append(message.content)
            return "\n".join(chunks) or None
        except Exception:
            logger.debug("Failed to fetch seed message for thread %d", thread.id, exc_info=True)
            return None

    async def _evict_active_run(
        self,
        thread: discord.Thread | discord.TextChannel,
        *,
        interrupt: bool,
        notice: str,
    ) -> None:
        """Clear the thread's active run so the caller can register a new one.

        Must be called while holding ``self._thread_locks[thread.id]``. When
        ``interrupt`` is True the in-flight runner is SIGINT'd (the ``notice`` is
        posted first); otherwise we simply wait for it to finish — queue
        semantics. Either way we await the run's task so its cleanup (its own
        ``finally``) completes before the caller registers a replacement, which
        is what keeps at most one runner per thread.

        No deadlock: a task is only in ``_active_tasks`` after it finished its
        own phase 1 and released the lock, so it never blocks on the lock we
        hold here.
        """
        existing_runner = self._active_runners.get(thread.id)
        if existing_runner is None:
            return
        existing_task = self._active_tasks.get(thread.id)
        if interrupt:
            with contextlib.suppress(discord.HTTPException):
                await thread.send(notice)
            with contextlib.suppress(Exception):
                await existing_runner.interrupt()
        if (
            existing_task is not None
            and existing_task is not asyncio.current_task()
            and not existing_task.done()
        ):
            with contextlib.suppress(Exception):
                await existing_task

    async def _run_claude(
        self,
        user_message: discord.Message,
        thread: discord.Thread | discord.TextChannel,
        prompt: str,
        session_id: str | None,
        images: list[ImageData] | None = None,
        fork: bool = False,
        working_dir_override: str | None = None,
        chat_only: bool = False,
        result_sink: Callable[[str | None, str | None], Awaitable[None]] | None = None,
        interrupt_existing: bool = False,
        interrupt_notice: str = "-# ⚡ Interrupted. Starting with new instruction...",
    ) -> None:
        """Execute Claude Code CLI and stream results to the thread.

        This is the single serialization point for a thread: at most one Claude
        run may be active per thread at any time. Under the thread's per-thread
        lock it evicts whatever run is already in flight — interrupting it when
        ``interrupt_existing`` is set, otherwise waiting for it to finish (queue
        semantics) — then builds and *registers* the new runner, and only then
        releases the lock and starts the subprocess.

        Registering the runner under the *same* lock that checks for an existing
        one is what closes the race: without it, two near-simultaneous messages
        both pass the "nothing is running" check during the awaits below (model
        lookup, runner build) and both spawn parallel CLI processes in the same
        thread. The subprocess itself runs *outside* the lock so a later message
        can still interrupt this run.
        """
        session_id, prompt = await self._prepare_cross_backend_handoff(
            thread,
            prompt,
            session_id,
        )
        dashboard = self._get_dashboard()
        description = prompt[:100].replace("\n", " ")

        current_task = asyncio.current_task()
        lock = self._thread_locks.setdefault(thread.id, asyncio.Lock())

        # --- Phase 1: atomically take the thread's single run slot -----------
        # Everything from evicting the previous run through registering this one
        # happens under the lock, so no concurrent _run_claude can observe an
        # empty slot and spawn a parallel process.
        async with lock:
            await self._evict_active_run(
                thread, interrupt=interrupt_existing, notice=interrupt_notice
            )

            # Mark thread as PROCESSING when Claude starts
            if dashboard is not None:
                await dashboard.set_state(
                    thread.id,
                    ThreadState.PROCESSING,
                    description,
                    thread=thread,
                )

            model_override = await self._get_current_model()
            effective_model = model_override or self.runner.model

            async def _notify_stall() -> None:
                threshold = status._stall_hard
                await thread.send(
                    f"-# ⚠️ No activity for {threshold}s — could be extended thinking "
                    "or context compression. Will resume automatically."
                )

            status = StatusManager(
                user_message,
                on_hard_stall=_notify_stall,
                model=effective_model,
            )
            await status.set_thinking()

            tools_override = await self._get_allowed_tools()
            effort_override = await self._get_current_effort()

            runner = await self._build_runner_for_thread(
                thread_id=thread.id,
                model_override=model_override,
                tools_override=tools_override,
                fork_session=fork,
                working_dir_override=working_dir_override,
                effort_override=effort_override,
            )
            # Register as the sole active run BEFORE releasing the lock. Track
            # the task too so a later eviction can await our cleanup.
            self._active_runners[thread.id] = runner
            if current_task is not None:
                self._active_tasks[thread.id] = current_task

            # In chat_only mode, skip the "Session running" message and stop button.
            stop_view: StopView | None = None
            if not chat_only:
                stop_view = StopView(runner)
                stop_msg = await thread.send("-# ⏺ Session running", view=stop_view)
                stop_view.set_message(stop_msg)

        # --- Phase 2: run the subprocess OUTSIDE the lock --------------------
        # The lock is released so a later message can interrupt this run. The
        # runner is already registered, so that message will find and evict it.
        try:
            await run_claude_with_config(
                RunConfig(
                    thread=thread,
                    runner=runner,
                    repo=self.repo,
                    prompt=prompt,
                    session_id=session_id,
                    status=status,
                    registry=self._registry,
                    ask_repo=self._ask_repo,
                    lounge_repo=self._lounge_repo,
                    file_activity=getattr(self.bot, "file_activity", None),
                    stop_view=stop_view,
                    worktree_manager=getattr(self.bot, "worktree_manager", None),
                    images=images,
                    attach_on_request=wants_file_attachment(prompt),
                    inbox_repo=getattr(self.bot, "inbox_repo", None),
                    inbox_dashboard=dashboard,
                    claude_command=runner.command,
                    chat_only=chat_only,
                    notify_user_id=user_message.author.id,
                    result_sink=result_sink,
                    backend_settings=self._backend_settings,
                    codex_command=(
                        self._factory.codex_command if self._factory is not None else "codex"
                    ),
                )
            )
        finally:
            if stop_view is not None:
                await stop_view.disable()
            # Identity-guarded cleanup: only clear our own entries. A successor
            # that evicted us may already have registered its runner/task, and
            # we must not delete it. _thread_locks is intentionally NOT popped:
            # the lock must stay stable for the thread's lifetime, or a later
            # message could create a fresh Lock and run concurrently with one
            # still holding the old object.
            if self._active_runners.get(thread.id) is runner:
                self._active_runners.pop(thread.id, None)
            if self._active_tasks.get(thread.id) is current_task:
                self._active_tasks.pop(thread.id, None)

            # Transition to WAITING_INPUT so owner knows a reply is needed
            if dashboard is not None:
                await dashboard.set_state(
                    thread.id,
                    ThreadState.WAITING_INPUT,
                    description,
                    thread=thread,
                )
