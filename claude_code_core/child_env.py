"""Credentials owned by the relay must not become ambient agent authority.

Provider credentials remain available to the selected CLI. The local control
plane credential is injected explicitly by the runner after this filter.
This is an environment boundary, not an operating-system sandbox.
"""

from __future__ import annotations

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
