"""Reserved restart — restart the bot once nothing is running, keeping the queue.

Restarting the bot under load used to mean waiting for an idle moment by hand.
New turns keep arriving, so that moment may never come, and a restart dropped
every run that was waiting for a session slot.

A reservation changes the order of events:

1. The slot queue is **held**: running sessions continue, but no waiter starts
   and every new request joins the queue (whatever its entry point — chat,
   console, scheduler, REST spawn all go through the same scheduler).
2. When no session is running, every queued run is written to
   ``pending_resumes`` with the prompt it would have sent, and the restart
   action runs.
3. On startup the queued runs are replayed (``ClaudeChatCog.on_ready``), in
   queue order, through the normal slot limit.

Cancelling releases the hold and the queue drains as usual.  A failed restart
action also releases the hold — a bot that is stuck holding its queue forever
is worse than one that did not restart.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .session_slots import SessionSlotScheduler

logger = logging.getLogger(__name__)

#: ``pending_resumes.reason`` for a run that was queued, not running, at shutdown.
QUEUED_RESTART_REASON = "queued_before_restart"

#: ``pending_resumes.reason`` for the thread that asked to be resumed after the restart.
RESERVED_RESTART_REASON = "reserved_restart"

#: Environment variable holding the restart command (shell-style words, no shell).
RESTART_COMMAND_ENV = "CCDB_RESTART_COMMAND"

DEFAULT_POLL_INTERVAL = 2.0
# A restart command that is still running after this long is assumed to be
# killing us (``systemctl restart`` of our own unit never returns to us).
_COMMAND_GRACE_SECONDS = 30.0

RestartAction = Callable[[], Awaitable[None]]


async def persist_queued_runs(slots: SessionSlotScheduler, resume_repo: Any) -> list[int]:
    """Write every queued run to ``pending_resumes`` so it survives a restart.

    Returns the thread ids written.  Safe to call more than once: the table
    keeps one row per thread, and every call writes the same prompt.
    """
    if resume_repo is None:
        return []
    written: list[int] = []
    for entry in slots.waiting_entries():
        if not entry.resume_prompt:
            continue
        try:
            await resume_repo.mark(
                entry.thread_key,
                reason=QUEUED_RESTART_REASON,
                resume_prompt=entry.resume_prompt,
            )
            written.append(entry.thread_key)
        except Exception:
            logger.warning(
                "Could not save queued run of thread %d for restart",
                entry.thread_key,
                exc_info=True,
            )
    if written:
        logger.info("Saved %d queued run(s) to replay after restart", len(written))
    return written


def build_restart_action(
    bot: Any,
    *,
    command: str | None = None,
    grace_seconds: float = _COMMAND_GRACE_SECONDS,
) -> RestartAction:
    """The restart to run once idle.

    With ``CCDB_RESTART_COMMAND`` (e.g. ``sudo systemctl restart my-bot``) that
    command runs.  Without it the bot closes itself and relies on its
    supervisor (systemd ``Restart=always``, Docker ``restart:``) to start it again.

    The command is expected to end this process.  If we are still alive
    ``grace_seconds`` after it was started (still running) or after it exited,
    the restart did not happen and this raises, so the queue is released.
    """
    raw = command if command is not None else os.getenv(RESTART_COMMAND_ENV, "")
    argv = shlex.split(raw) if raw.strip() else []

    async def _run_command() -> None:
        logger.info("Reserved restart: running %s", argv[0])
        proc = await asyncio.create_subprocess_exec(*argv)
        try:
            code = await asyncio.wait_for(proc.wait(), timeout=grace_seconds)
        except TimeoutError as exc:
            raise RuntimeError(f"restart command still running after {grace_seconds:.0f}s") from exc
        if code != 0:
            raise RuntimeError(f"restart command exited with {code}")
        await asyncio.sleep(grace_seconds)
        raise RuntimeError("restart command succeeded but this process was not restarted")

    async def _close_bot() -> None:
        logger.info("Reserved restart: closing the bot for the supervisor to restart it")
        await bot.close()

    return _run_command if argv else _close_bot


class RestartReservation:
    """Hold the queue, wait for the running sessions, then restart."""

    def __init__(
        self,
        slots: SessionSlotScheduler,
        restart: RestartAction,
        *,
        resume_repo: Any = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
    ) -> None:
        self._slots = slots
        self._restart = restart
        self._resume_repo = resume_repo
        self._poll_interval = poll_interval
        self._task: asyncio.Task[None] | None = None
        self._phase = "idle"
        self._requested_by: str | None = None
        self._reason: str | None = None
        self._reserved_at: float | None = None
        self._deadline: float | None = None
        self._last_error: str | None = None
        self._resume_thread_id: int | None = None
        self._resume_prompt: str | None = None

    @property
    def reserved(self) -> bool:
        return self._task is not None and not self._task.done()

    def status(self) -> dict[str, object]:
        snapshot = self._slots.snapshot()
        return {
            "reserved": self.reserved,
            "phase": self._phase,
            "requested_by": self._requested_by,
            "reason": self._reason,
            "reserved_at": self._reserved_at,
            "deadline": self._deadline,
            "running": sum(1 for i in snapshot if i.running),
            "waiting": sum(1 for i in snapshot if not i.running),
            "last_error": self._last_error,
            "resume_thread_id": (
                str(self._resume_thread_id) if self._resume_thread_id is not None else None
            ),
        }

    def reserve(
        self,
        *,
        requested_by: str = "",
        reason: str = "",
        resume_thread_id: int | None = None,
        resume_prompt: str | None = None,
        max_wait_seconds: float | None = None,
    ) -> bool:
        """Start the reservation. False when one is already pending.

        ``resume_thread_id`` names a thread to continue after the restart
        (typically the session that asked for it, to verify the new build).
        It is saved just before restarting, so the wait for the running
        sessions cannot outlast the pending-resume TTL.

        ``max_wait_seconds`` gives up (and releases the queue) when the running
        sessions take longer than that.  ``None`` waits as long as it takes.
        """
        if self.reserved:
            return False
        self._resume_thread_id = resume_thread_id
        self._resume_prompt = resume_prompt or None
        self._requested_by = requested_by or None
        self._reason = reason or None
        self._reserved_at = time.time()
        self._deadline = self._reserved_at + max_wait_seconds if max_wait_seconds else None
        self._last_error = None
        self._phase = "draining"
        self._slots.hold()
        logger.info(
            "Restart reserved (by=%s): %d running, waiting for them to finish",
            requested_by or "-",
            self._slots.running_count,
        )
        self._task = asyncio.create_task(self._run())
        return True

    def cancel(self) -> bool:
        """Drop the reservation and let the queue run. False if none, or too late."""
        if not self.reserved or self._phase == "restarting":
            return False
        assert self._task is not None
        self._task.cancel()
        self._task = None
        self._release("idle")
        logger.info("Restart reservation cancelled")
        return True

    def _release(self, phase: str, error: str | None = None) -> None:
        self._phase = phase
        self._last_error = error
        self._slots.unhold()

    async def _run(self) -> None:
        while self._slots.running_count > 0:
            if self._deadline is not None and time.time() >= self._deadline:
                logger.warning("Reserved restart gave up: sessions still running")
                self._release("idle", "timed out waiting for running sessions")
                return
            await asyncio.sleep(self._poll_interval)
        self._phase = "restarting"
        saved: list[int] = []
        try:
            saved = await self._save_for_restart()
            await self._restart()
        except Exception as exc:
            logger.exception("Reserved restart failed; releasing the queue")
            # The queue runs here after all; replaying it later would run it twice.
            await self._forget(saved)
            self._release("idle", f"{type(exc).__name__}: {exc}")

    async def _save_for_restart(self) -> list[int]:
        saved = await persist_queued_runs(self._slots, self._resume_repo)
        thread_id = self._resume_thread_id
        # A queued message of that thread already resumes it, and a second row
        # would replace the message (one row per thread).
        if self._resume_repo is not None and thread_id is not None and thread_id not in saved:
            await self._resume_repo.mark(
                thread_id, reason=RESERVED_RESTART_REASON, resume_prompt=self._resume_prompt
            )
            saved.append(thread_id)
        return saved

    async def _forget(self, thread_ids: list[int]) -> None:
        if self._resume_repo is None:
            return
        for thread_id in thread_ids:
            try:
                await self._resume_repo.delete_by_thread(thread_id)
            except Exception:
                logger.warning("Could not drop saved run of thread %d", thread_id, exc_info=True)


# ---------------------------------------------------------------------------
# Process-wide instance
# ---------------------------------------------------------------------------
_reservation: RestartReservation | None = None


def get_restart_reservation() -> RestartReservation | None:
    return _reservation


def set_restart_reservation(reservation: RestartReservation | None) -> None:
    global _reservation  # noqa: PLW0603
    _reservation = reservation
