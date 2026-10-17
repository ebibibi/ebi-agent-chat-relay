"""job_failure_triage.py — auto-investigate scheduler job failures (custom Cog)

Watches a Discord channel for scheduler webhook messages containing failure
or timeout embeds. When detected, creates a thread and launches a Claude Code
session to investigate the root cause and attempt a fix.

Configuration (environment variables):
    JOB_TRIAGE_CHANNEL_ID  (required) Channel ID to monitor for job failure embeds.
                           If not set the Cog is silently disabled.
    DISCORD_OWNER_ID       (optional) User ID to @-mention in the thread.

Detection criteria:
    - Message is in the monitored channel
    - Message was sent by a webhook (message.webhook_id is set)
    - Message has at least one embed with title "Job Failed" or "Job Timeout"
    - The same message is not already being triaged
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
# Configuration
# ---------------------------------------------------------------------------

_raw_channel_id = os.environ.get("JOB_TRIAGE_CHANNEL_ID", "")
JOB_TRIAGE_CHANNEL_ID: int | None = int(_raw_channel_id) if _raw_channel_id else None

_FAILURE_TITLES = frozenset({"Job Failed", "Job Timeout"})

_BOLD_PATTERN = re.compile(r"\*\*(.+?)\*\*")

_INVESTIGATION_PROMPT = """\
Schedulerのジョブが失敗した。調査して、可能なら修正してほしい。

## 失敗情報

- **ジョブ名**: {job_name}
- **Job ID**: {job_id}
- **種別**: {title}
- **スケジュール**: {schedule}
- **エラー**: {error}

## 調査手順

1. `~/scheduler/` リポジトリを確認
2. `config/jobs.yaml` から該当ジョブの定義を確認
3. ジョブのスクリプト（`scripts/` 配下）を読んでエラー原因を特定
4. 直近のログ（`~/scheduler/logs/`）を確認
5. 修正可能なら修正してコミット・プッシュ
6. 修正不可能（外部API障害等）なら原因と対処方針を報告

調査結果と対応内容をこのスレッドに報告してください。
"""


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _extract_failure_info(embed: discord.Embed) -> dict[str, str] | None:
    """Extract job failure details from a Discord embed.

    Returns a dict with keys: title, job_id, job_name, error, schedule.
    Returns None if the embed is not a failure/timeout notification.
    """
    if not embed.title or embed.title not in _FAILURE_TITLES:
        return None

    fields: dict[str, str] = {}
    for field in embed.fields:
        fields[field.name] = field.value

    job_id = fields.get("Job ID", "unknown")

    # Extract job name from bold text in description: "**job_name** が失敗しました"
    job_name = job_id
    if embed.description:
        m = _BOLD_PATTERN.search(embed.description)
        if m:
            job_name = m.group(1)

    return {
        "title": embed.title,
        "job_id": job_id,
        "job_name": job_name,
        "error": fields.get("Error", "No error details"),
        "schedule": fields.get("Schedule", "unknown"),
    }


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------


class JobFailureTriageCog(commands.Cog):
    """Watches for scheduler job failure embeds and auto-investigates them."""

    def __init__(self, bot: commands.Bot, runner: object, components: object) -> None:
        self.bot = bot
        self.runner = runner
        self.components = components
        self._triaging: set[int] = set()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Check each incoming message for job failure embeds."""
        if message.author == self.bot.user:
            return

        if JOB_TRIAGE_CHANNEL_ID is None or message.channel.id != JOB_TRIAGE_CHANNEL_ID:
            return

        # Only process webhook messages (scheduler sends via webhook)
        if not message.webhook_id:
            return

        if not message.embeds:
            return

        # Check the first embed for failure/timeout
        failure_info = _extract_failure_info(message.embeds[0])
        if failure_info is None:
            return

        if message.id in self._triaging:
            return

        self._triaging.add(message.id)
        try:
            await self._start_triage(message, failure_info)
        except Exception:
            logger.exception(
                "JobFailureTriageCog: unexpected error during triage (message_id=%d)",
                message.id,
            )
        finally:
            self._triaging.discard(message.id)

    async def _start_triage(
        self, alert_message: discord.Message, failure_info: dict[str, str]
    ) -> None:
        """Create a thread and run Claude Code to investigate the failure."""
        if not isinstance(alert_message.channel, discord.TextChannel):
            logger.warning("JobFailureTriageCog: channel is not a TextChannel — skipping")
            return

        if self.runner is None:
            logger.warning("JobFailureTriageCog: runner is None — cannot start Claude")
            return

        job_name = failure_info["job_name"]
        title_emoji = "🚨" if failure_info["title"] == "Job Failed" else "⏰"

        logger.info(
            "JobFailureTriageCog: %s detected for %s (message=%d) — starting triage",
            failure_info["title"],
            job_name,
            alert_message.id,
        )

        thread = await alert_message.create_thread(
            name=f"{title_emoji} Triage: {job_name}"[:100],
            auto_archive_duration=THREAD_AUTO_ARCHIVE_MINUTES,
        )

        owner_id = os.environ.get("DISCORD_OWNER_ID", "")
        mention = f"<@{owner_id}> " if owner_id else ""
        await thread.send(
            f"{mention}{title_emoji} **{job_name}** の失敗を検知。 AI が調査を開始します..."
        )

        prompt = _INVESTIGATION_PROMPT.format(**failure_info)

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
    if JOB_TRIAGE_CHANNEL_ID is None:
        logger.warning(
            "JobFailureTriageCog: JOB_TRIAGE_CHANNEL_ID is not set — Cog disabled. "
            "Set the environment variable to enable job failure auto-triage."
        )
        return

    await bot.add_cog(JobFailureTriageCog(bot, runner, components))
    logger.info(
        "JobFailureTriageCog loaded — monitoring channel %d for job failure embeds",
        JOB_TRIAGE_CHANNEL_ID,
    )
