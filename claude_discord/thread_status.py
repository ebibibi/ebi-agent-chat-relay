"""Show how a thread's last turn ended in its title (``✅`` / ``❓`` / ``⚠️``).

The emoji reactions on the human's message say what a turn is doing while it
runs; nobody is looking at them once it stops.  What the channel list has to
answer is the question after that — *is this thread waiting on me?* — and the
title is the only per-thread surface Discord offers.

Only end-of-turn states belong here.  A thread may be renamed twice per ten
minutes, so anything that changes per message ("running") would exhaust the
budget and leave the title stale exactly when it matters.
"""

from __future__ import annotations

import asyncio
import logging

import discord

from .thread_marker import set_outcome_thread_name

logger = logging.getLogger(__name__)


async def apply_thread_outcome(thread: object, outcome: str | None) -> None:
    """Rename *thread* to show *outcome* (``None`` clears it). Never raises.

    Anything that is not a Discord thread — a channel, a non-Discord surface —
    has no title of its own to change and is left alone.
    """
    if isinstance(thread, discord.Thread):
        await _rename(thread, outcome)


async def _rename(thread: discord.Thread, outcome: str | None) -> Exception | None:
    """Apply *outcome* to the title as it is now; return the failure, if any."""
    current = thread.name if isinstance(thread.name, str) else ""
    if not current:
        return None  # nothing to prefix; a nameless thread is not ours to title
    renamed = set_outcome_thread_name(current, outcome)
    if renamed == current.strip():
        return None
    try:
        await thread.edit(name=renamed)
    except Exception as exc:  # rate limited, archived, missing permission
        logger.warning("Failed to set outcome %r on thread %d", outcome, thread.id, exc_info=True)
        return exc
    logger.info("thread %d outcome %s: %r -> %r", thread.id, outcome, current, renamed)
    return None


# Per thread: the outcome still to apply, and the task applying it.  A rename
# can sit in discord.py's rate-limit sleep for up to ten minutes; requests that
# arrive meanwhile only overwrite the wanted outcome, so the thread gets one
# more rename to the latest state instead of a queue of stale ones.
_wanted: dict[int, str | None] = {}
_workers: dict[int, asyncio.Task[Exception | None]] = {}


def request_thread_outcome(
    thread: discord.Thread, outcome: str | None
) -> asyncio.Task[Exception | None]:
    """Queue *outcome* for *thread*, coalescing with any rename still pending.

    The returned task resolves once the thread shows the latest requested
    outcome, to the last rename's failure or ``None``.
    """
    _wanted[thread.id] = outcome
    worker = _workers.get(thread.id)
    if worker is None or worker.done():
        worker = asyncio.create_task(_drain(thread))
        _workers[thread.id] = worker
    return worker


async def _drain(thread: discord.Thread) -> Exception | None:
    error: Exception | None = None
    try:
        while thread.id in _wanted:
            error = await _rename(thread, _wanted.pop(thread.id))
    finally:
        _workers.pop(thread.id, None)
    return error


def schedule_thread_outcome(thread: object, outcome: str | None) -> None:
    """Fire-and-forget :func:`request_thread_outcome`.

    Backgrounded because discord.py sleeps through a rename rate limit, and
    neither the end of a turn nor the human's next message may wait on that.
    """
    if isinstance(thread, discord.Thread):
        request_thread_outcome(thread, outcome)
