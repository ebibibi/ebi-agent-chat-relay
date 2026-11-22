"""POST /api/spawn can pin the new thread's AI (Issue #1406).

Automation jobs choose their AI independently of the Console default. The choice is stored as
the new thread's settings, so every later turn in that thread (a reply, a wait's resume, a relay)
keeps it, and it wins over the parent's child default.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_discord.backend_settings import BackendSettings
from claude_discord.cogs.claude_chat import ClaudeChatCog
from claude_discord.database.models import init_db
from claude_discord.database.settings_repo import SettingsRepository
from claude_discord.turn_ai import TurnAI
from tests.test_console_spawn import native  # noqa: F401  (fixture)

PARENT_ID = 1_500_000_000_000_000_001


async def test_headless_spawn_pins_ai_for_the_first_and_later_turns(native):  # noqa: F811
    response = await native.client.post(
        "/api/spawn",
        json={
            "prompt": "Nightly job",
            "ai": {"backend": "codex", "model": "gpt-5.5", "effort": "low"},
        },
    )
    assert response.status == 201
    key = int((await response.json())["thread_id"])
    await asyncio.wait_for(native.ready.wait(), timeout=3)
    backend, model, effort, _ = native.observed[0]
    assert (backend, model, effort) == ("codex", "gpt-5.5", "low")
    # Stored, not per-turn: the thread's next turn reads the same settings.
    assert await native.settings.current_backend(key) == "codex"
    assert await native.settings.current_model("codex", key) == "gpt-5.5"
    assert await native.settings.current_effort("codex", key) == "low"


async def test_headless_spawn_ai_wins_over_the_parent_child_default(native):  # noqa: F811
    parent = await native.host.frontend.open(external_id="parent", title="Parent")
    child_settings = native.settings.for_children()
    await child_settings.set_backend("codex", thread_id=parent.thread_key)
    await child_settings.set_model("codex", "parent-model", thread_id=parent.thread_key)
    response = await native.client.post(
        "/api/spawn",
        json={
            "prompt": "Job",
            "parent_thread_id": parent.thread_key,
            "ai": {"backend": "claude", "model": None, "effort": None},
        },
    )
    assert response.status == 201
    await asyncio.wait_for(native.ready.wait(), timeout=3)
    backend, model, _, _ = native.observed[0]
    # model null → the backend's own default, not the parent's codex model.
    assert (backend, model) == ("claude", "sonnet")


async def test_headless_spawn_without_ai_keeps_the_default(native):  # noqa: F811
    response = await native.client.post("/api/spawn", json={"prompt": "Job"})
    assert response.status == 201
    await asyncio.wait_for(native.ready.wait(), timeout=3)
    assert native.observed[0][:2] == ("claude", "sonnet")


@pytest.mark.parametrize(
    "ai",
    [
        "codex",
        {"backend": "gpt"},
        {"backend": "codex", "effort": "ludicrous"},
        {"backend": "codex", "model": "bad model; rm -rf"},
        {"backend": "codex", "extra": 1},
        {"backend": "claude", "model": 5},
    ],
)
async def test_invalid_ai_is_refused_before_anything_is_created(native, ai):  # noqa: F811
    response = await native.client.post("/api/spawn", json={"prompt": "Job", "ai": ai})
    assert response.status == 400
    assert "error" in await response.json()
    assert not native.host._tasks
    assert await native.conversations.list_all() == []


def _cog(settings: BackendSettings) -> tuple[ClaudeChatCog, MagicMock]:
    thread = MagicMock(spec=discord.Thread)
    thread.id = 42
    thread.name = "child"
    thread.mention = "<#42>"
    thread.send = AsyncMock()
    channel = MagicMock()
    channel.create_thread = AsyncMock(return_value=thread)
    bot = MagicMock()
    bot.get_channel.return_value = None
    cog = ClaudeChatCog(bot=bot, repo=MagicMock(), runner=MagicMock())
    cog._backend_settings = settings
    return cog, channel


async def _settings(tmp_path) -> BackendSettings:
    db = str(tmp_path / "s.db")
    await init_db(db)
    return BackendSettings(
        SettingsRepository(db),
        env_backend="claude",
        env_model_for_claude="opus",
        env_model_for_codex="",
    )


async def test_discord_spawn_pins_ai_before_the_first_run(tmp_path):
    settings = await _settings(tmp_path)
    await settings.for_children().set_backend("pi", thread_id=PARENT_ID)
    cog, channel = _cog(settings)
    ran = asyncio.Event()

    async def run(*args, **kwargs):
        assert await settings.current_backend(42) == "codex"
        assert await settings.current_effort("codex", 42) == "medium"
        ran.set()

    cog._run_claude = run
    await cog.spawn_session(
        channel,
        "job",
        parent_thread_id=PARENT_ID,
        ai=TurnAI(backend="codex", effort="medium"),
    )
    await asyncio.wait_for(ran.wait(), 2)
    assert await settings.current_backend(42) == "codex"


async def test_discord_api_forwards_ai_to_spawn_session():
    from aiohttp.test_utils import TestClient, TestServer

    from claude_discord.ext.api_server import ApiServer

    channel = MagicMock(spec=discord.TextChannel)
    thread = MagicMock()
    thread.id = 7
    thread.name = "t"
    cog = MagicMock()
    cog.spawn_session = AsyncMock(return_value=thread)
    bot = MagicMock()
    bot.headless = False
    bot.cogs = {"ClaudeChatCog": cog}
    bot.get_channel.return_value = channel
    api = ApiServer(repo=MagicMock(), bot=bot, host="127.0.0.1", port=0, default_channel_id=1)
    async with TestClient(TestServer(api.app)) as client:
        response = await client.post(
            "/api/spawn",
            json={"prompt": "job", "ai": {"backend": "claude", "model": "opus", "effort": "high"}},
        )
        assert response.status == 201
    assert cog.spawn_session.await_args.kwargs["ai"] == TurnAI(
        backend="claude", model="opus", effort="high"
    )
