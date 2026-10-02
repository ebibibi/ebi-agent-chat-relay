"""Two more "your move" outcomes: 👀 review a deliverable, 📋 do a task yourself.

❓ is now only "reply to answer"; the three are exclusive with ✅ and ⚠️.
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_code_core.memory_surface import MemorySurface
from claude_discord.cogs._run_helper import _build_system_context
from claude_discord.cogs.run_config import RunConfig
from claude_discord.database.models import init_db
from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.discord_ui.chunker import DISCORD_CAPABILITIES
from claude_discord.ext.api_server import ApiServer
from claude_discord.thread_marker import (
    ACTION_MARKER_ENV_VAR,
    DEFAULT_ACTION_MARKER,
    DEFAULT_REVIEW_MARKER,
    DEFAULT_SCHEDULED_MARKER,
    DEFAULT_WAITING_MARKER,
    OUTCOME_ACTION,
    OUTCOME_REVIEW,
    OUTCOME_WAITING,
    REVIEW_MARKER_ENV_VAR,
    retag_thread_name,
    set_outcome_thread_name,
)

REVIEW = DEFAULT_REVIEW_MARKER
ACTION = DEFAULT_ACTION_MARKER
WAIT = DEFAULT_WAITING_MARKER
SCHED = DEFAULT_SCHEDULED_MARKER


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(REVIEW_MARKER_ENV_VAR, raising=False)
    monkeypatch.delenv(ACTION_MARKER_ENV_VAR, raising=False)


def test_default_markers() -> None:
    assert REVIEW == "\U0001f440"  # 👀
    assert ACTION == "\U0001f4cb"  # 📋


class TestNames:
    def test_review(self) -> None:
        assert set_outcome_thread_name("Draft", OUTCOME_REVIEW) == f"{REVIEW} Draft"

    def test_action_replaces_waiting(self) -> None:
        assert set_outcome_thread_name(f"{WAIT} Fix", OUTCOME_ACTION) == f"{ACTION} Fix"

    def test_sits_in_front_of_scheduled(self) -> None:
        assert set_outcome_thread_name(f"{SCHED} Fix", OUTCOME_ACTION) == f"{ACTION} {SCHED} Fix"

    def test_cleared_by_none(self) -> None:
        assert set_outcome_thread_name(f"{REVIEW} Draft", None) == "Draft"

    def test_retitle_drops_them(self) -> None:
        assert retag_thread_name(f"{ACTION} Old", "New") == "New"

    def test_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ACTION_MARKER_ENV_VAR, "")
        assert set_outcome_thread_name("Fix", OUTCOME_ACTION) == "Fix"


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


@pytest.mark.parametrize(("path", "marker"), [("review", REVIEW), ("action", ACTION)])
async def test_endpoints(api_client: TestClient, bot: MagicMock, path: str, marker: str) -> None:
    thread = _thread(f"{WAIT} Fix")
    bot.get_channel.return_value = thread

    resp = await api_client.post(f"/api/threads/42/{path}")

    assert resp.status == 200
    assert await resp.json() == {"status": "marked", "thread_name": f"{marker} Fix"}


async def test_prompt_explains_all_three() -> None:
    runner = MagicMock()
    runner.working_dir = "/tmp/example"
    runner.api_port = 8080
    config = RunConfig(
        surface=MemorySurface(capabilities=DISCORD_CAPABILITIES), runner=runner, prompt="go"
    )
    context = await _build_system_context(config) or ""
    for path, marker in (("waiting", WAIT), ("review", REVIEW), ("action", ACTION)):
        assert f"/api/threads/$DISCORD_THREAD_ID/{path}" in context
        assert marker in context


async def test_prompt_omits_a_disabled_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(REVIEW_MARKER_ENV_VAR, "")
    runner = MagicMock()
    runner.working_dir = "/tmp/example"
    runner.api_port = 8080
    config = RunConfig(
        surface=MemorySurface(capabilities=DISCORD_CAPABILITIES), runner=runner, prompt="go"
    )
    context = await _build_system_context(config) or ""
    assert "/review" not in context
    assert "/action" in context


def test_waiting_is_still_its_own_outcome() -> None:
    assert OUTCOME_WAITING not in (OUTCOME_REVIEW, OUTCOME_ACTION)
