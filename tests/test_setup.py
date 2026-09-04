"""Tests for setup_bridge() auto-discovery function."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_discord.setup import BridgeComponents, setup_bridge


def _make_bot() -> MagicMock:
    bot = MagicMock()
    bot.loop = MagicMock()
    bot.add_cog = AsyncMock()
    return bot


def _make_runner() -> MagicMock:
    runner = MagicMock()
    runner.clone.return_value = runner
    return runner


@pytest.mark.asyncio
async def test_setup_bridge_registers_core_cogs(tmp_path: object) -> None:
    """setup_bridge should register ClaudeChatCog, SessionManageCog, SkillCommandCog."""
    bot = _make_bot()
    runner = _make_runner()

    result = await setup_bridge(
        bot,
        runner,
        session_db_path=str(tmp_path / "sessions.db"),  # type: ignore[operator]
        claude_channel_id=12345,
        enable_scheduler=False,
    )

    cog_names = [call.args[0].__class__.__name__ for call in bot.add_cog.call_args_list]
    assert "ClaudeChatCog" in cog_names
    assert "SessionManageCog" in cog_names
    assert "SkillCommandCog" in cog_names
    assert isinstance(result, BridgeComponents)


@pytest.mark.asyncio
async def test_setup_bridge_registers_scheduler_when_enabled(tmp_path: object) -> None:
    """setup_bridge should register SchedulerCog when enable_scheduler=True."""
    bot = _make_bot()
    runner = _make_runner()

    result = await setup_bridge(
        bot,
        runner,
        session_db_path=str(tmp_path / "sessions.db"),  # type: ignore[operator]
        enable_scheduler=True,
        task_db_path=str(tmp_path / "tasks.db"),  # type: ignore[operator]
    )

    cog_names = [call.args[0].__class__.__name__ for call in bot.add_cog.call_args_list]
    assert "SchedulerCog" in cog_names
    assert result.task_repo is not None


@pytest.mark.asyncio
async def test_setup_bridge_wires_backend_settings_into_components_and_scheduler(
    tmp_path: object,
) -> None:
    """Headless cogs should receive the same backend resolver as chat commands."""
    from claude_discord.backend_factory import BackendFactory

    bot = _make_bot()
    runner = _make_runner()
    runner.model = "sonnet"
    factory = BackendFactory(
        claude_command="claude",
        codex_command="codex",
        permission_mode="acceptEdits",
        working_dir=None,
        timeout_seconds=300,
        dangerously_skip_permissions=False,
        allowed_tools=None,
        append_system_prompt=None,
        effort=None,
    )

    result = await setup_bridge(
        bot,
        runner,
        session_db_path=str(tmp_path / "sessions.db"),  # type: ignore[operator]
        task_db_path=str(tmp_path / "tasks.db"),  # type: ignore[operator]
        enable_scheduler=True,
        backend_factory=factory,
    )

    assert result.backend_factory is factory
    assert result.backend_settings is not None
    scheduler_cog = next(
        call.args[0]
        for call in bot.add_cog.call_args_list
        if call.args[0].__class__.__name__ == "SchedulerCog"
    )
    assert scheduler_cog.backend_factory is factory
    assert scheduler_cog.backend_settings is result.backend_settings


@pytest.mark.asyncio
async def test_setup_bridge_skips_scheduler_when_disabled(tmp_path: object) -> None:
    """setup_bridge should NOT register SchedulerCog when enable_scheduler=False."""
    bot = _make_bot()
    runner = _make_runner()

    result = await setup_bridge(
        bot,
        runner,
        session_db_path=str(tmp_path / "sessions.db"),  # type: ignore[operator]
        enable_scheduler=False,
    )

    cog_names = [call.args[0].__class__.__name__ for call in bot.add_cog.call_args_list]
    assert "SchedulerCog" not in cog_names
    assert result.task_repo is None


@pytest.mark.asyncio
async def test_setup_bridge_returns_components(tmp_path: object) -> None:
    """setup_bridge should return BridgeComponents with session_repo."""
    bot = _make_bot()
    runner = _make_runner()

    result = await setup_bridge(
        bot,
        runner,
        session_db_path=str(tmp_path / "sessions.db"),  # type: ignore[operator]
        enable_scheduler=False,
    )

    assert isinstance(result, BridgeComponents)
    assert result.session_repo is not None
    assert result.session_repo.db_path == str(tmp_path / "sessions.db")  # type: ignore[operator]
    assert result.frontend_threads is not None
    assert result.ask_repo is not None
    assert result.usage_repo is not None
    assert result.frontend is not None
    assert result.frontend.name == "multi"


@pytest.mark.asyncio
async def test_setup_bridge_skips_skill_cog_without_channel_id(tmp_path: object) -> None:
    """setup_bridge should skip SkillCommandCog when claude_channel_id is None."""
    bot = _make_bot()
    runner = _make_runner()

    await setup_bridge(
        bot,
        runner,
        session_db_path=str(tmp_path / "sessions.db"),  # type: ignore[operator]
        claude_channel_id=None,
        enable_scheduler=False,
    )

    cog_names = [call.args[0].__class__.__name__ for call in bot.add_cog.call_args_list]
    assert "SkillCommandCog" not in cog_names


# ---------------------------------------------------------------------------
# apply_to_api_server()
# ---------------------------------------------------------------------------


def _make_api_server() -> MagicMock:
    server = MagicMock()
    server.task_repo = None
    server.lounge_repo = None
    server.port = 8099
    return server


@pytest.mark.asyncio
async def test_setup_bridge_registers_notification_dispatcher(tmp_path: object) -> None:
    """An API server means scheduled notifications must have a delivery loop.

    Regression: /api/schedule accepted and stored notifications that nothing
    ever read back, because the only send loop lived in a consumer's custom
    Cog and pointed at a different database file.
    """
    from claude_discord.database.notification_repo import NotificationRepository

    bot = _make_bot()
    api_server = _make_api_server()
    api_server.repo = MagicMock(spec=NotificationRepository)

    await setup_bridge(
        bot,
        _make_runner(),
        api_server=api_server,
        session_db_path=str(tmp_path / "sessions.db"),  # type: ignore[operator]
        enable_scheduler=False,
    )

    dispatchers = [
        call.args[0]
        for call in bot.add_cog.call_args_list
        if call.args[0].__class__.__name__ == "NotificationDispatchCog"
    ]
    assert len(dispatchers) == 1, "an API server must come with exactly one dispatcher"
    # Sharing the object — not a matching path — is what prevents the drift.
    assert dispatchers[0].repo is api_server.repo


@pytest.mark.asyncio
async def test_setup_bridge_skips_dispatcher_without_api_server(tmp_path: object) -> None:
    """No API server means nothing writes notifications, so nothing polls."""
    bot = _make_bot()

    await setup_bridge(
        bot,
        _make_runner(),
        session_db_path=str(tmp_path / "sessions.db"),  # type: ignore[operator]
        enable_scheduler=False,
    )

    cog_names = [call.args[0].__class__.__name__ for call in bot.add_cog.call_args_list]
    assert "NotificationDispatchCog" not in cog_names


def test_apply_to_api_server_wires_task_and_lounge_repos(tmp_path: object) -> None:
    """apply_to_api_server should set task_repo and lounge_repo on the ApiServer."""
    from claude_discord.database.lounge_repo import LoungeRepository
    from claude_discord.database.repository import SessionRepository
    from claude_discord.database.task_repo import TaskRepository

    session_repo = MagicMock(spec=SessionRepository)
    task_repo = MagicMock(spec=TaskRepository)
    lounge_repo = MagicMock(spec=LoungeRepository)

    components = BridgeComponents(
        session_repo=session_repo,
        task_repo=task_repo,
        lounge_repo=lounge_repo,
    )
    api_server = _make_api_server()

    components.apply_to_api_server(api_server)

    assert api_server.task_repo is task_repo
    assert api_server.lounge_repo is lounge_repo


def test_apply_to_api_server_wires_ingest_repo() -> None:
    """apply_to_api_server should wire the ingest result repository."""
    from claude_discord.database.ingest_repo import IngestResultRepository
    from claude_discord.database.repository import SessionRepository

    session_repo = MagicMock(spec=SessionRepository)
    ingest_repo = MagicMock(spec=IngestResultRepository)

    components = BridgeComponents(
        session_repo=session_repo,
        ingest_repo=ingest_repo,
    )
    api_server = _make_api_server()
    api_server.ingest_repo = None

    components.apply_to_api_server(api_server)

    assert api_server.ingest_repo is ingest_repo


def test_apply_to_api_server_skips_none_repos() -> None:
    """apply_to_api_server should not overwrite existing repos with None."""
    from claude_discord.database.repository import SessionRepository

    session_repo = MagicMock(spec=SessionRepository)
    components = BridgeComponents(
        session_repo=session_repo,
        task_repo=None,
        lounge_repo=None,
    )
    api_server = _make_api_server()
    existing_task_repo = MagicMock()
    api_server.task_repo = existing_task_repo

    components.apply_to_api_server(api_server)

    # None repos must not overwrite existing values
    assert api_server.task_repo is existing_task_repo


def test_apply_to_api_server_is_idempotent() -> None:
    """apply_to_api_server called twice should leave the same repo references."""
    from claude_discord.database.lounge_repo import LoungeRepository
    from claude_discord.database.repository import SessionRepository
    from claude_discord.database.task_repo import TaskRepository

    session_repo = MagicMock(spec=SessionRepository)
    task_repo = MagicMock(spec=TaskRepository)
    lounge_repo = MagicMock(spec=LoungeRepository)

    components = BridgeComponents(
        session_repo=session_repo,
        task_repo=task_repo,
        lounge_repo=lounge_repo,
    )
    api_server = _make_api_server()

    components.apply_to_api_server(api_server)
    components.apply_to_api_server(api_server)

    assert api_server.task_repo is task_repo
    assert api_server.lounge_repo is lounge_repo


@pytest.mark.asyncio
async def test_setup_bridge_auto_wires_api_server(tmp_path: object) -> None:
    """setup_bridge(api_server=...) should auto-wire repos and set runner.api_port."""
    bot = _make_bot()
    runner = _make_runner()
    runner.api_port = None  # Not set yet
    api_server = _make_api_server()

    result = await setup_bridge(
        bot,
        runner,
        api_server=api_server,
        session_db_path=str(tmp_path / "sessions.db"),  # type: ignore[operator]
        enable_scheduler=True,
        task_db_path=str(tmp_path / "tasks.db"),  # type: ignore[operator]
    )

    # Repos should be wired automatically
    assert api_server.task_repo is result.task_repo
    assert api_server.lounge_repo is result.lounge_repo
    # runner.api_port should be set from api_server.port
    assert runner.api_port == api_server.port


@pytest.mark.asyncio
async def test_setup_bridge_registers_skill_cog_with_only_claude_channel_ids(
    tmp_path: object,
) -> None:
    """SkillCommandCog should be registered when only claude_channel_ids is supplied."""
    bot = _make_bot()
    runner = _make_runner()

    await setup_bridge(
        bot,
        runner,
        session_db_path=str(tmp_path / "sessions.db"),  # type: ignore[operator]
        claude_channel_id=None,
        claude_channel_ids={111, 222},
        enable_scheduler=False,
    )

    cog_names = [call.args[0].__class__.__name__ for call in bot.add_cog.call_args_list]
    assert "SkillCommandCog" in cog_names


@pytest.mark.asyncio
async def test_setup_bridge_merges_channel_ids(tmp_path: object) -> None:
    """Both claude_channel_id and claude_channel_ids should be merged into the full set."""
    from claude_discord.cogs.claude_chat import ClaudeChatCog

    bot = _make_bot()
    runner = _make_runner()

    await setup_bridge(
        bot,
        runner,
        session_db_path=str(tmp_path / "sessions.db"),  # type: ignore[operator]
        claude_channel_id=111,
        claude_channel_ids={222, 333},
        enable_scheduler=False,
    )

    chat_cog = next(
        call.args[0]
        for call in bot.add_cog.call_args_list
        if isinstance(call.args[0], ClaudeChatCog)
    )
    assert chat_cog._channel_ids == {111, 222, 333}


@pytest.mark.asyncio
async def test_setup_bridge_preserves_existing_runner_api_port(tmp_path: object) -> None:
    """setup_bridge should not overwrite runner.api_port if already set."""
    bot = _make_bot()
    runner = _make_runner()
    runner.api_port = 9999  # Already set
    api_server = _make_api_server()
    api_server.port = 8099

    await setup_bridge(
        bot,
        runner,
        api_server=api_server,
        session_db_path=str(tmp_path / "sessions.db"),  # type: ignore[operator]
        enable_scheduler=False,
    )

    # Should NOT overwrite the existing value
    assert runner.api_port == 9999


@pytest.mark.asyncio
async def test_setup_bridge_sets_factory_api_port(tmp_path: object) -> None:
    """setup_bridge(api_server=...) should also set backend_factory.api_port."""
    from claude_discord.backend_factory import BackendFactory

    bot = _make_bot()
    runner = _make_runner()
    runner.api_port = None
    api_server = _make_api_server()
    factory = BackendFactory(
        claude_command="claude",
        codex_command="codex",
        permission_mode="acceptEdits",
        working_dir=None,
        timeout_seconds=300,
        dangerously_skip_permissions=False,
        allowed_tools=None,
        append_system_prompt=None,
        effort=None,
    )

    await setup_bridge(
        bot,
        runner,
        api_server=api_server,
        backend_factory=factory,
        session_db_path=str(tmp_path / "sessions.db"),  # type: ignore[operator]
        enable_scheduler=False,
    )

    assert factory.api_port == api_server.port


@pytest.mark.asyncio
async def test_setup_bridge_preserves_existing_factory_api_port(tmp_path: object) -> None:
    """setup_bridge should not overwrite factory.api_port if already set."""
    from claude_discord.backend_factory import BackendFactory

    bot = _make_bot()
    runner = _make_runner()
    runner.api_port = None
    api_server = _make_api_server()
    api_server.port = 8099
    factory = BackendFactory(
        claude_command="claude",
        codex_command="codex",
        permission_mode="acceptEdits",
        working_dir=None,
        timeout_seconds=300,
        dangerously_skip_permissions=False,
        allowed_tools=None,
        append_system_prompt=None,
        effort=None,
        api_port=7777,
    )

    await setup_bridge(
        bot,
        runner,
        api_server=api_server,
        backend_factory=factory,
        session_db_path=str(tmp_path / "sessions.db"),  # type: ignore[operator]
        enable_scheduler=False,
    )

    assert factory.api_port == 7777


@pytest.mark.asyncio
async def test_setup_bridge_passes_max_concurrent_to_chat_cog(tmp_path: object) -> None:
    """max_concurrent parameter should be forwarded to ClaudeChatCog."""
    from claude_discord.cogs.claude_chat import ClaudeChatCog

    bot = _make_bot()
    runner = _make_runner()

    await setup_bridge(
        bot,
        runner,
        session_db_path=str(tmp_path / "sessions.db"),  # type: ignore[operator]
        claude_channel_id=111,
        max_concurrent=7,
        enable_scheduler=False,
    )

    chat_cog = next(
        call.args[0]
        for call in bot.add_cog.call_args_list
        if isinstance(call.args[0], ClaudeChatCog)
    )
    assert chat_cog._max_concurrent == 7


@pytest.mark.asyncio
async def test_setup_bridge_reads_max_concurrent_from_env(tmp_path: object) -> None:
    """MAX_CONCURRENT_SESSIONS env var should be used when parameter is None."""
    from unittest.mock import patch

    from claude_discord.cogs.claude_chat import ClaudeChatCog

    bot = _make_bot()
    runner = _make_runner()

    with patch.dict("os.environ", {"MAX_CONCURRENT_SESSIONS": "10"}):
        await setup_bridge(
            bot,
            runner,
            session_db_path=str(tmp_path / "sessions.db"),  # type: ignore[operator]
            claude_channel_id=111,
            enable_scheduler=False,
        )

    chat_cog = next(
        call.args[0]
        for call in bot.add_cog.call_args_list
        if isinstance(call.args[0], ClaudeChatCog)
    )
    assert chat_cog._max_concurrent == 10


@pytest.mark.asyncio
async def test_setup_bridge_attaches_worktree_manager_when_pre_initialized_to_none(
    tmp_path: object,
) -> None:
    """Regression: WorktreeManager must attach when bot pre-initializes the attr to None.

    ClaudeDiscordBot.__init__ sets ``self.worktree_manager = None`` so the attribute
    always exists. A ``not hasattr(bot, "worktree_manager")`` gate would silently skip
    the assignment, leaving the bot without a manager while the log line still claimed
    it was enabled.
    """
    from claude_discord.worktree import WorktreeManager

    bot = _make_bot()
    bot.worktree_manager = None  # mirror ClaudeDiscordBot.__init__ behaviour
    runner = _make_runner()

    await setup_bridge(
        bot,
        runner,
        session_db_path=str(tmp_path / "sessions.db"),  # type: ignore[operator]
        worktree_base_dir=str(tmp_path),  # type: ignore[arg-type]
        enable_scheduler=False,
    )

    assert isinstance(bot.worktree_manager, WorktreeManager)


@pytest.mark.asyncio
async def test_setup_bridge_warns_when_worktree_base_dir_is_unset(
    tmp_path: object,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unset WORKTREE_BASE_DIR must warn, not pass silently.

    Sessions are instructed to create ``wt-{thread_id}`` regardless of this setting,
    so a disabled manager means worktrees accumulate forever. Only the *enabled*
    branch used to log, which made the leaking configuration the quiet one.
    """
    import logging

    monkeypatch.delenv("WORKTREE_BASE_DIR", raising=False)
    bot = _make_bot()
    bot.worktree_manager = None
    runner = _make_runner()

    with caplog.at_level(logging.WARNING, logger="claude_discord.setup"):
        await setup_bridge(
            bot,
            runner,
            session_db_path=str(tmp_path / "sessions.db"),  # type: ignore[operator]
            enable_scheduler=False,
        )

    assert any(
        "WORKTREE_BASE_DIR" in record.message
        for record in caplog.records
        if record.levelno >= logging.WARNING
    )
    assert bot.worktree_manager is None


@pytest.mark.asyncio
async def test_setup_bridge_defaults_max_concurrent_to_3(tmp_path: object) -> None:
    """Without env var or parameter, max_concurrent defaults to 3."""
    from unittest.mock import patch

    from claude_discord.cogs.claude_chat import ClaudeChatCog

    bot = _make_bot()
    runner = _make_runner()

    with patch.dict("os.environ", {}, clear=False):
        # Ensure env var is not set
        import os

        os.environ.pop("MAX_CONCURRENT_SESSIONS", None)
        await setup_bridge(
            bot,
            runner,
            session_db_path=str(tmp_path / "sessions.db"),  # type: ignore[operator]
            claude_channel_id=111,
            enable_scheduler=False,
        )

    chat_cog = next(
        call.args[0]
        for call in bot.add_cog.call_args_list
        if isinstance(call.args[0], ClaudeChatCog)
    )
    assert chat_cog._max_concurrent == 3


@pytest.mark.asyncio
async def test_setup_bridge_accepts_codex_runner(tmp_path: object) -> None:
    """setup_bridge should accept any SessionBackend, not just ClaudeRunner."""
    from claude_code_core.codex_runner import CodexRunner

    bot = _make_bot()
    runner = CodexRunner(command="codex", model="o4-mini")

    result = await setup_bridge(
        bot,
        runner,  # type: ignore[arg-type]  # CodexRunner satisfies SessionBackend
        session_db_path=str(tmp_path / "sessions.db"),  # type: ignore[operator]
        claude_channel_id=12345,
        enable_scheduler=False,
    )

    cog_names = [call.args[0].__class__.__name__ for call in bot.add_cog.call_args_list]
    assert "ClaudeChatCog" in cog_names
    assert isinstance(result, BridgeComponents)
