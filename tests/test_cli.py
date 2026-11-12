"""Tests for the ccdb CLI wizard.

TDD: These tests were written BEFORE the implementation.
They define the expected behavior of the setup wizard.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_discord_response(status: int, data: object) -> MagicMock:
    """Create a mock aiohttp response."""
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value=data)
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _make_session(get_response: MagicMock) -> MagicMock:
    """Create a mock aiohttp.ClientSession."""
    session = MagicMock()
    session.get = MagicMock(return_value=get_response)
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    return session


# ---------------------------------------------------------------------------
# validate_token
# ---------------------------------------------------------------------------


class TestValidateToken:
    """Tests for validate_token()."""

    @pytest.mark.asyncio
    async def test_returns_bot_name_on_success(self) -> None:
        from claude_discord.cli import validate_token

        resp = _make_discord_response(200, {"username": "MyBot", "discriminator": "1234"})
        session = _make_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            result = await validate_token("Bot valid.token.here")
        assert result == "MyBot#1234"

    @pytest.mark.asyncio
    async def test_returns_none_on_401(self) -> None:
        from claude_discord.cli import validate_token

        resp = _make_discord_response(401, {"message": "401: Unauthorized"})
        session = _make_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            result = await validate_token("Bot bad.token")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_network_error(self) -> None:
        import aiohttp

        from claude_discord.cli import validate_token

        session = MagicMock()
        session.get = MagicMock(side_effect=aiohttp.ClientError())
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        with patch("aiohttp.ClientSession", return_value=session):
            result = await validate_token("Bot valid.token.here")
        assert result is None


# ---------------------------------------------------------------------------
# fetch_channels
# ---------------------------------------------------------------------------


class TestFetchChannels:
    """Tests for fetch_channels() — returns list of (channel_id, name, guild_name)."""

    @pytest.mark.asyncio
    async def test_returns_text_channels(self) -> None:
        from claude_discord.cli import fetch_channels

        channels_resp = _make_discord_response(
            200,
            [
                {"id": "999", "name": "general", "type": 0},  # text channel
                {"id": "888", "name": "voice-chat", "type": 2},  # voice — skip
                {"id": "777", "name": "dev", "type": 0},  # text channel
            ],
        )

        session = MagicMock()
        session.__aenter__ = AsyncMock(return_value=session)
        session.__aexit__ = AsyncMock(return_value=False)
        session.get = MagicMock(
            side_effect=[
                _make_discord_response(200, [{"id": "111", "name": "My Server"}]),
                channels_resp,
            ]
        )

        with patch("aiohttp.ClientSession", return_value=session):
            channels = await fetch_channels("Bot valid.token")

        assert len(channels) == 2
        assert channels[0] == ("999", "general", "My Server")
        assert channels[1] == ("777", "dev", "My Server")

    @pytest.mark.asyncio
    async def test_returns_empty_on_error(self) -> None:
        from claude_discord.cli import fetch_channels

        resp = _make_discord_response(401, {"message": "Unauthorized"})
        session = _make_session(resp)
        with patch("aiohttp.ClientSession", return_value=session):
            channels = await fetch_channels("Bot bad.token")
        assert channels == []


# ---------------------------------------------------------------------------
# write_env
# ---------------------------------------------------------------------------


class TestWriteEnv:
    """Tests for write_env()."""

    def test_writes_required_vars(self, tmp_path: Path) -> None:
        from claude_discord.cli import write_env

        env_file = tmp_path / ".env"
        write_env(
            path=env_file,
            token="mytoken",
            channel_id="123",
            owner_id="456",
            working_dir="/home/user/project",
            model="sonnet",
        )
        content = env_file.read_text()
        assert "DISCORD_BOT_TOKEN=mytoken" in content
        assert "DISCORD_CHANNEL_ID=123" in content
        assert "DISCORD_OWNER_ID=456" in content
        assert "CLAUDE_WORKING_DIR=/home/user/project" in content
        assert "CLAUDE_MODEL=sonnet" in content

    def test_skips_empty_owner_id(self, tmp_path: Path) -> None:
        from claude_discord.cli import write_env

        env_file = tmp_path / ".env"
        write_env(
            path=env_file,
            token="mytoken",
            channel_id="123",
            owner_id="",
            working_dir="",
            model="sonnet",
        )
        content = env_file.read_text()
        # owner_id line should still exist but be empty
        assert "DISCORD_OWNER_ID=" in content

    def test_does_not_overwrite_without_flag(self, tmp_path: Path) -> None:
        from claude_discord.cli import write_env

        env_file = tmp_path / ".env"
        env_file.write_text("DISCORD_BOT_TOKEN=original\n")
        with pytest.raises(FileExistsError):
            write_env(
                path=env_file,
                token="new",
                channel_id="123",
                owner_id="",
                working_dir="",
                model="sonnet",
            )
        assert "original" in env_file.read_text()

    def test_overwrites_with_flag(self, tmp_path: Path) -> None:
        from claude_discord.cli import write_env

        env_file = tmp_path / ".env"
        env_file.write_text("DISCORD_BOT_TOKEN=original\n")
        write_env(
            path=env_file,
            token="new",
            channel_id="123",
            owner_id="",
            working_dir="",
            model="sonnet",
            overwrite=True,
        )
        assert "new" in env_file.read_text()
        assert "original" not in env_file.read_text()


# ---------------------------------------------------------------------------
# check_claude_cli
# ---------------------------------------------------------------------------


class TestCheckClaudeCli:
    """Tests for check_claude_cli()."""

    def test_returns_true_when_installed(self) -> None:
        from claude_discord.cli import check_claude_cli

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="claude 1.0.0")
            assert check_claude_cli() is True

    def test_returns_false_when_not_found(self) -> None:

        from claude_discord.cli import check_claude_cli

        with patch("subprocess.run", side_effect=FileNotFoundError()):
            assert check_claude_cli() is False


# ---------------------------------------------------------------------------
# main() — subcommand routing
# ---------------------------------------------------------------------------


class TestMain:
    """Tests for the top-level main() entry point."""

    def test_no_args_shows_help(self, capsys: pytest.CaptureFixture[str]) -> None:
        from claude_discord.cli import main

        with patch("sys.argv", ["ccdb"]), pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert "setup" in captured.out or "setup" in captured.err

    def test_unknown_command_exits_nonzero(self) -> None:
        from claude_discord.cli import main

        with patch("sys.argv", ["ccdb", "nonexistent"]), pytest.raises(SystemExit) as exc_info:
            main()
        assert exc_info.value.code != 0

    def test_start_without_env_exits_with_message(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        from claude_discord.cli import cmd_start

        env_path = tmp_path / ".env"
        with pytest.raises(SystemExit) as exc_info:
            cmd_start(env_path=env_path)
        assert exc_info.value.code != 0
        captured = capsys.readouterr()
        assert "setup" in captured.out or "setup" in captured.err

    def test_start_cogs_dir_sets_env(self) -> None:
        """--cogs-dir flag sets CUSTOM_COGS_DIR environment variable."""
        from claude_discord.cli import main

        with (
            patch("sys.argv", ["ccdb", "start", "--cogs-dir", "/tmp/my-cogs"]),
            patch("claude_discord.cli.cmd_start") as mock_start,
            patch.dict("os.environ", {}, clear=False),
        ):
            import os

            main()
            assert os.environ.get("CUSTOM_COGS_DIR") == "/tmp/my-cogs"
            mock_start.assert_called_once()

    def test_start_without_cogs_dir_no_env(self) -> None:
        """Without --cogs-dir, CUSTOM_COGS_DIR is not set."""
        import os

        from claude_discord.cli import main

        os.environ.pop("CUSTOM_COGS_DIR", None)
        with (
            patch("sys.argv", ["ccdb", "start"]),
            patch("claude_discord.cli.cmd_start") as mock_start,
        ):
            main()
            assert "CUSTOM_COGS_DIR" not in os.environ or os.environ["CUSTOM_COGS_DIR"] == ""
            mock_start.assert_called_once()


# ---------------------------------------------------------------------------
# _run_start — .env loading (Issue #398)
# ---------------------------------------------------------------------------


class TestRunStartEnvLoading:
    """Regression tests for Issue #398: .env not loaded from --env path."""

    @pytest.mark.asyncio
    async def test_run_start_loads_dotenv_from_explicit_path(self, tmp_path: Path) -> None:
        """_run_start must call load_dotenv(env_path) so vars are available to main."""
        env_file = tmp_path / ".env"
        env_file.write_text("DISCORD_BOT_TOKEN=test-token-123\nDISCORD_CHANNEL_ID=999\n")

        with (
            patch("dotenv.load_dotenv") as mock_load_dotenv,
            patch("claude_discord.main.main", new_callable=AsyncMock) as mock_main,
        ):
            from claude_discord.cli import _run_start

            await _run_start(env_file)
            mock_load_dotenv.assert_any_call(env_file)
            mock_main.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_start_exits_when_env_missing(self, tmp_path: Path) -> None:
        """_run_start must exit with error when .env does not exist."""
        from claude_discord.cli import _run_start

        missing = tmp_path / "nonexistent.env"
        with pytest.raises(SystemExit):
            await _run_start(missing)


# ---------------------------------------------------------------------------
# load_config — .env discovery from CWD (Issue #398)
# ---------------------------------------------------------------------------


class TestLoadConfigCwd:
    """Regression tests for Issue #398: load_config must find .env in CWD."""

    def test_load_config_uses_usecwd(self) -> None:
        """load_config must pass usecwd=True so .env is found relative to CWD."""
        with patch("claude_discord.main.load_dotenv") as mock_ld:
            mock_ld.return_value = True
            with (
                patch.dict(
                    "os.environ",
                    {"DISCORD_BOT_TOKEN": "tok", "DISCORD_CHANNEL_ID": "123"},
                ),
            ):
                from claude_discord.main import load_config

                load_config()
            args, kwargs = mock_ld.call_args
            assert kwargs.get("usecwd") is True or (args and args[0] is not None)


class TestConsoleOnlyMode:
    async def test_explicit_console_mode_overrides_file_and_has_local_defaults(self, tmp_path):
        import os

        from claude_discord.cli import _run_start

        env_file = tmp_path / ".env"
        env_file.write_text("CCDB_FRONTENDS=discord\nDISCORD_BOT_TOKEN=unused\n")
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("claude_discord.main.main", new_callable=AsyncMock) as run,
        ):
            await _run_start(env_file, mode="console")
            assert os.environ["CCDB_FRONTENDS"] == "console"
            assert os.environ["API_PORT"] == "8099"
            assert os.environ["CCDB_CONSOLE_PORT"] == "8100"
            run.assert_awaited_once()

    async def test_console_mode_preserves_configured_ports(self, tmp_path):
        import os

        from claude_discord.cli import _run_start

        env_file = tmp_path / ".env"
        env_file.write_text("API_PORT=8181\nCCDB_CONSOLE_PORT=8182\n")
        with (
            patch.dict(os.environ, {}, clear=True),
            patch("claude_discord.main.main", new_callable=AsyncMock),
        ):
            await _run_start(env_file, mode="console")
            assert os.environ["API_PORT"] == "8181"
            assert os.environ["CCDB_CONSOLE_PORT"] == "8182"

    def test_console_mode_is_explicit_in_cli(self):
        from claude_discord.cli import main

        with (
            patch("sys.argv", ["ccdb", "start", "--mode", "console"]),
            patch("claude_discord.cli.cmd_start") as start,
        ):
            main()
            assert start.call_args.kwargs["mode"] == "console"


async def test_console_cli_can_start_without_a_default_env_file(tmp_path, monkeypatch):
    import os

    from claude_discord.cli import _run_start

    monkeypatch.chdir(tmp_path)
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("claude_discord.main.main", new_callable=AsyncMock) as run,
    ):
        await _run_start(Path(".env"), mode="console")
        run.assert_awaited_once()


async def test_console_cli_does_not_ignore_an_explicit_missing_env_file(tmp_path):
    from claude_discord.cli import _run_start

    with pytest.raises(SystemExit):
        await _run_start(tmp_path / "missing.env", mode="console")
