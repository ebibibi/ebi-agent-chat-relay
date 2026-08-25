"""Todoist overdue task watchdog — 30-minute check loop.

Checks Todoist for overdue tasks via todoist.sh and posts a warning
embed to the default channel.  Each task is only notified once per day.

Usage:
    CUSTOM_COGS_DIR=examples/ebibot/cogs ccdb start
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from datetime import datetime

import discord
from discord.ext import commands, tasks

logger = logging.getLogger(__name__)

# Default path — can be overridden via TODOIST_SH env var
TODOIST_SH = os.getenv(
    "TODOIST_SH",
    os.path.expanduser("~/.claude/skills/todoist/scripts/todoist.sh"),
)

# ---------------------------------------------------------------------------
# Embeds
# ---------------------------------------------------------------------------

_COLOR_WARN = 0xFF6B6B
_COLOR_DANGER = 0xFF4444
_COLOR_CRITICAL = 0xFF0000
_FETCH_ATTEMPTS = 2

_TEMPLATES = {
    "warn": {"title": "Hey! You have overdue tasks!", "color": _COLOR_WARN},
    "danger": {"title": "Seriously?! Are you slacking off?!", "color": _COLOR_DANGER},
    "critical": {
        "title": "\U0001f6a8\U0001f6a8\U0001f6a8 Too many overdue tasks!",
        "color": _COLOR_CRITICAL,
    },
}


def _get_level(count: int) -> str:
    if count >= 6:
        return "critical"
    if count >= 3:
        return "danger"
    return "warn"


def _build_watchdog_embed(overdue_tasks: list[dict]) -> discord.Embed:
    count = len(overdue_tasks)
    level = _get_level(count)
    template = _TEMPLATES[level]

    task_lines = []
    for task in overdue_tasks[:15]:
        content = task.get("content", "???")
        due = task.get("due", "")
        task_lines.append(f"- **{content}**  (due: {due})")

    if count > 15:
        task_lines.append(f"...and {count - 15} more")

    embed = discord.Embed(
        title=template["title"],
        description=f"**{count}** overdue task(s)!\n\n" + "\n".join(task_lines),
        color=template["color"],
        timestamp=datetime.now(),
    )
    embed.set_footer(text="EbiBot Watchdog")
    return embed


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------


class WatchdogCog(commands.Cog):
    """Todoist overdue task monitor."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._notified_today: set[str] = set()
        self._last_reset_date: str = ""

    async def cog_load(self) -> None:
        self.check_overdue.start()
        logger.info("WatchdogCog loaded, overdue check loop started")

    async def cog_unload(self) -> None:
        self.check_overdue.cancel()

    def _reset_daily(self) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        if self._last_reset_date != today:
            self._notified_today.clear()
            self._last_reset_date = today

    def _is_active_hours(self) -> bool:
        hour = datetime.now().hour
        return 8 <= hour < 23

    def _fetch_overdue_tasks(self) -> list[dict]:
        for attempt in range(1, _FETCH_ATTEMPTS + 1):
            try:
                result = subprocess.run(
                    [TODOIST_SH, "tasks", "--filter", "(overdue)"],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
            except FileNotFoundError as e:
                logger.error("Todoist fetch error: %s", e)
                return []
            except subprocess.TimeoutExpired as e:
                error: Exception = e
            else:
                if result.returncode != 0:
                    logger.error("todoist.sh failed: %s", result.stderr)
                    return []
                try:
                    data = json.loads(result.stdout)
                except json.JSONDecodeError as e:
                    error = e
                else:
                    return data if isinstance(data, list) else []

            if attempt < _FETCH_ATTEMPTS:
                logger.warning("Todoist fetch attempt %d failed; retrying: %s", attempt, error)
            else:
                logger.error("Todoist fetch error after %d attempts: %s", attempt, error)

        return []

    @tasks.loop(minutes=30)
    async def check_overdue(self) -> None:
        """Check Todoist for overdue tasks every 30 minutes."""
        if not self._is_active_hours():
            return

        self._reset_daily()

        overdue_tasks = self._fetch_overdue_tasks()
        if not overdue_tasks:
            return

        new_tasks = []
        for task in overdue_tasks:
            task_id = task.get("id", "")
            if task_id and task_id not in self._notified_today:
                new_tasks.append(task)
                self._notified_today.add(task_id)

        if not new_tasks:
            return

        channel_id = getattr(self.bot, "default_channel_id", None) or getattr(
            self.bot, "channel_id", None
        )
        if not channel_id:
            logger.warning("No default channel ID set — cannot send watchdog alert")
            return

        channel = self.bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await self.bot.fetch_channel(channel_id)
            except Exception as e:
                logger.error("Failed to fetch channel: %s", e)
                return

        embed = _build_watchdog_embed(new_tasks)
        await channel.send(embed=embed)
        logger.info("Watchdog alert sent: %d task(s)", len(new_tasks))

    @check_overdue.before_loop
    async def before_check_overdue(self) -> None:
        await self.bot.wait_until_ready()


# ---------------------------------------------------------------------------
# setup() — called by load_custom_cogs
# ---------------------------------------------------------------------------


async def setup(bot: commands.Bot, runner: object, components: object) -> None:
    """Entry point for the custom Cog loader."""
    await bot.add_cog(WatchdogCog(bot))
