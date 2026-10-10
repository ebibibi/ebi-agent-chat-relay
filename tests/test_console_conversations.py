"""The console owns its conversations: no chat thread, same session runner.

Starting work from the console used to open a Discord thread. These tests pin
that it now runs through the console's own surface, keeps the transcript
itself, resumes the same session on a reply, and shows outcomes on the board.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_code_core.frontend import (
    Choice,
    ChoicePrompt,
    Notice,
    NoticeLevel,
)
from claude_discord.cogs import _run_helper
from claude_discord.console.auth import ConsoleAuthConfig, ConsoleAuthenticator
from claude_discord.console.conversations import CONSOLE_FRONTEND, ConversationRepository
from claude_discord.console.host import ConsoleSessionHost
from claude_discord.console.server import CSRF_HEADER, ConsoleServer
from claude_discord.console.surface import ConsoleFrontend, ConsoleSurface, apply_outcome
from claude_discord.console.work_repo import WorkItemRepository
from claude_discord.database.frontend_thread_repo import FrontendThreadRepository
from claude_discord.database.models import init_db
from claude_discord.thread_marker import OUTCOME_WAITING, thread_outcome

TOKEN = "k" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}
WRITE = {**AUTH, CSRF_HEADER: "1"}


class FakeSessions:
    """What the session table would say: the last session id per key."""

    def __init__(self) -> None:
        self.records: dict[int, SimpleNamespace] = {}

    async def get(self, key: int) -> SimpleNamespace | None:
        return self.records.get(key)


@pytest.fixture
async def env(tmp_path):
    db = str(tmp_path / "sessions.db")
    await init_db(db)
    work = WorkItemRepository(db)
    await work.init_db()
    conversations = ConversationRepository(db)
    await conversations.init_db()
    frontend = ConsoleFrontend(conversations, FrontendThreadRepository(db))

    runs: list = []
    sessions = FakeSessions()

    async def run_session(config) -> str | None:
        runs.append(config)
        await config.surface.send_text(f"answer to: {config.prompt}")
        sessions.records[config.surface.thread_key] = SimpleNamespace(
            session_id=f"s{len(runs)}", backend="claude", working_dir=None
        )
        return f"s{len(runs)}"

    settings = MagicMock()
    settings.current_backend = AsyncMock(return_value="claude")
    settings.current_model = AsyncMock(return_value=None)
    settings.current_effort = AsyncMock(return_value=None)
    settings.repo = None
    factory = MagicMock()
    factory.codex_command = "codex"
    host = ConsoleSessionHost(
        frontend=frontend,
        session_repo=sessions,
        backend_factory=factory,
        backend_settings=settings,
        run_session=run_session,
    )

    api = MagicMock()
    api.default_channel_id = None
    api.lineage_repo = None
    api.session_repo = None
    api.bot.get_channel.return_value = None
    chat_cog = MagicMock()
    chat_cog.spawn_session = AsyncMock()
    api.bot.cogs = {"ClaudeChatCog": chat_cog}
    api._running_thread_ids.return_value = set()
    console = ConsoleServer(
        api,
        work,
        ConsoleAuthenticator(ConsoleAuthConfig(token=TOKEN)),
        port=0,
        conversations=conversations,
        host_session=host,
    )
    async with TestClient(TestServer(console.app)) as client:
        yield SimpleNamespace(
            client=client,
            host=host,
            runs=runs,
            conversations=conversations,
            chat_cog=chat_cog,
        )


async def _settle(host: ConsoleSessionHost) -> None:
    while host._tasks:
        await asyncio.gather(*list(host._tasks))


async def _start(env, title: str = "Plan Sunday") -> str:
    created = await env.client.post("/console/api/items", json={"title": title}, headers=WRITE)
    capture_id = (await created.json())["item"]["id"]
    started = await env.client.post(
        f"/console/api/items/{capture_id}/start", json={}, headers=WRITE
    )
    assert started.status == 201
    await _settle(env.host)
    return (await started.json())["item"]["id"]


async def test_starting_work_opens_no_chat_thread(env) -> None:
    item_id = await _start(env)

    env.chat_cog.spawn_session.assert_not_called()
    [run] = env.runs
    assert run.surface.frontend == CONSOLE_FRONTEND
    assert run.session_origin == CONSOLE_FRONTEND
    assert run.prompt == "Plan Sunday"
    assert item_id == f"t{run.surface.thread_key}"


async def test_the_transcript_is_kept_by_the_console(env) -> None:
    item_id = await _start(env)

    response = await env.client.get(f"/console/api/items/{item_id}/messages", headers=AUTH)
    assert response.status == 200
    messages = (await response.json())["messages"]
    assert [(m["is_bot"], m["content"]) for m in messages] == [
        (False, "Plan Sunday"),
        (True, "answer to: Plan Sunday"),
    ]


async def test_a_reply_resumes_the_same_session(env) -> None:
    item_id = await _start(env)

    replied = await env.client.post(
        f"/console/api/items/{item_id}/reply", json={"text": "and Monday?"}, headers=WRITE
    )
    assert replied.status == 202
    await _settle(env.host)

    first, second = env.runs
    assert second.surface.thread_key == first.surface.thread_key
    assert second.session_id == "s1"
    assert second.prompt == "and Monday?"


async def test_the_board_shows_the_conversation_and_its_outcome(env) -> None:
    item_id = await _start(env)
    key = int(item_id[1:])

    await apply_outcome(env.conversations, key, OUTCOME_WAITING)
    board = await (await env.client.get("/console/api/board", headers=AUTH)).json()
    [item] = board["items"]
    assert (item["id"], item["status"], item["bucket"]) == (item_id, "waiting", "me")
    assert item["title"] == "Plan Sunday"

    # A reply answers the question, so the marker goes.
    await env.client.post(
        f"/console/api/items/{item_id}/reply", json={"text": "yes"}, headers=WRITE
    )
    await _settle(env.host)
    conversation = await env.conversations.get(key)
    assert thread_outcome(conversation.name) is None


async def test_done_marks_the_conversation_and_reopen_clears_it(env) -> None:
    item_id = await _start(env)
    key = int(item_id[1:])

    await env.client.post(f"/console/api/items/{item_id}/done", json={}, headers=WRITE)
    assert thread_outcome((await env.conversations.get(key)).name) == "done"

    await env.client.post(f"/console/api/items/{item_id}/reopen", json={}, headers=WRITE)
    assert thread_outcome((await env.conversations.get(key)).name) is None


async def test_turns_of_one_conversation_run_one_at_a_time(env) -> None:
    item_id = await _start(env)
    gate = asyncio.Event()
    order: list[str] = []
    original = env.host._run_session

    async def slow(config):
        order.append(f"start {config.prompt}")
        if config.prompt == "first":
            await gate.wait()
        result = await original(config)
        order.append(f"end {config.prompt}")
        return result

    env.host._run_session = slow
    for text in ("first", "second"):
        await env.client.post(
            f"/console/api/items/{item_id}/reply", json={"text": text}, headers=WRITE
        )
    await asyncio.sleep(0.05)
    gate.set()
    await _settle(env.host)
    assert order == ["start first", "end first", "start second", "end second"]


async def test_start_is_refused_where_agents_cannot_run(tmp_path) -> None:
    db = str(tmp_path / "sessions.db")
    work = WorkItemRepository(db)
    await work.init_db()
    api = MagicMock()
    api.bot.cogs = {}
    console = ConsoleServer(api, work, ConsoleAuthenticator(ConsoleAuthConfig(token=TOKEN)), port=0)
    async with TestClient(TestServer(console.app)) as client:
        created = await client.post("/console/api/items", json={"title": "x"}, headers=WRITE)
        capture_id = (await created.json())["item"]["id"]
        started = await client.post(
            f"/console/api/items/{capture_id}/start", json={}, headers=WRITE
        )
    assert started.status == 503


class TestSurface:
    @pytest.fixture
    async def surface(self, tmp_path) -> ConsoleSurface:
        db = str(tmp_path / "sessions.db")
        await init_db(db)
        repo = ConversationRepository(db)
        await repo.init_db()
        frontend = ConsoleFrontend(repo, FrontendThreadRepository(db))
        return await frontend.open(external_id="c1", title="t")

    async def test_a_question_lands_in_the_transcript(self, surface) -> None:
        prompt = ChoicePrompt(
            question="Which day?",
            choices=(Choice(value="sat", label="Saturday"), Choice(value="sun", label="Sunday")),
        )
        assert await surface.prompt_choice(prompt) is None
        [message] = await surface._repo.history(surface.thread_key, 10)
        assert "Which day?" in message.content and "Sunday" in message.content

    async def test_only_warnings_and_errors_are_kept(self, surface) -> None:
        await surface.send_notice(Notice(level=NoticeLevel.INFO, title="Session started"))
        await surface.send_notice(Notice(level=NoticeLevel.ERROR, title="Error", body="boom"))
        history = await surface._repo.history(surface.thread_key, 10)
        assert [m.content for m in history] == ["**Error**\nboom"]

    async def test_the_frontend_resolves_only_its_own_keys(self, tmp_path, surface) -> None:
        db = str(tmp_path / "sessions.db")
        ledger = FrontendThreadRepository(db)
        frontend = ConsoleFrontend(surface._repo, ledger)
        assert (await frontend.resolve_surface(surface.thread_key)).external_id == "c1"
        teams_key = await ledger.register("teams", "19:x")
        assert await frontend.resolve_surface(teams_key) is None


async def test_the_runner_shows_outcomes_on_a_surface_without_a_thread() -> None:
    surface = MagicMock()
    surface.set_outcome = AsyncMock()
    config = SimpleNamespace(thread=None, surface=surface)
    await _run_helper._show_outcome(config, OUTCOME_WAITING)
    surface.set_outcome.assert_awaited_once_with(OUTCOME_WAITING)


async def test_an_agent_marks_its_console_conversation_through_the_control_plane(
    tmp_path,
) -> None:
    from claude_discord.database.notification_repo import NotificationRepository
    from claude_discord.ext.api_server import ApiServer

    db = str(tmp_path / "sessions.db")
    await init_db(db)
    conversations = ConversationRepository(db)
    await conversations.init_db()
    await conversations.create(4242, "Plan Sunday")
    notifications = NotificationRepository(str(tmp_path / "n.db"))
    await notifications.init_db()
    bot = MagicMock()
    bot.cogs = {}
    bot.get_channel.return_value = None
    api = ApiServer(repo=notifications, bot=bot, default_channel_id=1, host="127.0.0.1", port=0)
    api.console_conversations = conversations
    async with TestClient(TestServer(api.app)) as client:
        response = await client.post("/api/threads/4242/waiting")
    assert response.status == 200
    assert thread_outcome((await conversations.get(4242)).name) == OUTCOME_WAITING
    bot.get_channel.assert_not_called()


async def test_a_wait_resumes_a_console_conversation(env) -> None:
    from claude_discord.cogs.wait_watcher import WaitWatcherCog

    item_id = await _start(env)
    key = int(item_id[1:])
    bot = MagicMock()
    bot.console_sessions = env.host
    watcher = WaitWatcherCog(bot, MagicMock())

    assert await watcher._deliver_to_discord(key, "CI finished: success") is True
    await _settle(env.host)
    assert env.runs[-1].prompt == "CI finished: success"
    bot.get_channel.assert_not_called()
