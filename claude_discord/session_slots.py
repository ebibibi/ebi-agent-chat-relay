"""Session slot scheduler — the concurrency limit, with a queue people can steer.

``MAX_CONCURRENT_SESSIONS`` used to be a bare ``asyncio.Semaphore``: first come,
first served, and nothing anyone could do about it.  When every slot is busy
the operator usually knows better than arrival order — *this* thread is urgent,
*that* long-running one can wait.  This module keeps the same limit but makes
the queue reorderable:

- **prioritize**: a waiting run jumps ahead of every non-prioritized waiter.
- **defer**: a waiting run steps back behind everyone else and starts on its
  own once nobody is ahead of it.
- **pause**: a *running* session is interrupted to free its slot and re-queued
  as deferred, so it resumes automatically (same CLI session, via ``--resume``)
  when a slot is free again.  Pausing is refused when nobody is waiting —
  freeing a slot nobody will take only restarts the same session at once.

The scheduler is frontend-neutral and lives in one process-wide instance so
every entry point (chat, scheduler, webhooks, REST ingest) shares one limit,
exactly as the semaphore did.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import IntEnum

logger = logging.getLogger(__name__)


class SlotPriority(IntEnum):
    """Queue order. Higher starts first; equal priority keeps arrival order."""

    DEFERRED = -1
    NORMAL = 0
    PRIORITIZED = 1


@dataclass
class SlotEntry:
    """One run that holds, or is waiting for, a session slot."""

    thread_key: int
    label: str
    seq: int
    priority: SlotPriority = SlotPriority.NORMAL
    running: bool = False
    pause_requested: bool = False
    resumed_from_pause: bool = False
    enqueued_at: float = field(default_factory=time.time)
    started_at: float | None = None
    on_pause: Callable[[], Awaitable[None]] | None = None
    _granted: asyncio.Future[None] | None = None


@dataclass(frozen=True)
class SlotInfo:
    """Read-only view of an entry, for UIs and the REST API."""

    thread_key: int
    label: str
    running: bool
    priority: SlotPriority
    position: int | None
    resumed_from_pause: bool
    pause_requested: bool
    since: float

    def as_dict(self) -> dict[str, object]:
        return {
            "thread_id": str(self.thread_key),
            "label": self.label,
            "state": "running" if self.running else "waiting",
            "priority": self.priority.name.lower(),
            "position": self.position,
            "resumed_from_pause": self.resumed_from_pause,
            "pause_requested": self.pause_requested,
            "since": self.since,
        }


class SlotActionError(Exception):
    """A queue action that cannot apply to the thread's current state."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class SessionSlotScheduler:
    """Concurrency limiter whose waiting queue can be reordered at runtime."""

    def __init__(self, max_slots: int) -> None:
        if max_slots < 1:
            raise ValueError("max_slots must be >= 1")
        self._max_slots = max_slots
        self._running: list[SlotEntry] = []
        self._waiting: list[SlotEntry] = []
        self._seq = itertools.count()

    @property
    def max_slots(self) -> int:
        return self._max_slots

    @property
    def running_count(self) -> int:
        return len(self._running)

    # ------------------------------------------------------------------
    # Acquire / release
    # ------------------------------------------------------------------

    def would_wait(self, priority: SlotPriority = SlotPriority.NORMAL) -> bool:
        """True if a new run with ``priority`` could not start right now."""
        if len(self._running) >= self._max_slots:
            return True
        return any(w.priority >= priority for w in self._waiting)

    async def acquire(
        self,
        thread_key: int,
        *,
        label: str = "",
        priority: SlotPriority = SlotPriority.NORMAL,
        resumed_from_pause: bool = False,
    ) -> SlotEntry:
        """Wait for a slot and return the entry that now holds it.

        Cancellation while waiting removes the entry from the queue; if the
        slot was granted in the same tick it is handed on, never leaked.
        """
        loop = asyncio.get_running_loop()
        entry = SlotEntry(
            thread_key=thread_key,
            label=label or str(thread_key),
            seq=next(self._seq),
            priority=priority,
            resumed_from_pause=resumed_from_pause,
            _granted=loop.create_future(),
        )
        self._waiting.append(entry)
        self._dispatch()
        try:
            await entry._granted  # type: ignore[misc]
        except asyncio.CancelledError:
            if entry in self._waiting:
                self._waiting.remove(entry)
            elif entry.running:
                self.release(entry)
            raise
        return entry

    def release(self, entry: SlotEntry) -> None:
        """Give the slot back and start the next eligible waiter."""
        if entry in self._running:
            self._running.remove(entry)
        entry.running = False
        entry.on_pause = None
        self._dispatch()

    def _ordered_waiting(self) -> list[SlotEntry]:
        return sorted(self._waiting, key=lambda e: (-e.priority, e.seq))

    def _dispatch(self) -> None:
        while len(self._running) < self._max_slots and self._waiting:
            nxt = self._ordered_waiting()[0]
            self._waiting.remove(nxt)
            nxt.running = True
            nxt.started_at = time.time()
            self._running.append(nxt)
            if nxt._granted is not None and not nxt._granted.done():
                nxt._granted.set_result(None)

    # ------------------------------------------------------------------
    # Queue actions
    # ------------------------------------------------------------------

    def _find_waiting(self, thread_key: int) -> SlotEntry | None:
        return next((e for e in self._waiting if e.thread_key == thread_key), None)

    def _find_running(self, thread_key: int) -> SlotEntry | None:
        return next((e for e in self._running if e.thread_key == thread_key), None)

    def _require_waiting(self, thread_key: int) -> SlotEntry:
        entry = self._find_waiting(thread_key)
        if entry is not None:
            return entry
        if self._find_running(thread_key) is not None:
            raise SlotActionError("already_running", "This thread is already running.")
        raise SlotActionError("not_queued", "This thread is not waiting for a slot.")

    def prioritize(self, thread_key: int) -> SlotEntry:
        """Move a waiting run ahead of every non-prioritized waiter."""
        entry = self._require_waiting(thread_key)
        entry.priority = SlotPriority.PRIORITIZED
        logger.info("Slot queue: prioritized thread %d", thread_key)
        self._dispatch()
        return entry

    def defer(self, thread_key: int) -> SlotEntry:
        """Move a waiting run behind everyone else; it still starts on its own."""
        entry = self._require_waiting(thread_key)
        entry.priority = SlotPriority.DEFERRED
        logger.info("Slot queue: deferred thread %d", thread_key)
        return entry

    async def pause(self, thread_key: int) -> SlotEntry:
        """Interrupt a running session so its slot goes to the queue.

        The caller that owns the run sees ``pause_requested`` once the
        interrupted stream ends and re-queues the session as deferred.
        """
        entry = self._find_running(thread_key)
        if entry is None:
            if self._find_waiting(thread_key) is not None:
                raise SlotActionError(
                    "not_running", "This thread is still waiting; use defer instead."
                )
            raise SlotActionError("not_running", "This thread is not running.")
        if entry.pause_requested:
            raise SlotActionError("already_pausing", "This thread is already being paused.")
        if entry.on_pause is None:
            raise SlotActionError("not_pausable", "This run cannot be paused.")
        if not self._waiting:
            raise SlotActionError(
                "nothing_waiting",
                "No other session is waiting for a slot, so pausing would only "
                "restart this one. Use Stop to end it instead.",
            )
        entry.pause_requested = True
        logger.info("Slot queue: pausing thread %d", thread_key)
        await entry.on_pause()
        return entry

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def position(self, thread_key: int) -> int | None:
        """1-based queue position of a waiting thread, or None."""
        for index, entry in enumerate(self._ordered_waiting(), start=1):
            if entry.thread_key == thread_key:
                return index
        return None

    def snapshot(self) -> list[SlotInfo]:
        """Running entries (oldest first) followed by the queue in start order."""
        running = [
            SlotInfo(
                thread_key=e.thread_key,
                label=e.label,
                running=True,
                priority=e.priority,
                position=None,
                resumed_from_pause=e.resumed_from_pause,
                pause_requested=e.pause_requested,
                since=e.started_at or e.enqueued_at,
            )
            for e in self._running
        ]
        waiting = [
            SlotInfo(
                thread_key=e.thread_key,
                label=e.label,
                running=False,
                priority=e.priority,
                position=index,
                resumed_from_pause=e.resumed_from_pause,
                pause_requested=False,
                since=e.enqueued_at,
            )
            for index, e in enumerate(self._ordered_waiting(), start=1)
        ]
        return running + waiting


# ---------------------------------------------------------------------------
# Process-wide instance
# ---------------------------------------------------------------------------
_scheduler: SessionSlotScheduler | None = None


def configure_session_slots(max_slots: int) -> SessionSlotScheduler:
    """Create the process-wide scheduler. Called once from ``setup_bridge()``."""
    global _scheduler  # noqa: PLW0603
    _scheduler = SessionSlotScheduler(max_slots)
    return _scheduler


def get_session_slots() -> SessionSlotScheduler | None:
    """The process-wide scheduler, or None when no limit was configured."""
    return _scheduler


def set_session_slots(scheduler: SessionSlotScheduler | None) -> None:
    """Replace the process-wide scheduler (tests and embedders)."""
    global _scheduler  # noqa: PLW0603
    _scheduler = scheduler
