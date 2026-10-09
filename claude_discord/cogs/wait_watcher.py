"""WaitWatcherCog — runs due wait probes and resumes threads when a wait ends.

See :mod:`claude_discord.waits` for what a wait is and why it exists.  This Cog
is the "when": a loop that looks at due waits, runs each probe (no model, no
session slot), stores the result, and — once the probe says done, the wait
times out, or the probe keeps failing — starts the thread's next turn with a
fixed continuation prompt.

The resume goes through ``ClaudeChatCog.deliver_relayed_message`` with
``interrupt=False``: it posts the prompt into the thread so the humans watching
see why the session woke up, and it queues behind a turn already running in the
thread under the per-thread lock, exactly like a human reply.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from discord.ext import commands, tasks

from .. import waits
from ..waits import (
    MAX_CONSECUTIVE_ERRORS,
    OUTCOME_DONE,
    OUTCOME_PROBE_ERROR,
    OUTCOME_TIMEOUT,
    ProbeResult,
    Verdict,
    build_wait_prompt,
    evaluate_probe,
    run_probe,
)

if TYPE_CHECKING:
    from ..database.wait_repo import Wait, WaitRepository

logger = logging.getLogger(__name__)

#: How often due waits are looked for. Each wait has its own interval (>= 30 s);
#: this only bounds how late a due probe can start.
LOOP_INTERVAL_SECONDS = 15
#: Probes run at the same time — they are cheap, but a burst of 50 `az` calls is not.
MAX_PARALLEL_PROBES = 4

ProbeFn = Callable[..., Awaitable[ProbeResult]]
DeliverFn = Callable[[int, str], Awaitable[bool]]


class WaitWatcherCog(commands.Cog):
    """Poll due waits and resume their threads.

    Args:
        bot: The Discord bot.
        repo: Where waits are stored.
        probe: Runs one probe (tests inject a fake).
        deliver: Starts the thread's next turn with a prompt; returns False when
            the thread could not be reached. Defaults to the Discord path.
        clock: Unix-time source (tests inject a fake).
    """

    def __init__(
        self,
        bot: commands.Bot,
        repo: WaitRepository,
        *,
        probe: ProbeFn | None = None,
        deliver: DeliverFn | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.bot = bot
        self.repo = repo
        self._probe: ProbeFn = probe or run_probe
        self._deliver: DeliverFn = deliver or self._deliver_to_discord
        self._clock = clock or time.time
        self._slots = asyncio.Semaphore(MAX_PARALLEL_PROBES)
        self._in_flight: set[int] = set()
        self._resumes: set[asyncio.Task[None]] = set()
        self._loop = self._watch_loop

    async def cog_load(self) -> None:
        self._loop.start()
        waits.set_watcher_active(True)
        logger.info("WaitWatcherCog loaded — watching for due waits")

    async def cog_unload(self) -> None:
        self._loop.cancel()
        waits.set_watcher_active(False)

    @tasks.loop(seconds=LOOP_INTERVAL_SECONDS)
    async def _watch_loop(self) -> None:
        try:
            await self.tick()
        except Exception:
            logger.exception("WaitWatcherCog: tick failed")

    @_watch_loop.before_loop
    async def _before_loop(self) -> None:
        await self.bot.wait_until_ready()

    async def tick(self) -> None:
        """Check every due wait once. Waits whose previous check is still running are skipped."""
        due = [w for w in await self.repo.due(now=self._clock()) if w.id not in self._in_flight]
        await asyncio.gather(*(self._check(w) for w in due))

    async def drain(self) -> None:
        """Wait for resumes started by earlier ticks (tests, clean shutdown)."""
        if self._resumes:
            await asyncio.gather(*self._resumes, return_exceptions=True)

    async def _check(self, wait: Wait) -> None:
        self._in_flight.add(wait.id)
        try:
            async with self._slots:
                result = await self._probe(wait.argv, cwd=wait.cwd)
            now = self._clock()
            # done_pattern is caller-supplied: a pathological regex must stall a
            # worker thread, not the event loop the whole bot runs on.
            verdict = await asyncio.to_thread(evaluate_probe, wait.to_spec(), result)
            errors = wait.consecutive_errors + 1 if verdict is Verdict.ERROR else 0
            if verdict is Verdict.DONE:
                await self._finish(wait, OUTCOME_DONE, result, now)
            elif errors >= MAX_CONSECUTIVE_ERRORS:
                await self._finish(wait, OUTCOME_PROBE_ERROR, result, now)
            elif now >= wait.deadline:
                await self._finish(wait, OUTCOME_TIMEOUT, result, now)
            else:
                await self.repo.record_probe(
                    wait.id,
                    result,
                    next_check_at=now + wait.interval_seconds,
                    consecutive_errors=errors,
                )
        except Exception:
            logger.exception("WaitWatcherCog: checking wait %d failed", wait.id)
        finally:
            self._in_flight.discard(wait.id)

    async def _finish(self, wait: Wait, outcome: str, result: ProbeResult, now: float) -> None:
        await self.repo.record_probe(
            wait.id, result, next_check_at=now, consecutive_errors=wait.consecutive_errors
        )
        # finish() is the claim: only the caller that flips the row resumes,
        # so a cancel or a second watcher racing this one cannot double-fire.
        if not await self.repo.finish(wait.id, outcome, now=now):
            return
        logger.info("Wait %d (thread %d) ended: %s", wait.id, wait.thread_id, outcome)
        prompt = build_wait_prompt(
            wait_id=wait.id,
            label=wait.label,
            argv=wait.argv,
            outcome=outcome,
            exit_code=result.exit_code,
            output=result.output,
            note=wait.note,
            error=result.error,
        )
        # The resumed turn can run for a long time; it must not hold up the loop.
        task = asyncio.create_task(self._resume(wait, prompt), name=f"ccdb-wait-{wait.id}")
        self._resumes.add(task)
        task.add_done_callback(self._resumes.discard)

    async def _resume(self, wait: Wait, prompt: str) -> None:
        try:
            delivered = await self._deliver(wait.thread_id, prompt)
        except Exception:
            logger.exception("WaitWatcherCog: resuming thread %d failed", wait.thread_id)
            return
        if not delivered:
            logger.warning(
                "WaitWatcherCog: could not resume thread %d for wait %d",
                wait.thread_id,
                wait.id,
            )

    async def _deliver_to_discord(self, thread_id: int, prompt: str) -> bool:
        import discord

        cog = self.bot.cogs.get("ClaudeChatCog")
        if cog is None:
            return False
        thread = self.bot.get_channel(thread_id)
        if thread is None:
            try:
                thread = await self.bot.fetch_channel(thread_id)
            except discord.HTTPException:
                return False
        if not isinstance(thread, discord.Thread):
            return False
        await cog.deliver_relayed_message(thread, prompt, interrupt=False)  # type: ignore[attr-defined]
        return True
