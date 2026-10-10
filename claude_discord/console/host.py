"""Run agent turns for console conversations, with no chat platform in between.

The console's counterpart of ``TeamsSessionHost``: it builds the runner the
same way, resumes the same session, and hands the turn to the same session
runner Discord uses. Only the surface differs.

Turns for one conversation run one at a time. A reply that arrives while a
turn is running waits for it, the way a chat reply queues behind work in
flight — a reply never interrupts.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from ..backend_settings import session_is_resumable
from ..cogs._run_helper import run_claude_with_config
from ..cogs.run_config import RunConfig
from ..execution_settings import apply_thread_execution_mode
from ..thread_marker import OUTCOME_ERROR
from .conversations import CONSOLE_FRONTEND
from .surface import ConsoleFrontend, ConsoleSurface, apply_outcome

logger = logging.getLogger(__name__)


class ConsoleSessionHost:
    """Start and continue console conversations."""

    def __init__(
        self,
        *,
        frontend: ConsoleFrontend,
        session_repo: Any,
        backend_factory: Any,
        backend_settings: Any,
        run_session: Callable[[RunConfig], Awaitable[str | None]] = run_claude_with_config,
        lounge_repo: Any = None,
        ask_repo: Any = None,
        usage_repo: Any = None,
        registry: Any = None,
        worktree_manager: Any = None,
    ) -> None:
        self.frontend = frontend
        self._session_repo = session_repo
        self._factory = backend_factory
        self._settings = backend_settings
        self._run_session = run_session
        self._lounge_repo = lounge_repo
        self._ask_repo = ask_repo
        self._usage_repo = usage_repo
        self._registry = registry
        self._worktree_manager = worktree_manager
        self._locks: dict[int, asyncio.Lock] = {}
        # Strong references: the loop keeps only weak ones to running tasks.
        self._tasks: set[asyncio.Task[Any]] = set()

    async def owns(self, thread_key: int) -> bool:
        return await self.frontend.owns(thread_key)

    async def start(self, *, external_id: str, title: str, prompt: str, author: str) -> int:
        """Open a conversation for *external_id* and start its first turn.

        Returns as soon as the conversation exists; the turn runs in the
        background.
        """
        surface = await self.frontend.open(external_id=external_id, title=title)
        await self._submit(surface, prompt, author)
        return surface.thread_key

    async def reply(self, thread_key: int, prompt: str, author: str) -> None:
        """Continue a conversation with the human's message."""
        surface = await self.frontend.resolve_surface(thread_key)
        if surface is None:
            raise LookupError(f"unknown console conversation {thread_key}")
        # A reply answers whatever the marker asked; clear it like a chat reply would.
        await apply_outcome(self.frontend.repo, thread_key, None)
        await self._submit(surface, prompt, author)

    async def _submit(self, surface: ConsoleSurface, prompt: str, author: str) -> None:
        await self.frontend.repo.append(
            surface.thread_key, author=author, is_bot=False, content=prompt
        )
        task = asyncio.create_task(self._run_queued(surface, prompt))
        self._tasks.add(task)
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("console: turn failed", exc_info=task.exception())

    async def _run_queued(self, surface: ConsoleSurface, prompt: str) -> None:
        lock = self._locks.setdefault(surface.thread_key, asyncio.Lock())
        async with lock:
            try:
                await self._run(surface, prompt)
            except Exception:
                logger.exception("console: turn for %d failed", surface.thread_key)
                await surface.send_text("⚠️ The turn could not run. See the bot log.")
                await surface.set_outcome(OUTCOME_ERROR)

    async def _run(self, surface: ConsoleSurface, prompt: str) -> None:
        thread_key = surface.thread_key
        record = await self._session_repo.get(thread_key)
        backend = await self._settings.current_backend(thread_key)
        model = await self._settings.current_model(backend, thread_key)
        runner = self._factory.build(backend=backend, model=model, thread_id=thread_key)
        await apply_thread_execution_mode(runner, getattr(self._settings, "repo", None), thread_key)

        session_id = None
        if record is not None and session_is_resumable(record.backend, backend):
            session_id = record.session_id
            if record.working_dir:
                runner.working_dir = record.working_dir

        effort = await self._settings.current_effort(backend, thread_key)
        if effort is not None and hasattr(runner, "effort"):
            runner.effort = effort

        await self._run_session(
            RunConfig(
                surface=surface,
                runner=runner,
                repo=self._session_repo,
                prompt=prompt,
                session_id=session_id,
                lounge_repo=self._lounge_repo,
                ask_repo=self._ask_repo,
                usage_repo=self._usage_repo,
                registry=self._registry,
                worktree_manager=self._worktree_manager,
                backend_settings=self._settings,
                codex_command=self._factory.codex_command,
                claude_command=runner.command,
                session_origin=CONSOLE_FRONTEND,
            )
        )
