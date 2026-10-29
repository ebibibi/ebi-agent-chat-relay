"""Connections: subscription vs provider routes, discovered and applied per turn."""

from __future__ import annotations

import pytest

from claude_code_core.codex_runner import CodexRunner
from claude_code_core.connections import (
    SUBSCRIPTION,
    apply_claude_env,
    codex_args,
    connections_for,
    resolve,
)
from claude_code_core.runner import ClaudeRunner
from claude_discord.backend_settings import BackendSettings
from claude_discord.connection_choice import apply_connection
from claude_discord.console import commands
from claude_discord.database.models import init_db
from claude_discord.database.settings_repo import SettingsRepository

FOUNDRY_CONFIG = """
model = "gpt-5.4-mini"
model_provider = "foundry"

[model_providers.foundry]
name = "Personal Foundry"
base_url = "https://aip-models.openai.azure.com/openai/v1"
wire_api = "responses"

[model_providers."bad id!"]
name = "ignored"
"""


@pytest.fixture
def codex_home(tmp_path, monkeypatch):
    home = tmp_path / "codex"
    home.mkdir()
    (home / "config.toml").write_text(FOUNDRY_CONFIG)
    monkeypatch.setenv("CODEX_HOME", str(home))
    for key in ("CLAUDE_CODE_USE_FOUNDRY", "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.delenv("CCDB_CLI_ENV_FILE", raising=False)
    return home


def test_claude_without_a_provider_has_only_the_subscription() -> None:
    [only] = connections_for("claude", {})
    assert (only.id, only.kind, only.default) == (SUBSCRIPTION, "subscription", True)


def test_claude_foundry_env_is_the_default_and_subscription_stays_offered() -> None:
    env = {"CLAUDE_CODE_USE_FOUNDRY": "1", "ANTHROPIC_FOUNDRY_RESOURCE": "res"}
    subs, provider = connections_for("claude", env)
    assert not subs.default and provider.default
    assert provider.kind == "foundry" and "res" in provider.label
    assert resolve("claude", None, env) == provider
    assert resolve("claude", SUBSCRIPTION, env) == subs
    assert resolve("claude", "gone", env) == provider  # a vanished choice falls back


def test_claude_api_key_alone_is_a_route() -> None:
    _, provider = connections_for("claude", {"ANTHROPIC_API_KEY": "k"})
    assert provider.kind == "api"


def test_codex_providers_come_from_config_toml(codex_home) -> None:
    subs, foundry = connections_for("codex", {"CODEX_HOME": str(codex_home)})
    assert subs.id == SUBSCRIPTION and not subs.default
    assert foundry.id == "foundry" and foundry.default and foundry.kind == "foundry"
    assert foundry.model == "gpt-5.4-mini" and foundry.detail == "aip-models.openai.azure.com"


def test_codex_without_config_is_subscription_only(tmp_path) -> None:
    [only] = connections_for("codex", {"CODEX_HOME": str(tmp_path / "none")})
    assert only.id == SUBSCRIPTION and only.default


def test_apply_claude_env_drops_provider_keys_only_for_the_subscription() -> None:
    env = {"CLAUDE_CODE_USE_FOUNDRY": "1", "CLAUDE_CODE_OAUTH_TOKEN": "t", "PATH": "/bin"}
    subs, provider = connections_for("claude", env)
    assert apply_claude_env(subs, env) == {"CLAUDE_CODE_OAUTH_TOKEN": "t", "PATH": "/bin"}
    assert apply_claude_env(provider, env) is env
    assert apply_claude_env(None, env) is env
    assert "CLAUDE_CODE_USE_FOUNDRY" in env  # the input is not changed


def test_codex_args_only_when_leaving_the_configured_default(codex_home) -> None:
    subs, foundry = connections_for("codex", {"CODEX_HOME": str(codex_home)})
    assert codex_args(foundry) == []
    assert codex_args(subs) == ["-c", 'model_provider="openai"']
    assert codex_args(None) == []


def test_runners_apply_and_clone_the_connection(codex_home, tmp_path, monkeypatch) -> None:
    overlay = tmp_path / "overlay.env"
    overlay.write_text("CLAUDE_CODE_USE_FOUNDRY=1\n")
    monkeypatch.setenv("CCDB_CLI_ENV_FILE", str(overlay))
    claude = ClaudeRunner(command="claude")
    assert claude._build_env().get("CLAUDE_CODE_USE_FOUNDRY") == "1"
    claude.connection = connections_for("claude", {})[0]
    assert "CLAUDE_CODE_USE_FOUNDRY" not in claude._build_env()
    assert claude.clone().connection == claude.connection

    codex = CodexRunner(command="codex")
    codex.connection = connections_for("codex", {"CODEX_HOME": str(codex_home)})[0]
    args = codex._build_args("hi", None)
    assert args[args.index('model_provider="openai"') - 1] == "-c"
    assert codex.clone().connection == codex.connection


@pytest.fixture
async def settings(tmp_path):
    db = str(tmp_path / "s.db")
    await init_db(db)
    return BackendSettings(
        SettingsRepository(db), env_backend="codex", env_model_for_claude="", env_model_for_codex=""
    )


async def test_settings_thread_over_global_and_cleared(settings) -> None:
    assert await settings.chosen_connection("codex") is None
    await settings.set_connection("codex", "foundry")
    await settings.set_connection("codex", SUBSCRIPTION, thread_id=7)
    assert await settings.chosen_connection("codex", 7) == SUBSCRIPTION
    assert await settings.chosen_connection("codex", 8) == "foundry"
    assert await settings.clear_thread_overrides(7) == 1
    assert await settings.chosen_connection("codex", 7) == "foundry"


async def test_apply_connection_sets_the_runner(settings, codex_home) -> None:
    runner = CodexRunner(command="codex")
    assert (await apply_connection(runner, settings, "codex", 1)).id == "foundry"
    await settings.set_connection("codex", SUBSCRIPTION, thread_id=1)
    assert (await apply_connection(runner, settings, "codex", 1)).id == SUBSCRIPTION
    assert runner.connection.id == SUBSCRIPTION
    assert await apply_connection(object(), settings, "codex", 1) is None


async def test_console_catalog_and_command(settings, codex_home) -> None:
    async def models(backend):
        return [{"value": "gpt-5.5", "label": ""}]

    cat = await commands.catalog(settings, 3, lambda b: [], models)
    assert cat["connection"]["id"] == "foundry"
    assert [c["id"] for c in cat["connections"]] == [SUBSCRIPTION, "foundry"]
    assert [m["value"] for m in cat["models"]] == ["gpt-5.4-mini"]  # the provider's own model
    assert "connection" in {c["name"] for c in cat["commands"]}

    res = await commands.run_command(settings, 3, "connection", SUBSCRIPTION)
    assert "ChatGPT subscription" in res.message
    cat = await commands.catalog(settings, 3, lambda b: [], models)
    assert cat["connection"]["id"] == SUBSCRIPTION
    assert [m["value"] for m in cat["models"]] == ["gpt-5.5"]
    with pytest.raises(commands.CommandError):
        await commands.run_command(settings, 3, "connection", "nope")
