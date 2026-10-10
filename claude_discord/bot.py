"""Discord Bot class."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord
from discord.ext import commands

from .claude.types import AskOption, AskQuestion
from .collision import FileActivityTracker
from .concurrency import SessionRegistry
from .discord_ui.ask_bus import ask_bus
from .discord_ui.ask_view import AskView

if TYPE_CHECKING:
    from .database.ask_repo import PendingAskRepository
    from .database.inbox_repo import ThreadInboxRepository
    from .database.lounge_repo import LoungeRepository
    from .discord_ui.thread_dashboard import ThreadStatusDashboard
    from .worktree import WorktreeManager

logger = logging.getLogger(__name__)


class ClaudeDiscordBot(commands.Bot):
    """Discord bot that bridges messages to Claude Code CLI."""

    def __init__(
        self,
        channel_id: int,
        owner_id: int | None = None,
        ask_repo: PendingAskRepository | None = None,
        lounge_repo: LoungeRepository | None = None,
        lounge_channel_id: int | None = None,
        worktree_manager: WorktreeManager | None = None,
        *,
        headless: bool = False,
    ) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.guilds = True

        super().__init__(
            command_prefix="!",  # Not used, but required
            intents=intents,
        )
        self.channel_id = channel_id
        self.owner_id = owner_id
        #: True when no Discord frontend is enabled: the bot object still
        #: hosts the Cogs (scheduler, waits, …) but never logs in.
        self.headless = headless
        self.session_registry = SessionRegistry()
        # Which files each live session writes — CollisionWatchCog compares
        # these to spot two sessions editing the same file.
        self.file_activity = FileActivityTracker()
        # Optional repo for AskUserQuestion restart recovery
        self.ask_repo: PendingAskRepository | None = ask_repo
        # Populated after on_ready when the channel is resolved
        self.thread_dashboard: ThreadStatusDashboard | None = None
        # AI Lounge — casual shared space for concurrent Claude sessions (optional)
        self.lounge_repo: LoungeRepository | None = lounge_repo
        self.lounge_channel_id: int | None = lounge_channel_id
        # Worktree lifecycle manager — cleans up session worktrees after runs
        self.worktree_manager: WorktreeManager | None = worktree_manager
        # Thread inbox repository — None until THREAD_INBOX_ENABLED=true and setup_bridge runs
        self.inbox_repo: ThreadInboxRepository | None = None

    async def wait_until_ready(self) -> None:
        """Do not block a headless bot's loops on a login that never happens."""
        if self.headless:
            return
        await super().wait_until_ready()

    async def on_ready(self) -> None:
        logger.info("Logged in as %s (ID: %s)", self.user, self.user.id if self.user else "?")
        logger.info("Watching channel ID: %d", self.channel_id)

        # Re-register persistent AskViews for any questions that were pending
        # when the bot last shut down.  This prevents "Interaction Failed" on
        # old buttons; instead users see a clear "session ended" message.
        await self._restore_pending_ask_views()

        # Initialise the thread-status dashboard once we have a live channel object
        channel = self.get_channel(self.channel_id)
        if isinstance(channel, discord.TextChannel):
            from .discord_ui.thread_dashboard import ThreadStatusDashboard

            self.thread_dashboard = ThreadStatusDashboard(
                channel=channel,
                owner_id=self.owner_id,
            )
            await self.thread_dashboard.initialize()
            logger.info("Thread status dashboard initialised in channel %d", self.channel_id)

            # If inbox is enabled, restore persistent entries from DB into dashboard.
            inbox_repo = getattr(self, "inbox_repo", None)
            if inbox_repo is not None:
                await self.thread_dashboard.refresh_inbox(inbox_repo)
                logger.info("Thread inbox restored from DB")
        else:
            logger.warning(
                "Could not resolve channel %d to a TextChannel; dashboard disabled",
                self.channel_id,
            )

        # Cleanup orphaned session worktrees from previous bot runs.
        # At startup there are no active sessions, so all clean session
        # worktrees are safe to remove.
        if self.worktree_manager is not None:
            import asyncio

            asyncio.create_task(self._cleanup_orphaned_worktrees())

        # Sync slash commands per-guild for instant availability.
        # Global-only sync (the old approach) can take up to 1 hour to propagate.
        try:
            for guild in self.guilds:
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                logger.info(
                    "Synced %d slash commands to guild %s (%d)",
                    len(synced),
                    guild.name,
                    guild.id,
                )
        except Exception:
            logger.exception("Failed to sync slash commands")

    async def _cleanup_orphaned_worktrees(self) -> None:
        """Remove leftover clean session worktrees from previous bot runs.

        Runs in a background task so it does not block on_ready().
        """
        import asyncio

        assert self.worktree_manager is not None  # caller ensures this
        try:
            results = await asyncio.to_thread(
                self.worktree_manager.cleanup_orphaned,
                set(),  # no active sessions at startup
            )
            removed = [r for r in results if r.removed]
            skipped = [r for r in results if not r.removed and "does not exist" not in r.reason]
            if removed:
                logger.info(
                    "Startup worktree cleanup: removed %d orphaned worktree(s): %s",
                    len(removed),
                    [r.path for r in removed],
                )
            if skipped:
                logger.warning(
                    "Startup worktree cleanup: skipped %d worktree(s) (dirty or locked): %s",
                    len(skipped),
                    [(r.path, r.reason) for r in skipped],
                )
        except Exception:
            logger.exception("Error during startup worktree cleanup")

    async def _restore_pending_ask_views(self) -> None:
        """Re-register persistent AskViews for questions pending before restart.

        For each pending ask found in the DB, we create an AskView and call
        ``bot.add_view()`` so discord.py can route button clicks to it.  When
        clicked, the view tries ``ask_bus.post_answer()`` which returns False
        (no live session), so it sends an ephemeral "session ended" message and
        cleans up the DB entry.
        """
        if self.ask_repo is None:
            return

        records = await self.ask_repo.list_all()
        if not records:
            return

        logger.info(
            "Restoring %d pending AskUserQuestion view(s) from previous run",
            len(records),
        )
        for record in records:
            questions_raw = record.questions()
            for q_idx in range(record.question_idx, len(questions_raw)):
                q_raw = questions_raw[q_idx]
                question = AskQuestion(
                    question=q_raw.get("question", ""),
                    header=q_raw.get("header") or "",
                    multi_select=q_raw.get("multi_select", False),
                    options=[
                        AskOption(
                            label=o.get("label", ""),
                            description=o.get("description") or "",
                        )
                        for o in q_raw.get("options", [])
                    ],
                )
                view = AskView(
                    question,
                    thread_id=record.thread_id,
                    q_idx=q_idx,
                    bus=ask_bus,
                    ask_repo=self.ask_repo,
                )
                self.add_view(view)
                logger.debug(
                    "Restored AskView for thread %d q_idx=%d",
                    record.thread_id,
                    q_idx,
                )
