"""/queue — see and steer the session slot queue from any channel.

When every session slot is busy, the operator can move a waiting thread to the
front, send one to the back, or pause a running thread so it resumes on its own
once the waiting ones are through.  See ``session_slots.py``.
"""

from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

from ..discord_ui.slot_views import QueueControlView, queue_embed
from ..session_slots import get_session_slots

logger = logging.getLogger(__name__)


class SessionQueueCog(commands.Cog):
    """Slash command for the session slot queue."""

    def __init__(self, bot: commands.Bot, *, allowed_user_ids: set[int] | None = None) -> None:
        self.bot = bot
        # Pausing someone's running session is an operator action: same rule as /skill.
        self._allowed_user_ids = allowed_user_ids

    def _is_authorized(self, user_id: int) -> bool:
        if self._allowed_user_ids is None:
            return True
        return user_id in self._allowed_user_ids

    @app_commands.command(
        name="queue",
        description="Show the session slot queue: run a thread next, defer it, or pause one",
    )
    async def queue_command(self, interaction: discord.Interaction) -> None:
        if not self._is_authorized(interaction.user.id):
            await interaction.response.send_message(
                "You don't have permission to use this command.", ephemeral=True
            )
            return
        slots = get_session_slots()
        if slots is None:
            await interaction.response.send_message(
                "No session limit is configured, so nothing ever waits.", ephemeral=True
            )
            return
        await interaction.response.send_message(
            embed=queue_embed(slots), view=QueueControlView(slots), ephemeral=True
        )
