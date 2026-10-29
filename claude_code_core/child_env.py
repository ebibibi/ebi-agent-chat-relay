"""Credentials owned by the relay must not become ambient agent authority.

Provider credentials remain available to the selected CLI. The local control
plane credential is injected explicitly by the runner after this filter.
This is an environment boundary, not an operating-system sandbox.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from pathlib import Path

logger = logging.getLogger(__name__)

STRIPPED_ENV_KEYS = frozenset(
    {
        "CLAUDECODE",
        "DISCORD_BOT_TOKEN",
        "DISCORD_TOKEN",
        "DISCORD_WEBHOOK_URL",
        "API_SECRET_KEY",
        "CCDB_AGUI_URL",
        "CCDB_AGUI_TOKEN",
        "CCDB_INGEST_TOKEN",
        "CCDB_TEAMS_APP_PASSWORD",
        "CCDB_TEAMS_QUEUE_URL",
        "CCDB_API_URL",
        "CCDB_API_SECRET",
    }
)


def strip_transport_credentials(env: dict[str, str]) -> dict[str, str]:
    """Filter inherited and overlay environments with the same policy."""
    return {key: value for key, value in env.items() if key not in STRIPPED_ENV_KEYS}


def cli_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment an agent CLI is started with, before per-turn additions.

    Relay credentials stripped and the ``CCDB_CLI_ENV_FILE`` overlay applied, as
    ``ClaudeRunner._build_env`` does — so anything that must see what the CLI
    sees (its sign-in, its provider) reads the same home and variables.
    """
    base = dict(os.environ if source is None else source)
    env = {k: v for k, v in base.items() if k not in STRIPPED_ENV_KEYS}
    overlay = base.get("CCDB_CLI_ENV_FILE")
    if overlay:
        try:
            for line in Path(overlay).read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env[key] = value
        except OSError:
            logger.debug("CLI env overlay not found: %s", overlay)
    return strip_transport_credentials(env)
