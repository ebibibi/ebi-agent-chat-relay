"""Default backend, model and effort from the Ebi AI Console."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.backend_settings import BackendSettings
from claude_discord.console.auth import ConsoleAuthConfig, ConsoleAuthenticator
from claude_discord.console.server import ConsoleServer
from claude_discord.console.work_repo import WorkItemRepository
from claude_discord.database.models import init_db
from claude_discord.database.settings_repo import SettingsRepository

TOKEN = "t" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}
WRITE = {**AUTH, "X-Console-Request": "1"}


@pytest.fixture
async def env(tmp_path, monkeypatch):
    db = str(tmp_path / "sessions.db")
    work = WorkItemRepository(db)
    await work.init_db()
    await init_db(db)
    repo = SettingsRepository(db)
    settings = BackendSettings(
        repo, env_backend="claude", env_model_for_claude="", env_model_for_codex=""
    )
    api = MagicMock()
    api.default_channel_id = None
    api.lineage_repo = None
    api.session_repo = None
    api.bot.get_channel.return_value = None
    api.bot.cogs = {}
    api._running_thread_ids.return_value = set()
    api.components.backend_settings = settings
    from claude_discord.console import commands

    async def no_discovery(backend):
        return [{"value": f"{backend}-model", "label": ""}]

    monkeypatch.setattr(commands, "model_choices", no_discovery)
    console = ConsoleServer(api, work, ConsoleAuthenticator(ConsoleAuthConfig(token=TOKEN)), port=0)
    async with TestClient(TestServer(console.app)) as client:
        yield client, settings


async def test_defaults_are_read_and_changed_globally(env) -> None:
    client, settings = env
    data = await (await client.get("/console/api/defaults", headers=AUTH)).json()
    assert data["backend"] == "claude" and "codex" in data["backends"]
    assert data["skills"] == []

    res = await client.post(
        "/console/api/defaults", json={"name": "backend", "arg": "codex"}, headers=WRITE
    )
    body = await res.json()
    assert res.status == 200 and "as the default" in body["message"]
    assert body["catalog"]["backend"] == "codex"
    assert await settings.current_backend() == "codex"
    assert (
        await settings.current_backend(12345) == "codex"
    )  # no thread override: follows the default

    res = await client.post(
        "/console/api/defaults", json={"name": "effort", "arg": "high"}, headers=WRITE
    )
    assert res.status == 200
    assert await settings.current_effort("codex") == "high"


async def test_bad_default_changes_are_400(env) -> None:
    client, _ = env
    for body in (
        {"name": "backend", "arg": "nope"},
        {"name": "kb-save", "arg": ""},
        {"name": "model", "arg": "two words"},
        {"name": "model", "arg": "x" * 201},
    ):
        res = await client.post("/console/api/defaults", json=body, headers=WRITE)
        assert res.status == 400, body
