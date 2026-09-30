"""How the last turn ended, shown in the thread title: ✅ done, ❓ waiting, ⚠️ error.

At most one outcome shows at a time, it sits in front of the scheduled marker,
and a human reply clears whichever one is there.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_code_core.memory_surface import MemorySurface
from claude_discord.claude.types import AskOption, AskQuestion, MessageType, StreamEvent
from claude_discord.cogs import _run_helper
from claude_discord.cogs._run_helper import _build_system_context, run_claude_with_config
from claude_discord.cogs.claude_chat import ClaudeChatCog
from claude_discord.cogs.run_config import RunConfig
from claude_discord.database.models import init_db
from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.discord_ui.chunker import DISCORD_CAPABILITIES
from claude_discord.ext.api_server import ApiServer
from claude_discord.thread_marker import (
    DEFAULT_DONE_MARKER,
    DEFAULT_ERROR_MARKER,
    DEFAULT_SCHEDULED_MARKER,
    DEFAULT_SPAWN_MARKER,
    DEFAULT_WAITING_MARKER,
    DONE_MARKER_ENV_VAR,
    ERROR_MARKER_ENV_VAR,
    OUTCOME_DONE,
    OUTCOME_ERROR,
    OUTCOME_WAITING,
    SCHEDULED_MARKER_ENV_VAR,
    WAITING_MARKER_ENV_VAR,
    family_code,
    mark_done_thread_name,
    mark_scheduled_thread_name,
    retag_thread_name,
    set_outcome_thread_name,
    unmark_done_thread_name,
)
from claude_discord.thread_status import apply_thread_outcome

from .conftest import make_async_gen

DONE = DEFAULT_DONE_MARKER
WAIT = DEFAULT_WAITING_MARKER
ERR = DEFAULT_ERROR_MARKER
SCHED = DEFAULT_SCHEDULED_MARKER


@pytest.fixture(autouse=True)
def _default_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        DONE_MARKER_ENV_VAR,
        WAITING_MARKER_ENV_VAR,
        ERROR_MARKER_ENV_VAR,
        SCHEDULED_MARKER_ENV_VAR,
    ):
        monkeypatch.delenv(var, raising=False)


# ---------------------------------------------------------------------------
# name helpers
# ---------------------------------------------------------------------------


class TestSetOutcome:
    @pytest.mark.parametrize(
        ("outcome", "marker"),
        [(OUTCOME_DONE, DONE), (OUTCOME_WAITING, WAIT), (OUTCOME_ERROR, ERR)],
    )
    def test_prefixes_the_marker(self, outcome: str, marker: str) -> None:
        assert set_outcome_thread_name("Fix", outcome) == f"{marker} Fix"

    def test_a_new_outcome_replaces_the_old_one(self) -> None:
        assert set_outcome_thread_name(f"{ERR} Fix", OUTCOME_DONE) == f"{DONE} Fix"

    def test_none_clears_any_outcome(self) -> None:
        assert set_outcome_thread_name(f"{WAIT} Fix", None) == "Fix"

    def test_is_idempotent(self) -> None:
        once = set_outcome_thread_name("Fix", OUTCOME_WAITING)
        assert set_outcome_thread_name(once, OUTCOME_WAITING) == once

    def test_goes_in_front_of_the_scheduled_marker(self) -> None:
        assert set_outcome_thread_name(f"{SCHED} Fix", OUTCOME_WAITING) == f"{WAIT} {SCHED} Fix"

    def test_scheduled_goes_behind_an_outcome(self) -> None:
        assert mark_scheduled_thread_name(f"{ERR} Fix") == f"{ERR} {SCHED} Fix"

    def test_mark_done_replaces_waiting(self) -> None:
        assert mark_done_thread_name(f"{WAIT} Fix") == f"{DONE} Fix"

    def test_unmark_done_leaves_other_outcomes(self) -> None:
        assert unmark_done_thread_name(f"{WAIT} Fix") == f"{WAIT} Fix"

    def test_disabled_marker_is_not_written(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ERROR_MARKER_ENV_VAR, "")
        assert set_outcome_thread_name("Fix", OUTCOME_ERROR) == "Fix"

    def test_a_non_string_name_does_not_hang(self) -> None:
        # MagicMock names answer every startswith() with a truthy mock.
        set_outcome_thread_name(MagicMock(), OUTCOME_ERROR)  # type: ignore[arg-type]


class TestRetitleDropsOutcomes:
    @pytest.mark.parametrize("marker", [WAIT, ERR])
    def test_retitle_drops_the_outcome(self, marker: str) -> None:
        assert retag_thread_name(f"{marker} Old", "New") == "New"

    def test_retitle_keeps_scheduled_and_lineage(self) -> None:
        tag = f"{DEFAULT_SPAWN_MARKER}{family_code(1)}"
        assert retag_thread_name(f"{ERR} {SCHED} {tag} Old", "New") == f"{SCHED} {tag} New"


# ---------------------------------------------------------------------------
# apply_thread_outcome
# ---------------------------------------------------------------------------


def _thread(name: str) -> MagicMock:
    thread = MagicMock(spec=discord.Thread)
    thread.id = 42
    thread.name = name
    thread.edit = AsyncMock()
    return thread


class TestApplyThreadOutcome:
    async def test_renames_the_thread(self) -> None:
        thread = _thread("Fix")
        await apply_thread_outcome(thread, OUTCOME_ERROR)
        thread.edit.assert_awaited_once_with(name=f"{ERR} Fix")

    async def test_unchanged_name_is_not_renamed(self) -> None:
        thread = _thread(f"{ERR} Fix")
        await apply_thread_outcome(thread, OUTCOME_ERROR)
        thread.edit.assert_not_awaited()

    async def test_a_channel_is_left_alone(self) -> None:
        channel = MagicMock(spec=discord.TextChannel)
        channel.edit = AsyncMock()
        await apply_thread_outcome(channel, OUTCOME_ERROR)
        channel.edit.assert_not_awaited()

    async def test_a_failed_rename_is_swallowed(self) -> None:
        thread = _thread("Fix")
        thread.edit.side_effect = RuntimeError("rate limited")
        await apply_thread_outcome(thread, OUTCOME_ERROR)


# ---------------------------------------------------------------------------
# human reply clears any outcome
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("marker", [WAIT, ERR])
async def test_reply_clears_the_outcome(marker: str) -> None:
    thread = _thread(f"{marker} {SCHED} Fix")

    ClaudeChatCog.__new__(ClaudeChatCog)._schedule_clear_done_marker(thread)
    await asyncio.sleep(0)

    thread.edit.assert_awaited_once_with(name=f"{SCHED} Fix")


# ---------------------------------------------------------------------------
# POST /api/threads/{id}/waiting
# ---------------------------------------------------------------------------


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


async def test_waiting_endpoint_replaces_done(api_client: TestClient, bot: MagicMock) -> None:
    thread = _thread(f"{DONE} Fix")
    bot.get_channel.return_value = thread

    resp = await api_client.post("/api/threads/42/waiting")

    assert resp.status == 200
    assert await resp.json() == {"status": "marked", "thread_name": f"{WAIT} Fix"}
    thread.edit.assert_awaited_once_with(name=f"{WAIT} Fix")


async def test_waiting_endpoint_is_idempotent(api_client: TestClient, bot: MagicMock) -> None:
    bot.get_channel.return_value = _thread(f"{WAIT} Fix")
    resp = await api_client.post("/api/threads/42/waiting")
    assert (await resp.json())["status"] == "unchanged"


# ---------------------------------------------------------------------------
# run helper: error and AskUserQuestion
# ---------------------------------------------------------------------------


def _result(error: str | None = None) -> StreamEvent:
    return StreamEvent(
        message_type=MessageType.RESULT, is_complete=True, session_id="s-1", error=error
    )


async def test_turn_ending_in_error_marks_the_thread(thread: MagicMock, runner: MagicMock) -> None:
    runner.run = make_async_gen([_result(error="API Error: 529 overloaded")])
    with patch.object(_run_helper, "schedule_thread_outcome") as mark:
        await run_claude_with_config(RunConfig(thread=thread, runner=runner, prompt="go"))
    mark.assert_called_once_with(thread, OUTCOME_ERROR)


async def test_successful_turn_sets_no_outcome(thread: MagicMock, runner: MagicMock) -> None:
    runner.run = make_async_gen([_result()])
    with patch.object(_run_helper, "schedule_thread_outcome") as mark:
        await run_claude_with_config(RunConfig(thread=thread, runner=runner, prompt="go"))
    mark.assert_not_called()


async def test_crashed_run_marks_the_thread(thread: MagicMock, runner: MagicMock) -> None:
    def _boom(*_a: object, **_k: object):
        raise RuntimeError("CLI vanished")

    runner.run = _boom
    with patch.object(_run_helper, "schedule_thread_outcome") as mark:
        await run_claude_with_config(RunConfig(thread=thread, runner=runner, prompt="go"))
    mark.assert_called_once_with(thread, OUTCOME_ERROR)


def _ask_events() -> list[StreamEvent]:
    return [
        StreamEvent(message_type=MessageType.SYSTEM, session_id="s-1"),
        StreamEvent(
            message_type=MessageType.ASSISTANT,
            ask_questions=[AskQuestion(question="Pick one", options=[AskOption(label="A")])],
        ),
        _result(),
    ]


async def test_open_question_marks_waiting(thread: MagicMock, runner: MagicMock) -> None:
    runner.run = make_async_gen(_ask_events())
    with (
        patch.object(_run_helper, "schedule_thread_outcome") as mark,
        patch.object(_run_helper, "collect_ask_answers", new=AsyncMock(return_value=None)),
    ):
        await run_claude_with_config(RunConfig(thread=thread, runner=runner, prompt="go"))
    # Unanswered: the thread is still waiting on the human.
    assert mark.call_args_list == [((thread, OUTCOME_WAITING),)]


async def test_answered_question_clears_waiting(thread: MagicMock, runner: MagicMock) -> None:
    runner.run = make_async_gen(_ask_events())
    with (
        patch.object(_run_helper, "schedule_thread_outcome") as mark,
        patch.object(_run_helper, "collect_ask_answers", new=AsyncMock(return_value="A")),
        # Stub only the resumed run; the outer call is the real function.
        patch.object(_run_helper, "run_claude_with_config", new=AsyncMock()),
    ):
        await run_claude_with_config(RunConfig(thread=thread, runner=runner, prompt="go"))
    assert [c.args[1] for c in mark.call_args_list] == [OUTCOME_WAITING, None]


# ---------------------------------------------------------------------------
# system prompt
# ---------------------------------------------------------------------------


def _prompt_runner() -> MagicMock:
    runner = MagicMock()
    runner.working_dir = "/tmp/example"
    runner.api_port = 8080
    return runner


async def test_prompt_explains_the_waiting_endpoint() -> None:
    config = RunConfig(
        surface=MemorySurface(capabilities=DISCORD_CAPABILITIES),
        runner=_prompt_runner(),
        prompt="do it",
    )
    context = await _build_system_context(config) or ""
    assert "/api/threads/$DISCORD_THREAD_ID/waiting" in context
    assert WAIT in context


async def test_prompt_omits_waiting_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(WAITING_MARKER_ENV_VAR, "")
    config = RunConfig(
        surface=MemorySurface(capabilities=DISCORD_CAPABILITIES),
        runner=_prompt_runner(),
        prompt="do it",
    )
    assert "/waiting" not in (await _build_system_context(config) or "")
