"""Ebi AI Console messages count toward the operator's attention (docs/attention.md).

The chat frontends tell a person from the relay by the author. The Console
posts its replies as the bot and stores the relay's own turns (waits,
restarts, relays) under plain names, so it meters at the entry point instead.
These tests pin both halves: what a person types is recorded, what the relay
injects is not, and history re-derives the same rows without doubling them.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_code_core.attention import SOURCE_BACKFILL, AttentionConfig
from claude_code_core.attention_repo import AttentionRecorder, HumanActivityRepository
from claude_discord.console.attention import backfill_console, is_human_author, measure
from claude_discord.console.auth import ConsoleAuthConfig, ConsoleAuthenticator
from claude_discord.console.conversations import CONSOLE_FRONTEND, ConversationRepository
from claude_discord.console.host import ConsoleSessionHost
from claude_discord.console.server import CSRF_HEADER, RESUME_PROMPT, ConsoleServer
from claude_discord.console.surface import ConsoleFrontend
from claude_discord.console.uploads import with_attachments
from claude_discord.console.work_repo import WorkItemRepository
from claude_discord.database.frontend_thread_repo import FrontendThreadRepository
from claude_discord.database.models import init_db

TOKEN = "k" * 40
WRITE = {"Authorization": f"Bearer {TOKEN}", CSRF_HEADER: "1"}
WIDE = (datetime(2000, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC))


class FakeSessions:
    def __init__(self) -> None:
        self.records: dict[int, SimpleNamespace] = {}

    async def get(self, key: int) -> SimpleNamespace | None:
        return self.records.get(key)


async def _env(tmp_path, *, console_author: str | None = None, metering: bool = True):
    db = str(tmp_path / "sessions.db")
    await init_db(db)
    work = WorkItemRepository(db)
    await work.init_db()
    conversations = ConversationRepository(db)
    await conversations.init_db()
    activity = HumanActivityRepository(db)
    await activity.init_db()
    recorder = AttentionRecorder(activity, AttentionConfig(console_author=console_author))
    frontend = ConsoleFrontend(
        conversations, FrontendThreadRepository(db), files_dir=tmp_path / "console_files"
    )
    sessions = FakeSessions()
    runs: list = []

    async def run_session(config) -> str | None:
        runs.append(config)
        await config.surface.send_text("ok")
        sessions.records[config.surface.thread_key] = SimpleNamespace(
            session_id="s", backend="claude", working_dir=None
        )
        return "s"

    settings = MagicMock()
    settings.current_backend = AsyncMock(return_value="claude")
    settings.current_model = AsyncMock(return_value=None)
    settings.current_effort = AsyncMock(return_value=None)
    settings.repo = None
    host = ConsoleSessionHost(
        frontend=frontend,
        session_repo=sessions,
        backend_factory=MagicMock(),
        backend_settings=settings,
        run_session=run_session,
        attention_recorder=recorder if metering else None,
    )
    api = MagicMock()
    api.default_channel_id = None
    api.lineage_repo = None
    api.session_repo = None
    api.bot.get_channel.return_value = None
    api.bot.cogs = {}
    api._running_thread_ids.return_value = set()
    api.components = SimpleNamespace(attention_recorder=recorder)
    console = ConsoleServer(
        api,
        work,
        ConsoleAuthenticator(ConsoleAuthConfig(token=TOKEN)),
        port=0,
        conversations=conversations,
        host_session=host,
    )
    return SimpleNamespace(
        db=db, host=host, console=console, activity=activity, runs=runs, recorder=recorder
    )


@pytest.fixture
async def env(tmp_path):
    built = await _env(tmp_path)
    async with TestClient(TestServer(built.console.app)) as client:
        built.client = client
        yield built


async def _settle(host: ConsoleSessionHost) -> None:
    while host._tasks:
        await asyncio.gather(*list(host._tasks))


async def _start(env, title: str = "Plan Sunday") -> str:
    created = await env.client.post("/console/api/items", json={"title": title}, headers=WRITE)
    item = (await created.json())["item"]["id"]
    started = await env.client.post(f"/console/api/items/{item}/start", json={}, headers=WRITE)
    assert started.status == 201
    await _settle(env.host)
    return (await started.json())["item"]["id"]


async def _rows(env):
    return await env.activity.list_between(*WIDE)


async def _human_message_ids(db: str) -> list[str]:
    import aiosqlite

    async with aiosqlite.connect(db) as conn:
        cursor = await conn.execute("SELECT id FROM console_messages WHERE is_bot = 0 ORDER BY id")
        return [str(row[0]) for row in await cursor.fetchall()]


# -- pure helpers ---------------------------------------------------------


def test_measure_counts_typing_and_attachments_separately(tmp_path) -> None:
    files = [("/f/a/x.png", tmp_path / "x.png"), ("/f/b/y.pdf", tmp_path / "y.pdf")]

    assert measure("hello") == (5, 0)
    assert measure(with_attachments("hello", files)) == (5, 2)
    assert measure(with_attachments("", files[:1])) == (0, 1)
    assert measure("") == (0, 0)


@pytest.mark.parametrize(
    ("author", "human"),
    [
        ("oidc:someone@example.com", True),
        ("token", True),
        ("passkey:laptop", True),
        ("wait", False),
        ("restart", False),
        ("thread 1566810794161602560", False),
        ("", False),
    ],
)
def test_only_signed_in_people_are_human_authors(author: str, human: bool) -> None:
    assert is_human_author(author) is human


# -- live recording ---------------------------------------------------------


async def test_starting_and_replying_from_the_console_are_recorded(env) -> None:
    item_id = await _start(env, "Plan Sunday")
    replied = await env.client.post(
        f"/console/api/items/{item_id}/reply", json={"text": "and Monday?"}, headers=WRITE
    )
    assert replied.status == 202
    await _settle(env.host)

    rows = await _rows(env)
    assert [(r.frontend, r.char_count, r.author_id) for r in rows] == [
        (CONSOLE_FRONTEND, len("Plan Sunday"), "token"),
        (CONSOLE_FRONTEND, len("and Monday?"), "token"),
    ]
    key = item_id[1:]
    assert {r.conversation_id for r in rows} == {key}
    assert {r.thread_title for r in rows} == {"Plan Sunday"}
    # Keyed by the transcript's own ids, the key backfill derives from history.
    assert [r.message_id for r in rows] == await _human_message_ids(env.db)


async def test_relay_injected_turns_are_not_recorded(env) -> None:
    item_id = await _start(env)
    key = int(item_id[1:])
    before = len(await _rows(env))

    await env.host.reply(key, "CI finished: success", "wait")
    await env.host.reply(key, "🔄 Bot restarted.\ncarry on", "restart")
    await env.host.relay(key, "[MESSAGE FROM ANOTHER AI SESSION]", "thread 42", interrupt=False)
    resumed = await env.client.post(f"/console/api/items/{item_id}/resume", headers=WRITE)
    assert resumed.status == 202
    await _settle(env.host)

    assert len(await _rows(env)) == before
    assert any(run.prompt == RESUME_PROMPT for run in env.runs)


async def test_the_console_author_setting_merges_with_the_chat_identity(tmp_path) -> None:
    built = await _env(tmp_path, console_author="123456789")
    async with TestClient(TestServer(built.console.app)) as client:
        built.client = client
        await _start(built)

    [row] = await _rows(built)
    assert row.author_id == "123456789"


async def test_a_console_reply_into_a_chat_thread_counts_under_that_thread(env) -> None:
    thread = SimpleNamespace(id=987654321, name="A Discord thread")

    await env.console._meter_chat_reply(thread, "looks good, merge it", "token")

    [row] = await _rows(env)
    assert (row.frontend, row.conversation_id, row.thread_title) == (
        "discord",
        "987654321",
        "A Discord thread",
    )
    assert row.char_count == len("looks good, merge it")
    assert row.message_id.startswith("console-")


async def test_metering_switched_off_records_nothing(tmp_path) -> None:
    built = await _env(tmp_path)
    built.recorder.config = AttentionConfig(enabled=False)
    async with TestClient(TestServer(built.console.app)) as client:
        built.client = client
        await _start(built)

    assert await _rows(built) == []


# -- backfill ---------------------------------------------------------------


async def test_backfill_recovers_what_was_typed_and_nothing_else(tmp_path) -> None:
    built = await _env(tmp_path, metering=False)
    repo = ConversationRepository(built.db)
    await repo.create(7, "Old work")
    typed = [
        ("oidc:someone@example.com", "first question"),
        ("token", "second one"),
    ]
    for author, content in typed:
        await repo.append(7, author=author, is_bot=False, content=content)
    for author, content in [
        ("wait", "CI finished"),
        ("restart", "🔄 Bot restarted."),
        ("thread 42", "a relay"),
        ("token", "⏹ Stopped by token"),
        ("token", RESUME_PROMPT),
    ]:
        await repo.append(7, author=author, is_bot=False, content=content)
    await repo.append(7, author="agent", is_bot=True, content="an answer")
    await repo.append(7, author="Ebi Workspace", is_bot=True, content="a brief")

    stats = await backfill_console(
        built.db, built.activity, since=WIDE[0], until=WIDE[1], skip_contents=(RESUME_PROMPT,)
    )

    assert (stats.human_messages, stats.inserted) == (2, 2)
    rows = await _rows(built)
    assert [(r.author_id, r.char_count, r.source, r.thread_title) for r in rows] == [
        ("oidc:someone@example.com", len("first question"), SOURCE_BACKFILL, "Old work"),
        ("token", len("second one"), SOURCE_BACKFILL, "Old work"),
    ]


async def test_backfill_does_not_double_what_was_recorded_live(env) -> None:
    item_id = await _start(env)
    await env.client.post(
        f"/console/api/items/{item_id}/reply", json={"text": "more"}, headers=WRITE
    )
    await _settle(env.host)

    stats = await backfill_console(env.db, env.activity, since=WIDE[0], until=WIDE[1])

    assert (stats.human_messages, stats.already_recorded, stats.inserted) == (2, 2, 0)
    assert len(await _rows(env)) == 2


async def test_backfill_honours_the_range_and_the_author_override(tmp_path) -> None:
    built = await _env(tmp_path, metering=False)
    repo = ConversationRepository(built.db)
    await repo.create(8, "Range")
    await repo.append(8, author="token", is_bot=False, content="inside")
    now = datetime.now(UTC)

    outside = await backfill_console(
        built.db, built.activity, since=now + timedelta(hours=1), until=now + timedelta(hours=2)
    )
    inside = await backfill_console(
        built.db,
        built.activity,
        since=now - timedelta(hours=1),
        until=now + timedelta(hours=1),
        author_id="owner",
    )

    assert (outside.scanned, inside.inserted) == (0, 1)
    [row] = await _rows(built)
    assert row.author_id == "owner"
