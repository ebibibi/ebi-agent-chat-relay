"""SchedulerCog — SQLite-backed periodic Claude Code task executor.

Design:
- Tasks are stored in ``scheduled_tasks`` DB table and registered via REST API
  (Claude Code calls POST /api/tasks from within a chat session).
- A single 30-second master loop checks for due tasks and spawns them.
- ``discord.ext.tasks`` is used only for the master loop — individual tasks
  are not @tasks.loop decorated (they are runtime-dynamic).
- Claude handles all domain logic (what to check, how to deduplicate).
  ccdb only manages scheduling.

See: Issue #90, CLAUDE.md §Key Design Decisions #7-9.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from discord.ext import commands, tasks

from claude_code_core.frontend import ConversationSurface, Notice, NoticeLevel

from ..frontend import DiscordFrontend
from ..scheduled_marker import ScheduledThreadMarker
from ._run_helper import run_claude_with_config
from .headless_backend import build_headless_runner
from .run_config import RunConfig

if TYPE_CHECKING:
    from claude_code_core.backend import SessionBackend
    from claude_code_core.frontend import SessionFrontend

    from ..backend_factory import BackendFactory
    from ..backend_settings import BackendSettings
    from ..database.repository import SessionRepository
    from ..database.task_repo import TaskRepository
    from ..database.wait_repo import WaitRepository

logger = logging.getLogger(__name__)

# How often the master loop wakes up to check for due tasks.
MASTER_LOOP_INTERVAL_SECONDS = 30


class SchedulerCog(commands.Cog):
    """Cog that periodically runs Claude Code tasks stored in SQLite.

    Args:
        bot: The Discord bot instance.
        runner: Base ClaudeRunner to clone per task execution.
        repo: TaskRepository for reading/updating scheduled tasks.
    """

    def __init__(
        self,
        bot: commands.Bot,
        runner: SessionBackend,
        *,
        repo: TaskRepository,
        session_repo: SessionRepository | None = None,
        backend_factory: BackendFactory | None = None,
        backend_settings: BackendSettings | None = None,
        frontend: SessionFrontend | None = None,
        wait_repo: WaitRepository | None = None,
    ) -> None:
        self.bot = bot
        # Threads with an active wait (/api/waits) will also be posted into
        # later, so they share the ⏰ marker. One reconciler owns the marker —
        # two would keep undoing each other's renames.
        self.wait_repo = wait_repo
        self.runner = runner
        self.repo = repo
        self.session_repo = session_repo
        self.backend_factory = backend_factory
        self.backend_settings = backend_settings
        # Where scheduled runs are posted. Defaults to Discord so an existing
        # deployment needs no wiring; a Teams deployment passes its own and
        # this Cog does not change.
        self.frontend: SessionFrontend = frontend or DiscordFrontend(bot)
        # Track in-flight tasks to avoid double-running the same task_id.
        self._running: set[int] = set()
        # Shows which threads are waiting on a follow-up task (the ⏰ title).
        self.thread_marker = ScheduledThreadMarker(bot)
        self._marker_task: asyncio.Task[None] | None = None

    async def cog_load(self) -> None:
        """Start the master loop when the Cog is loaded."""
        self._master_loop.start()
        logger.info("SchedulerCog loaded — master loop started")

    def cog_unload(self) -> None:
        """Cancel the master loop when the Cog is unloaded."""
        self._master_loop.cancel()
        logger.info("SchedulerCog unloaded — master loop stopped")

    @tasks.loop(seconds=MASTER_LOOP_INTERVAL_SECONDS)
    async def _master_loop(self) -> None:
        """Wake up every 30 s, find due tasks, and spawn them concurrently."""
        due = await self.repo.get_due()
        if due:
            logger.info("SchedulerCog: %d task(s) due", len(due))
        for task in due:
            task_id: int = task["id"]
            if task_id in self._running:
                logger.debug("Task %d still running — skipping", task_id)
                continue

            # Advance next_run_at *before* spawning to prevent duplicate runs
            # if the loop fires again before the task finishes.
            await self.repo.update_next_run(task_id, interval_seconds=task["interval_seconds"])

            # Claimed here, not only inside _run_task, so the reconcile below
            # already sees a fired one-shot as no longer waiting.
            self._running.add(task_id)
            asyncio.create_task(
                self._run_task(task),
                name=f"ccdb-scheduler-{task_id}",
            )

        self._start_marker_reconcile()

    def _start_marker_reconcile(self) -> None:
        """Sync thread titles with the pending tasks, in the background.

        Backgrounded because discord.py sleeps through a rename rate limit (two
        per ten minutes per thread) and the master loop must not wait on that —
        a due task would be late by the length of the sleep.  A reconcile still
        in flight makes this tick skip; the next tick picks up whatever changed.
        """
        if self._marker_task is not None and not self._marker_task.done():
            return
        self._marker_task = asyncio.create_task(
            self._reconcile_markers(), name="ccdb-scheduler-thread-marker"
        )

    async def _reconcile_markers(self) -> None:
        try:
            await self.thread_marker.reconcile(await self._pending_thread_ids())
        except Exception:
            logger.exception("SchedulerCog: failed to update scheduled thread markers")

    async def _pending_thread_ids(self) -> set[int]:
        """Threads an enabled task will post into later.

        A one-shot that is already running is no longer *waiting* — it will be
        disabled when it finishes — so its thread drops out as soon as it fires.
        A recurring task keeps its thread pending while it runs.
        """
        pending = {
            task["thread_id"]
            for task in await self.repo.get_all()
            if task["enabled"]
            and task.get("thread_id")
            and not (task["one_shot"] and task["id"] in self._running)
        }
        if self.wait_repo is not None:
            pending |= await self.wait_repo.active_thread_ids()
        return pending

    @_master_loop.before_loop
    async def _before_master_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def _run_task(self, task: dict) -> None:
        """Execute a single scheduled task in a Discord thread.

        When ``thread_id`` is set, posts into that existing thread (follow-up
        mode) and optionally resumes the previous session.  Otherwise, creates
        a new thread in the parent channel (original behavior).

        When ``one_shot`` is True, the task is disabled after execution.
        """
        task_id: int = task["id"]
        self._running.add(task_id)
        try:
            thread_id = task.get("thread_id")
            session_id: str | None = None
            surface: ConversationSurface | None = None

            if thread_id:
                # Follow-up mode: post into the existing conversation.
                surface = await self.frontend.resolve_surface(thread_id)
                if surface is None:
                    logger.warning(
                        "SchedulerCog: conversation %d not found for task %d (%s) — falling back",
                        thread_id,
                        task_id,
                        task["name"],
                    )
                else:
                    await surface.send_notice(
                        Notice(level=NoticeLevel.INFO, body=f"🔄 **[Follow-up]** `{task['name']}`")
                    )
                    # Try to resume the previous session in this conversation
                    if self.session_repo is not None:
                        record = await self.session_repo.get(thread_id)
                        if record is not None:
                            session_id = record.session_id
                            logger.info(
                                "SchedulerCog: resuming session %s in thread %d",
                                session_id,
                                thread_id,
                            )

            if surface is None:
                surface = await self._open_new_conversation(task)
                if surface is None:
                    return

            cloned = await build_headless_runner(
                self.runner,
                factory=self.backend_factory,
                settings=self.backend_settings,
                thread_id=surface.thread_key,
                working_dir=task.get("working_dir"),
            )

            registry = getattr(self.bot, "session_registry", None)
            await run_claude_with_config(
                RunConfig(
                    surface=surface,
                    runner=cloned,
                    repo=self.session_repo,
                    prompt=task["prompt"],
                    session_id=session_id,
                    registry=registry,
                    backend_settings=self.backend_settings,
                )
            )

            # One-shot tasks auto-disable after execution
            if task.get("one_shot"):
                await self.repo.set_enabled(task_id, enabled=False)
                logger.info("SchedulerCog: one-shot task %d (%s) disabled", task_id, task["name"])

        except Exception:
            logger.exception("SchedulerCog: task %d (%s) failed", task_id, task["name"])
        finally:
            self._running.discard(task_id)

    async def _open_new_conversation(self, task: dict) -> ConversationSurface | None:
        """Start a fresh conversation under the task's parent channel.

        A missing or unusable parent is a configuration problem, not a
        transient one, so it is logged and the task is skipped — the master
        loop must survive one badly configured task.
        """
        try:
            return await self.frontend.create_surface(
                parent_id=str(task["channel_id"]),
                title=f"🔄 [Scheduled] {task['name']}",
            )
        except LookupError:
            logger.warning(
                "SchedulerCog: cannot open a conversation under %s for task %d (%s)",
                task["channel_id"],
                task["id"],
                task["name"],
            )
            return None
