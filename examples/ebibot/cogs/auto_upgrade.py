"""Auto Upgrade — ccdb AutoUpgradeCog configuration for EbiBot.

Flow:
1. Merge to ccdb main -> GitHub Actions -> Discord webhook "ebibot-upgrade"
2. AutoUpgradeCog fetches the bot's own git checkout and reads the changed files
3. No Python changed (Console static files, docs, tests): fast-forward now, no restart
4. Otherwise: reserve a restart that waits until nobody has used the bot for
   CCDB_RESTART_QUIET_SECONDS (default 15 min); pre-start.sh pulls on the way up

This file is config-only.  Execution logic lives in ccdb's AutoUpgradeCog.

Usage:
    CUSTOM_COGS_DIR=examples/ebibot/cogs ccdb start
"""

from __future__ import annotations

import os

from claude_discord.cogs.auto_upgrade import AutoUpgradeCog, UpgradeConfig

_UV = os.getenv("UV_PATH", os.path.expanduser("~/.local/bin/uv"))
_WORKING_DIR = os.getenv("EBIBOT_WORKING_DIR", os.path.expanduser("~/discord-bot"))
_SERVICE_NAME = os.getenv("EBIBOT_SERVICE_NAME", "discord-bot.service")

EBIBOT_UPGRADE_CONFIG = UpgradeConfig(
    package_name="claude-code-discord-bridge",
    trigger_prefix="\U0001f504 ebibot-upgrade",
    working_dir=_WORKING_DIR,
    upgrade_command=[_UV, "lock", "--upgrade-package", "claude-code-discord-bridge"],
    sync_command=[_UV, "sync"],
    restart_command=["/usr/bin/sudo", "/usr/bin/systemctl", "restart", _SERVICE_NAME],
    upgrade_approval=False,
    restart_approval=False,
    slash_command_enabled=True,
    git_checkout=True,
)


async def setup(bot: object, runner: object, components: object) -> None:
    """Entry point for the custom Cog loader."""
    cog = AutoUpgradeCog(bot=bot, config=EBIBOT_UPGRADE_CONFIG)  # type: ignore[arg-type]
    await bot.add_cog(cog)  # type: ignore[union-attr]
