"""Entry point for claude-code-discord-bridge bot.

Standalone launcher that uses ``setup_bridge()`` for full Cog auto-setup
and optionally loads custom Cogs from an external directory via
``CUSTOM_COGS_DIR`` env or ``--cogs-dir`` CLI flag.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from dotenv import find_dotenv, load_dotenv

from .bot import ClaudeDiscordBot
from .cog_loader import load_custom_cogs
from .deployment import DataLayout
from .setup import setup_bridge
from .teams_integration import FrontendRouter, build_teams_runtime, parse_frontends
from .utils.logger import setup_logging

logger = logging.getLogger(__name__)


def load_config() -> dict[str, str]:
    """Load and validate configuration from environment."""
    load_dotenv(find_dotenv(usecwd=True))

    token = os.getenv("DISCORD_BOT_TOKEN", "")
    if not token:
        logger.error("DISCORD_BOT_TOKEN is required")
        sys.exit(1)

    channel_id = os.getenv("DISCORD_CHANNEL_ID", "")
    if not channel_id:
        logger.error("DISCORD_CHANNEL_ID is required")
        sys.exit(1)

    def _env(new: str, old: str, default: str = "") -> str:
        """Read CCDB_* env var with CLAUDE_* fallback."""
        return os.getenv(new) or os.getenv(old, default)

    backend = os.getenv("CCDB_BACKEND", "claude")
    frontends = parse_frontends(os.getenv("CCDB_FRONTENDS", ""))
    # Default model is backend-specific: Claude needs an explicit alias
    # ("sonnet"), but Codex defers to its own config.toml default when left
    # empty (so we never pin a stale Codex model version).
    default_model = "sonnet" if backend == "claude" else ""

    return {
        "token": token,
        "channel_id": channel_id,
        "backend": backend,
        "frontends": ",".join(frontends),
        "command": _env("CCDB_COMMAND", "CLAUDE_COMMAND", ""),
        # Per-backend explicit command paths. Used by BackendFactory when
        # the user switches backend at runtime via /backend.
        "claude_command": _env("CCDB_CLAUDE_COMMAND", "CLAUDE_COMMAND", ""),
        "codex_command": os.getenv("CCDB_CODEX_COMMAND", ""),
        "agui_url": os.getenv("CCDB_AGUI_URL", ""),
        "agui_token": os.getenv("CCDB_AGUI_TOKEN", ""),
        "model": _env("CCDB_MODEL", "CLAUDE_MODEL", default_model),
        "permission_mode": _env("CCDB_PERMISSION_MODE", "CLAUDE_PERMISSION_MODE", "acceptEdits"),
        "working_dir": _env("CCDB_WORKING_DIR", "CLAUDE_WORKING_DIR", ""),
        "dangerously_skip_permissions": _env(
            "CCDB_DANGEROUSLY_SKIP_PERMISSIONS", "CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS", ""
        ),
        "allowed_tools": _env("CCDB_ALLOWED_TOOLS", "CLAUDE_ALLOWED_TOOLS", ""),
        "effort": _env("CCDB_EFFORT", "CLAUDE_EFFORT", ""),
        "append_system_prompt": os.getenv("APPEND_SYSTEM_PROMPT", ""),
        "max_concurrent": os.getenv("MAX_CONCURRENT_SESSIONS", "3"),
        "timeout": os.getenv("SESSION_TIMEOUT_SECONDS", "300"),
        "owner_id": os.getenv("DISCORD_OWNER_ID", ""),
        "channel_ids": _env("CCDB_CHANNEL_IDS", "CLAUDE_CHANNEL_IDS", ""),
        "monitor_all_channels": _env(
            "CCDB_MONITOR_ALL_CHANNELS", "CLAUDE_MONITOR_ALL_CHANNELS", "false"
        ),
        "api_host": os.getenv("API_HOST", "127.0.0.1"),
        "api_secret": os.getenv("CCDB_API_SECRET", ""),
        "api_port": os.getenv("API_PORT", ""),
        "custom_cogs_dir": os.getenv("CUSTOM_COGS_DIR", ""),
        "cli_sessions_path": os.getenv("CLI_SESSIONS_PATH", ""),
        "thread_inbox_enabled": os.getenv("THREAD_INBOX_ENABLED", "false"),
    }


async def main() -> None:
    """Start the bot."""
    setup_logging()
    config = load_config()
    enabled_frontends = parse_frontends(config["frontends"])

    channel_id = int(config["channel_id"])

    # Parse optional multi-channel IDs
    claude_channel_ids: set[int] | None = None
    if config["channel_ids"]:
        claude_channel_ids = {
            int(x.strip()) for x in config["channel_ids"].split(",") if x.strip().isdigit()
        } or None

    # Parse allowed tools
    allowed_tools: list[str] | None = None
    if config["allowed_tools"]:
        allowed_tools = [t.strip() for t in config["allowed_tools"].split(",") if t.strip()] or None

    # Create runner via backend factory (CCDB_BACKEND=claude|codex)
    backend_name = config["backend"]
    # BackendFactory is the runtime authority for building Claude/Codex
    # runners on demand (e.g. when the user switches via /backend).
    from .backend_factory import BackendFactory

    factory = BackendFactory(
        claude_command=config["claude_command"]
        or (config["command"] if backend_name == "claude" else "")
        or "claude",
        codex_command=config["codex_command"]
        or (config["command"] if backend_name == "codex" else "")
        or "codex",
        permission_mode=config["permission_mode"],
        working_dir=config["working_dir"] or None,
        timeout_seconds=int(config["timeout"]),
        dangerously_skip_permissions=config["dangerously_skip_permissions"].lower()
        in ("true", "1", "yes"),
        allowed_tools=allowed_tools,
        append_system_prompt=config["append_system_prompt"] or None,
        effort=config["effort"] or None,
        agui_url=config["agui_url"] or None,
        agui_token=config["agui_token"] or None,
        api_secret=config["api_secret"] or None,
    )

    runner = factory.build(backend=backend_name, model=config["model"] or None)

    owner_id = int(config["owner_id"]) if config["owner_id"] else None
    bot = ClaudeDiscordBot(
        channel_id=channel_id,
        owner_id=owner_id,
    )

    # Optional API server
    api_server = None
    if config["api_port"]:
        from .database.notification_repo import NotificationRepository
        from .ext.api_server import ApiServer

        # Everything else derives its path from the deployment root; this one
        # used to be hardcoded, which meant two deployments sharing a working
        # directory would silently share their scheduled notifications.
        notification_repo = NotificationRepository(DataLayout.from_env().notifications_db)
        await notification_repo.init_db()
        api_server = ApiServer(
            repo=notification_repo,
            bot=bot,
            default_channel_id=channel_id,
            host=config["api_host"],
            port=int(config["api_port"]),
            api_secret=config["api_secret"] or None,
            ingest_token=os.getenv("CCDB_INGEST_TOKEN") or None,
            ingest_host=os.getenv("CCDB_INGEST_HOST") or None,
            ingest_port=int(os.environ["CCDB_INGEST_PORT"])
            if os.getenv("CCDB_INGEST_PORT")
            else None,
            max_body_bytes=int(os.environ["CCDB_MAX_BODY_BYTES"])
            if os.getenv("CCDB_MAX_BODY_BYTES")
            else None,
            working_dir=config["working_dir"] or None,
            transcripts_path=config["cli_sessions_path"] or None,
        )

    async with bot:
        # Full Cog auto-setup via setup_bridge
        allowed_user_ids = {owner_id} if owner_id else None
        components = await setup_bridge(
            bot,
            runner,
            api_server=api_server,
            backend_factory=factory,
            allowed_user_ids=allowed_user_ids,
            claude_channel_id=channel_id,
            claude_channel_ids=claude_channel_ids,
            data_root=os.getenv("CCDB_DATA_ROOT") or None,
            cli_sessions_path=config["cli_sessions_path"] or None,
            enable_thread_inbox=config["thread_inbox_enabled"].lower() == "true",
            monitor_all_channels=config["monitor_all_channels"].lower() in ("true", "1", "yes"),
        )

        # Load custom Cogs from external directory
        cogs_dir = config["custom_cogs_dir"]
        if cogs_dir:
            await load_custom_cogs(Path(cogs_dir), bot, runner, components)

        # Cleanup old sessions on startup
        deleted = await components.session_repo.cleanup_old(days=30)
        if deleted:
            logger.info("Cleaned up %d old sessions", deleted)

        # Start API server if configured
        if api_server is not None:
            await api_server.start()

        teams_runtime = None
        try:
            if "teams" in enabled_frontends:
                if (
                    components.backend_settings is None
                    or components.frontend_threads is None
                    or not isinstance(components.frontend, FrontendRouter)
                ):
                    raise RuntimeError("Teams requires the normal backend and session-store wiring")
                teams_runtime = await build_teams_runtime(
                    os.environ,
                    components=components,
                    backend_factory=factory,
                    registry=getattr(bot, "session_registry", None),
                    worktree_manager=getattr(bot, "worktree_manager", None),
                )
                components.frontend.add(teams_runtime.frontend)
                await teams_runtime.start()
                logger.info("Teams activity puller started beside Discord")

            # Handle signals (add_signal_handler is not supported on Windows)
            if sys.platform != "win32":
                loop = asyncio.get_running_loop()
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.add_signal_handler(sig, lambda: asyncio.create_task(bot.close()))

            await bot.start(config["token"])
        finally:
            if teams_runtime is not None:
                await teams_runtime.close()
                logger.info("Teams activity puller stopped")


if __name__ == "__main__":
    asyncio.run(main())
