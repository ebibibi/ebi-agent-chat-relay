"""/attention — how much of *your* time the relay took today and this week.

Shows the invoking user's own estimate (ephemeral): today, the last 7 days,
and the threads that took the most of it. See docs/attention.md for the model.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import discord
from discord import app_commands
from discord.ext import commands

from claude_code_core.attention import GROUP_BY_DAY, GROUP_BY_THREAD, AttentionParams
from claude_code_core.attention_repo import HumanActivityRepository, load_report

if TYPE_CHECKING:
    from ..bot import ClaudeDiscordBot

logger = logging.getLogger(__name__)

__all__ = ["AttentionCog", "format_attention_summary", "format_minutes"]

WEEK_DAYS = 7
TOP_THREADS = 5
_TITLE_LIMIT = 60


def format_minutes(minutes: float) -> str:
    """``83.4`` → ``1h 23m``; ``7`` → ``7m``."""
    total = round(minutes)
    hours, rest = divmod(total, 60)
    return f"{hours}h {rest:02d}m" if hours else f"{rest}m"


def format_attention_summary(
    today: dict[str, Any], week_days: dict[str, Any], week_threads: dict[str, Any]
) -> str:
    """Render the three reports as one short message."""
    lines = [
        "⏱️ **Your attention (estimate)**",
        f"Today: **{format_minutes(float(today['total_minutes']))}**"
        f" · {today['total_messages']} msgs",
    ]
    week_total = float(week_days["total_minutes"])
    lines.append(
        f"Last {WEEK_DAYS} days: **{format_minutes(week_total)}**"
        f" (avg {format_minutes(week_total / WEEK_DAYS)}/day)"
        f" · {week_days['total_messages']} msgs"
    )
    threads = list(week_threads["rows"])[:TOP_THREADS]
    if threads:
        lines.append("Top threads this week:")
        for i, row in enumerate(threads, start=1):
            title = str(row.get("title") or row.get("conversation_id"))[:_TITLE_LIMIT]
            lines.append(f"{i}. {title} — {format_minutes(float(row['minutes']))}")
    params = week_days["parameters"]
    lines.append(
        f"-# idle gap {params['idle_gap_minutes']:g}m · lead-in {params['lead_in_minutes']:g}m"
        f" · {params['timezone']} · docs/attention.md"
    )
    return "\n".join(lines)


class AttentionCog(commands.Cog):
    """Expose the attention estimate as an ephemeral slash command."""

    def __init__(
        self,
        bot: ClaudeDiscordBot,
        *,
        repo: HumanActivityRepository,
        params: AttentionParams | None = None,
    ) -> None:
        self.bot = bot
        self.repo = repo
        self.params = params or AttentionParams()

    def _today(self) -> date:
        tz = self.params.tz
        return datetime.now(tz).date() if tz else datetime.now().astimezone().date()

    async def build_summary(self, author_id: str) -> str:
        today = self._today()
        week_start = today - timedelta(days=WEEK_DAYS - 1)
        common = {"author_id": author_id, "end": today}
        today_report = await load_report(
            self.repo, self.params, start=today, group_by=GROUP_BY_DAY, **common
        )
        week_days = await load_report(
            self.repo, self.params, start=week_start, group_by=GROUP_BY_DAY, **common
        )
        week_threads = await load_report(
            self.repo, self.params, start=week_start, group_by=GROUP_BY_THREAD, **common
        )
        return format_attention_summary(today_report, week_days, week_threads)

    @app_commands.command(
        name="attention",
        description="Estimate how much of your own time threads took today and this week",
    )
    async def attention(self, interaction: discord.Interaction) -> None:
        try:
            text = await self.build_summary(str(interaction.user.id))
        except Exception:
            logger.exception("Could not build the /attention summary")
            text = "⚠️ Could not read the attention data. See the bot log."
        await interaction.response.send_message(text, ephemeral=True)
