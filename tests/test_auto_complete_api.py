"""Evidence-based auto-completion — measuring facts and finishing after the turn (#1477)."""

from __future__ import annotations

import asyncio
import os
import tempfile
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.auto_complete import (
    MODE_DRY_RUN,
    MODE_OFF,
    MODE_ON,
    SCOPE_CHILDREN,
    AutoCompletePolicy,
    Evidence,
)
from claude_discord.database.lineage_repo import ThreadLineageRepository
from claude_discord.database.models import init_db
from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.ext.api_server import ApiServer
from claude_discord.ext.auto_complete_api import AutoCompleter

PARENT = 11
THREAD = 12
CHILD = 13
CANDIDATE_TITLE = "\U0001f3c1 work"

EVIDENCE = Evidence(
    deliverables=("https://github.com/o/r/pull/1",),
    verification=("checks green",),
    within_scope=True,
)
EVIDENCE_BODY = {
    "evidence": {
        "deliverables": ["https://github.com/o/r/pull/1"],
        "verification": ["checks green"],
        "within_scope": True,
    }
}


@dataclass
class FakeHost:
    running: set[int] = field(default_factory=set)
    titles: dict[int, str] = field(default_factory=dict)
    done: set[int] = field(default_factory=set)
    parents: dict[int, int] = field(default_factory=lambda: {THREAD: PARENT})
    waits: dict[int, int] = field(default_factory=dict)
    requests: dict[int, int] = field(default_factory=dict)
    teams: set[int] = field(default_factory=set)
    notes: list[tuple[int, str]] = field(default_factory=list)
    completed: list[int] = field(default_factory=list)

    def running_thread_ids(self) -> set[int]:
        return set(self.running)

    async def thread_title(self, thread_id: int) -> str | None:
        return self.titles.get(thread_id)

    async def human_done(self, thread_id: int) -> bool:
        return thread_id in self.done

    async def parent_of(self, thread_id: int) -> int | None:
        return self.parents.get(thread_id)

    async def children_of(self, thread_id: int) -> list[int]:
        return [c for c, p in self.parents.items() if p == thread_id]

    async def active_waits(self, thread_id: int) -> int:
        return self.waits.get(thread_id, 0)

    async def open_requests(self, thread_id: int) -> int:
        return self.requests.get(thread_id, 0)

    async def teams_reply_draft(self, thread_id: int) -> bool:
        return thread_id in self.teams

    async def post_note(self, thread_id: int, text: str) -> None:
        self.notes.append((thread_id, text))

    async def complete(self, thread_id: int) -> None:
        self.completed.append(thread_id)


def _completer(host: FakeHost, mode: str = MODE_ON) -> AutoCompleter:
    policy = AutoCompletePolicy(mode=mode, scope=SCOPE_CHILDREN)
    return AutoCompleter(
        host,
        policy=lambda: policy,
        poll_seconds=0.001,
        settle_seconds=0,
        title_timeout_seconds=0.05,
        idle_timeout_seconds=1,
    )


async def _drain(completer: AutoCompleter) -> None:
    await asyncio.wait_for(completer.join(), timeout=2)


class TestAssess:
    async def test_off_does_not_measure(self) -> None:
        host = FakeHost()
        verdict = await _completer(host, MODE_OFF).assess(THREAD, EVIDENCE)
        assert verdict.mode == MODE_OFF
        assert verdict.complete is False

    async def test_measures_waits_requests_and_children(self) -> None:
        host = FakeHost(
            parents={THREAD: PARENT, CHILD: THREAD},
            waits={THREAD: 1},
            requests={CHILD: 1},
            titles={CHILD: "child"},
        )
        verdict = await _completer(host).assess(THREAD, EVIDENCE)
        text = " ".join(verdict.reasons)
        assert "active wait" in text
        assert f"child thread {CHILD} is not finished" in text
        assert f"child thread {CHILD} has an open decision request" in text

    async def test_a_child_marked_candidate_or_done_counts_as_finished(self) -> None:
        host = FakeHost(parents={THREAD: PARENT, CHILD: THREAD, 14: THREAD})
        host.titles[CHILD] = CANDIDATE_TITLE
        host.done.add(14)
        verdict = await _completer(host).assess(THREAD, EVIDENCE)
        assert verdict.complete is True

    async def test_teams_reply_draft_is_refused(self) -> None:
        host = FakeHost(teams={THREAD})
        verdict = await _completer(host).assess(THREAD, EVIDENCE)
        assert verdict.complete is False


class TestFinish:
    async def test_completes_after_the_turn_ends(self) -> None:
        host = FakeHost(running={THREAD}, titles={THREAD: CANDIDATE_TITLE})
        completer = _completer(host)
        completer.schedule(THREAD, EVIDENCE)
        await asyncio.sleep(0.01)
        assert host.completed == []  # the turn is still running
        host.running.clear()
        await _drain(completer)
        assert host.completed == [THREAD]
        # The note comes first, so its own message cannot make the verdict stale.
        assert host.notes and host.notes[0][0] == THREAD
        assert "https://github.com/o/r/pull/1" in host.notes[0][1]
        assert "reopen" in host.notes[0][1].lower()

    async def test_a_human_reply_cancels(self) -> None:
        # The reply removes the 🏁 and starts a new turn.
        host = FakeHost(titles={THREAD: "work"})
        completer = _completer(host)
        completer.schedule(THREAD, EVIDENCE)
        await asyncio.sleep(0.005)
        host.running.add(THREAD)
        await _drain(completer)
        assert host.completed == []

    async def test_another_marker_cancels(self) -> None:
        host = FakeHost(titles={THREAD: "❓ work"})
        completer = _completer(host)
        completer.schedule(THREAD, EVIDENCE)
        await _drain(completer)
        assert host.completed == []

    async def test_waits_for_a_rename_that_lands_late(self) -> None:
        host = FakeHost(titles={THREAD: "work"})
        completer = _completer(host)
        completer.schedule(THREAD, EVIDENCE)
        await asyncio.sleep(0.005)
        host.titles[THREAD] = CANDIDATE_TITLE
        await _drain(completer)
        assert host.completed == [THREAD]

    async def test_rechecks_the_facts_at_the_end(self) -> None:
        host = FakeHost(titles={THREAD: CANDIDATE_TITLE}, waits={THREAD: 1})
        completer = _completer(host)
        completer.schedule(THREAD, EVIDENCE)
        await _drain(completer)
        assert host.completed == []

    async def test_dry_run_never_completes(self) -> None:
        host = FakeHost(titles={THREAD: CANDIDATE_TITLE})
        completer = _completer(host, MODE_DRY_RUN)
        completer.schedule(THREAD, EVIDENCE)
        await _drain(completer)
        assert host.completed == []

    async def test_a_new_candidate_replaces_the_pending_one(self) -> None:
        host = FakeHost(running={THREAD}, titles={THREAD: CANDIDATE_TITLE})
        completer = _completer(host)
        completer.schedule(THREAD, EVIDENCE)
        completer.schedule(THREAD, EVIDENCE)
        host.running.clear()
        await _drain(completer)
        assert host.completed == [THREAD]

    async def test_a_failing_host_is_logged_not_raised(self) -> None:
        host = FakeHost(titles={THREAD: CANDIDATE_TITLE})
        host.complete = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
        completer = _completer(host)
        completer.schedule(THREAD, EVIDENCE)
        await _drain(completer)


# --- the /candidate endpoint -------------------------------------------------


@pytest.fixture
async def api_setup(monkeypatch: pytest.MonkeyPatch):
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(path)
    repo = NotificationRepository(path)
    await repo.init_db()
    lineage = ThreadLineageRepository(path)
    await lineage.record(THREAD, PARENT, "AB")
    bot = MagicMock()
    bot.cogs = {"ClaudeChatCog": MagicMock(_active_runners={})}
    bot.session_registry = None
    api = ApiServer(
        repo=repo, bot=bot, default_channel_id=1, host="127.0.0.1", port=0, lineage_repo=lineage
    )
    api._apply_thread_outcome = AsyncMock(  # type: ignore[method-assign]
        return_value=__import__("aiohttp").web.json_response(
            {"status": "marked", "thread_name": CANDIDATE_TITLE}
        )
    )
    scheduled: list[int] = []
    api.auto_completer.schedule = lambda tid, ev: scheduled.append(tid)  # type: ignore[method-assign]
    client = TestClient(TestServer(api.app))
    await client.start_server()
    yield client, api, scheduled, monkeypatch
    await client.close()
    os.unlink(path)


async def test_candidate_without_evidence_is_unchanged_when_off(api_setup) -> None:
    client, _, scheduled, monkeypatch = api_setup
    monkeypatch.delenv("CCDB_AUTO_COMPLETE", raising=False)
    resp = await client.post(f"/api/threads/{THREAD}/candidate")
    assert resp.status == 200
    assert "auto_complete" not in await resp.json()
    assert scheduled == []


async def test_candidate_with_evidence_schedules_when_on(api_setup) -> None:
    client, _, scheduled, monkeypatch = api_setup
    monkeypatch.setenv("CCDB_AUTO_COMPLETE", "on")
    resp = await client.post(f"/api/threads/{THREAD}/candidate", json=EVIDENCE_BODY)
    assert resp.status == 200
    body = await resp.json()
    assert body["auto_complete"]["complete"] is True
    assert body["auto_complete"]["scheduled"] is True
    assert scheduled == [THREAD]


async def test_candidate_reports_reasons_in_dry_run(api_setup) -> None:
    client, _, scheduled, monkeypatch = api_setup
    monkeypatch.setenv("CCDB_AUTO_COMPLETE", "dry-run")
    resp = await client.post(f"/api/threads/{THREAD}/candidate", json=EVIDENCE_BODY)
    body = await resp.json()
    assert body["auto_complete"]["eligible"] is True
    assert body["auto_complete"]["scheduled"] is False
    assert scheduled == []


async def test_malformed_evidence_is_refused_before_the_mark(api_setup) -> None:
    client, api, _, monkeypatch = api_setup
    monkeypatch.setenv("CCDB_AUTO_COMPLETE", "on")
    resp = await client.post(
        f"/api/threads/{THREAD}/candidate", json={"evidence": {"deliverables": 3}}
    )
    assert resp.status == 400
    api._apply_thread_outcome.assert_not_awaited()


async def test_lineage_children_of(tmp_path) -> None:
    path = str(tmp_path / "l.db")
    await init_db(path)
    lineage = ThreadLineageRepository(path)
    await lineage.record(CHILD, THREAD, "AB")
    await lineage.record(14, THREAD, "AB")
    await lineage.record(15, CHILD, "AB")
    assert await lineage.children_of(THREAD) == [CHILD, 14]
    assert await lineage.children_of(99) == []


async def test_host_counts_requests_on_either_side_and_deferred_ones() -> None:
    from types import SimpleNamespace

    from claude_discord.ext.auto_complete_api import ApiCompletionHost

    open_cards = [
        SimpleNamespace(thread_id=THREAD, source_thread_id=THREAD, deferred_at=None),
        SimpleNamespace(thread_id=PARENT, source_thread_id=THREAD, deferred_at=None),
        SimpleNamespace(thread_id=THREAD, source_thread_id=THREAD, deferred_at="2026-11-21"),
        SimpleNamespace(thread_id=99, source_thread_id=99, deferred_at=None),
    ]
    api = MagicMock()
    api.decision_repo.list_open = AsyncMock(return_value=open_cards)
    api.wait_repo.list_active = AsyncMock(return_value=[object(), object()])
    api.session_repo.get = AsyncMock(return_value=None)
    host = ApiCompletionHost(api)
    assert await host.open_requests(THREAD) == 3
    assert await host.active_waits(THREAD) == 2
    assert await host.teams_reply_draft(THREAD) is False


async def test_host_posts_a_mention_free_note_in_a_discord_thread() -> None:
    import discord

    from claude_discord.ext.auto_complete_api import ApiCompletionHost

    channel = MagicMock()
    channel.send = AsyncMock()
    api = MagicMock()
    api.console_conversations = None
    api.bot.get_channel.return_value = channel
    await ApiCompletionHost(api).post_note(THREAD, "@everyone done")
    kwargs = channel.send.await_args.kwargs
    assert kwargs["allowed_mentions"].everyone is False
    assert isinstance(kwargs["allowed_mentions"], discord.AllowedMentions)


async def test_pending_set_follows_the_scheduled_completion() -> None:
    from claude_discord.ext.auto_complete_api import auto_completion_pending

    host = FakeHost(running={THREAD}, titles={THREAD: CANDIDATE_TITLE})
    completer = _completer(host)
    completer.schedule(THREAD, EVIDENCE)
    assert auto_completion_pending(THREAD) is True
    host.running.clear()
    await _drain(completer)
    assert auto_completion_pending(THREAD) is False


async def test_host_completion_tells_the_parent() -> None:
    from claude_discord.ext.auto_complete_api import ApiCompletionHost

    api = MagicMock()
    api.console_conversations = None
    api._console = MagicMock()
    api._console.complete_item = AsyncMock()
    api.notify_parent_of_outcome = AsyncMock(return_value="notified")
    channel = MagicMock()
    channel.name = CANDIDATE_TITLE
    api.bot.get_channel.return_value = channel
    await ApiCompletionHost(api).complete(THREAD)
    api._console.complete_item.assert_awaited_once_with(f"t{THREAD}")
    thread_id, outcome, name = api.notify_parent_of_outcome.await_args.args
    assert (thread_id, outcome) == (THREAD, "done")
    assert name.startswith("✅")
