"""One-call setup for all ccdb bridge Cogs.

Consumers call this instead of manually wiring each Cog.
New Cogs added to ccdb are automatically included — no consumer code changes needed.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from discord.ext.commands import Bot

    from claude_code_core.backend import SessionBackend
    from claude_code_core.frontend import SessionFrontend

    from .backend_factory import BackendFactory
    from .backend_settings import BackendSettings
    from .database.ask_repo import PendingAskRepository
    from .database.claims_repo import ClaimRepository
    from .database.frontend_thread_repo import FrontendThreadRepository
    from .database.ingest_repo import IngestResultRepository
    from .database.lounge_repo import LoungeRepository
    from .database.notification_repo import NotificationRepository
    from .database.repository import SessionRepository, UsageStatsRepository
    from .database.resume_repo import PendingResumeRepository
    from .database.settings_repo import SettingsRepository
    from .database.summary_repo import ThreadSummaryRepository
    from .database.task_repo import TaskRepository
    from .ext.api_server import ApiServer

from .deployment import DEFAULT_DATA_ROOT, DataLayout

logger = logging.getLogger(__name__)


@dataclass
class BridgeComponents:
    """References to initialized bridge components.

    After calling setup_bridge(), pass this to apply_to_api_server() so the
    ApiServer gains access to all repos without manual wiring::

        components = await setup_bridge(bot, runner, api_server=api_server)

    Or manually if you need more control::

        components = await setup_bridge(bot, runner)
        components.apply_to_api_server(api_server)
    """

    session_repo: SessionRepository
    task_repo: TaskRepository | None = None
    lounge_repo: LoungeRepository | None = None
    claims_repo: ClaimRepository | None = None
    resume_repo: PendingResumeRepository | None = None
    ingest_repo: IngestResultRepository | None = None
    summary_repo: ThreadSummaryRepository | None = None
    backend_factory: BackendFactory | None = None
    backend_settings: BackendSettings | None = None
    #: How this deployment reaches a conversation without knowing the platform.
    #: Discord's is built here; a custom Cog can read it instead of calling
    #: ``bot.get_channel`` and hard-coding Discord into its own logic.
    frontend: SessionFrontend | None = None
    frontend_threads: FrontendThreadRepository | None = None
    settings_repo: SettingsRepository | None = None
    ask_repo: PendingAskRepository | None = None
    usage_repo: UsageStatsRepository | None = None
    #: The scheduled-notification store the API server writes through, exposed
    #: so a custom Cog scheduling a reminder lands in the same database the
    #: dispatcher reads.  A Cog that opens its own file writes into a void.
    notification_repo: NotificationRepository | None = None

    def apply_to_api_server(self, api_server: ApiServer) -> None:
        """Wire all optional repos to an ApiServer instance.

        Idempotent — safe to call multiple times.  Only non-None repos are
        applied, so repos that are disabled (e.g. scheduler off) are left as-is.

        When a new repo is added to BridgeComponents in the future, add it here
        and consumers automatically pick it up without changing their own code.
        """
        if self.task_repo is not None:
            api_server.task_repo = self.task_repo
        if self.lounge_repo is not None:
            api_server.lounge_repo = self.lounge_repo
        if self.claims_repo is not None:
            api_server.claims_repo = self.claims_repo
        if self.resume_repo is not None:
            api_server.resume_repo = self.resume_repo
        if self.ingest_repo is not None:
            api_server.ingest_repo = self.ingest_repo
        if self.summary_repo is not None:
            api_server.summary_repo = self.summary_repo
        api_server.session_repo = self.session_repo


async def setup_bridge(
    bot: Bot,
    runner: SessionBackend,
    *,
    api_server: ApiServer | None = None,
    data_root: str | None = None,
    session_db_path: str | None = None,
    allowed_user_ids: set[int] | None = None,
    claude_channel_id: int | None = None,
    claude_channel_ids: set[int] | None = None,
    mention_only_channel_ids: set[int] | None = None,
    inline_reply_channel_ids: set[int] | None = None,
    chat_only_channel_ids: set[int] | None = None,
    cli_sessions_path: str | None = None,
    enable_scheduler: bool = True,
    task_db_path: str | None = None,
    lounge_channel_id: int | None = None,
    max_concurrent: int | None = None,
    worktree_base_dir: str | None = None,
    enable_thread_inbox: bool = False,
    auto_rename_threads: bool | None = None,
    monitor_all_channels: bool | None = None,
    mention_anywhere: bool | None = None,
    thread_context_days: int | None = None,
    context_links_config: str | None = None,
    backend_factory: BackendFactory | None = None,
) -> BridgeComponents:
    """Initialize and register all ccdb Cogs in one call.

    This is the recommended way for consumers to set up ccdb.
    New Cogs added to ccdb will be automatically included.

    Pass ``api_server`` to automatically wire all repos and set the runner's
    ``api_port`` — consumers then need zero manual wiring::

        components = await setup_bridge(bot, runner, api_server=api_server, ...)
        # Done — no manual repo wiring needed.

    Args:
        bot: Discord bot instance.
        runner: SessionBackend (ClaudeRunner, CodexRunner, etc.).
        api_server: Optional ApiServer to auto-wire repos into.  Also sets
                    runner.api_port so CCDB_API_URL is available to Claude.
        data_root: Directory this deployment owns. Every database, worktree
            and log defaults to living under it, so isolating a second
            deployment on the same host is one setting rather than several.
            Defaults to ``CCDB_DATA_ROOT``, then to ``data`` — the historical
            location, so an upgrade never moves a live database.
        session_db_path: Path for session SQLite DB. Overrides *data_root* for
            this one file; the override is logged at startup because it is the
            only remaining way two deployments can end up sharing state.
        allowed_user_ids: Set of Discord user IDs allowed to use Claude.
        claude_channel_id: Primary channel ID for Claude chat.  Kept for
                           backward compatibility.  Also used as the fallback
                           thread-creation target in SkillCommandCog.
        claude_channel_ids: Additional channel IDs that need **no @mention** —
                            everything posted there (and in threads under them)
                            goes to Claude.  Combined with ``claude_channel_id``
                            to form the full set.  Everywhere else in the guild
                            the bot answers only when @mentioned, so this list
                            configures where ccdb is *silent by default*, not
                            where it exists.
        mention_anywhere: When True (default), an @mention summons Claude in any
                          guild channel or thread, not just the configured ones.
                          Set False to restore the strict listed-channels-only
                          behaviour.  Defaults to the CCDB_MENTION_ANYWHERE env var.
        thread_context_days: How many days of a foreign thread's history to feed
                             Claude when a mention wakes it there, so it answers
                             about the conversation it just walked into.  ``0``
                             disables the transcript.  Defaults to the
                             CCDB_THREAD_CONTEXT_DAYS env var, then 7.
        mention_only_channel_ids: Channel IDs carved back out of the no-mention
                                  set above.  Largely redundant now that not
                                  listing a channel has the same effect; kept for
                                  configurations that list a parent channel and
                                  want one child excluded.  Defaults to the
                                  MENTION_ONLY_CHANNEL_IDS env var (comma-separated).
        chat_only_channel_ids: Channel IDs where only text responses are shown.
                               Tool embeds, thinking blocks, and session chrome are
                               hidden.  Useful for public channels where non-technical
                               users are watching.  Defaults to CHAT_ONLY_CHANNEL_IDS
                               env var (comma-separated).
        cli_sessions_path: Path to ~/.claude/projects for session sync.
        enable_scheduler: Whether to enable SchedulerCog.
        task_db_path: Path for scheduled tasks SQLite DB. Overrides *data_root*.
        lounge_channel_id: Discord channel ID for AI Lounge messages.
                           Defaults to COORDINATION_CHANNEL_ID env var.
        worktree_base_dir: Base directory to scan for session worktrees
                           (e.g. ``/home/user``). When set, a WorktreeManager
                           is created and attached to the bot, enabling automatic
                           cleanup of session worktrees at session end and startup.
                           Defaults to WORKTREE_BASE_DIR env var, or None (disabled).
        auto_rename_threads: When True, rename each new thread with a Claude-generated
                             title derived from the first user message.  Runs as a
                             background task so it never delays the session start.
                             Defaults to THREAD_AUTO_RENAME env var (off by default).
        context_links_config: Path to a JSON config that maps project keywords to
                              external resource links (Obsidian notes, GitHub repos,
                              etc.).  Defaults to CONTEXT_LINKS_CONFIG env var, or
                              ``context_links.json`` in the working directory.

    Returns:
        BridgeComponents with references to initialized repositories.
    """
    from .cogs.claude_chat import ClaudeChatCog
    from .cogs.collision_watch import CollisionWatchCog
    from .cogs.context_links import ContextLinksCog
    from .cogs.scheduler import SchedulerCog
    from .cogs.session_manage import SessionManageCog
    from .cogs.skill_command import SkillCommandCog
    from .cross_backend_handoff import ConversationHistoryReader
    from .database.inbox_repo import ThreadInboxRepository
    from .database.task_repo import TaskRepository
    from .discord_ui.thread_context import DEFAULT_DAYS
    from .worktree import WorktreeManager

    # Build the full set of claude channel IDs from both parameters
    _all_channel_ids: set[int] = set()
    if claude_channel_id is not None:
        _all_channel_ids.add(claude_channel_id)
    if claude_channel_ids is not None:
        _all_channel_ids.update(claude_channel_ids)

    # Mention-only channels — fall back to MENTION_ONLY_CHANNEL_IDS env var
    if mention_only_channel_ids is None:
        _env_mention = os.getenv("MENTION_ONLY_CHANNEL_IDS", "")
        mention_only_channel_ids = {
            int(x.strip()) for x in _env_mention.split(",") if x.strip().isdigit()
        } or None

    # Inline-reply channels — fall back to INLINE_REPLY_CHANNEL_IDS env var
    if inline_reply_channel_ids is None:
        _env_inline = os.getenv("INLINE_REPLY_CHANNEL_IDS", "")
        inline_reply_channel_ids = {
            int(x.strip()) for x in _env_inline.split(",") if x.strip().isdigit()
        } or None

    # Chat-only channels — fall back to CHAT_ONLY_CHANNEL_IDS env var
    if chat_only_channel_ids is None:
        _env_chat_only = os.getenv("CHAT_ONLY_CHANNEL_IDS", "")
        chat_only_channel_ids = {
            int(x.strip()) for x in _env_chat_only.split(",") if x.strip().isdigit()
        } or None

    # Lounge channel — fall back to COORDINATION_CHANNEL_ID env var for backward compat
    if lounge_channel_id is None:
        ch_str = os.getenv("COORDINATION_CHANNEL_ID", "")
        lounge_channel_id = int(ch_str) if ch_str.isdigit() else None

    # Thread auto-rename — fall back to THREAD_AUTO_RENAME env var (off by default)
    if auto_rename_threads is None:
        auto_rename_threads = os.getenv("THREAD_AUTO_RENAME", "").lower() in (
            "true",
            "1",
            "yes",
        )
    if auto_rename_threads:
        logger.info("Thread auto-rename enabled (THREAD_AUTO_RENAME)")

    # Monitor-all-channels — fall back to CLAUDE_MONITOR_ALL_CHANNELS env var
    if monitor_all_channels is None:
        monitor_all_channels = os.getenv("CLAUDE_MONITOR_ALL_CHANNELS", "").lower() in (
            "true",
            "1",
            "yes",
        )
    if monitor_all_channels:
        logger.info("Monitor-all-channels enabled — bot will respond in ANY guild channel")

    # Mention-anywhere — fall back to CCDB_MENTION_ANYWHERE env var (on by default).
    # This is what turns claude_channel_ids into "channels that need no mention":
    # everywhere else the bot answers only when someone @mentions it.
    if mention_anywhere is None:
        mention_anywhere = os.getenv("CCDB_MENTION_ANYWHERE", "true").lower() not in (
            "false",
            "0",
            "no",
        )
    if not mention_anywhere:
        logger.info("Mention-anywhere disabled — bot only listens in configured channels")

    # Thread context window — fall back to CCDB_THREAD_CONTEXT_DAYS env var, then 7 days.
    if thread_context_days is None:
        _env_days = os.getenv("CCDB_THREAD_CONTEXT_DAYS", "")
        thread_context_days = int(_env_days) if _env_days.lstrip("-").isdigit() else DEFAULT_DAYS
    if thread_context_days != DEFAULT_DAYS:
        logger.info("Thread context window: %d day(s)", thread_context_days)

    # Max concurrent sessions — fall back to MAX_CONCURRENT_SESSIONS env var, then 3
    if max_concurrent is None:
        _env_max = os.getenv("MAX_CONCURRENT_SESSIONS", "")
        max_concurrent = int(_env_max) if _env_max.isdigit() else 3
    if max_concurrent != 3:
        logger.info("Max concurrent sessions: %d", max_concurrent)

    from .cogs._run_helper import configure_pr_completion_gate, configure_session_limit

    configure_session_limit(max_concurrent)
    pr_completion_owner = os.getenv("CCDB_PR_COMPLETION_OWNER", "").strip()
    configure_pr_completion_gate(pr_completion_owner or None)
    if pr_completion_owner:
        logger.info("Owner PR completion gate enabled for %s", pr_completion_owner)

    # WorktreeManager — attach to bot so cogs can access it via bot.worktree_manager
    if worktree_base_dir is None:
        worktree_base_dir = os.getenv("WORKTREE_BASE_DIR")
    if worktree_base_dir is not None:
        if getattr(bot, "worktree_manager", None) is None:
            bot.worktree_manager = WorktreeManager(base_dir=worktree_base_dir)  # type: ignore[attr-defined]
        logger.info("WorktreeManager enabled (base_dir=%s)", worktree_base_dir)
    else:
        # Every session is told to create ``wt-{thread_id}``; without a base dir
        # nothing ever removes them, and the leak is silent because the enabled
        # branch is the only one that used to log. Say so on the disabled path.
        logger.warning(
            "WorktreeManager disabled: WORKTREE_BASE_DIR is not set. Sessions will "
            "keep creating wt-{thread_id} worktrees and nothing will clean them up."
        )

    # --- Deployment layout -------------------------------------------------
    # One root per deployment. Ten repositories share session_db_path, so this
    # single file is where cross-customer leakage would happen if two
    # deployments ever pointed at the same place.
    layout = DataLayout.for_root(
        data_root if data_root is not None else os.getenv("CCDB_DATA_ROOT") or DEFAULT_DATA_ROOT,
        sessions_db=session_db_path,
        tasks_db=task_db_path,
        worktrees_dir=worktree_base_dir,
    )
    session_db_path = layout.sessions_db
    task_db_path = layout.tasks_db
    escaped = layout.paths_outside_root()
    if escaped:
        logger.warning(
            "Deployment root is %s but these paths live outside it: %s — "
            "two deployments sharing any of them would share state",
            layout.root,
            ", ".join(f"{k}={v}" for k, v in sorted(escaped.items())),
        )

    # --- Session DB (also hosts lounge_messages and pending_resumes tables) ---
    # Everything below this line is frontend-neutral, so it lives in its own
    # module: a Teams deployment needs the same stores and none of the Discord
    # wiring that follows.
    from .frontend import DiscordFrontend
    from .stores import build_session_stores
    from .teams_integration import FrontendRouter

    stores = await build_session_stores(session_db_path)

    # The frontend seam. Built once and handed to everything that needs to
    # reach a conversation, so a Cog never has to call bot.get_channel itself.
    frontend = FrontendRouter(DiscordFrontend(bot, ledger=stores.frontend_threads))
    session_repo = stores.sessions
    settings_repo = stores.settings
    ask_repo = stores.asks
    lounge_repo = stores.lounge
    claims_repo = stores.claims
    resume_repo = stores.resumes
    usage_repo = stores.usage
    ingest_repo = stores.ingest
    summary_repo = stores.summaries

    # Attach repos to bot so generic cogs (e.g. AutoUpgradeCog) can discover them
    # without a hard import dependency on ccdb internals.
    bot.session_repo = session_repo  # type: ignore[attr-defined]
    bot.resume_repo = resume_repo  # type: ignore[attr-defined]

    # --- Thread inbox (optional — THREAD_INBOX_ENABLED=true) ---
    if enable_thread_inbox:
        inbox_repo = ThreadInboxRepository(session_db_path)
        bot.inbox_repo = inbox_repo  # type: ignore[attr-defined]
        logger.info("Thread inbox enabled")

    # --- ClaudeChatCog ---
    # Build BackendSettings up front so we can also pass it into
    # ClaudeChatCog (so per-thread /backend overrides take effect on spawn).
    backend_settings: BackendSettings | None = None
    if backend_factory is not None:
        from .backend_settings import BackendSettings

        _runner_class = runner.__class__.__name__
        backend_settings = BackendSettings(
            settings_repo,
            env_backend=_runner_class.replace("Runner", "").lower(),
            env_model_for_claude=(runner.model if _runner_class == "ClaudeRunner" else ""),
            env_model_for_codex=(runner.model if _runner_class == "CodexRunner" else ""),
        )

    chat_cog = ClaudeChatCog(
        bot,  # type: ignore[arg-type]  # consumers pass their own Bot subclass
        repo=session_repo,
        runner=runner,
        factory=backend_factory,
        backend_settings=backend_settings,
        conversation_history=ConversationHistoryReader(
            claude_sessions_root=cli_sessions_path,
        ),
        max_concurrent=max_concurrent,
        allowed_user_ids=allowed_user_ids,
        ask_repo=ask_repo,
        lounge_repo=lounge_repo,
        resume_repo=resume_repo,
        settings_repo=settings_repo,
        channel_ids=_all_channel_ids or None,
        mention_only_channel_ids=mention_only_channel_ids or None,
        inline_reply_channel_ids=inline_reply_channel_ids or None,
        chat_only_channel_ids=chat_only_channel_ids or None,
        auto_rename_threads=auto_rename_threads,
        monitor_all_channels=monitor_all_channels,
        mention_anywhere=mention_anywhere,
        thread_context_days=thread_context_days,
    )
    await bot.add_cog(chat_cog)
    logger.info("Registered ClaudeChatCog")

    # --- CollisionWatchCog (auto-enabled; no-op until two sessions overlap) ---
    await bot.add_cog(
        CollisionWatchCog(
            bot,
            lounge_repo=lounge_repo,
            lounge_channel_id=lounge_channel_id,
        )
    )
    logger.info("Registered CollisionWatchCog")

    # --- SessionManageCog ---
    session_manage_cog = SessionManageCog(
        bot,  # type: ignore[arg-type]  # consumers pass their own Bot subclass
        repo=session_repo,
        cli_sessions_path=cli_sessions_path,
        settings_repo=settings_repo,
        usage_repo=usage_repo,
    )
    await bot.add_cog(session_manage_cog)
    logger.info("Registered SessionManageCog")

    # --- SkillCommandCog (requires at least one channel ID) ---
    if _all_channel_ids:
        # Primary channel: prefer the explicit claude_channel_id, else pick from set
        _primary_channel_id = claude_channel_id or next(iter(_all_channel_ids))
        skill_cog = SkillCommandCog(
            bot,
            repo=session_repo,
            runner=runner,
            claude_channel_id=_primary_channel_id,
            claude_channel_ids=_all_channel_ids,
            allowed_user_ids=allowed_user_ids,
        )
        await bot.add_cog(skill_cog)
        logger.info("Registered SkillCommandCog")

    # --- SchedulerCog (optional) ---
    task_repo: TaskRepository | None = None
    if enable_scheduler:
        os.makedirs(os.path.dirname(task_db_path) or ".", exist_ok=True)
        task_repo = TaskRepository(task_db_path)
        await task_repo.init_db()
        # Honour ScheduleWakeup tool calls (harness /loop self-pacing) by
        # registering one-shot resume tasks in this scheduler.
        from .cogs._run_helper import configure_wakeup_scheduler

        configure_wakeup_scheduler(task_repo)
        scheduler_cog = SchedulerCog(
            bot,
            runner,
            repo=task_repo,
            session_repo=session_repo,
            backend_factory=backend_factory,
            backend_settings=backend_settings,
            frontend=frontend,
        )
        await bot.add_cog(scheduler_cog)
        logger.info("Registered SchedulerCog")

    # --- ContextLinksCog (optional — CONTEXT_LINKS_CONFIG or context_links.json) ---
    if context_links_config is None:
        context_links_config = os.getenv("CONTEXT_LINKS_CONFIG", "context_links.json")
    context_links_cog = ContextLinksCog(
        bot,
        config_path=context_links_config,
        channel_ids=_all_channel_ids or None,
    )
    await bot.add_cog(context_links_cog)
    if context_links_cog._config is not None:
        logger.info("Registered ContextLinksCog (config=%s)", context_links_config)

    # --- BackendCommandCog (optional — only if a BackendFactory was provided) ---
    if backend_factory is not None:
        from .cogs.backend_command import BackendCommandCog

        assert backend_settings is not None
        backend_cmd_cog = BackendCommandCog(
            bot,  # type: ignore[arg-type]
            settings=backend_settings,
            factory=backend_factory,
            chat_cog=chat_cog,
        )
        await bot.add_cog(backend_cmd_cog)
        logger.info("Registered BackendCommandCog")

        # --- OllamaCommandCog: management for the `local` backend's runtime ---
        # Registered alongside /backend rather than gated on reachability: the
        # command whose job is to diagnose an unreachable Ollama must exist
        # when Ollama is unreachable.
        from .cogs.ollama_command import OllamaCommandCog

        await bot.add_cog(
            OllamaCommandCog(
                bot,  # type: ignore[arg-type]
                settings=backend_settings,
                chat_cog=chat_cog,
            )
        )
        logger.info("Registered OllamaCommandCog")

    # --- AskCommandCog (auto-discovered: only when anonymization rules exist) ---
    # Zero-config by the same rule as the gateway itself — no rules file, no
    # command, because a /ask that forwards real names is worse than none.
    from claude_code_core.privacy import get_gateway

    try:
        if get_gateway() is not None:
            from .cogs.ask_command import AskCommandCog

            await bot.add_cog(AskCommandCog(bot))  # type: ignore[arg-type]
            logger.info("Registered AskCommandCog (anonymized external escalation)")
    except Exception:
        # A broken rules file must not take the whole bot down; the gateway
        # raises deliberately, and the chat path surfaces it on first use.
        logger.exception("Could not register AskCommandCog")

    components = BridgeComponents(
        session_repo=session_repo,
        task_repo=task_repo,
        lounge_repo=lounge_repo,
        claims_repo=claims_repo,
        resume_repo=resume_repo,
        ingest_repo=ingest_repo,
        summary_repo=summary_repo,
        backend_factory=backend_factory,
        backend_settings=backend_settings,
        frontend=frontend,
        frontend_threads=stores.frontend_threads,
        settings_repo=settings_repo,
        ask_repo=ask_repo,
        usage_repo=usage_repo,
    )

    # Auto-wire repos to ApiServer and set runner.api_port if provided
    if api_server is not None:
        # An API that accepts POST /api/schedule has to deliver it, so the
        # dispatcher ships with the endpoint rather than with the consumer.
        # It is handed the API's own repository object: matching two paths is
        # a convention that drifts, sharing one object is a structure.
        from .cogs.notification_dispatch import NotificationDispatchCog

        await bot.add_cog(
            NotificationDispatchCog(
                bot,
                repo=api_server.repo,
                default_channel_id=claude_channel_id,
            )
        )
        logger.info("Registered NotificationDispatchCog")

        # Custom Cogs schedule reminders through this rather than opening a
        # database of their own.
        components.notification_repo = api_server.repo

        components.apply_to_api_server(api_server)
        if runner.api_port is None:
            runner.api_port = api_server.port
        if hasattr(runner, "api_secret") and getattr(runner, "api_secret", None) is None:
            setattr(runner, "api_secret", api_server.api_secret)  # noqa: B010 - optional backend field
        if backend_factory is not None and backend_factory.api_port is None:
            backend_factory.api_port = api_server.port
        if backend_factory is not None and backend_factory.api_secret is None:
            backend_factory.api_secret = api_server.api_secret
        logger.info("Auto-wired repos to ApiServer (port=%d)", api_server.port)

    return components
