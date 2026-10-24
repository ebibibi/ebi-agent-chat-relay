"""Console turns use the account pool, and the transcript names each answer's model.

A conversation started from the console used to run on the relay's own login
even when ``CCDB_ACCOUNT_POOLS_FILE`` configured a pool, so a Codex account at
its limit could not hand over to the next one. These tests pin that the
console host plans, records and fails over a turn the way a Discord thread does.
"""

from __future__ import annotations

import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_code_core.account_pool import AccountBinding
from claude_code_core.account_router import Failover, TurnPlan
from claude_discord.console.conversations import ConversationRepository
from claude_discord.console.host import ConsoleSessionHost
from claude_discord.console.surface import ConsoleFrontend
from claude_discord.database.frontend_thread_repo import FrontendThreadRepository
from claude_discord.database.models import init_db


class FakeSessions:
    def __init__(self) -> None:
        self.records: dict[int, SimpleNamespace] = {}

    async def get(self, key: int) -> SimpleNamespace | None:
        return self.records.get(key)


def _plan(profile: str, *, moved_from: str | None = None) -> TurnPlan:
    return TurnPlan(
        binding=AccountBinding(
            backend="codex", profile=profile, env={"CODEX_HOME": f"/h/{profile}"}
        ),
        selection=SimpleNamespace(all_exhausted=False),  # type: ignore[arg-type]
        session_id=None,
        moved_from=moved_from,
    )


async def _settle(host: ConsoleSessionHost) -> None:
    while host._tasks:
        await asyncio.gather(*list(host._tasks), return_exceptions=True)


@pytest.fixture
async def env(tmp_path, monkeypatch):
    db = str(tmp_path / "sessions.db")
    await init_db(db)
    conversations = ConversationRepository(db)
    await conversations.init_db()
    frontend = ConsoleFrontend(conversations, FrontendThreadRepository(db))

    # The Codex quota probe would start a real ``codex app-server``.
    probe = AsyncMock()
    monkeypatch.setattr("claude_discord.console.host.refresh_codex_usage", probe)

    runs: list = []
    sessions = FakeSessions()

    async def run_session(config) -> str | None:
        runs.append(config)
        await config.surface.send_text(f"answer {len(runs)}")
        sessions.records[config.surface.thread_key] = SimpleNamespace(
            session_id=f"s{len(runs)}", backend="codex", working_dir=None
        )
        return f"s{len(runs)}"

    settings = MagicMock()
    settings.current_backend = AsyncMock(return_value="codex")
    settings.current_model = AsyncMock(return_value="gpt-5.5")
    settings.current_effort = AsyncMock(return_value=None)
    settings.repo = None
    factory = MagicMock()
    factory.codex_command = "codex"
    factory.build.side_effect = lambda **kw: SimpleNamespace(
        model=kw.get("model"), account=None, command="codex", working_dir=None
    )

    router = MagicMock()
    router.pinned_home = AsyncMock(return_value=None)
    router.plan_turn = AsyncMock(return_value=_plan("work"))
    router.finish_turn = AsyncMock(return_value=None)

    def make_host(account_router=router):
        return ConsoleSessionHost(
            frontend=frontend,
            session_repo=sessions,
            backend_factory=factory,
            backend_settings=settings,
            run_session=run_session,
            account_router=account_router,
        )

    yield SimpleNamespace(
        make_host=make_host,
        runs=runs,
        router=router,
        probe=probe,
        conversations=conversations,
        settings=settings,
        sessions=sessions,
        db=db,
    )


async def _transcript(env, key: int) -> list:
    return await env.conversations.history(key, 50)


async def test_console_turn_runs_on_the_pool_profile(env) -> None:
    host = env.make_host()
    key = await host.start(external_id="w1", title="t", prompt="hi", author="me")
    await _settle(host)

    [run] = env.runs
    env.router.plan_turn.assert_awaited_once_with(backend="codex", thread_id=key, session_id=None)
    assert run.runner.account.profile == "work"
    assert run.account_turn is not None and run.account_turn.binding.profile == "work"
    env.router.finish_turn.assert_awaited_once_with(run.account_turn)
    # Codex reports no quota in its stream; the probe is what fills the pool's usage.
    env.probe.assert_awaited_once()


async def test_console_turn_moved_to_another_profile_says_so(env) -> None:
    env.router.plan_turn.return_value = _plan("spare", moved_from="work")
    host = env.make_host()
    key = await host.start(external_id="w2", title="t", prompt="hi", author="me")
    await _settle(host)

    texts = [m.content for m in await _transcript(env, key)]
    assert any("`work` → `spare`" in t for t in texts)


async def test_console_turn_retries_once_on_the_next_profile(env) -> None:
    env.router.finish_turn.side_effect = [
        Failover("work", 2_000_000_000, next_profile="spare", retry=True),
        Failover("spare", 2_000_000_000, next_profile=None, retry=True),
    ]
    host = env.make_host()
    key = await host.start(external_id="w3", title="t", prompt="hi", author="me")
    await _settle(host)

    # The original turn and exactly one retry, both with the person's prompt.
    assert [r.prompt for r in env.runs] == ["hi", "hi"]
    notices = [m.content for m in await _transcript(env, key) if "usage limit" in m.content]
    assert len(notices) == 2
    # The console cannot render Discord's <t:…:R> timestamps.
    assert all("<t:" not in n for n in notices)
    assert "Retrying this turn on `spare`" in notices[0]


async def test_planning_failure_still_runs_the_turn(env) -> None:
    env.router.plan_turn.side_effect = RuntimeError("pool db locked")
    host = env.make_host()
    await host.start(external_id="w4", title="t", prompt="hi", author="me")
    await _settle(host)

    [run] = env.runs
    assert run.account_turn is None
    assert run.runner.account is None


async def test_without_a_pool_nothing_changes(env) -> None:
    host = env.make_host(account_router=None)
    await host.start(external_id="w5", title="t", prompt="hi", author="me")
    await _settle(host)

    [run] = env.runs
    assert run.account_turn is None
    env.probe.assert_not_awaited()


async def test_agent_messages_carry_the_model(env) -> None:
    host = env.make_host(account_router=None)
    key = await host.start(external_id="w6", title="t", prompt="hi", author="me")
    await _settle(host)

    by_author = {m.author: m.as_dict()["model"] for m in await _transcript(env, key)}
    assert by_author == {"me": None, "agent": "gpt-5.5"}


async def test_without_a_model_the_backend_is_shown(env) -> None:
    env.settings.current_model.return_value = None
    host = env.make_host(account_router=None)
    key = await host.start(external_id="w7", title="t", prompt="hi", author="me")
    await _settle(host)

    [agent] = [m for m in await _transcript(env, key) if m.is_bot]
    assert agent.model == "codex"


async def test_an_existing_transcript_gains_the_model_column(tmp_path) -> None:
    db = str(tmp_path / "old.db")
    with sqlite3.connect(db) as conn:
        conn.executescript(
            "CREATE TABLE console_messages (id INTEGER PRIMARY KEY AUTOINCREMENT,"
            " thread_key INTEGER NOT NULL, author TEXT NOT NULL, is_bot INTEGER NOT NULL,"
            " content TEXT NOT NULL, created_at TEXT NOT NULL);"
            "INSERT INTO console_messages (thread_key, author, is_bot, content, created_at)"
            " VALUES (1, 'agent', 1, 'old answer', '2026-01-01T00:00:00Z');"
        )
    repo = ConversationRepository(db)
    await repo.init_db()
    await repo.init_db()  # idempotent

    await repo.append(1, author="agent", is_bot=True, content="new", model="opus")
    assert [(m.content, m.model) for m in await repo.history(1, 10)] == [
        ("old answer", None),
        ("new", "opus"),
    ]


async def test_backend_switch_reads_the_transcript_from_the_pool_profile(
    env, tmp_path, monkeypatch
) -> None:
    """The old session ran as a pool profile; its text is read from that profile's home."""
    readers: list = []

    async def carry(reader, record, backend, prompt):
        readers.append(reader)
        return prompt

    monkeypatch.setattr("claude_discord.console.host.carry_history_across_backends", carry)
    profile_home = tmp_path / "claude-work"
    env.router.pinned_home.return_value = profile_home
    host = env.make_host()
    key = await host.start(external_id="w8", title="t", prompt="hi", author="me")
    await _settle(host)
    env.sessions.records[key] = SimpleNamespace(
        session_id="s-claude", backend="claude", working_dir=None
    )
    await host.reply(key, "next", "me")
    await _settle(host)

    env.router.pinned_home.assert_awaited_with(key, "claude")
    assert readers[-1]._claude_sessions_root == profile_home / "projects"
