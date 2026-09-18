"""When a thread has drifted far enough to deserve a new title.

``THREAD_AUTO_RENAME`` names a thread once, from its first message.  Work does
not stay still: a thread opened about a failing test ends the evening deploying
a scheduler, and the channel list still advertises the failing test.  Scanning
that list is how anyone decides which thread to open, so a stale title is not
cosmetic — it is wrong information in the one place people read.

Keeping the title honest means *re-titling*, which costs a model call and a
Discord rename.  Neither can be spent per message: Discord accepts **two
renames per ten minutes** per thread, and a title that changes on every reply is
noisier than one that never changes.  This module holds that budget and nothing
else — it decides *when* to ask; :mod:`thread_renamer` decides *what* to ask
and the cog applies the answer.

The two thresholds are deliberately an AND, not an OR.  Messages alone would
re-title a burst of quick replies about the same thing; time alone would
re-title a thread nobody has spoken in.  A subject changes when someone keeps
talking *and* time has passed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, replace

# A thread is tracked only while it is being replied to, but nothing removes the
# entry of a thread that goes quiet forever, so the map is bounded and evicts
# its oldest entries.  The cost of evicting a live thread is one skipped
# re-title, which the next message re-arms.
DEFAULT_MAX_TRACKED_THREADS = 500


@dataclass(frozen=True)
class RetitlePolicy:
    """How much drift buys a rename.

    ``min_interval_seconds`` defaults to fifteen minutes: comfortably outside
    Discord's two-renames-per-ten-minutes limit even when the creation-time
    rename and a re-title land back to back.
    """

    min_interval_seconds: float = 900.0
    messages_before_retitle: int = 3
    recent_messages_kept: int = 5
    message_chars_kept: int = 400


@dataclass(frozen=True)
class _ThreadState:
    recent: tuple[str, ...]
    messages_since_rename: int
    last_rename_at: float


class RetitleTracker:
    """Per-thread drift accounting for the auto re-titler.

    Every mutation replaces the frozen state it read, so a state object handed
    out (the claimed message tuple) can never be changed underneath its reader.
    """

    def __init__(
        self,
        policy: RetitlePolicy | None = None,
        max_threads: int = DEFAULT_MAX_TRACKED_THREADS,
    ) -> None:
        self._policy = policy or RetitlePolicy()
        self._max_threads = max_threads
        self._states: dict[int, _ThreadState] = {}

    @property
    def policy(self) -> RetitlePolicy:
        return self._policy

    def record(self, thread_id: int, text: str, now: float | None = None) -> None:
        """Note that *text* was said in *thread_id*."""
        trimmed = text.strip()
        if not trimmed:
            return
        moment = time.monotonic() if now is None else now
        state = self._states.get(thread_id)
        if state is None:
            # The thread was titled when it was created (or when it was first
            # seen), so its cooldown starts now rather than at the epoch —
            # otherwise the very next message would re-title a fresh thread.
            state = _ThreadState(recent=(), messages_since_rename=0, last_rename_at=moment)
            self._evict_if_full()
        kept = (*state.recent, trimmed[: self._policy.message_chars_kept])
        self._states[thread_id] = replace(
            state,
            recent=kept[-self._policy.recent_messages_kept :],
            messages_since_rename=state.messages_since_rename + 1,
        )

    def claim(self, thread_id: int, now: float | None = None) -> tuple[str, ...] | None:
        """Take the re-title slot for *thread_id*, or ``None`` when it is not due.

        Claiming resets the budget *before* the caller does any awaiting, so two
        replies arriving back to back cannot both start a rename.  A claim that
        then fails costs one window of staleness, not a duplicate rename.
        """
        state = self._states.get(thread_id)
        if state is None or not state.recent:
            return None
        moment = time.monotonic() if now is None else now
        if state.messages_since_rename < self._policy.messages_before_retitle:
            return None
        if moment - state.last_rename_at < self._policy.min_interval_seconds:
            return None
        self._states[thread_id] = replace(
            state,
            recent=(),
            messages_since_rename=0,
            last_rename_at=moment,
        )
        return state.recent

    def forget(self, thread_id: int) -> None:
        """Stop tracking *thread_id* (it was deleted, archived or cleared)."""
        self._states.pop(thread_id, None)

    def tracked_thread_ids(self) -> tuple[int, ...]:
        return tuple(self._states)

    def _evict_if_full(self) -> None:
        while len(self._states) >= self._max_threads:
            oldest = next(iter(self._states))
            del self._states[oldest]
