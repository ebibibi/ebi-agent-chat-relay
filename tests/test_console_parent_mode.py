"""Parent mode switches in the Ebi AI Console (#1489)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.backend_settings import BackendSettings
from claude_discord.console.auth import ConsoleAuthConfig, ConsoleAuthenticator
from claude_discord.console.server import ConsoleServer
from claude_discord.console.work_repo import WorkItemRepository
from claude_discord.database.lineage_repo import ThreadLineageRepository
from claude_discord.database.models import init_db
from claude_discord.database.settings_repo import SettingsRepository
from claude_discord.parent_mode import ParentModeSettings

TOKEN = "t" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}
WRITE = {**AUTH, "X-Console-Request": "1"}


@pytest.fixture
async def env(tmp_path, monkeypatch):
    monkeypatch.delenv("CCDB_PARENT_MODE_DEFAULT", raising=False)
    monkeypatch.delenv("CCDB_PARENT_MODE_MAX_CHILDREN", raising=False)
    db = str(tmp_path / "sessions.db")
    work = WorkItemRepository(db)
    await work.init_db()
    await init_db(db)
    repo = SettingsRepository(db)
    api = MagicMock()
    api.default_channel_id = None
    api.lineage_repo = ThreadLineageRepository(db)
    api.session_repo = None
    api.bot.get_channel.return_value = None
    api.bot.cogs = {}
    api._running_thread_ids.return_value = set()
    api.components.backend_settings = BackendSettings(
        repo, env_backend="claude", env_model_for_claude="", env_model_for_codex=""
    )
    monkeypatch.setattr(ConsoleServer, "_command_key", AsyncMock(return_value=(123, None)))
    console = ConsoleServer(api, work, ConsoleAuthenticator(ConsoleAuthConfig(token=TOKEN)), port=0)
    async with TestClient(TestServer(console.app)) as client:
        yield client, ParentModeSettings(repo), api.lineage_repo


DEFAULTS = "/console/api/defaults/parent-mode"
ITEM = "/console/api/items/t123/parent-mode"


async def test_defaults_need_auth_and_csrf(env):
    client, _, _ = env
    assert (await client.get(DEFAULTS)).status == 401
    assert (await client.post(DEFAULTS, json={"enabled": True}, headers=AUTH)).status == 403


async def test_defaults_read_and_change(env):
    client, settings, _ = env
    data = await (await client.get(DEFAULTS, headers=AUTH)).json()
    assert data == {"enabled": False, "max_children": 5, "max_children_limit": 50}
    res = await client.post(DEFAULTS, json={"enabled": True, "max_children": 3}, headers=WRITE)
    assert res.status == 200
    assert (await res.json())["enabled"] is True
    assert await settings.default_enabled() is True
    assert await settings.max_children() == 3


@pytest.mark.parametrize(
    "body", [{"enabled": "yes"}, {"max_children": 0}, {"max_children": "3"}, {"max_children": True}]
)
async def test_bad_defaults_are_400(env, body):
    client, _, _ = env
    assert (await client.post(DEFAULTS, json=body, headers=WRITE)).status == 400


async def test_item_follows_default_until_overridden(env):
    client, settings, _ = env
    await settings.set_default(True)
    data = await (await client.get(ITEM, headers=AUTH)).json()
    assert data["mode"] == "default" and data["enabled"] is True and data["source"] == "default"

    res = await client.post(ITEM, json={"mode": "off"}, headers=WRITE)
    data = await res.json()
    assert data["mode"] == "off" and data["enabled"] is False and data["source"] == "thread"
    assert await settings.thread_override(123) is False

    res = await client.post(ITEM, json={"mode": "default"}, headers=WRITE)
    assert (await res.json())["enabled"] is True
    assert await settings.thread_override(123) is None


async def test_item_reports_a_child(env):
    client, settings, lineage = env
    await settings.set_default(True)
    await lineage.record(123, 99, "K1")
    data = await (await client.get(ITEM, headers=AUTH)).json()
    assert data["is_child"] is True and data["enabled"] is False and data["source"] == "child"


@pytest.mark.parametrize("body", [{}, {"mode": "maybe"}, {"mode": 1}])
async def test_bad_item_changes_are_400(env, body):
    client, _, _ = env
    assert (await client.post(ITEM, json=body, headers=WRITE)).status == 400
