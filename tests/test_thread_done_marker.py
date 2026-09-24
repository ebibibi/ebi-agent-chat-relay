"""The "ready to close" thread marker (issue #769).

The agent marks a finished thread via ``POST /api/threads/{id}/done``; a human
reply clears it; a retitle never carries it forward.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_code_core.memory_surface import MemorySurface
from claude_discord.cogs._run_helper import _build_system_context
from claude_discord.cogs.claude_chat import ClaudeChatCog
from claude_discord.cogs.run_config import RunConfig
from claude_discord.database.models import init_db
from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.discord_ui.chunker import DISCORD_CAPABILITIES
from claude_discord.ext.api_server import ApiServer
from claude_discord.thread_marker import (
    DEFAULT_DONE_MARKER,
    DEFAULT_SPAWN_MARKER,
    DONE_MARKER_ENV_VAR,
    MAX_THREAD_NAME_LENGTH,
    family_code,
    mark_done_thread_name,
    retag_thread_name,
    unmark_done_thread_name,
)

DONE = DEFAULT_DONE_MARKER


@pytest.fixture(autouse=True)
def _default_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DONE_MARKER_ENV_VAR, raising=False)


# ---------------------------------------------------------------------------
# name helpers
# ---------------------------------------------------------------------------


class TestMarkDone:
    def test_prefixes_the_marker(self) -> None:
        assert mark_done_thread_name("Fix the build") == f"{DONE} Fix the build"

    def test_is_idempotent(self) -> None:
        once = mark_done_thread_name("Fix the build")
        assert mark_done_thread_name(once) == once

    def test_goes_in_front_of_lineage_tags(self) -> None:
        name = f"{DEFAULT_SPAWN_MARKER}{family_code(1)} Fix the build"
        assert mark_done_thread_name(name) == f"{DONE} {name}"

    def test_keeps_the_marker_when_truncating(self) -> None:
        marked = mark_done_thread_name("x" * 200)
        assert marked.startswith(DONE)
        assert len(marked) == MAX_THREAD_NAME_LENGTH

    def test_empty_env_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(DONE_MARKER_ENV_VAR, "")
        assert mark_done_thread_name("Fix the build") == "Fix the build"

    def test_custom_marker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(DONE_MARKER_ENV_VAR, "[done]")
        assert mark_done_thread_name("Fix") == "[done] Fix"


class TestUnmarkDone:
    def test_removes_the_marker(self) -> None:
        assert unmark_done_thread_name(f"{DONE} Fix the build") == "Fix the build"

    def test_unmarked_name_is_unchanged(self) -> None:
        assert unmark_done_thread_name("Fix the build") == "Fix the build"

    def test_a_marker_mid_title_is_left_alone(self) -> None:
        assert unmark_done_thread_name(f"Fix {DONE} build") == f"Fix {DONE} build"


class TestRetitleDropsDone:
    def test_retitle_of_a_done_thread_drops_the_marker(self) -> None:
        assert retag_thread_name(f"{DONE} Old", "New") == "New"

    def test_retitle_keeps_lineage_behind_the_done_marker(self) -> None:
        tag = f"{DEFAULT_SPAWN_MARKER}{family_code(1)}"
        assert retag_thread_name(f"{DONE} {tag} Old", "New") == f"{tag} New"

    def test_a_suggested_title_copying_the_marker_is_cleaned(self) -> None:
        assert retag_thread_name("Old", f"{DONE} New") == "New"


# ---------------------------------------------------------------------------
# POST /api/threads/{id}/done
# ---------------------------------------------------------------------------


def _thread(name: str) -> MagicMock:
    thread = MagicMock(spec=discord.Thread)
    thread.name = name
    thread.edit = AsyncMock()
    return thread


@pytest.fixture
def bot() -> MagicMock:
    b = MagicMock()
    b.cogs = {}
    b.get_channel.return_value = None
    b.fetch_channel = AsyncMock(side_effect=RuntimeError("Unknown Channel"))
    return b


@pytest.fixture
async def api_client(bot: MagicMock):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(path)
    repo = NotificationRepository(path)
    await repo.init_db()
    api = ApiServer(repo=repo, bot=bot, default_channel_id=1, host="127.0.0.1", port=0)
    client = TestClient(TestServer(api.app))
    await client.start_server()
    yield client
    await client.close()
    os.unlink(path)


async def test_endpoint_marks_the_thread(api_client: TestClient, bot: MagicMock) -> None:
    thread = _thread("Fix the build")
    bot.get_channel.return_value = thread

    resp = await api_client.post("/api/threads/42/done")

    assert resp.status == 200
    body = await resp.json()
    assert body == {"status": "marked", "thread_name": f"{DONE} Fix the build"}
    thread.edit.assert_awaited_once_with(name=f"{DONE} Fix the build")


async def test_endpoint_does_not_rename_twice(api_client: TestClient, bot: MagicMock) -> None:
    thread = _thread(f"{DONE} Fix the build")
    bot.get_channel.return_value = thread

    resp = await api_client.post("/api/threads/42/done")

    assert (await resp.json())["status"] == "unchanged"
    thread.edit.assert_not_awaited()


async def test_endpoint_unknown_thread_is_404(api_client: TestClient) -> None:
    resp = await api_client.post("/api/threads/42/done")
    assert resp.status == 404


async def test_endpoint_rejects_a_non_thread(api_client: TestClient, bot: MagicMock) -> None:
    bot.get_channel.return_value = MagicMock(spec=discord.TextChannel)
    resp = await api_client.post("/api/threads/42/done")
    assert resp.status == 400


async def test_endpoint_reports_a_failed_rename(api_client: TestClient, bot: MagicMock) -> None:
    thread = _thread("Fix the build")
    thread.edit.side_effect = RuntimeError("rate limited")
    bot.get_channel.return_value = thread

    resp = await api_client.post("/api/threads/42/done")

    assert resp.status == 502


async def test_endpoint_rejects_a_bad_id(api_client: TestClient) -> None:
    resp = await api_client.post("/api/threads/abc/done")
    assert resp.status == 400


# ---------------------------------------------------------------------------
# human reply clears the marker
# ---------------------------------------------------------------------------


def _cog() -> ClaudeChatCog:
    cog = ClaudeChatCog.__new__(ClaudeChatCog)  # only the marker helpers are exercised
    return cog


async def test_reply_clears_the_done_marker() -> None:
    thread = _thread(f"{DONE} Fix the build")

    _cog()._schedule_clear_done_marker(thread)
    await asyncio.sleep(0)

    thread.edit.assert_awaited_once_with(name="Fix the build")


async def test_reply_in_an_unmarked_thread_does_not_rename() -> None:
    thread = _thread("Fix the build")

    _cog()._schedule_clear_done_marker(thread)
    await asyncio.sleep(0)

    thread.edit.assert_not_awaited()


async def test_a_failed_clear_is_swallowed() -> None:
    thread = _thread(f"{DONE} Fix the build")
    thread.edit.side_effect = RuntimeError("rate limited")

    _cog()._schedule_clear_done_marker(thread)
    await asyncio.sleep(0)

    thread.edit.assert_awaited_once()


# ---------------------------------------------------------------------------
# system prompt
# ---------------------------------------------------------------------------


def _runner(api_port: int | None) -> MagicMock:
    runner = MagicMock()
    runner.working_dir = "/tmp/example"
    runner.api_port = api_port
    return runner


async def test_prompt_explains_the_done_endpoint() -> None:
    config = RunConfig(
        surface=MemorySurface(capabilities=DISCORD_CAPABILITIES),
        runner=_runner(8080),
        prompt="do it",
    )
    context = await _build_system_context(config)
    assert "/api/threads/$DISCORD_THREAD_ID/done" in context
    assert DONE in context


async def test_prompt_omits_it_without_the_api() -> None:
    config = RunConfig(
        surface=MemorySurface(capabilities=DISCORD_CAPABILITIES),
        runner=_runner(None),
        prompt="do it",
    )
    assert "/done" not in (await _build_system_context(config) or "")


async def test_prompt_omits_it_where_threads_cannot_be_renamed() -> None:
    config = RunConfig(surface=MemorySurface(), runner=_runner(8080), prompt="do it")
    assert "/done" not in (await _build_system_context(config) or "")


async def test_prompt_omits_it_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DONE_MARKER_ENV_VAR, "")
    config = RunConfig(
        surface=MemorySurface(capabilities=DISCORD_CAPABILITIES),
        runner=_runner(8080),
        prompt="do it",
    )
    assert "/done" not in (await _build_system_context(config) or "")
