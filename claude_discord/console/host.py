"""Run agent turns for console conversations, with no chat platform in between.

The console's counterpart of ``TeamsSessionHost``: it builds the runner the
same way, resumes the same session, and hands the turn to the same session
runner Discord uses. Only the surface differs.

Turns for one conversation run one at a time. A human's reply that arrives
while a turn is running goes into that turn when its agent can take input
(Claude Code), the way the CLI's own UI does; otherwise it waits for the turn
to finish. A reply never interrupts.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from claude_code_core.account_pool import AccountTurn

from .. import live_runs
from ..account_turns import (
    failover_notice,
    plan_account_turn,
    plan_notice,
    refresh_codex_usage,
)
from ..backend_settings import session_is_resumable
from ..cogs._run_helper import run_claude_with_config
from ..cogs.run_config import RunConfig
from ..cross_backend_handoff import ConversationHistoryReader, carry_history_across_backends
from ..execution_settings import apply_thread_execution_mode, thread_execution_mode
from ..thread_marker import OUTCOME_ERROR
from .conversations import CONSOLE_FRONTEND
from .surface import AGENT_AUTHOR, ConsoleFrontend, ConsoleSurface, apply_outcome

if TYPE_CHECKING:
    from claude_code_core.account_router import AccountRouter, Failover

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
        conversation_history: ConversationHistoryReader | None = None,
        account_router: AccountRouter | None = None,
    ) -> None:
        self.frontend = frontend
        # Account pools (CCDB_ACCOUNT_POOLS_FILE), the same router Discord turns
        # use. None = one implicit login per backend.
        self._account_router = account_router
        self._conversation_history = conversation_history or ConversationHistoryReader()
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

    async def stop(self, thread_key: int, who: str) -> bool:
        """Press Stop on the conversation's running turn. False if nothing runs."""
        if not await self.frontend.live.stop(thread_key):
            return False
        # The CLI's own interruption output is a diagnostic; say plainly what happened.
        await self.frontend.repo.append(
            thread_key, author=who, is_bot=False, content=f"⏹ Stopped by {who}"
        )
        return True

    async def start(
        self, *, external_id: str, title: str, prompt: str, author: str, brief: str | None = None
    ) -> int:
        """Open a conversation for *external_id* and start its first turn.

        Returns as soon as the conversation exists; the turn runs in the
        background. *brief* goes to the agent with the prompt (see ``_submit``).
        """
        surface = await self.frontend.open(external_id=external_id, title=title)
        await self._submit(surface, prompt, author, brief=brief)
        return surface.thread_key

    async def reply(
        self,
        thread_key: int,
        prompt: str,
        author: str,
        *,
        into_running_turn: bool = False,
        brief: str | None = None,
    ) -> bool:
        """Continue a conversation with the human's message.

        Returns True when the message went into the turn already running,
        False when it was queued as a turn of its own.
        """
        surface = await self.frontend.resolve_surface(thread_key)
        if surface is None:
            raise LookupError(f"unknown console conversation {thread_key}")
        # A reply answers whatever the marker asked; clear it like a chat reply would.
        await apply_outcome(self.frontend.repo, thread_key, None)
        return await self._submit(
            surface, prompt, author, into_running_turn=into_running_turn, brief=brief
        )

    async def relay(self, thread_key: int, prompt: str, author: str, *, interrupt: bool) -> str:
        """Deliver another session's message, with the relay API's queue/interrupt meaning.

        ``interrupt`` presses Stop on the running turn first, so the message runs as
        soon as that turn has ended; otherwise it waits behind it. Returns
        ``"interrupted"`` when a running turn was stopped, else ``"queued"``.
        Raises LookupError when *thread_key* is not a console conversation.
        """
        if await self.frontend.resolve_surface(thread_key) is None:
            raise LookupError(f"unknown console conversation {thread_key}")
        stopped = interrupt and await self.frontend.live.stop(thread_key)
        if stopped:
            await self.frontend.repo.append(
                thread_key,
                author=AGENT_AUTHOR,
                is_bot=True,
                content="⚡ Interrupted by another session's message",
            )
        await self.reply(thread_key, prompt, author)
        return "interrupted" if stopped else "queued"

    async def resume_queued(self, thread_key: int, prompt: str) -> None:
        """Run a turn that was queued when the bot restarted.

        The human's message is already in the transcript, so it is not
        appended again.
        """
        surface = await self.frontend.resolve_surface(thread_key)
        if surface is None:
            raise LookupError(f"unknown console conversation {thread_key}")
        await surface.send_text(
            "🔄 Bot restarted. Running the message that was queued before the restart."
        )
        self._spawn(surface, prompt)

    async def _submit(
        self,
        surface: ConsoleSurface,
        prompt: str,
        author: str,
        *,
        into_running_turn: bool = False,
        brief: str | None = None,
    ) -> bool:
        """Record the human's message and run it.

        *brief* (Workspace instructions) is recorded as a bot message of its own, so
        the conversation keeps only what the person wrote and the Console shows the
        brief under activity. The agent receives both.
        """
        await self.frontend.repo.append(
            surface.thread_key, author=author, is_bot=False, content=prompt
        )
        if brief:
            await self.frontend.repo.append(
                surface.thread_key, author="Ebi Workspace", is_bot=True, content=brief
            )
            prompt = f"{prompt}\n\n{brief}"
        if into_running_turn and await self._inject(surface.thread_key, prompt):
            return True
        self._spawn(surface, prompt)
        return False

    async def _inject(self, thread_key: int, prompt: str) -> bool:
        """Deliver into the running turn, if it runs under this conversation's settings."""
        return await live_runs.try_inject(
            thread_key,
            prompt,
            backend=await self._settings.current_backend(thread_key),
            execution_mode=await thread_execution_mode(
                getattr(self._settings, "repo", None), thread_key
            ),
        )

    def _spawn(self, surface: ConsoleSurface, prompt: str) -> None:
        # Taken now, not when the task first runs: a reply arriving in between
        # must already see that this one is waiting.
        release = live_runs.hold(surface.thread_key)
        task = asyncio.create_task(self._run_queued(surface, prompt, release))
        self._tasks.add(task)
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("console: turn failed", exc_info=task.exception())

    async def _run_queued(
        self, surface: ConsoleSurface, prompt: str, release: Callable[[], None] = lambda: None
    ) -> None:
        lock = self._locks.setdefault(surface.thread_key, asyncio.Lock())
        try:
            async with lock:
                release()
                await self._run_locked(surface, prompt)
        finally:
            release()

    async def _run_locked(self, surface: ConsoleSurface, prompt: str) -> None:
        try:
            await self._run(surface, prompt)
        except Exception:
            logger.exception("console: turn for %d failed", surface.thread_key)
            await surface.send_text("⚠️ The turn could not run. See the bot log.")
            await surface.set_outcome(OUTCOME_ERROR)
        finally:
            # What was live is in the transcript now.
            self.frontend.live.clear(surface.thread_key)

    async def _run(
        self, surface: ConsoleSurface, prompt: str, *, _account_retry: bool = False
    ) -> None:
        user_prompt = prompt
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
        else:
            # A new backend cannot resume the old session natively; carry its
            # text so the conversation continues instead of starting blank.
            prompt = await carry_history_across_backends(
                self._conversation_history, record, backend, prompt
            )

        effort = await self._settings.current_effort(backend, thread_key)
        if effort is not None and hasattr(runner, "effort"):
            runner.effort = effort

        # The transcript names the model that wrote each answer.
        model_name = getattr(runner, "model", None)
        surface.agent_model = model_name if isinstance(model_name, str) and model_name else backend

        account_turn, session_id, prompt = await self._plan_account(
            surface, runner, backend, session_id, prompt
        )

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
                account_turn=account_turn,
            )
        )
        failover = await self._finish_account_turn(surface, account_turn)
        # Opt-in failover (retry_on_exhaustion): rerun the same message once on
        # the next profile, as a Discord thread does. A second rejection just reports.
        if failover is not None and failover.retry and not _account_retry:
            await self._run(surface, user_prompt, _account_retry=True)

    async def _plan_account(
        self,
        surface: ConsoleSurface,
        runner: Any,
        backend: str,
        session_id: str | None,
        prompt: str,
    ) -> tuple[AccountTurn | None, str | None, str]:
        """Choose the pool login for this turn; pass through without a pool."""
        if self._account_router is None:
            return None, session_id, prompt
        try:
            plan, session_id, prompt = await plan_account_turn(
                self._account_router,
                backend=backend,
                thread_id=surface.thread_key,
                session_id=session_id,
                prompt=prompt,
            )
        except Exception:
            # Never lose the person's turn to pool bookkeeping: run on the
            # relay's own login and say so in the log.
            logger.exception("Account pool: planning failed for %d", surface.thread_key)
            return None, session_id, prompt
        if plan is None or not hasattr(runner, "account"):
            return None, session_id, prompt
        runner.account = plan.binding
        notice = plan_notice(plan)
        if notice:
            await surface.send_text(notice)
        return AccountTurn(binding=plan.binding), session_id, prompt

    async def _finish_account_turn(
        self, surface: ConsoleSurface, turn: AccountTurn | None
    ) -> Failover | None:
        """Record a quota rejection, tell the conversation, refresh Codex usage."""
        if turn is None or self._account_router is None:
            return None
        if turn.binding.backend == "codex":
            task = asyncio.create_task(
                refresh_codex_usage(
                    self._account_router, turn.binding, self._factory.codex_command
                ),
                name=f"codex-usage-{turn.binding.profile}",
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        try:
            failover = await self._account_router.finish_turn(turn)
        except Exception:
            logger.exception("Account pool: could not record the outcome of a turn")
            return None
        if failover is not None:
            await surface.send_text(failover_notice(failover, relative=True))
        return failover
