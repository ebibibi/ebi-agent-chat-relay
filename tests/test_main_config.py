"""Tests for claude_discord.main.load_config()."""

from __future__ import annotations

from unittest.mock import patch

import pytest


class TestLoadConfig:
    """Tests for load_config() environment parsing.

    load_dotenv() is patched out in all tests to prevent .env file
    from polluting test results.
    """

    def test_missing_token_exits(self) -> None:
        """Missing DISCORD_BOT_TOKEN causes sys.exit(1)."""
        from claude_discord.main import load_config

        with (
            patch("claude_discord.main.load_dotenv"),
            patch.dict(
                "os.environ",
                {"DISCORD_CHANNEL_ID": "123"},
                clear=True,
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            load_config()
        assert exc_info.value.code == 1

    def test_missing_channel_id_exits(self) -> None:
        """Missing DISCORD_CHANNEL_ID causes sys.exit(1)."""
        from claude_discord.main import load_config

        with (
            patch("claude_discord.main.load_dotenv"),
            patch.dict(
                "os.environ",
                {"DISCORD_BOT_TOKEN": "fake-token"},
                clear=True,
            ),
            pytest.raises(SystemExit) as exc_info,
        ):
            load_config()
        assert exc_info.value.code == 1

    def test_valid_config_returns_dict(self) -> None:
        """Valid token + channel returns a dict with all keys."""
        from claude_discord.main import load_config

        with (
            patch("claude_discord.main.load_dotenv"),
            patch.dict(
                "os.environ",
                {
                    "DISCORD_BOT_TOKEN": "fake-token",
                    "DISCORD_CHANNEL_ID": "123456",
                },
                clear=True,
            ),
        ):
            config = load_config()

        assert config["token"] == "fake-token"
        assert config["channel_id"] == "123456"
        # "command" defaults to empty; backend-aware default is picked in main()
        assert config["command"] == ""
        assert config["model"] == "sonnet"
        assert config["permission_mode"] == "acceptEdits"
        assert config["backend"] == "claude"  # CCDB_BACKEND default
        assert config["max_concurrent"] == "3"
        assert config["timeout"] == "300"
        assert config["custom_cogs_dir"] == ""
        assert config["frontends"] == "discord"

    def test_discord_and_teams_frontends_are_loaded(self) -> None:
        from claude_discord.main import load_config

        with (
            patch("claude_discord.main.load_dotenv"),
            patch.dict(
                "os.environ",
                {
                    "DISCORD_BOT_TOKEN": "tok",
                    "DISCORD_CHANNEL_ID": "111",
                    "CCDB_FRONTENDS": "discord,teams",
                },
                clear=True,
            ),
        ):
            config = load_config()

        assert config["frontends"] == "discord,teams"

    def test_invalid_frontend_selection_fails_before_startup(self) -> None:
        from claude_discord.main import load_config

        with (
            patch("claude_discord.main.load_dotenv"),
            patch.dict(
                "os.environ",
                {
                    "DISCORD_BOT_TOKEN": "tok",
                    "DISCORD_CHANNEL_ID": "111",
                    "CCDB_FRONTENDS": "discord,email",
                },
                clear=True,
            ),
            pytest.raises(ValueError, match="CCDB_FRONTENDS"),
        ):
            load_config()

    def test_all_optional_env_vars(self) -> None:
        """Optional env vars are correctly read."""
        from claude_discord.main import load_config

        with (
            patch("claude_discord.main.load_dotenv"),
            patch.dict(
                "os.environ",
                {
                    "DISCORD_BOT_TOKEN": "tok",
                    "DISCORD_CHANNEL_ID": "111",
                    "CLAUDE_COMMAND": "/usr/bin/claude",
                    "CLAUDE_MODEL": "opus",
                    "CLAUDE_PERMISSION_MODE": "bypassPermissions",
                    "CLAUDE_WORKING_DIR": "/home/test",
                    "MAX_CONCURRENT_SESSIONS": "5",
                    "SESSION_TIMEOUT_SECONDS": "600",
                    "DISCORD_OWNER_ID": "999",
                    "CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS": "true",
                    "CLAUDE_CHANNEL_IDS": "111,222,333",
                    "API_HOST": "0.0.0.0",
                    "API_PORT": "9000",
                    "CLAUDE_ALLOWED_TOOLS": "Read,Write",
                    "CUSTOM_COGS_DIR": "/my/cogs",
                    "CLI_SESSIONS_PATH": "~/.claude/projects",
                },
                clear=True,
            ),
        ):
            config = load_config()

        assert config["command"] == "/usr/bin/claude"
        assert config["model"] == "opus"
        assert config["working_dir"] == "/home/test"
        assert config["max_concurrent"] == "5"
        assert config["owner_id"] == "999"
        assert config["channel_ids"] == "111,222,333"
        assert config["api_host"] == "0.0.0.0"
        assert config["api_port"] == "9000"
        assert config["allowed_tools"] == "Read,Write"
        assert config["custom_cogs_dir"] == "/my/cogs"
        assert config["cli_sessions_path"] == "~/.claude/projects"

    def test_cli_sessions_path_defaults_to_empty(self) -> None:
        """CLI_SESSIONS_PATH defaults to empty string when not set."""
        from claude_discord.main import load_config

        with (
            patch("claude_discord.main.load_dotenv"),
            patch.dict(
                "os.environ",
                {
                    "DISCORD_BOT_TOKEN": "tok",
                    "DISCORD_CHANNEL_ID": "111",
                },
                clear=True,
            ),
        ):
            config = load_config()

        assert config["cli_sessions_path"] == ""

    def test_dangerously_skip_permissions_is_string(self) -> None:
        """dangerously_skip_permissions is returned as string, not bool."""
        from claude_discord.main import load_config

        with (
            patch("claude_discord.main.load_dotenv"),
            patch.dict(
                "os.environ",
                {
                    "DISCORD_BOT_TOKEN": "tok",
                    "DISCORD_CHANNEL_ID": "111",
                    "CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS": "true",
                },
                clear=True,
            ),
        ):
            config = load_config()

        # Must be str to satisfy dict[str, str] return type
        assert isinstance(config["dangerously_skip_permissions"], str)


class TestAllowedToolsParsing:
    """Tests for CLAUDE_ALLOWED_TOOLS env var parsing in main()."""

    def _parse_allowed_tools(self, env_value: str) -> list[str] | None:
        """Replicate the parsing logic from main() for unit testing."""
        if not env_value:
            return None
        return [t.strip() for t in env_value.split(",") if t.strip()] or None

    def test_comma_separated(self) -> None:
        result = self._parse_allowed_tools("Bash,Read,Write")
        assert result == ["Bash", "Read", "Write"]

    def test_whitespace_trimmed(self) -> None:
        result = self._parse_allowed_tools("Bash , Read , Write")
        assert result == ["Bash", "Read", "Write"]

    def test_empty_string_returns_none(self) -> None:
        result = self._parse_allowed_tools("")
        assert result is None

    def test_only_commas_returns_none(self) -> None:
        result = self._parse_allowed_tools(",,,")
        assert result is None

    def test_single_tool(self) -> None:
        result = self._parse_allowed_tools("Bash")
        assert result == ["Bash"]

    def test_trailing_comma(self) -> None:
        result = self._parse_allowed_tools("Bash,Read,")
        assert result == ["Bash", "Read"]


class TestExampleCogImports:
    """Verify that example cog files can be imported and have setup()."""

    @pytest.mark.parametrize(
        "cog_name",
        ["auto_upgrade", "docs_sync", "reminder"],
    )
    def test_example_cog_has_setup(self, cog_name: str) -> None:
        """Each example cog file exposes an async setup() function."""
        import importlib.util
        import sys
        from pathlib import Path

        cog_path = Path(__file__).parent.parent / "examples" / "ebibot" / "cogs" / f"{cog_name}.py"
        assert cog_path.exists(), f"{cog_path} does not exist"

        module_name = f"_test_example_{cog_name}"
        spec = importlib.util.spec_from_file_location(module_name, cog_path)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        setup_fn = getattr(module, "setup", None)
        assert setup_fn is not None, f"{cog_name}.py missing setup() function"
        assert callable(setup_fn)

        del sys.modules[module_name]
