"""The console owns its conversations: no chat thread, same session runner.

Starting work from the console used to open a Discord thread. These tests pin
that it now runs through the console's own surface, keeps the transcript
itself, resumes the same session on a reply, and shows outcomes on the board.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import quote

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
    frontend = ConsoleFrontend(
        conversations, FrontendThreadRepository(db), files_dir=tmp_path / "console_files"
    )

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
            console=console,
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


async def _notify_api(tmp_path):
    from claude_discord.database.notification_repo import NotificationRepository
    from claude_discord.ext.api_server import ApiServer

    db = str(tmp_path / "sessions.db")
    await init_db(db)
    conversations = ConversationRepository(db)
    await conversations.init_db()
    await conversations.create(4242, "[keep] Health")
    notifications = NotificationRepository(str(tmp_path / "n.db"))
    await notifications.init_db()
    bot = MagicMock()
    bot.cogs = {}
    bot.get_channel.return_value = None
    bot.fetch_channel = AsyncMock(side_effect=RuntimeError("Unknown Channel"))
    api = ApiServer(repo=notifications, bot=bot, default_channel_id=1, host="127.0.0.1", port=0)
    api.console_conversations = conversations
    return api, conversations, bot


async def test_notify_posts_into_a_console_conversation(tmp_path) -> None:
    """A reminder job keeps working after its conversation moves to the console."""
    api, conversations, bot = await _notify_api(tmp_path)
    async with TestClient(TestServer(api.app)) as client:
        response = await client.post(
            "/api/notify",
            json={"channel_id": 4242, "message": "<@418192003549888523> 🌙 Evening BP?"},
        )
        assert response.status == 200
        assert (await response.json())["frontend"] == "console"
    messages = await conversations.history(4242, 10)
    assert [m.content for m in messages] == ["🌙 Evening BP?"]
    assert messages[0].is_bot
    bot.get_channel.assert_not_called()


async def test_notify_still_goes_to_discord_for_other_ids(tmp_path) -> None:
    api, conversations, bot = await _notify_api(tmp_path)
    async with TestClient(TestServer(api.app)) as client:
        response = await client.post("/api/notify", json={"channel_id": 999, "message": "hi"})
    assert response.status == 500
    bot.get_channel.assert_called_once_with(999)


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


async def test_live_and_stop_endpoints(env) -> None:
    item_id = await _start(env)
    live = await env.client.get(f"/console/api/items/{item_id}/live", headers=AUTH)
    assert live.status == 200
    assert (await live.json()) == {"running": False, "live": None}

    stop = await env.client.post(f"/console/api/items/{item_id}/stop", json={}, headers=WRITE)
    assert stop.status == 409

    pressed: list[bool] = []

    async def on_stop() -> None:
        pressed.append(True)

    surface = await env.host.frontend.resolve_surface(int(item_id[1:]))
    await surface.offer_interrupt(on_stop)
    stop = await env.client.post(f"/console/api/items/{item_id}/stop", json={}, headers=WRITE)
    assert stop.status == 202 and pressed == [True]
    history = await env.conversations.history(int(item_id[1:]), 10)
    assert history[-1].content == "⏹ Stopped by token"

    assert (await env.client.get("/console/api/items/t1/live", headers=AUTH)).status == 404


async def test_files_need_authentication_and_a_real_path(env) -> None:
    path = "/console/api/files/1/" + "0" * 32 + "/a.txt"
    assert (await env.client.get(path)).status == 401
    assert (await env.client.get(path, headers=AUTH)).status == 404


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


async def _upload(env, data: bytes = PNG, content_type: str = "image/png", name: str | None = None):
    headers = {**WRITE, "Content-Type": content_type}
    if name is not None:
        headers["X-Filename"] = quote(name)
    return await env.client.post("/console/api/uploads", data=data, headers=headers)


async def test_a_pasted_image_is_stored_and_served_back(env) -> None:
    response = await _upload(env)
    assert response.status == 201
    url = (await response.json())["url"]
    assert url.startswith("/console/api/files/0/") and url.endswith(".png")
    served = await env.client.get(url, headers=AUTH)
    assert served.status == 200
    assert await served.read() == PNG


async def test_images_are_checked_and_bounded(env) -> None:
    # Says PNG, is not: the bytes decide, not the header.
    assert (await _upload(env, b"GIF89a" + b"\x00" * 10, "image/png")).status == 400
    assert (await _upload(env, b"", "image/png")).status == 400
    too_big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (10 * 1024 * 1024)
    assert (await _upload(env, too_big)).status == 413
    assert (
        await env.client.post(
            "/console/api/uploads", data=PNG, headers={**AUTH, "Content-Type": "image/png"}
        )
    ).status == 403  # CSRF header still required


async def test_a_reply_hands_pasted_images_to_the_agent_by_path(env) -> None:
    item_id = await _start(env)
    url = (await (await _upload(env)).json())["url"]

    replied = await env.client.post(
        f"/console/api/items/{item_id}/reply",
        json={"text": "what is this?", "images": [url]},
        headers=WRITE,
    )
    assert replied.status == 202
    await _settle(env.host)

    prompt = env.runs[-1].prompt
    assert prompt.startswith("what is this?")
    # The agent gets a local path it can open; the transcript keeps a link that previews.
    assert f"]({url})" in prompt
    local = prompt.split(" — ")[-1].strip().splitlines()[0]
    assert local.endswith(".png") and local.startswith("/")


async def test_a_reply_may_be_only_an_image_but_not_a_forged_link(env) -> None:
    item_id = await _start(env)
    url = (await (await _upload(env)).json())["url"]
    only_image = await env.client.post(
        f"/console/api/items/{item_id}/reply", json={"images": [url]}, headers=WRITE
    )
    assert only_image.status == 202
    forged = await env.client.post(
        f"/console/api/items/{item_id}/reply",
        json={"text": "x", "images": ["/console/api/files/0/" + "0" * 32 + "/nope.png"]},
        headers=WRITE,
    )
    assert forged.status == 400


async def test_a_screenshot_larger_than_the_json_limit_is_accepted(env) -> None:
    screenshot = PNG + b"\x00" * (2 * 1024 * 1024)
    assert (await _upload(env, screenshot)).status == 201


async def test_any_file_is_kept_under_its_own_safe_name_and_served_as_a_download(env) -> None:
    response = await _upload(env, b"a,b\n1,2\n", "text/csv", "売上 2026(10月).csv")
    assert response.status == 201
    url = (await response.json())["url"]
    assert url.startswith("/console/api/files/0/") and " " not in url
    served = await env.client.get(url, headers=AUTH)
    assert served.status == 200
    assert await served.read() == b"a,b\n1,2\n"
    assert served.headers["Content-Type"] == "application/octet-stream"
    assert served.headers["Content-Disposition"].startswith("attachment;")


@pytest.mark.parametrize(
    "raw, kept",
    [
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\me\\report.pdf", "report.pdf"),
        (".bashrc", "bashrc"),
        ("a<b>:c?.txt", "a_b__c_.txt"),
        ("x" * 300 + ".log", None),
        ("", None),
        ("///", None),
    ],
)
def test_a_browser_file_name_becomes_a_bare_safe_name(raw, kept) -> None:
    from claude_discord.console.uploads import MAX_NAME_CHARS, safe_name

    name = safe_name(quote(raw))
    if raw.startswith("x"):
        assert name is not None and len(name) <= MAX_NAME_CHARS and name.endswith(".log")
    else:
        assert name == kept


async def test_other_files_have_a_larger_bound(env) -> None:
    from claude_discord.console.uploads import MAX_UPLOAD_BYTES

    big = b"\x00" * (12 * 1024 * 1024)
    assert (await _upload(env, big, "application/zip", "logs.zip")).status == 201
    too_big = b"\x00" * (MAX_UPLOAD_BYTES + 1)
    assert (await _upload(env, too_big, "application/zip", "huge.zip")).status == 413
    assert (await _upload(env, b"", "text/plain", "empty.txt")).status == 400


async def test_a_reply_hands_any_attachment_to_the_agent_by_path(env) -> None:
    item_id = await _start(env)
    pdf = (await (await _upload(env, b"%PDF-1.7", "application/pdf", "spec.pdf")).json())["url"]
    png = (await (await _upload(env)).json())["url"]
    replied = await env.client.post(
        f"/console/api/items/{item_id}/reply",
        json={"text": "read these", "files": [pdf], "images": [png]},
        headers=WRITE,
    )
    assert replied.status == 202
    await _settle(env.host)
    prompt = env.runs[-1].prompt
    assert "📎 Attached files" in prompt
    assert "spec.pdf" in prompt and ".png" in prompt
    too_many = await env.client.post(
        f"/console/api/items/{item_id}/reply",
        json={"text": "x", "files": [pdf] * 11},
        headers=WRITE,
    )
    assert too_many.status == 400
    not_a_list = await env.client.post(
        f"/console/api/items/{item_id}/reply", json={"text": "x", "files": 3}, headers=WRITE
    )
    assert not_a_list.status == 400


async def test_files_attached_to_a_capture_reach_the_agent(env) -> None:
    url = (await (await _upload(env, b"# spec", "text/markdown", "spec.md")).json())["url"]
    created = await env.client.post(
        "/console/api/items",
        json={"title": "Read the spec", "note": "carefully", "files": [url], "start": True},
        headers=WRITE,
    )
    assert created.status == 201
    await _settle(env.host)
    prompt = env.runs[-1].prompt
    assert prompt.startswith("Read the spec\n\ncarefully")
    assert "📎 Attached files" in prompt and "spec.md" in prompt
    forged = await env.client.post(
        "/console/api/items",
        json={"title": "x", "files": ["/etc/passwd"]},
        headers=WRITE,
    )
    assert forged.status == 400


class FakeSettings:
    """BackendSettings, in memory: a thread override per key, defaulting to claude."""

    def __init__(self) -> None:
        self.backend: dict[int, str] = {}
        self.model: dict[tuple[int, str], str] = {}
        self.effort: dict[tuple[int, str], str] = {}

    async def current_backend(self, thread_id=None):
        return self.backend.get(thread_id, "claude")

    async def current_model(self, backend, thread_id=None):
        return self.model.get((thread_id, backend))

    async def current_effort(self, backend, thread_id=None):
        return self.effort.get((thread_id, backend))

    async def set_backend(self, backend, *, thread_id=None):
        self.backend[thread_id] = backend

    async def set_model(self, backend, model, *, thread_id=None):
        self.model[(thread_id, backend)] = model

    async def set_effort(self, backend, effort, *, thread_id=None):
        self.effort[(thread_id, backend)] = effort


SKILLS = {
    "claude": [{"name": "kb-save", "description": "save a KB entry"}],
    "codex": [
        {"name": "kb-save", "description": "save a KB entry"},
        {"name": "codex-only", "description": "a Codex skill"},
    ],
}


async def test_slash_command_catalog_follows_the_backend(env) -> None:
    settings = FakeSettings()
    env.console.api.components.backend_settings = settings
    env.console._skills = lambda backend: SKILLS.get(backend, [])
    item_id = await _start(env)
    key = int(item_id[1:])

    got = await (
        await env.client.get(f"/console/api/items/{item_id}/commands", headers=AUTH)
    ).json()
    assert got["backend"] == "claude"
    assert [c["name"] for c in got["commands"]] == ["backend", "model", "effort"]
    assert [c["name"] for c in got["builtins"]] == ["compact", "context", "cost"]
    assert got["efforts"] == ["low", "medium", "high", "max"]
    assert [s["name"] for s in got["skills"]] == ["kb-save"]

    switched = await env.client.post(
        f"/console/api/items/{item_id}/command",
        json={"name": "backend", "arg": "codex"},
        headers=WRITE,
    )
    assert switched.status == 200
    body = await switched.json()
    assert settings.backend[key] == "codex"
    # What is offered next is the new backend's.
    assert body["catalog"]["backend"] == "codex"
    assert [b["name"] for b in body["catalog"]["builtins"]] == ["compact"]
    assert "xhigh" in body["catalog"]["efforts"]
    assert "codex-only" in [s["name"] for s in body["catalog"]["skills"]]


async def test_model_and_effort_are_set_for_this_conversation_only(env) -> None:
    settings = FakeSettings()
    env.console.api.components.backend_settings = settings
    env.console._skills = lambda backend: []
    item_id = await _start(env)
    key = int(item_id[1:])

    async def run(name, arg):
        return await env.client.post(
            f"/console/api/items/{item_id}/command", json={"name": name, "arg": arg}, headers=WRITE
        )

    assert (await run("model", "opus")).status == 200
    assert settings.model[(key, "claude")] == "opus"
    assert (await run("effort", "HIGH")).status == 200
    assert settings.effort[(key, "claude")] == "high"
    assert (await run("effort", "ultra")).status == 400  # not a Claude level
    assert (await run("backend", "gpt")).status == 400
    assert (await run("model", "two words")).status == 400
    assert (await run("nope", "")).status == 400
    assert None not in settings.backend  # never the global default


async def test_a_skill_is_sent_to_the_agent_in_the_backends_own_form(env) -> None:
    settings = FakeSettings()
    env.console.api.components.backend_settings = settings
    env.console._skills = lambda backend: SKILLS.get(backend, [])
    item_id = await _start(env)
    key = int(item_id[1:])

    ran = await env.client.post(
        f"/console/api/items/{item_id}/command",
        json={"name": "kb-save", "arg": "the CSP fix"},
        headers=WRITE,
    )
    assert ran.status == 202
    await _settle(env.host)
    assert env.runs[-1].prompt == "/kb-save the CSP fix"

    settings.backend[key] = "codex"
    await env.client.post(
        f"/console/api/items/{item_id}/command", json={"name": "codex-only"}, headers=WRITE
    )
    await _settle(env.host)
    assert env.runs[-1].prompt == 'Use the "codex-only" skill.'


async def test_compact_on_codex_runs_through_app_server_not_as_a_reply(env) -> None:
    settings = FakeSettings()
    env.console.api.components.backend_settings = settings
    env.console._skills = lambda backend: []
    item_id = await _start(env)
    key = int(item_id[1:])
    settings.backend[key] = "codex"
    sessions = FakeSessions()
    env.console.api.session_repo = sessions
    compacted: list[str] = []

    async def compactor(session_id, **_):
        compacted.append(session_id)

    env.console._codex_compact = compactor

    async def compact():
        return await env.client.post(
            f"/console/api/items/{item_id}/command", json={"name": "compact"}, headers=WRITE
        )

    got = await (
        await env.client.get(f"/console/api/items/{item_id}/commands", headers=AUTH)
    ).json()
    assert got["builtins"] == [
        {"name": "compact", "description": got["builtins"][0]["description"], "handled": True}
    ]
    # No Codex session yet (or one minted by another CLI): nothing to compact.
    assert (await compact()).status == 409
    sessions.records[key] = SimpleNamespace(session_id="0a1b-2c", backend="codex")
    env.console.api._running_thread_ids.return_value = {key}
    assert (await compact()).status == 409  # never under a running turn
    env.console.api._running_thread_ids.return_value = set()
    runs_before = len(env.runs)

    done = await compact()
    assert done.status == 200
    assert compacted == ["0a1b-2c"]
    assert len(env.runs) == runs_before  # the agent was not sent "/compact" as words


async def test_a_reply_waits_while_the_conversation_is_compacted(env) -> None:
    item_id = await _start(env)
    env.console._compacting.add(int(item_id[1:]))
    sent = await env.client.post(
        f"/console/api/items/{item_id}/reply", json={"text": "hi"}, headers=WRITE
    )
    assert sent.status == 409


async def test_compact_failure_is_reported(env) -> None:
    from claude_discord.console.codex_compact import CompactError

    settings = FakeSettings()
    env.console.api.components.backend_settings = settings
    item_id = await _start(env)
    key = int(item_id[1:])
    settings.backend[key] = "codex"
    sessions = FakeSessions()
    sessions.records[key] = SimpleNamespace(session_id="0a1b", backend="codex")
    env.console.api.session_repo = sessions

    async def broken(session_id, **_):
        raise CompactError("Codex refused: no rollout found")

    env.console._codex_compact = broken
    res = await env.client.post(
        f"/console/api/items/{item_id}/command", json={"name": "compact"}, headers=WRITE
    )
    assert res.status == 502
    assert "no rollout found" in (await res.json())["error"]
    assert key not in env.console._compacting


async def test_a_queued_turn_replays_after_restart_without_a_second_human_message(env) -> None:
    item_id = await _start(env)
    key = int(item_id[1:])

    await env.host.resume_queued(key, "and Tuesday?")
    await _settle(env.host)

    _, replayed = env.runs
    assert replayed.prompt == "and Tuesday?"
    assert replayed.session_id == "s1"
    response = await env.client.get(f"/console/api/items/{item_id}/messages", headers=AUTH)
    human = [m["content"] for m in (await response.json())["messages"] if not m["is_bot"]]
    assert human == ["Plan Sunday"]


async def test_replaying_an_unknown_conversation_raises(env) -> None:
    with pytest.raises(LookupError):
        await env.host.resume_queued(424242, "hello")


@pytest.mark.parametrize("length", [4000, 4001, 6000])
async def test_long_reply_preserves_full_text_in_downloadable_attachment(env, length) -> None:
    item_id = await _start(env)
    text = ("日本語🎉\n" * length)[: length - 1] + "終"
    response = await env.client.post(
        f"/console/api/items/{item_id}/reply", json={"text": text}, headers=WRITE
    )
    assert response.status == 202
    await _settle(env.host)
    prompt = env.runs[-1].prompt
    if length == 4000:
        assert prompt.startswith(text.strip())
        return
    assert len(prompt) < 4000
    files = list(env.host.frontend.files_dir.glob("0/*/message.txt"))
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8") == text.strip()
    assert str(files[0]) in prompt
    url = prompt.split("](", 1)[1].split(")", 1)[0]
    download = await env.client.get(url, headers=AUTH)
    assert download.status == 200
    assert await download.text() == text.strip()


async def test_long_capture_autostarts_with_full_note_as_attachment(env) -> None:
    text = "詳しい依頼\n" * 1500
    response = await env.client.post(
        "/console/api/items",
        json={"title": "Long request", "note": text, "start": True},
        headers=WRITE,
    )
    data = await response.json()
    assert response.status == 201
    assert "start_error" not in data
    await _settle(env.host)
    files = list(env.host.frontend.files_dir.glob("0/*/message.txt"))
    assert len(files) == 1
    assert files[0].read_text(encoding="utf-8") == text.strip()
    assert str(files[0]) in env.runs[-1].prompt


async def test_long_reply_keeps_existing_attachments(env) -> None:
    item_id = await _start(env)
    upload = await _upload(env, b"supporting context", "text/plain", "context.txt")
    url = (await upload.json())["url"]
    response = await env.client.post(
        f"/console/api/items/{item_id}/reply",
        json={"text": "x" * 4001, "files": [url]},
        headers=WRITE,
    )
    assert response.status == 202
    await _settle(env.host)
    assert "message.txt" in env.runs[-1].prompt
    assert url in env.runs[-1].prompt


async def test_combined_start_prompt_over_limit_becomes_attachment(env) -> None:
    response = await env.client.post(
        "/console/api/items",
        json={"title": "t" * 200, "note": "n" * 3900, "start": True},
        headers=WRITE,
    )
    assert response.status == 201
    assert "start_error" not in await response.json()
    await _settle(env.host)
    [file] = list(env.host.frontend.files_dir.glob("0/*/message.txt"))
    assert file.read_text(encoding="utf-8") == "t" * 200 + "\n\n" + "n" * 3900
    assert str(file) in env.runs[-1].prompt


async def test_switching_backend_carries_the_conversation_into_the_new_session(env) -> None:
    item_id = await _start(env)
    reads: list[tuple[str, str]] = []

    class Reader:
        def read(self, backend: str, session_id: str) -> str:
            reads.append((backend, session_id))
            return "User:\nPlan Sunday\n\nAssistant:\nanswer to: Plan Sunday"

    env.host._conversation_history = Reader()
    env.host._settings.current_backend = AsyncMock(return_value="codex")
    sent = await env.client.post(
        f"/console/api/items/{item_id}/reply", json={"text": "and Monday?"}, headers=WRITE
    )
    assert sent.status == 202
    await _settle(env.host)

    resumed = env.runs[-1]
    assert reads == [("claude", "s1")]
    assert resumed.session_id is None  # Codex cannot resume a Claude session ID
    assert "answer to: Plan Sunday" in resumed.prompt
    assert resumed.prompt.endswith("Current user message:\nand Monday?")


async def test_a_reply_goes_into_the_turn_already_running(env) -> None:
    """Issue #1129: the agent reads a reply mid-turn instead of after it."""
    from claude_discord import live_runs

    item_id = await _start(env)
    key = int(item_id[1:])
    received: list[str] = []

    class _Live:
        async def inject_user_message(self, text: str, images=None) -> bool:
            received.append(text)
            return True

    run = live_runs.LiveRun(runner=_Live())
    live_runs.register(key, run)
    try:
        replied = await env.client.post(
            f"/console/api/items/{item_id}/reply", json={"text": "also Monday"}, headers=WRITE
        )
        await _settle(env.host)
    finally:
        live_runs.unregister(key, run)

    assert (await replied.json())["delivery"] == "injected"
    assert received == ["also Monday"]
    assert len(env.runs) == 1, "no second turn is started for an injected reply"
    messages = (
        await (await env.client.get(f"/console/api/items/{item_id}/messages", headers=AUTH)).json()
    )["messages"]
    assert (False, "also Monday") in [(m["is_bot"], m["content"]) for m in messages]


async def test_a_reply_queues_when_the_running_turn_cannot_take_it(env) -> None:
    item_id = await _start(env)

    replied = await env.client.post(
        f"/console/api/items/{item_id}/reply", json={"text": "and Monday?"}, headers=WRITE
    )
    await _settle(env.host)

    assert (await replied.json())["delivery"] == "queued"
    assert len(env.runs) == 2


async def test_a_later_reply_does_not_overtake_one_already_waiting(env) -> None:
    """Codex review of #1129: initial → B → A reordered a correction before its subject."""
    import asyncio

    from claude_discord import live_runs

    item_id = await _start(env)
    key = int(item_id[1:])
    received: list[str] = []

    class _Live:
        async def inject_user_message(self, text: str, images=None) -> bool:
            received.append(text)
            return True

    lock = env.host._locks.setdefault(key, asyncio.Lock())
    await lock.acquire()  # a turn is running
    try:
        await env.client.post(
            f"/console/api/items/{item_id}/reply", json={"text": "A"}, headers=WRITE
        )
        run = live_runs.LiveRun(runner=_Live())
        live_runs.register(key, run)
        try:
            replied = await env.client.post(
                f"/console/api/items/{item_id}/reply", json={"text": "B"}, headers=WRITE
            )
        finally:
            live_runs.unregister(key, run)
    finally:
        lock.release()
    await _settle(env.host)

    assert (await replied.json())["delivery"] == "queued"
    assert received == []
    assert [r.prompt for r in env.runs[1:]] == ["A", "B"]
