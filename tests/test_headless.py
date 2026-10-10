"""Running ccdb with no Discord login (``CCDB_FRONTENDS`` without ``discord``)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from claude_code_core.memory_surface import MemoryFrontend
from claude_discord.bot import ClaudeDiscordBot
from claude_discord.main import headless_problems, load_config
from claude_discord.teams_integration import FrontendRouter, parse_frontends

CONSOLE_ENV = {"API_PORT": "8099", "CCDB_CONSOLE_PORT": "8100"}


def test_console_is_a_known_frontend() -> None:
    assert parse_frontends("console") == ("console",)
    assert parse_frontends("console,teams") == ("console", "teams")


class TestHeadlessProblems:
    def test_a_console_deployment_with_its_ports_is_fine(self) -> None:
        assert headless_problems(("console",), CONSOLE_ENV) == []

    def test_the_console_needs_both_ports(self) -> None:
        problems = headless_problems(("console",), {})
        assert any("API_PORT" in p for p in problems)
        assert any("CCDB_CONSOLE_PORT" in p for p in problems)

    def test_teams_alone_is_reachable(self) -> None:
        assert headless_problems(("teams",), {}) == []


class TestLoadConfig:
    def test_discord_credentials_are_not_required_without_discord(self) -> None:
        env = {"CCDB_FRONTENDS": "console", **CONSOLE_ENV}
        with patch("claude_discord.main.load_dotenv"), patch.dict("os.environ", env, clear=True):
            config = load_config()
        assert config["frontends"] == "console"
        assert config["token"] == ""

    def test_an_unreachable_headless_deployment_is_refused(self) -> None:
        env = {"CCDB_FRONTENDS": "console"}
        with (
            patch("claude_discord.main.load_dotenv"),
            patch.dict("os.environ", env, clear=True),
            pytest.raises(SystemExit),
        ):
            load_config()

    def test_discord_still_requires_its_token(self) -> None:
        env = {"CCDB_FRONTENDS": "discord,console", "DISCORD_CHANNEL_ID": "1", **CONSOLE_ENV}
        with (
            patch("claude_discord.main.load_dotenv"),
            patch.dict("os.environ", env, clear=True),
            pytest.raises(SystemExit),
        ):
            load_config()


async def test_a_headless_bot_does_not_wait_for_a_login() -> None:
    bot = ClaudeDiscordBot(channel_id=0, headless=True)
    await asyncio.wait_for(bot.wait_until_ready(), timeout=1)


class TestReplacePrimary:
    async def test_new_conversations_open_on_the_new_primary(self) -> None:
        discord = MemoryFrontend(name="discord")
        console = MemoryFrontend(name="console")
        router = FrontendRouter(discord)

        router.replace_primary(console)
        surface = await router.create_surface(parent_id="tasks", title="nightly")

        assert router.primary == "console"
        assert surface.frontend == "console"
        assert discord.created_titles == []

    async def test_the_old_primary_no_longer_resolves(self) -> None:
        discord = MemoryFrontend(name="discord")
        old = await discord.create_surface(parent_id="c", title="t")
        router = FrontendRouter(discord)
        router.replace_primary(MemoryFrontend(name="console"))
        assert await router.resolve_surface(old.thread_key) is None


async def test_the_console_becomes_primary_on_a_headless_bot(tmp_path) -> None:
    from claude_discord.console.conversations import ConversationRepository
    from claude_discord.console.server import _build_session_host
    from claude_discord.database.frontend_thread_repo import FrontendThreadRepository
    from claude_discord.database.models import init_db

    db = str(tmp_path / "sessions.db")
    await init_db(db)
    router = FrontendRouter(MemoryFrontend(name="discord"))
    api = MagicMock()
    api.bot.headless = True
    api.components.frontend = router
    api.components.frontend_threads = FrontendThreadRepository(db)
    conversations = ConversationRepository(db)
    await conversations.init_db()

    host = await _build_session_host(api, conversations)

    assert host is not None
    assert router.primary == "console"
