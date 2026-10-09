"""Discord controls for the session slot queue (see ``session_slots.py``).

- ``SlotWaitView`` sits on the "waiting for a slot" message in the waiting
  thread itself: ⏫ run this next, or ⏬ let the others go first.
- ``QueueControlView`` backs ``/queue`` and can act on any thread from
  anywhere: prioritize or defer a waiting thread, or pause a running one so it
  resumes automatically later.
"""

from __future__ import annotations

import contextlib
import logging
import time

import discord

from ..session_slots import SessionSlotScheduler, SlotActionError, SlotInfo, SlotPriority
from .embeds import COLOR_INFO, COLOR_SUCCESS

logger = logging.getLogger(__name__)

_MAX_SELECT_OPTIONS = 25


def waiting_embed(
    scheduler: SessionSlotScheduler, thread_key: int, *, resumed: bool
) -> discord.Embed:
    """The message a queued run shows while it waits."""
    position = scheduler.position(thread_key)
    head = (
        "⏸️ Paused — resumes automatically when a slot is free"
        if resumed
        else "⏳ Waiting for a free session slot…"
    )
    lines = [f"{head} ({scheduler.max_slots} max sessions running)"]
    if position is not None:
        lines.append(f"-# Queue position: {position}")
    return discord.Embed(description="\n".join(lines), color=COLOR_INFO)


def started_embed(*, resumed: bool) -> discord.Embed:
    text = "▶️ Resumed from pause" if resumed else "▶️ Slot free — starting"
    return discord.Embed(description=text, color=COLOR_SUCCESS)


def _describe(info: SlotInfo) -> str:
    minutes = max(0, int((time.time() - info.since) // 60))
    if info.running:
        tag = " ⏸️ pausing" if info.pause_requested else ""
        return f"▶️ <#{info.thread_key}> — {minutes} min{tag}"
    tag = {
        SlotPriority.PRIORITIZED: " ⏫ next",
        SlotPriority.DEFERRED: " ⏬ later",
    }.get(info.priority, "")
    paused = " (paused)" if info.resumed_from_pause else ""
    return f"{info.position}. <#{info.thread_key}>{paused} — waiting {minutes} min{tag}"


def queue_embed(scheduler: SessionSlotScheduler) -> discord.Embed:
    """Overview of running and waiting sessions for ``/queue``."""
    snapshot = scheduler.snapshot()
    running = [_describe(i) for i in snapshot if i.running]
    waiting = [_describe(i) for i in snapshot if not i.running]
    embed = discord.Embed(
        title=f"\U0001f6a6 Session slots — {len(running)}/{scheduler.max_slots} running",
        color=COLOR_INFO,
    )
    embed.add_field(name="Running", value="\n".join(running) or "-# none", inline=False)
    embed.add_field(name="Waiting", value="\n".join(waiting) or "-# none", inline=False)
    return embed


async def _apply(
    interaction: discord.Interaction,
    scheduler: SessionSlotScheduler,
    action: str,
    thread_key: int,
) -> None:
    """Run a queue action and answer the click ephemerally."""
    # The waiting message sits in a shared thread, so anyone who can see it
    # can click it. Steering the queue is an operator action.
    if not scheduler.is_authorized(interaction.user.id):
        with contextlib.suppress(discord.HTTPException):
            await interaction.response.send_message(
                "You don't have permission to change the session queue.", ephemeral=True
            )
        return
    try:
        if action == "prioritize":
            scheduler.prioritize(thread_key)
            message = f"⏫ <#{thread_key}> will start next."
        elif action == "defer":
            scheduler.defer(thread_key)
            message = f"⏬ <#{thread_key}> will let the others go first."
        else:
            await scheduler.pause(thread_key)
            message = (
                f"⏸️ Pausing <#{thread_key}> — it resumes automatically "
                "once the waiting sessions are through."
            )
    except SlotActionError as exc:
        message = f"⚠️ {exc}"
    with contextlib.suppress(discord.HTTPException):
        await interaction.response.send_message(message, ephemeral=True)


class SlotWaitView(discord.ui.View):
    """⏫ / ⏬ buttons on the waiting message of a queued run."""

    def __init__(self, scheduler: SessionSlotScheduler, thread_key: int) -> None:
        super().__init__(timeout=None)
        self._scheduler = scheduler
        self._thread_key = thread_key

    @discord.ui.button(label="⏫ Run this next", style=discord.ButtonStyle.primary)
    async def prioritize_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await _apply(interaction, self._scheduler, "prioritize", self._thread_key)

    @discord.ui.button(label="⏬ Let others go first", style=discord.ButtonStyle.secondary)
    async def defer_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await _apply(interaction, self._scheduler, "defer", self._thread_key)

    async def close(self, message: discord.Message | None, *, resumed: bool) -> None:
        """Remove the buttons once the run has its slot."""
        self.stop()
        if message is None:
            return
        with contextlib.suppress(discord.HTTPException):
            await message.edit(embed=started_embed(resumed=resumed), view=None)


class _ThreadSelect(discord.ui.Select):
    def __init__(
        self,
        scheduler: SessionSlotScheduler,
        action: str,
        placeholder: str,
        infos: list[SlotInfo],
    ) -> None:
        options = [
            discord.SelectOption(
                label=(i.label or str(i.thread_key))[:100], value=str(i.thread_key)
            )
            for i in infos[:_MAX_SELECT_OPTIONS]
        ]
        super().__init__(placeholder=placeholder, options=options, min_values=1, max_values=1)
        self._scheduler = scheduler
        self._action = action

    async def callback(self, interaction: discord.Interaction) -> None:
        await _apply(interaction, self._scheduler, self._action, int(self.values[0]))


class QueueControlView(discord.ui.View):
    """Select menus for ``/queue``; only the menus that have targets appear."""

    def __init__(self, scheduler: SessionSlotScheduler) -> None:
        super().__init__(timeout=600)
        snapshot = scheduler.snapshot()
        waiting = [i for i in snapshot if not i.running]
        running = [i for i in snapshot if i.running and not i.pause_requested]
        if waiting:
            self.add_item(_ThreadSelect(scheduler, "prioritize", "⏫ Run next…", waiting))
            self.add_item(_ThreadSelect(scheduler, "defer", "⏬ Let others go first…", waiting))
        if running and waiting:
            self.add_item(
                _ThreadSelect(
                    scheduler,
                    "pause",
                    "⏸️ Pause (resumes automatically)…",
                    running,
                )
            )
