"""Keep the scheduled marker (``⏰``) on threads a scheduled task will post into.

A thread waiting on a follow-up task — registered through ``POST /api/tasks``
with a ``thread_id``, or bridged from a ``ScheduleWakeup`` call — looks exactly
like a thread nobody is working on.  The title is the only surface Discord
offers, so the marker lives there.

The marker is *reconciled* from the task table rather than toggled by each
code path that creates, edits or deletes a task: there are several such paths
(REST API, the wakeup bridge, consumers calling ``TaskRepository`` directly),
and a toggle missed on any one of them would leave a marker that lies.  The
scheduler's master loop hands over the set of pending thread IDs every tick;
only threads whose membership changed are looked at, so a quiet tick costs
no Discord calls.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import discord

from .thread_marker import (
    mark_scheduled_thread_name,
    scheduled_marker,
    unmark_scheduled_thread_name,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from discord.ext import commands

logger = logging.getLogger(__name__)


class ScheduledThreadMarker:
    """Rename threads as they enter and leave the set of pending threads.

    Starts from an empty set, so the first reconcile after a restart visits
    every pending thread once; an already-marked title is left alone, which
    matters because Discord allows a thread only two renames per ten minutes.
    A thread whose rename failed stays out of the known set and is retried on
    the next tick.
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._marked: set[int] = set()

    async def reconcile(self, pending: set[int]) -> None:
        """Mark threads newly in *pending*, unmark those that left it."""
        if not scheduled_marker():
            return
        for thread_id in pending - self._marked:
            if await self._rename(thread_id, mark_scheduled_thread_name):
                self._marked.add(thread_id)
        for thread_id in self._marked - pending:
            if await self._rename(thread_id, unmark_scheduled_thread_name):
                self._marked.discard(thread_id)

    async def _rename(self, thread_id: int, transform: Callable[[str], str]) -> bool:
        """Apply *transform* to the thread's name. True when the title is settled."""
        thread = self.bot.get_channel(thread_id)
        if thread is None:
            try:
                thread = await self.bot.fetch_channel(thread_id)
            except Exception:
                # Deleted or inaccessible: nothing to rename, and nothing to retry.
                logger.debug("scheduled marker: thread %d not found", thread_id)
                return True
        if not isinstance(thread, discord.Thread):
            return True
        current = thread.name or ""
        renamed = transform(current)
        if renamed == current:
            return True
        try:
            await thread.edit(name=renamed)
        except Exception:  # rate limited, archived, missing permission
            logger.warning(
                "Failed to update scheduled marker on thread %d", thread_id, exc_info=True
            )
            return False
        logger.info("thread %d scheduled marker: %r -> %r", thread_id, current, renamed)
        return True
