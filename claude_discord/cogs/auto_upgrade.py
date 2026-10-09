"""AutoUpgradeCog — Webhook-triggered package upgrade + optional restart.

Generic pattern for auto-upgrading a pip/uv package when a webhook fires.
Typical use: upstream library pushes a new release → CI sends webhook → bot upgrades itself.

Security design:
- Only processes messages with a webhook_id
- Optional webhook_id allowlist
- All commands are hardcoded or from UpgradeConfig — no user input in subprocess args
- Uses create_subprocess_exec (never shell=True)
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from dataclasses import dataclass

import discord
from discord import app_commands
from discord.ext import commands

from ..protocols import DrainAware
from ..thread_policy import THREAD_AUTO_ARCHIVE_MINUTES

logger = logging.getLogger(__name__)

# Default timeout for each subprocess step (seconds).
_STEP_TIMEOUT = 120


class UpgradeApprovalView(discord.ui.View):
    """A Discord View with a single approval button for upgrade/restart gates.

    Posts to the parent channel (not the upgrade thread) so users can approve
    from the bottom of the channel without scrolling up to find the thread.

    Usage::

        approved = asyncio.Event()
        content = "🔔 Approve restart?"
        view = UpgradeApprovalView(approved_event=approved, bot_id=bot.user.id, content=content)
        channel_msg = await channel.send(content, view=view)
        view.set_message(channel_msg)
        await approved.wait()           # blocks until button clicked

    The same ``approved`` event can be watched in parallel with a reaction loop
    so that *either* a reaction on the thread message *or* clicking this button
    grants approval.

    Call ``bump(channel)`` periodically to re-post the button at the bottom of
    the channel so it stays visible as other messages arrive above it.
    """

    def __init__(
        self,
        *,
        approved_event: asyncio.Event,
        bot_id: int | None = None,
        label: str = "✅ Approve",
        content: str = "",
    ) -> None:
        super().__init__(timeout=None)
        self._event = approved_event
        self._bot_id = bot_id
        self._content = content
        self._message: discord.Message | None = None
        # Override the default label set by the decorator
        for child in self.children:
            if isinstance(child, discord.ui.Button):
                child.label = label
                break

    def set_message(self, message: discord.Message | None) -> None:
        """Store the message this view is attached to for use by bump()."""
        self._message = message

    async def bump(self, channel: discord.abc.Messageable) -> None:
        """Re-post the approval button as the latest message in the channel.

        Deletes the old button message and sends a new one so the button stays
        accessible as other messages push it up. No-op if already approved.
        """
        if self._event.is_set():
            return
        old_message = self._message
        with contextlib.suppress(discord.HTTPException):
            new_message = await channel.send(self._content, view=self)
            self._message = new_message
        if old_message:
            with contextlib.suppress(discord.HTTPException):
                await old_message.delete()

    @discord.ui.button(label="✅ Approve", style=discord.ButtonStyle.green)
    async def approve(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        """Grant approval when any non-bot user clicks the button."""
        if self._bot_id is not None and interaction.user.id == self._bot_id:
            await interaction.response.defer()
            return
        button.disabled = True
        button.label = "✅ Approved"
        await interaction.response.edit_message(view=self)
        self._event.set()
        self.stop()


@dataclass(frozen=True)
class UpgradeConfig:
    """Configuration for auto-upgrade behaviour.

    Attributes:
        package_name: The pip/uv package name to upgrade.
        trigger_prefix: Webhook message prefix that triggers the upgrade.
        working_dir: Directory to run upgrade commands in.
        upgrade_command: Custom upgrade command as arg list.
            Defaults to ["uv", "lock", "--upgrade-package", <package_name>].
        sync_command: Custom sync command as arg list.
            Defaults to ["uv", "sync"].
        restart_command: Optional restart command
            (e.g. ["sudo", "systemctl", "restart", "my.service"]).
        allowed_webhook_ids: Optional set of allowed webhook IDs.
        channel_ids: Optional set of channel IDs to listen in.
        step_timeout: Timeout in seconds for each subprocess step.
        restart_approval: If True, wait for a user to react with ✅ before
            restarting. Useful when the bot is updated from within its own
            Discord sessions (self-update pattern). Default: False.
    """

    package_name: str
    trigger_prefix: str = "🔄 upgrade"
    working_dir: str = "."
    upgrade_command: list[str] | None = None
    sync_command: list[str] | None = None
    restart_command: list[str] | None = None
    allowed_webhook_ids: set[int] | None = None
    channel_ids: set[int] | None = None
    step_timeout: int = _STEP_TIMEOUT
    restart_approval: bool = False
    upgrade_approval: bool = False
    """If True, wait for a user to react with ✅ before running any upgrade
    commands. Useful when you want manual control over when updates are applied.
    When False (default), upgrade steps run automatically on webhook trigger."""
    slash_command_enabled: bool = False
    """If True, register a /upgrade slash command that manually triggers the upgrade
    pipeline. Defaults to False so existing bots are unaffected until explicitly opted in.
    When enabled, the command respects upgrade_approval and restart_approval flags."""


class AutoUpgradeCog(commands.Cog):
    """Cog that auto-upgrades a package when triggered by a Discord webhook.

    Usage::

        config = UpgradeConfig(
            package_name="claude-code-discord-bridge",
            trigger_prefix="🔄 ebibot-upgrade",
            working_dir="/home/user/my-bot",
            restart_command=["sudo", "systemctl", "restart", "my-bot.service"],
        )
        await bot.add_cog(AutoUpgradeCog(bot, config, drain_check=lambda: not active_sessions))

    Args:
        bot: The Discord bot instance.
        config: Upgrade configuration.
        drain_check: Optional callable that returns True when it is safe to restart
            (e.g. no active user sessions). Called repeatedly until True or timeout.
        drain_timeout: Maximum seconds to wait for drain_check to return True.
            After this, the restart proceeds regardless. Default: 300s.
        drain_poll_interval: Seconds between drain_check polls. Default: 10s.
    """

    def __init__(
        self,
        bot: commands.Bot,
        config: UpgradeConfig,
        drain_check: Callable[[], bool] | None = None,
        drain_timeout: int = 300,
        drain_poll_interval: int = 10,
    ) -> None:
        self.bot = bot
        self.config = config
        self._drain_check = drain_check
        self._drain_timeout = drain_timeout
        self._drain_poll_interval = drain_poll_interval
        self._lock = asyncio.Lock()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Handle upgrade trigger messages."""
        if not message.webhook_id:
            return

        if (
            self.config.allowed_webhook_ids is not None
            and message.webhook_id not in self.config.allowed_webhook_ids
        ):
            return

        if (
            self.config.channel_ids is not None
            and message.channel.id not in self.config.channel_ids
        ):
            return

        if message.content.strip() != self.config.trigger_prefix:
            return

        logger.info("Auto-upgrade trigger received: %r", self.config.trigger_prefix)

        if self._lock.locked():
            await message.reply("⏳ Upgrade is already running. Skipping.")
            return

        async with self._lock:
            await self._run_upgrade(message)

    @app_commands.command(name="upgrade", description="Manually trigger a package upgrade")
    async def upgrade_command(self, interaction: discord.Interaction) -> None:
        """Slash command entry point for manual upgrades.

        Only active when config.slash_command_enabled=True. Requires no webhook —
        any authorised user can trigger the upgrade from Discord directly.
        The same upgrade_approval / restart_approval safety gates apply.
        """
        if not self.config.slash_command_enabled:
            await interaction.response.send_message(
                "⚠️ Slash command upgrades are not enabled for this bot.",
                ephemeral=True,
            )
            return

        if self._lock.locked():
            await interaction.response.send_message(
                "⏳ Upgrade is already running. Please wait.",
                ephemeral=True,
            )
            return

        channel = interaction.channel
        # Resolve the text channel to create the upgrade thread in.
        # Works from both a TextChannel and a Thread (use the thread's parent).
        if isinstance(channel, discord.TextChannel):
            text_channel: discord.TextChannel = channel
        elif isinstance(channel, discord.Thread) and isinstance(
            channel.parent, discord.TextChannel
        ):
            text_channel = channel.parent
        else:
            await interaction.response.send_message(
                "⚠️ This command can only be used in a text channel or thread.",
                ephemeral=True,
            )
            return

        await interaction.response.defer()

        async with self._lock:
            thread = await text_channel.create_thread(
                name=self.config.trigger_prefix[:100],
                auto_archive_duration=THREAD_AUTO_ARCHIVE_MINUTES,
            )
            await self._run_pipeline(thread, status_target=None)

    async def _run_upgrade(self, trigger_message: discord.Message) -> None:
        """Execute the upgrade pipeline triggered by a webhook message."""
        thread = await trigger_message.create_thread(
            name=self.config.trigger_prefix[:100],
            auto_archive_duration=THREAD_AUTO_ARCHIVE_MINUTES,
        )
        await self._run_pipeline(thread, status_target=trigger_message)

    async def _run_pipeline(
        self,
        thread: discord.Thread,
        status_target: discord.Message | None,
    ) -> None:
        """Core upgrade pipeline: approve → upgrade → sync → restart.

        Args:
            thread: Discord thread to post progress updates into.
            status_target: Message to add ✅/❌ reaction to on completion/failure.
                           None when triggered via slash command (no message to react to).
        """
        try:
            # Step 0: Optional upgrade approval before any subprocess runs
            if self.config.upgrade_approval:
                await self._wait_for_approval(
                    status_target,
                    thread,
                    prompt=(
                        f"📦 New release of **{self.config.package_name}** detected. "
                        "React ✅ on this message to start the upgrade."
                    ),
                )

            # Step 1: Upgrade package
            upgrade_cmd = self.config.upgrade_command or [
                "uv",
                "lock",
                "--upgrade-package",
                self.config.package_name,
            ]
            ok = await self._run_step(thread, "upgrade", upgrade_cmd)
            if not ok:
                if status_target is not None:
                    await status_target.add_reaction("❌")
                return

            # Step 2: Sync dependencies
            sync_cmd = self.config.sync_command or ["uv", "sync"]
            ok = await self._run_step(thread, "sync", sync_cmd)
            if not ok:
                if status_target is not None:
                    await status_target.add_reaction("❌")
                return

            # Step 3: Optional restart
            if self.config.restart_command:
                if self.config.restart_approval:
                    await self._wait_for_approval(status_target, thread)
                # Snapshot active sessions BEFORE drain so we can resume them after restart.
                active_thread_ids = self._collect_active_thread_ids()
                await self._drain(thread)
                await self._mark_sessions_for_resume(active_thread_ids, thread)
                await self._restart(status_target, thread)
            else:
                if status_target is not None:
                    await status_target.add_reaction("✅")
                await thread.send("✅ Upgrade complete (no restart configured).")

        except discord.NotFound:
            # The thread was deleted mid-pipeline (e.g. swept while awaiting approval).
            logger.info("Upgrade thread %d no longer exists; aborting pipeline", thread.id)
        except TimeoutError:
            await self._report_failure(thread, status_target, "❌ Step timed out.")
        except Exception:
            logger.exception("Auto-upgrade error")
            await self._report_failure(
                thread, status_target, "❌ Upgrade failed with an unexpected error."
            )

    async def _report_failure(
        self,
        thread: discord.Thread,
        status_target: discord.Message | None,
        text: str,
    ) -> None:
        """Post *text* and mark the trigger ❌; a deleted thread/message is not an error."""
        try:
            await thread.send(text)
            if status_target is not None:
                await status_target.add_reaction("❌")
        except discord.NotFound:
            logger.info("Upgrade thread %d vanished before failure was reported", thread.id)

    def _collect_active_thread_ids(self) -> frozenset[int]:
        """Return the IDs of threads with currently-running Claude sessions.

        Iterates all Cogs looking for ``_active_runners`` dicts (duck-typed to
        avoid a hard import dependency on ``ClaudeChatCog``).  Call this
        *before* :meth:`_drain` so you capture sessions that are mid-run.
        """
        thread_ids: set[int] = set()
        for cog in self.bot.cogs.values():
            if cog is self:
                continue
            active_runners = getattr(cog, "_active_runners", None)
            if isinstance(active_runners, dict):
                thread_ids.update(active_runners.keys())
        return frozenset(thread_ids)

    async def _mark_sessions_for_resume(
        self,
        thread_ids: frozenset[int],
        status_thread: discord.Thread,
    ) -> None:
        """Mark *thread_ids* in the pending-resumes table.

        Called just before the restart command so that Claude sessions which
        were active at upgrade time are automatically resumed on the next bot
        startup.  No-op when ``bot.resume_repo`` is not configured.

        ``session_id`` is auto-resolved from ``bot.session_repo`` when
        available, enabling ``--resume`` continuity.
        """
        if not thread_ids:
            return

        resume_repo = getattr(self.bot, "resume_repo", None)
        if resume_repo is None:
            return

        session_repo = getattr(self.bot, "session_repo", None)
        marked = 0

        for tid in thread_ids:
            try:
                session_id: str | None = None
                if session_repo is not None:
                    record = await session_repo.get(tid)
                    if record is not None:
                        session_id = record.session_id

                await resume_repo.mark(
                    tid,
                    session_id=session_id,
                    reason="bot_upgrade",
                    resume_prompt=(
                        "The bot restarted after a package upgrade. "
                        "Please report what you were working on before resuming. "
                        "⚠️ Context may have been compressed, which means the approval status of "
                        "planned tasks could be lost. "
                        "Before making any code changes, commits, or PRs, "
                        "re-confirm with the user that they want you to proceed."
                    ),
                )
                marked += 1
            except Exception:
                logger.warning("Failed to mark thread %d for resume", tid, exc_info=True)

        if marked:
            await status_thread.send(
                f"📌 {marked} active session(s) marked for auto-resume after restart."
            )

    def _auto_drain_check(self) -> bool:
        """Check all DrainAware Cogs registered on the bot.

        Returns True when every DrainAware Cog has ``active_count == 0``.
        If no DrainAware Cogs are found, returns True (safe to restart).
        """
        return all(
            cog.active_count == 0
            for cog in self.bot.cogs.values()
            if isinstance(cog, DrainAware) and cog is not self
        )

    async def _drain(self, thread: discord.Thread) -> None:
        """Wait until drain_check returns True or drain_timeout elapses.

        If an explicit drain_check was provided, uses that.
        Otherwise, auto-discovers all DrainAware Cogs on the bot.
        Posts status updates to the Discord thread while waiting.
        """
        check = self._drain_check or self._auto_drain_check
        if check():
            return

        await thread.send(
            f"⏳ Upgrade ready — waiting for active sessions to finish "
            f"(max {self._drain_timeout}s)..."
        )
        elapsed = 0
        while elapsed < self._drain_timeout:
            await asyncio.sleep(self._drain_poll_interval)
            elapsed += self._drain_poll_interval
            if check():
                await thread.send(f"✅ Sessions finished ({elapsed}s). Restarting now...")
                return

        await thread.send(f"⚠️ Drain timeout ({self._drain_timeout}s elapsed) — restarting anyway.")

    async def _wait_for_approval(
        self,
        status_target: discord.Message | None,
        thread: discord.Thread,
        *,
        prompt: str | None = None,
    ) -> None:
        """Wait for a user to approve by reacting with ✅ or clicking a button.

        Posts a notification with a ✅ reaction in the upgrade thread, AND posts
        a button message in the parent channel so users can approve without
        scrolling up to find the thread. Either action grants approval.
        Sends periodic reminders every ``_drain_timeout`` seconds while waiting.

        Args:
            status_target: Message to watch for reactions. When None (slash command
                           flow), approval is collected via the thread message itself.
            thread: The thread to post status messages in.
            prompt: Custom prompt text. Defaults to a restart-approval message.
        """
        text = prompt or "📦 Update installed. React ✅ on this message to restart."
        approval_msg = await thread.send(text)
        await approval_msg.add_reaction("✅")

        # Shared event — set by either the reaction loop or the button callback.
        approved = asyncio.Event()

        # Post a button in the parent channel so approval is always one click
        # away at the bottom of the channel (no need to scroll up to the thread).
        channel_msg: discord.Message | None = None
        view: UpgradeApprovalView | None = None
        parent = getattr(thread, "parent", None)
        if parent is not None:
            bot_id = self.bot.user.id if self.bot.user else None
            msg_content = (
                f"🔔 **Approval needed** — {text}\n"
                "(React ✅ in the upgrade thread above, or click here ↓)"
            )
            view = UpgradeApprovalView(approved_event=approved, bot_id=bot_id, content=msg_content)
            try:
                channel_msg = await parent.send(msg_content, view=view)
                view.set_message(channel_msg)
            except Exception:
                logger.debug("Could not post approval button to parent channel", exc_info=True)
                view = None

        # Task 1: watch for the ✅ reaction on the thread message.
        async def _watch_reaction() -> None:
            while not approved.is_set():
                try:
                    event = await self.bot.wait_for(
                        "raw_reaction_add",
                        check=lambda e: (
                            e.message_id == approval_msg.id
                            and str(e.emoji) == "✅"
                            and (self.bot.user is None or e.user_id != self.bot.user.id)
                        ),
                        timeout=float(self._drain_timeout),
                    )
                    logger.info("Restart approved by user %s", event.user_id)
                    approved.set()
                except TimeoutError:
                    if not approved.is_set():
                        await thread.send(
                            "⏳ Still waiting for restart approval... "
                            "React ✅ above or click the button in the channel."
                        )
                        # Re-post the button at the bottom so it stays visible.
                        if view is not None and parent is not None:
                            await view.bump(parent)

        # Task 2: resolve as soon as the button (or reaction) sets the event.
        async def _watch_button() -> None:
            await approved.wait()

        reaction_task = asyncio.create_task(_watch_reaction())
        button_task = asyncio.create_task(_watch_button())

        done, pending = await asyncio.wait(
            {reaction_task, button_task},
            return_when=asyncio.FIRST_COMPLETED,
        )

        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        # Propagate unexpected exceptions from finished tasks.
        for task in done:
            task.result()

        # Remove the channel button — it's no longer needed.
        # view._message tracks the latest button post (may have been bumped).
        final_msg = (view._message if view is not None else None) or channel_msg
        if final_msg is not None:
            with contextlib.suppress(discord.NotFound):
                await final_msg.delete()

        await thread.send("👍 Restart approved!")

    async def _restart(
        self,
        status_target: discord.Message | None,
        thread: discord.Thread,
    ) -> None:
        """Execute the restart command (fire-and-forget).

        Uses create_subprocess_exec (not shell=True) — all args are from
        UpgradeConfig, not user input. Safe by construction.
        """
        await thread.send("🔄 Restarting...")
        if status_target is not None:
            await status_target.add_reaction("✅")
        await asyncio.sleep(1)
        assert self.config.restart_command is not None  # Caller checks this
        await asyncio.create_subprocess_exec(
            *self.config.restart_command,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

    async def _run_step(
        self,
        thread: discord.Thread,
        step_name: str,
        command: list[str],
    ) -> bool:
        """Run a single subprocess step, posting output to the thread.

        All command args come from UpgradeConfig (server-side config),
        not from user/webhook input. Uses create_subprocess_exec for safety.

        Returns True on success, False on failure.
        """
        cmd_str = " ".join(command)
        await thread.send(f"⚙️ `{cmd_str}`")

        proc = await asyncio.create_subprocess_exec(
            *command,
            cwd=self.config.working_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=self.config.step_timeout)
        output = stdout.decode("utf-8", errors="replace").strip()

        if output:
            # Truncate to fit Discord message limit
            truncated = output[:1800]
            await thread.send(f"```\n{truncated}\n```")

        if proc.returncode != 0:
            await thread.send(f"❌ `{step_name}` failed (exit code {proc.returncode}).")
            return False

        return True
