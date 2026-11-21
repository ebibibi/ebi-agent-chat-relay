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
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Any

from .human_activity import last_human_activity
from .session_slots import SessionSlotScheduler, get_session_slots

logger = logging.getLogger(__name__)

#: ``pending_resumes.reason`` for a run that was queued, not running, at shutdown.
QUEUED_RESTART_REASON = "queued_before_restart"

#: ``pending_resumes.reason`` for the thread that asked to be resumed after the restart.
RESERVED_RESTART_REASON = "reserved_restart"

#: Environment variable holding the restart command (shell-style words, no shell).
RESTART_COMMAND_ENV = "CCDB_RESTART_COMMAND"

#: Environment variable: seconds without human activity before a restart may begin.
QUIET_SECONDS_ENV = "CCDB_RESTART_QUIET_SECONDS"
DEFAULT_QUIET_SECONDS = 900.0
_MAX_EVENTS = 20

DEFAULT_POLL_INTERVAL = 2.0
# A restart command that is still running after this long is assumed to be
# killing us (``systemctl restart`` of our own unit never returns to us).
_COMMAND_GRACE_SECONDS = 30.0

RestartAction = Callable[[], Awaitable[None]]


def quiet_seconds_from_env() -> float:
    """``CCDB_RESTART_QUIET_SECONDS`` (default 15 minutes); a bad value falls back to it."""
    raw = os.getenv(QUIET_SECONDS_ENV, "").strip()
    if not raw:
        return DEFAULT_QUIET_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "%s=%r is not a number; using %.0f", QUIET_SECONDS_ENV, raw, DEFAULT_QUIET_SECONDS
        )
        return DEFAULT_QUIET_SECONDS
    return max(0.0, value)


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


async def _wait_for_bot_shutdown(bot: Any, timeout: float) -> bool:
    """Wait for the bot's signal handler to begin closing it after its service is stopped."""
    is_closed = getattr(bot, "is_closed", None)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, timeout)
    while True:
        if callable(is_closed):
            try:
                if is_closed() is True:
                    return True
            except Exception:
                logger.debug("Could not inspect bot shutdown state", exc_info=True)
        remaining = deadline - loop.time()
        if remaining <= 0:
            return False
        await asyncio.sleep(min(0.05, remaining))


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
        # A negative code means a signal ended the command. ``systemctl restart`` run from
        # inside the service is killed with the service's cgroup: that is the restart under
        # way, not a failure. Whether it worked is decided by this process surviving below.
        if code > 0:
            raise RuntimeError(f"restart command exited with {code}")
        if await _wait_for_bot_shutdown(bot, grace_seconds):
            # The supervisor has started closing this bot. Keep the held queue and
            # saved resumes intact; releasing them here can start work in a dying
            # process immediately before startup replays the same runs.
            return
        if code < 0:
            raise RuntimeError(
                f"restart command exited with {code} but this process was not stopped"
            )
        raise RuntimeError("restart command succeeded but this process was not restarted")

    async def _close_bot() -> None:
        logger.info("Reserved restart: closing the bot for the supervisor to restart it")
        await bot.close()

    return _run_command if argv else _close_bot


class RestartReservation:
    """Wait for a quiet moment, hold the queue, wait for the running sessions, then restart.

    Phases (``status()["phase"]``):

    - ``waiting_for_quiet``: nothing is held. The reservation waits until no
      person has acted for ``quiet_seconds`` and any postponement has passed.
    - ``draining``: the queue is held and the running turns are finishing. A
      person acting now sends the reservation back to ``waiting_for_quiet``
      and the queue runs again.
    - ``restarting``: the queued runs are saved and the restart action runs.

    An *urgent* reservation (a security fix, a broken bot) skips the quiet wait
    and is not sent back by human activity; it still lets running turns finish.
    """

    def __init__(
        self,
        slots: SessionSlotScheduler,
        restart: RestartAction,
        *,
        resume_repo: Any = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        quiet_seconds: float = 0.0,
        last_activity: Callable[[], float | None] = last_human_activity,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._slots = slots
        self._restart = restart
        self._resume_repo = resume_repo
        self._poll_interval = poll_interval
        self._quiet_seconds = max(0.0, quiet_seconds)
        self._last_activity = last_activity
        self._clock = clock
        self._task: asyncio.Task[None] | None = None
        self._phase = "idle"
        self._requested_by: str | None = None
        self._reason: str | None = None
        self._reserved_at: float | None = None
        self._deadline: float | None = None
        self._last_error: str | None = None
        self._resumes: dict[int, str | None] = {}
        self._urgent = False
        self._not_before: float | None = None
        self._drain_started: float | None = None
        self._requests = 0
        self._events: deque[dict[str, object]] = deque(maxlen=_MAX_EVENTS)

    @property
    def reserved(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def quiet_seconds(self) -> float:
        return self._quiet_seconds

    def status(self) -> dict[str, object]:
        snapshot = self._slots.snapshot()
        last = self._last_activity()
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
            "resume_thread_id": (str(next(iter(self._resumes))) if self._resumes else None),
            "urgent": self._urgent,
            "requests": self._requests,
            "quiet_seconds": self._quiet_seconds,
            "last_human_activity": last,
            "quiet_at": self._quiet_at(last),
            "not_before": self._not_before,
            "events": list(self._events),
        }

    # -- events ----------------------------------------------------------------

    def _event(self, text: str) -> None:
        """Log why the reservation did what it did, and keep it for the Console."""
        logger.info("Restart reservation: %s", text)
        self._events.append({"at": self._clock(), "text": text})

    # -- requests ----------------------------------------------------------------

    def reserve(
        self,
        *,
        requested_by: str = "",
        reason: str = "",
        resume_thread_id: int | None = None,
        resume_prompt: str | None = None,
        max_wait_seconds: float | None = None,
        urgent: bool = False,
    ) -> bool:
        """Start the reservation. False when one is already pending.

        A second request joins the pending one instead of being dropped: its
        thread to resume is added, and ``urgent`` makes the pending one urgent.
        Several deploys in a row therefore cost one restart.

        ``resume_thread_id`` names a thread to continue after the restart
        (typically the session that asked for it, to verify the new build).
        It is saved just before restarting, so the wait cannot outlast the
        pending-resume TTL.

        ``max_wait_seconds`` gives up (and releases the queue) when the restart
        has not happened that long after the reservation. ``None`` waits as
        long as it takes.
        """
        who = requested_by or "-"
        why = reason or "no reason given"
        if self.reserved:
            self._requests += 1
            self._add_resume(resume_thread_id, resume_prompt)
            joined = f"joined by {who} ({why})"
            if urgent and not self._urgent:
                self._urgent = True
                joined += "; now urgent"
            self._event(joined)
            return False
        self._resumes = {}
        self._add_resume(resume_thread_id, resume_prompt)
        self._requested_by = requested_by or None
        self._reason = reason or None
        self._reserved_at = self._clock()
        self._deadline = self._reserved_at + max_wait_seconds if max_wait_seconds else None
        self._last_error = None
        self._urgent = urgent
        self._not_before = None
        self._requests = 1
        self._events.clear()
        self._event(f"reserved by {who} ({why}){' — urgent' if urgent else ''}")
        if self._ready():
            self._start_drain()
        else:
            self._phase = "waiting_for_quiet"
            self._event(self._waiting_text())
        self._task = asyncio.create_task(self._run())
        return True

    def _add_resume(self, thread_id: int | None, prompt: str | None) -> None:
        if thread_id is not None and thread_id not in self._resumes:
            self._resumes[thread_id] = prompt or None

    def restart_now(self, *, requested_by: str = "") -> bool:
        """Skip the quiet wait (a person chose to restart now). False if nothing is reserved."""
        if not self.reserved or self._phase == "restarting":
            return False
        self._urgent = True
        self._not_before = None
        self._event(f"{requested_by or 'someone'} chose to restart now")
        return True

    def postpone(self, seconds: float, *, requested_by: str = "") -> bool:
        """Not before ``seconds`` from now. Releases a hold already in place."""
        if not self.reserved or self._phase == "restarting" or seconds <= 0:
            return False
        self._urgent = False
        self._not_before = self._clock() + seconds
        if self._phase == "draining":
            self._stop_drain()
        self._event(f"{requested_by or 'someone'} postponed it by {seconds / 60:.0f} min")
        return True

    def cancel(self) -> bool:
        """Drop the reservation and let the queue run. False if none, or too late."""
        if not self.reserved or self._phase == "restarting":
            return False
        assert self._task is not None
        self._task.cancel()
        self._task = None
        self._event("cancelled")
        self._release("idle")
        return True

    # -- the wait ------------------------------------------------------------------

    def _quiet_at(self, last: float | None) -> float | None:
        """When the quiet period ends (None: nobody has acted, already quiet)."""
        quiet = last + self._quiet_seconds if last is not None else None
        if self._not_before is None:
            return quiet
        return max(quiet or 0.0, self._not_before)

    def _ready(self) -> bool:
        if self._urgent:
            return True
        at = self._quiet_at(self._last_activity())
        return at is None or self._clock() >= at

    def _waiting_text(self) -> str:
        if self._not_before is not None and self._clock() < self._not_before:
            return "postponed; waiting for that time to pass"
        return f"waiting until nobody has used the bot for {self._quiet_seconds / 60:.0f} min"

    def _interrupted(self) -> bool:
        """A person acted after the hold started (or the restart was postponed)."""
        if self._urgent:
            return False
        last = self._last_activity()
        return last is not None and self._drain_started is not None and last > self._drain_started

    def _start_drain(self) -> None:
        self._phase = "draining"
        self._drain_started = self._clock()
        self._slots.hold()
        finishing = self._slots.finishing_count
        wrapping = f", {finishing} wrapping up" if finishing else ""
        self._event(
            f"quiet: holding the queue; {self._slots.running_count} running to finish{wrapping}"
        )

    def _stop_drain(self) -> None:
        self._drain_started = None
        self._phase = "waiting_for_quiet"
        self._slots.unhold()

    def _release(self, phase: str, error: str | None = None) -> None:
        self._phase = phase
        self._last_error = error
        self._drain_started = None
        self._slots.unhold()

    def _timed_out(self) -> bool:
        if self._deadline is None or self._clock() < self._deadline:
            return False
        self._event("gave up: no quiet moment before the deadline")
        self._release("idle", "timed out waiting for running sessions")
        return True

    async def _wait_for_quiet(self) -> bool:
        """Until the quiet rule allows a hold. False when the reservation gave up."""
        while not self._ready():
            if self._timed_out():
                return False
            await asyncio.sleep(self._poll_interval)
        self._start_drain()
        return True

    async def _drain(self) -> str:
        """Wait for the running turns: ``done``, ``again`` (back to waiting) or ``gave_up``."""
        while True:
            if self._phase != "draining":  # postponed meanwhile
                return "again"
            if self._timed_out():
                return "gave_up"
            if self._interrupted():
                self._stop_drain()
                self._event("someone is using the bot: released the queue, waiting again")
                return "again"
            # A turn that gave its slot back may still be delivering its result;
            # cutting it there resumes it after the restart for nothing.
            if self._slots.running_count == 0 and self._slots.finishing_count == 0:
                return "done"
            await asyncio.sleep(self._poll_interval)

    async def _run(self) -> None:
        while True:
            if self._phase != "draining" and not await self._wait_for_quiet():
                return
            outcome = await self._drain()
            if outcome == "gave_up":
                return
            if outcome == "done":
                break
        self._phase = "restarting"
        self._event("restarting")
        saved: list[int] = []
        try:
            saved = await self._save_for_restart()
            await self._restart()
        except Exception as exc:
            logger.exception("Reserved restart failed; releasing the queue")
            # The queue runs here after all; replaying it later would run it twice.
            await self._forget(saved)
            self._event(f"restart failed: {type(exc).__name__}")
            self._release("idle", f"{type(exc).__name__}: {exc}")

    async def _save_for_restart(self) -> list[int]:
        saved = await persist_queued_runs(self._slots, self._resume_repo)
        if self._resume_repo is None:
            return saved
        for thread_id, prompt in self._resumes.items():
            # A queued message of that thread already resumes it, and a second row
            # would replace the message (one row per thread).
            if thread_id in saved:
                continue
            await self._resume_repo.mark(
                thread_id, reason=RESERVED_RESTART_REASON, resume_prompt=prompt
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


def ensure_restart_reservation(bot: Any, resume_repo: Any) -> RestartReservation | None:
    """The process-wide reservation, created on first use. None without a session limit."""
    reservation = get_restart_reservation()
    if reservation is not None:
        return reservation
    slots = get_session_slots()
    if slots is None:
        return None
    reservation = RestartReservation(
        slots,
        build_restart_action(bot),
        resume_repo=resume_repo,
        quiet_seconds=quiet_seconds_from_env(),
    )
    set_restart_reservation(reservation)
    return reservation
