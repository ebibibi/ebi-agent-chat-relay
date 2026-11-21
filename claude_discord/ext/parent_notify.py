"""Tell a parent thread when a child it spawned finishes (Issue #820).

``/api/spawn`` records who spawned whom (``thread_lineage``), and a child marks
where it ended with ``/api/threads/{id}/candidate|done|waiting|review|action``.
Without this module nothing connects the two: the parent has to poll, or the
human relays each child's result by hand — which turns a fan-out of parallel
agents back into a single queue through one person.

With the opt-in (``notify_parent: true`` on the spawn, or
``CCDB_NOTIFY_PARENT_DEFAULT=1`` for every spawn that names a parent) the mark
queues one short report into the parent thread. ``queue`` delivery means an
idle parent starts a turn and a busy one reads it after the current turn, so
the parent can check the result, send follow-up work, and give its human one
combined report instead of one per child.

Guard rails, in the order they are checked:

- only a link the relay recorded at spawn time is followed — never a thread
  named in free text, and never a thread spawned without the opt-in;
- a mark that changed nothing, or the same outcome reported again within
  ``DEDUPE_SECONDS``, is not reported twice;
- each child reports at most ``CCDB_NOTIFY_PARENT_MAX_PER_CHILD`` times, so a
  parent and child handing work back and forth cannot wake each other forever;
- the report goes through the same :class:`~claude_discord.relay.RelayGuard`
  as any thread-to-thread message (pair and sender burst limits, hop 0).

A failed report is logged and never fails the child's mark.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ..relay import MAX_HOP, RelayGuard
from ..thread_marker import (
    OUTCOME_ACTION,
    OUTCOME_CANDIDATE,
    OUTCOME_DONE,
    OUTCOME_REVIEW,
    OUTCOME_WAITING,
)

if TYPE_CHECKING:
    from ..database.lineage_repo import ThreadLineageRepository

logger = logging.getLogger(__name__)

DEFAULT_ENV_VAR = "CCDB_NOTIFY_PARENT_DEFAULT"
MAX_PER_CHILD_ENV_VAR = "CCDB_NOTIFY_PARENT_MAX_PER_CHILD"
DEFAULT_MAX_PER_CHILD = 10
DEDUPE_SECONDS = 600.0

# What each mark means to the parent, in one line it can act on.
_STATES: dict[str, str] = {
    OUTCOME_CANDIDATE: "finished — it proposes that its work is complete",
    OUTCOME_DONE: "done — the thread was closed",
    OUTCOME_WAITING: "waiting — it needs an answer from the human",
    OUTCOME_REVIEW: "review — it has a deliverable for the human to look over",
    OUTCOME_ACTION: "action — the human has a task to do outside the chat",
}
NOTIFIED_OUTCOMES = frozenset(_STATES)

# (parent_thread_id, prompt, from_thread) -> delivered?
Dispatch = Callable[[int, str, int], Awaitable[bool]]


def default_enabled() -> bool:
    """Whether a spawn that does not say ``notify_parent`` opts in."""
    return os.environ.get(DEFAULT_ENV_VAR, "").strip().lower() in {"1", "true", "yes", "on"}


def max_per_child() -> int:
    raw = os.environ.get(MAX_PER_CHILD_ENV_VAR, "").strip()
    try:
        value = int(raw) if raw else DEFAULT_MAX_PER_CHILD
    except ValueError:
        logger.warning(
            "%s=%r is not an integer; using %d", MAX_PER_CHILD_ENV_VAR, raw, DEFAULT_MAX_PER_CHILD
        )
        return DEFAULT_MAX_PER_CHILD
    return max(1, value)


def parse_opt_in(raw: object) -> bool | None:
    """Read the ``notify_parent`` field of a spawn body.

    Returns None when absent (the deployment default applies). Raises
    ValueError for anything but a boolean, so a typo is a 400 rather than a
    silently ignored opt-in.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        return raw
    raise ValueError("notify_parent must be a boolean")


def build_report_prompt(*, child_thread_id: int, child_title: str, outcome: str) -> str:
    """The message the parent receives. Framed so it is never mistaken for the human."""
    state = _STATES.get(outcome, outcome)
    title = " ".join(child_title.split())[:200] or f"thread {child_thread_id}"
    return (
        f"[CHILD THREAD REPORT — thread {child_thread_id}]\n"
        "This is NOT from your human. The relay sends it because you spawned this "
        "thread with notify_parent.\n"
        f"Child: {title} (/#focus/t{child_thread_id})\n"
        f"State: {state}\n"
        "Read what it reported: "
        f"GET $CCDB_API_URL/api/threads/{child_thread_id}/messages?limit=5\n"
        "What to do: check the result against what you asked for. If more work is "
        "needed, send it to the child "
        f"(POST $CCDB_API_URL/api/threads/{child_thread_id}/message, hop 1) and wait "
        "for its next report. When your children are settled, give your human one "
        "combined report — do not forward each child's report separately. A child "
        "waiting on the human already shows its decision card in your panel; do not "
        "copy the question. Update your situation panel before ending the turn.\n"
        f"(Report chains are bounded: hop limit {MAX_HOP}, and a capped number of "
        "reports per child.)"
    )


@dataclass
class ParentNotifier:
    """Decides whether a child's mark becomes a report, and sends it."""

    lineage: ThreadLineageRepository
    guard: RelayGuard
    dispatch: Dispatch
    wall_clock: Callable[[], float] = time.time
    monotonic: Callable[[], float] = time.monotonic

    async def on_outcome(
        self, thread_id: int, outcome: str, *, thread_name: str, changed: bool
    ) -> str:
        """Report *outcome* of *thread_id* to its parent if it opted in.

        Returns a short reason (``sent`` or why not) for logs and tests. Never
        raises: the child's mark has already happened and must stand.
        """
        try:
            return await self._on_outcome(thread_id, outcome, thread_name, changed)
        except Exception:
            logger.exception("Could not report thread %s (%s) to its parent", thread_id, outcome)
            return "error"

    async def _on_outcome(
        self, thread_id: int, outcome: str, thread_name: str, changed: bool
    ) -> str:
        if outcome not in NOTIFIED_OUTCOMES:
            return "outcome_not_reported"
        target = await self.lineage.notify_target(thread_id)
        if target is None:
            return "not_opted_in"
        if not changed:
            return "unchanged"
        now = self.wall_clock()
        if (
            target.last_outcome == outcome
            and target.last_at is not None
            and now - target.last_at < DEDUPE_SECONDS
        ):
            return "duplicate"
        if target.count >= max_per_child():
            logger.info("thread %s reached its parent-report cap (%d)", thread_id, target.count)
            return "cap_reached"

        parent = target.parent_thread_id
        prompt = build_report_prompt(
            child_thread_id=thread_id, child_title=thread_name, outcome=outcome
        )
        tick = self.monotonic()
        refusal = self.guard.evaluate(
            from_thread=thread_id, to_thread=parent, hop=0, now=tick, text=prompt
        )
        if refusal is not None:
            logger.info("Parent report %s → %s refused: %s", thread_id, parent, refusal.reason)
            return f"refused:{refusal.reason}"
        reservation = self.guard.record(
            from_thread=thread_id, to_thread=parent, now=tick, text=prompt
        )
        delivered = False
        try:
            delivered = await self.dispatch(parent, prompt, thread_id)
        finally:
            if not delivered:
                self.guard.release(reservation)
        if not delivered:
            return "not_delivered"
        await self.lineage.record_notified(thread_id, outcome, now)
        logger.info("Reported thread %s (%s) to parent %s", thread_id, outcome, parent)
        return "sent"
