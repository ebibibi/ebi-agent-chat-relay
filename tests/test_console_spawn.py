"""Control-plane creation must stay native when Discord is disabled."""

from __future__ import annotations

import asyncio
import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.backend_settings import BackendSettings
from claude_discord.console.conversations import ConversationRepository
from claude_discord.console.host import ConsoleSessionHost
from claude_discord.console.surface import ConsoleFrontend
from claude_discord.database.frontend_thread_repo import FrontendThreadRepository
from claude_discord.database.ingest_repo import IngestResultRepository
from claude_discord.database.lineage_repo import ThreadLineageRepository
from claude_discord.database.models import init_db
from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.database.settings_repo import SettingsRepository
from claude_discord.ext.api_server import ApiServer


@pytest.fixture
async def native(tmp_path):
    db = str(tmp_path / "sessions.db")
    await init_db(db)
    conversations = ConversationRepository(db)
    await conversations.init_db()
    settings_repo = SettingsRepository(db)
    settings = BackendSettings(
        settings_repo, env_backend="claude", env_model_for_claude="sonnet", env_model_for_codex=""
    )
    frontend = ConsoleFrontend(
        conversations, FrontendThreadRepository(db), files_dir=tmp_path / "files"
    )
    observed = []
    ready = asyncio.Event()
    lineage = ThreadLineageRepository(db)

    async def run(config):
        key = config.surface.thread_key
        backend = await settings.current_backend(key)
        observed.append(
            (
                backend,
                await settings.current_model(backend, key),
                await settings.current_effort(backend, key),
                await lineage.list_all(),
            )
        )
        await config.surface.send_text("native answer")
        if config.result_sink is not None:
            await config.result_sink("native answer", None)
        ready.set()
        return None

    host = ConsoleSessionHost(
        frontend=frontend,
        session_repo=SimpleNamespace(get=AsyncMock(return_value=None)),
        backend_factory=MagicMock(),
        backend_settings=settings,
        run_session=run,
    )
    bot = MagicMock()
    bot.headless = True
    bot.console_sessions = host
    bot.get_channel.side_effect = AssertionError("Discord lookup must never happen")
    bot.fetch_channel = AsyncMock(side_effect=AssertionError("Discord fetch must never happen"))
    notifications = NotificationRepository(db)
    await notifications.init_db()
    api = ApiServer(repo=notifications, bot=bot, host="127.0.0.1", port=0)
    api.working_dir = str(tmp_path)
    api.ingest_repo = IngestResultRepository(db)
    await api.ingest_repo.init_db()
    api.lineage_repo = lineage
    api.console_conversations = conversations
    async with TestClient(TestServer(api.app)) as client:
        yield SimpleNamespace(
            client=client,
            api=api,
            host=host,
            bot=bot,
            conversations=conversations,
            settings=settings,
            lineage=lineage,
            observed=observed,
            ready=ready,
        )
    if host._tasks:
        await asyncio.gather(*host._tasks)


async def test_headless_spawn_needs_no_channel_and_retains_seed_and_files(native):
    response = await native.client.post(
        "/api/spawn",
        json={
            "prompt": "Read this first",
            "thread_name": "Native work",
            "auto_start": False,
            "attachments": [{"filename": "input.txt", "data": base64.b64encode(b"facts").decode()}],
        },
    )
    assert response.status == 201
    body = await response.json()
    assert body["frontend"] == "console"
    key = int(body["thread_id"])
    assert await native.host.owns(key)
    messages = await native.conversations.history(key, limit=50)
    assert any("Read this first" in m.content for m in messages)
    assert any("input.txt" in m.content and "/console/api/files/" in m.content for m in messages)
    assert not native.observed
    native.bot.get_channel.assert_not_called()


async def test_legacy_channel_does_not_send_console_child_to_discord(native):
    parent = await native.host.frontend.open(external_id="parent", title="Parent")
    child_settings = native.settings.for_children()
    await child_settings.set_backend("codex", thread_id=parent.thread_key)
    await child_settings.set_model("codex", "chosen-model", thread_id=parent.thread_key)
    await child_settings.set_effort("codex", "high", thread_id=parent.thread_key)
    response = await native.client.post(
        "/api/spawn",
        json={
            "prompt": "Child work",
            "parent_thread_id": parent.thread_key,
            "channel_id": 123,
        },
    )
    assert response.status == 201
    body = await response.json()
    assert body["parent_thread_id"] == str(parent.thread_key)
    await asyncio.wait_for(native.ready.wait(), timeout=3)
    backend, model, effort, relations = native.observed[0]
    assert (backend, model, effort) == ("codex", "chosen-model", "high")
    assert any(
        r.thread_id == int(body["thread_id"]) and r.parent_thread_id == parent.thread_key
        for r in relations
    )
    native.bot.fetch_channel.assert_not_awaited()


async def test_headless_spawn_fails_without_console_instead_of_falling_back(native):
    native.bot.console_sessions = None
    response = await native.client.post("/api/spawn", json={"prompt": "Do work", "channel_id": 123})
    assert response.status == 503
    native.bot.get_channel.assert_not_called()


async def test_native_history_is_readable_and_unknown_ids_never_fetch_discord(native):
    key = await native.host.spawn(prompt="seed", title="History", auto_start=False)
    await native.conversations.append(key, author="AI", is_bot=True, content="x" * 2500)
    response = await native.client.get(f"/api/threads/{key}/messages?limit=1")
    assert response.status == 200
    body = await response.json()
    assert body["messages"][0]["content"] == "x" * 2000
    assert body["messages"][0]["truncated"] is True
    assert (await native.client.get("/api/threads/123/messages")).status == 404
    native.bot.fetch_channel.assert_not_awaited()


async def test_native_ingest_keeps_auth_and_captures_result_without_discord(native):
    native.api.ingest_token = "test-ingest-secret"
    assert (await native.client.post("/api/ingest", json={"content": "external"})).status == 401
    response = await native.client.post(
        "/api/ingest",
        json={"content": "external", "channel_id": "unused"},
        headers={"Authorization": "Bearer test-ingest-secret"},
    )
    assert response.status == 201
    body = await response.json()
    await asyncio.wait_for(native.ready.wait(), timeout=3)
    result = await native.api.ingest_repo.get(body["result_id"])
    assert result["result"] == "native answer"
    native.bot.get_channel.assert_not_called()


@pytest.mark.parametrize("queued", [False, True])
async def test_headless_restart_resumes_native_work_but_preserves_discord_rows(native, queued):
    from claude_discord.cogs.claude_chat import ClaudeChatCog
    from claude_discord.database.resume_repo import PendingResume
    from claude_discord.main import _resume_headless_sessions
    from claude_discord.restart_reservation import QUEUED_RESTART_REASON

    key = await native.host.spawn(prompt="seed", title="Resume", auto_start=False)
    reason = QUEUED_RESTART_REASON if queued else "self_restart"
    rows = [
        PendingResume(i, tid, None, reason, "continue native", "now")
        for i, tid in enumerate([key, 123], 1)
    ]
    pending = SimpleNamespace(get_pending=AsyncMock(return_value=rows), delete=AsyncMock())
    cog = ClaudeChatCog(bot=native.bot, repo=MagicMock(), runner=MagicMock(), resume_repo=pending)
    native.bot.get_cog.return_value = cog
    await _resume_headless_sessions(native.bot)
    await asyncio.wait_for(native.ready.wait(), timeout=3)
    pending.delete.assert_awaited_once_with(1)
    native.bot.get_channel.assert_not_called()
    native.bot.fetch_channel.assert_not_awaited()


async def test_headless_lounge_does_not_mirror_stale_discord_channel(native):
    native.api.lounge_channel_id = 123
    await native.api._send_lounge_to_discord("test", "hello", "now")
    native.bot.get_channel.assert_not_called()


async def test_native_ingest_without_channel_preserves_attachment_paths_when_not_started(native):
    native.api.ingest_token = "test-ingest-secret"
    response = await native.client.post(
        "/api/ingest",
        json={
            "content": "Read the facts",
            "auto_start": False,
            "attachments": [{"filename": "facts.txt", "data": base64.b64encode(b"facts").decode()}],
        },
        headers={"Authorization": "Bearer test-ingest-secret"},
    )
    assert response.status == 201
    body = await response.json()
    assert "result_id" not in body
    assert body["attachments_saved"] == 1
    messages = await native.conversations.history(int(body["thread_id"]), limit=10)
    assert "facts.txt" in messages[0].content
    assert not native.observed
    native.bot.get_channel.assert_not_called()
