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

# Strong references: a bare create_task() result can be garbage-collected
# before it finishes.
_pending: set[asyncio.Task[None]] = set()


async def apply_thread_outcome(thread: object, outcome: str | None) -> None:
    """Rename *thread* to show *outcome* (``None`` clears it). Never raises.

    Anything that is not a Discord thread — a channel, a non-Discord surface —
    has no title of its own to change and is left alone.
    """
    if not isinstance(thread, discord.Thread):
        return
    current = thread.name if isinstance(thread.name, str) else ""
    if not current:
        return  # nothing to prefix; a nameless thread is not ours to title
    renamed = set_outcome_thread_name(current, outcome)
    if renamed == current.strip():
        return
    try:
        await thread.edit(name=renamed)
    except Exception:  # rate limited, archived, missing permission
        logger.warning("Failed to set outcome %r on thread %d", outcome, thread.id, exc_info=True)
        return
    logger.info("thread %d outcome %s: %r -> %r", thread.id, outcome, current, renamed)


def schedule_thread_outcome(thread: object, outcome: str | None) -> None:
    """Fire-and-forget :func:`apply_thread_outcome`.

    Backgrounded because discord.py sleeps through a rename rate limit, and
    neither the end of a turn nor the human's next message may wait on that.
    """
    if not isinstance(thread, discord.Thread):
        return
    task = asyncio.create_task(apply_thread_outcome(thread, outcome))
    _pending.add(task)
    task.add_done_callback(_pending.discard)
