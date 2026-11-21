"""Parent mode at the spawn boundary: notify default and the children cap (#1489)."""

from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.backend_settings import BackendSettings
from claude_discord.database.lineage_repo import ThreadLineageRepository
from claude_discord.database.models import init_db
from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.database.settings_repo import SettingsRepository
from claude_discord.ext.api_server import ApiServer
from claude_discord.ext.parent_mode_api import check_spawn
from claude_discord.parent_mode import ParentModeSettings
from claude_discord.thread_marker import family_code

PARENT = 1550121254361768021
NEW_CHILD = 1550121254361768999


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "CCDB_PARENT_MODE_DEFAULT",
        "CCDB_PARENT_MODE_MAX_CHILDREN",
        "CCDB_NOTIFY_PARENT_DEFAULT",
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
async def db_path():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(path)
    yield path
    os.unlink(path)


@pytest.fixture
def settings(db_path: str) -> ParentModeSettings:
    return ParentModeSettings(SettingsRepository(db_path))


class _Host:
    """A family in memory: titles decide which children are finished."""

    def __init__(self) -> None:
        self.parents: dict[int, int] = {}
        self.titles: dict[int, str] = {}
        self.done: set[int] = set()

    def add(self, child: int, parent: int, title: str) -> None:
        self.parents[child] = parent
        self.titles[child] = title

    async def parent_of(self, thread_id: int) -> int | None:
        return self.parents.get(thread_id)

    async def children_of(self, thread_id: int) -> list[int]:
        return [c for c, p in self.parents.items() if p == thread_id]

    async def thread_title(self, thread_id: int) -> str | None:
        return self.titles.get(thread_id)

    async def human_done(self, thread_id: int) -> bool:
        return thread_id in self.done


async def test_gate_is_open_when_parent_mode_is_off(settings) -> None:
    host = _Host()
    for i in range(10):
        host.add(100 + i, PARENT, "work")
    gate = await check_spawn(settings, host, PARENT)
    assert gate.parent_mode is False
    assert gate.refusal is None


async def test_gate_counts_only_unfinished_children(settings) -> None:
    await settings.set_thread(PARENT, True)
    await settings.set_max_children(2)
    host = _Host()
    host.add(101, PARENT, "🤖K1 running")
    host.add(102, PARENT, "🏁 🤖K1 candidate")
    host.add(103, PARENT, "🤖K1 closed by a human")
    host.done.add(103)
    gate = await check_spawn(settings, host, PARENT)
    assert gate.parent_mode is True
    assert gate.unfinished == 1
    assert gate.refusal is None


async def test_gate_refuses_at_the_cap(settings) -> None:
    await settings.set_thread(PARENT, True)
    await settings.set_max_children(2)
    host = _Host()
    host.add(101, PARENT, "a")
    host.add(102, PARENT, "b")
    gate = await check_spawn(settings, host, PARENT)
    assert gate.refusal is not None
    assert "at most 2" in gate.refusal
    assert gate.unfinished == 2


async def test_default_does_not_make_a_child_a_manager(settings) -> None:
    await settings.set_default(True)
    host = _Host()
    host.add(PARENT, 1, "child of someone")
    gate = await check_spawn(settings, host, PARENT)
    assert gate.parent_mode is False


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


@pytest.fixture
def bot() -> MagicMock:
    b = MagicMock()
    b.headless = False
    channel = MagicMock(spec=discord.TextChannel)
    b.get_channel.side_effect = lambda cid: channel if cid == 1 else None
    b.fetch_channel = AsyncMock(side_effect=RuntimeError("Unknown Channel"))
    child = MagicMock()
    child.id = NEW_CHILD
    child.name = "child"
    cog = MagicMock()
    cog.spawn_session = AsyncMock(return_value=child)
    b.cogs = {"ClaudeChatCog": cog}
    return b


@pytest.fixture
async def api(bot: MagicMock, db_path: str):
    repo = NotificationRepository(db_path)
    await repo.init_db()
    lineage = ThreadLineageRepository(db_path)
    server = ApiServer(
        repo=repo,
        bot=bot,
        default_channel_id=1,
        host="127.0.0.1",
        port=0,
        lineage_repo=lineage,
    )
    server.components = SimpleNamespace(
        backend_settings=BackendSettings(
            SettingsRepository(db_path),
            env_backend="claude",
            env_model_for_claude="",
            env_model_for_codex="",
        )
    )
    server._dispatch_relay = AsyncMock(  # type: ignore[method-assign]
        return_value=web.json_response({"status": "delivered"}, status=202)
    )
    client = TestClient(TestServer(server.app))
    await client.start_server()
    yield server, client, lineage
    await client.close()


async def test_spawn_from_parent_mode_defaults_notify_parent(api, settings) -> None:
    _, client, lineage = api
    await settings.set_thread(PARENT, True)
    resp = await client.post("/api/spawn", json={"prompt": "x", "parent_thread_id": PARENT})
    assert resp.status == 201
    assert (await resp.json())["notify_parent"] is True
    assert await lineage.notify_target(NEW_CHILD) is not None


async def test_explicit_notify_false_still_wins(api, settings) -> None:
    _, client, _ = api
    await settings.set_thread(PARENT, True)
    resp = await client.post(
        "/api/spawn",
        json={"prompt": "x", "parent_thread_id": PARENT, "notify_parent": False},
    )
    assert resp.status == 201
    assert (await resp.json())["notify_parent"] is False


async def test_spawn_without_parent_mode_is_unchanged(api) -> None:
    _, client, _ = api
    resp = await client.post("/api/spawn", json={"prompt": "x", "parent_thread_id": PARENT})
    assert resp.status == 201
    assert (await resp.json())["notify_parent"] is False


async def test_spawn_over_the_cap_is_refused(api, settings, bot) -> None:
    _, client, lineage = api
    await settings.set_thread(PARENT, True)
    await settings.set_max_children(1)
    await lineage.record(101, PARENT, family_code(PARENT))  # unfinished: no channel title
    resp = await client.post("/api/spawn", json={"prompt": "x", "parent_thread_id": PARENT})
    assert resp.status == 429
    body = await resp.json()
    assert body["max_children"] == 1
    assert body["unfinished_children"] == 1
    bot.cogs["ClaudeChatCog"].spawn_session.assert_not_awaited()
