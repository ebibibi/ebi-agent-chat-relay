"""alert_responder.py — generic alert-monitoring Cog (example custom Cog)

Watches a Discord channel for messages matching a configurable regex pattern.
When a match is detected, spins up a Claude Code session in a thread to
investigate and report the root cause.

Configuration (environment variables):
    ALERT_MONITOR_CHANNEL_ID  (required) Channel ID to monitor.
                              If not set the Cog is silently disabled.
    ALERT_MONITOR_PATTERN     Regex pattern that triggers investigation.
                              Default: "⚠️"
    ALERT_MONITOR_PROMPT      Prompt template passed to Claude Code.
                              Use {alert_text} as a placeholder for the
                              triggering message. Default: a simple English
                              template asking Claude to investigate and report.
    DISCORD_OWNER_ID          (optional) User ID to @-mention in the thread.

Detection criteria:
    - Message is in the monitored channel
    - Message author is not the bot itself
    - Message content matches ALERT_MONITOR_PATTERN
    - The same message is not already being investigated
"""

from __future__ import annotations

import logging
import os
import re

import discord
from discord.ext import commands

from claude_discord.cogs._run_helper import run_claude_with_config
from claude_discord.cogs.headless_backend import (
    backend_factory_from_components,
    backend_settings_from_components,
    build_headless_runner,
)
from claude_discord.cogs.run_config import RunConfig
from claude_discord.thread_policy import THREAD_AUTO_ARCHIVE_MINUTES

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration — all sourced from environment variables
# ---------------------------------------------------------------------------

_raw_channel_id = os.environ.get("ALERT_MONITOR_CHANNEL_ID", "")
ALERT_CHANNEL_ID: int | None = int(_raw_channel_id) if _raw_channel_id else None

_raw_pattern = os.environ.get("ALERT_MONITOR_PATTERN", r"⚠️")
_ALERT_PATTERN = re.compile(_raw_pattern)

_DEFAULT_INVESTIGATION_PROMPT = """\
The following automated alert was received. Please investigate the root cause.

## Alert

```
{alert_text}
```

Investigate the cause, implement a fix if possible, and report your findings \
(root cause summary, what was fixed, PR URL if applicable) in this thread.
"""

_INVESTIGATION_PROMPT = os.environ.get("ALERT_MONITOR_PROMPT", _DEFAULT_INVESTIGATION_PROMPT)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------


class AlertResponderCog(commands.Cog):
    """Watches a Discord channel for alert messages and auto-investigates them."""

    def __init__(self, bot: commands.Bot, runner: object, components: object) -> None:
        self.bot = bot
        self.runner = runner
        self.components = components
        # Prevent duplicate investigations for the same message
        self._investigating: set[int] = set()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Check each incoming message; start investigation if it matches."""
        # Ignore messages from the bot itself
        if message.author == self.bot.user:
            return

        # Ignore messages outside the monitored channel
        if ALERT_CHANNEL_ID is None or message.channel.id != ALERT_CHANNEL_ID:
            return

        # Ignore messages that don't match the alert pattern
        if not _ALERT_PATTERN.search(message.content):
            return

        # Skip if already investigating this message
        if message.id in self._investigating:
            return

        self._investigating.add(message.id)
        try:
            await self._start_investigation(message)
        except Exception:
            logger.exception(
                "AlertResponderCog: unexpected error during investigation (message_id=%d)",
                message.id,
            )
        finally:
            self._investigating.discard(message.id)

    async def _start_investigation(self, alert_message: discord.Message) -> None:
        """Create a thread and run Claude Code to investigate the alert."""
        if not isinstance(alert_message.channel, discord.TextChannel):
            logger.warning("AlertResponderCog: channel is not a TextChannel — skipping")
            return

        if self.runner is None:
            logger.warning("AlertResponderCog: runner is None — cannot start Claude")
            return

        logger.info(
            "AlertResponderCog: alert detected (channel=%d, message=%d) — starting investigation",
            alert_message.channel.id,
            alert_message.id,
        )

        # Create a thread on the alert message
        thread = await alert_message.create_thread(
            name=f"🔍 Investigation: {alert_message.content[:50]}",
            auto_archive_duration=THREAD_AUTO_ARCHIVE_MINUTES,
        )

        owner_id = os.environ.get("DISCORD_OWNER_ID", "")
        mention = f"<@{owner_id}> " if owner_id else ""
        await thread.send(
            f"{mention}🔍 Alert detected. The agent is investigating the root cause..."
        )

        prompt = _INVESTIGATION_PROMPT.format(alert_text=alert_message.content)

        session_repo = getattr(self.components, "session_repo", None)
        registry = getattr(self.bot, "session_registry", None)
        lounge_repo = getattr(self.components, "lounge_repo", None)

        backend_settings = backend_settings_from_components(self.components)
        cloned_runner = await build_headless_runner(
            self.runner,
            factory=backend_factory_from_components(self.components),
            settings=backend_settings,
            thread_id=thread.id,
        )

        await run_claude_with_config(
            RunConfig(
                thread=thread,
                runner=cloned_runner,
                prompt=prompt,
                session_id=None,
                repo=session_repo,
                registry=registry,
                lounge_repo=lounge_repo,
                backend_settings=backend_settings,
            )
        )


async def setup(bot: commands.Bot, runner: object, components: object) -> None:
    """Entry point called by the custom Cog loader."""
    if ALERT_CHANNEL_ID is None:
        logger.warning(
            "AlertResponderCog: ALERT_MONITOR_CHANNEL_ID is not set — Cog disabled. "
            "Set the environment variable to enable alert monitoring."
        )
        return

    await bot.add_cog(AlertResponderCog(bot, runner, components))
    logger.info(
        "AlertResponderCog loaded — monitoring channel %d for pattern %r",
        ALERT_CHANNEL_ID,
        _raw_pattern,
    )
