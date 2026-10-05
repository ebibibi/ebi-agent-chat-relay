"""claude-code-discord-bridge — Discord frontend for Claude Code CLI.

Built entirely by Claude Code itself. See README.md for details.

Quick start::

    from claude_discord import ClaudeChatCog, ClaudeRunner, SessionRepository

"""

from .claude.parser import parse_line
from .claude.runner import ClaudeRunner
from .claude.types import MessageType, StreamEvent, ToolCategory, ToolUseEvent
from .cog_loader import load_custom_cogs
from .cogs.ask_command import AskCommandCog
from .cogs.attention_command import AttentionCog
from .cogs.auto_upgrade import AutoUpgradeCog, UpgradeConfig
from .cogs.claude_chat import ClaudeChatCog
from .cogs.collision_watch import CollisionWatchCog
from .cogs.context_links import ContextLinksCog
from .cogs.event_processor import EventProcessor
from .cogs.notification_dispatch import NotificationDispatchCog
from .cogs.ollama_command import OllamaCommandCog
from .cogs.run_config import RunConfig
from .cogs.scheduler import SchedulerCog
from .cogs.session_manage import SessionManageCog
from .cogs.skill_command import SkillCommandCog
from .cogs.webhook_trigger import WebhookTrigger, WebhookTriggerCog
from .concurrency import ActiveSession, SessionRegistry
from .database.notification_repo import NotificationRepository
from .database.repository import SessionRepository
from .database.settings_repo import SettingsRepository
from .database.task_repo import TaskRepository as ScheduledTaskRepository
from .discord_ui.chunker import chunk_message
from .discord_ui.embeds import (
    error_embed,
    session_complete_embed,
    session_start_embed,
    tool_use_embed,
)
from .discord_ui.status import StatusManager
from .protocols import DrainAware
from .session_sync import CliSession, SessionMessage, extract_recent_messages, scan_cli_sessions
from .setup import BridgeComponents, setup_bridge
from .surface import DiscordSurface

__all__ = [
    "DiscordSurface",
    # Core
    "ClaudeRunner",
    "ClaudeChatCog",
    "ContextLinksCog",
    "RunConfig",
    "EventProcessor",
    # Concurrency
    "ActiveSession",
    "SessionRegistry",
    "SessionManageCog",
    "CollisionWatchCog",
    "AskCommandCog",
    "AttentionCog",
    "OllamaCommandCog",
    "SkillCommandCog",
    "SessionRepository",
    "SettingsRepository",
    # Session Sync
    "CliSession",
    "SessionMessage",
    "extract_recent_messages",
    "scan_cli_sessions",
    # Webhook & Automation
    "WebhookTriggerCog",
    "WebhookTrigger",
    "AutoUpgradeCog",
    "UpgradeConfig",
    # Scheduling
    "NotificationDispatchCog",
    "SchedulerCog",
    "ScheduledTaskRepository",
    "DrainAware",
    "NotificationRepository",
    # Types
    "MessageType",
    "StreamEvent",
    "ToolCategory",
    "ToolUseEvent",
    # Parsing
    "parse_line",
    # Setup
    "setup_bridge",
    "BridgeComponents",
    "load_custom_cogs",
    # UI
    "StatusManager",
    "chunk_message",
    "error_embed",
    "session_complete_embed",
    "session_start_embed",
    "tool_use_embed",
]
