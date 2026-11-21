"""Closing routine threads with a rescue record (#1490)."""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from claude_discord import auto_close
from claude_discord.auto_close import (
    AUTO_CLOSED_TAG,
    HUMAN_NEXT_TAG,
    KIND_SWEEP,
    KIND_UPGRADE,
    MODE_DRY_RUN,
    MODE_OFF,
    MODE_ON,
    AutoClosePolicy,
    CloseFacts,
    Closure,
    ClosureError,
    close_system_thread,
    evaluate_close,
    parse_closure,
    set_system_closer,
)
from claude_discord.console.work_repo import STATE_DONE, WorkItemRepository, thread_item_id
from claude_discord.ext.auto_close_api import (
    AutoCloser,
    handle_auto_close,
    handle_list_auto_closures,
    handle_sweep_threads,
)

THREAD = 21
SWEEPER = 99
CLEAR = CloseFacts(running=False, teams_reply_draft=False, active_waits=0, open_requests=0)
SWEEP = Closure(kind=KIND_SWEEP, reason="the PR merged and nothing is left")
ON_ALL = AutoClosePolicy(mode=MODE_ON, kinds=frozenset({KIND_UPGRADE, KIND_SWEEP}))


class TestPolicy:
    def test_default_is_off(self) -> None:
        policy = AutoClosePolicy.from_env({})
        assert policy.mode == MODE_OFF
        assert not policy.allows(KIND_UPGRADE)

    def test_on_allows_upgrades_only_by_default(self) -> None:
        policy = AutoClosePolicy.from_env({"CCDB_AUTO_CLOSE": "on"})
        assert policy.allows(KIND_UPGRADE)
        assert not policy.allows(KIND_SWEEP)

    def test_unknown_mode_is_off_and_unknown_kinds_are_dropped(self) -> None:
        assert AutoClosePolicy.from_env({"CCDB_AUTO_CLOSE": "yes"}).mode == MODE_OFF
        policy = AutoClosePolicy.from_env(
            {"CCDB_AUTO_CLOSE": "on", "CCDB_AUTO_CLOSE_KINDS": "sweep, everything"}
        )
        assert policy.kinds == frozenset({KIND_SWEEP})

    @pytest.mark.parametrize(("raw", "days"), [("", 30), ("7", 7), ("0", 1), ("x", 30)])
    def test_rescue_days(self, raw: str, days: int) -> None:
        env = {"CCDB_AUTO_CLOSE_RESCUE_DAYS": raw}
        assert AutoClosePolicy.from_env(env).rescue_days == days


class TestEvaluate:
    def test_clear_thread_closes(self) -> None:
        verdict = evaluate_close(ON_ALL, SWEEP, CLEAR)
        assert verdict.close and verdict.eligible

    def test_dry_run_judges_without_closing(self) -> None:
        policy = AutoClosePolicy(mode=MODE_DRY_RUN, kinds=frozenset({KIND_SWEEP}))
        verdict = evaluate_close(policy, SWEEP, CLEAR)
        assert verdict.eligible and not verdict.close

    def test_kind_not_enabled(self) -> None:
        policy = AutoClosePolicy(mode=MODE_ON)
        assert not evaluate_close(policy, SWEEP, CLEAR).eligible

    @pytest.mark.parametrize(
        "facts",
        [
            CloseFacts(True, False, 0, 0),
            CloseFacts(False, True, 0, 0),
            CloseFacts(False, False, 1, 0),
            CloseFacts(False, False, 0, 1),
            CloseFacts(False, False, 0, 0, (5,)),
        ],
    )
    def test_measured_facts_block(self, facts: CloseFacts) -> None:
        assert not evaluate_close(ON_ALL, SWEEP, facts).eligible

    def test_human_next_step_needs_followup(self) -> None:
        missing = Closure(kind=KIND_SWEEP, reason="r", human_next_step=True)
        assert not evaluate_close(ON_ALL, missing, CLEAR).eligible
        kept = Closure(kind=KIND_SWEEP, reason="r", human_next_step=True, followup="tasks.md")
        assert evaluate_close(ON_ALL, kept, CLEAR).close


class TestParse:
    def test_reason_is_required(self) -> None:
        with pytest.raises(ClosureError):
            parse_closure({}, kind=KIND_SWEEP)

    def test_kind_comes_from_the_route(self) -> None:
        closure = parse_closure({"reason": "r", "kind": "upgrade"}, kind=KIND_SWEEP)
        assert closure.kind == KIND_SWEEP

    @pytest.mark.parametrize(
        "body",
        [{"reason": "r", "human_next_step": "yes"}, {"reason": "r" * 400}, {"reason": 3}, []],
    )
    def test_malformed(self, body: Any) -> None:
        with pytest.raises(ClosureError):
            parse_closure(body, kind=KIND_SWEEP)


# ---------------------------------------------------------------------------
# Server side
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path() -> Iterator[str]:
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    yield path
    os.unlink(path)


class FakeConsole:
    def __init__(self, repo: WorkItemRepository) -> None:
        self.work_repo = repo
        self.completed: list[tuple[str, bool]] = []
        self.board: list[dict[str, Any]] = []

    async def complete_item(self, item_id: str, *, mirror_workspace: bool = True) -> Any:
        self.completed.append((item_id, mirror_workspace))
        return await self.work_repo.update(item_id, {"state": STATE_DONE}), None

    async def _board_items(self) -> list[dict[str, Any]]:
        return self.board


def _fake_api(console: FakeConsole | None, *, running: set[int] | None = None) -> Any:
    channel = MagicMock()
    channel.send = AsyncMock()
    bot = MagicMock()
    bot.get_channel = MagicMock(return_value=channel)
    api = SimpleNamespace(
        _console=console,
        console_conversations=None,
        bot=bot,
        lineage_repo=None,
        wait_repo=None,
        decision_repo=None,
        session_repo=None,
        _running_thread_ids=lambda: set(running or ()),
        _apply_thread_outcome=AsyncMock(),
    )
    api.channel = channel
    return api


async def _console(db_path: str) -> FakeConsole:
    repo = WorkItemRepository(db_path)
    await repo.init_db()
    return FakeConsole(repo)


class TestAutoCloser:
    @pytest.mark.asyncio
    async def test_close_records_then_completes_without_workspace(self, db_path: str) -> None:
        console = await _console(db_path)
        await console.work_repo.update(thread_item_id(THREAD), {"note": "human note"})
        api = _fake_api(console)
        closer = AutoCloser(api, policy=lambda: ON_ALL, settle_seconds=0)
        closure = Closure(
            kind=KIND_SWEEP,
            reason="over",
            summary="PR merged",
            human_next_step=True,
            followup="status.md",
        )
        verdict = await closer.close(THREAD, closure)
        assert verdict.close
        item = await console.work_repo.get(thread_item_id(THREAD))
        assert item is not None and item.state == STATE_DONE
        assert {AUTO_CLOSED_TAG, f"{AUTO_CLOSED_TAG}:sweep", HUMAN_NEXT_TAG} <= set(item.tags)
        assert item.note is not None and item.note.startswith("human note")
        assert "status.md" in item.note and "PR merged" in item.note
        assert console.completed == [(thread_item_id(THREAD), False)]
        text = api.channel.send.await_args.args[0]
        assert "Reopen" in text and "status.md" in text

    @pytest.mark.asyncio
    async def test_running_thread_is_kept(self, db_path: str) -> None:
        console = await _console(db_path)
        closer = AutoCloser(
            _fake_api(console, running={THREAD}), policy=lambda: ON_ALL, settle_seconds=0
        )
        verdict = await closer.close(THREAD, SWEEP)
        assert not verdict.close
        assert console.completed == []
        assert await console.work_repo.get(thread_item_id(THREAD)) is None

    @pytest.mark.asyncio
    async def test_without_console_marks_the_title(self) -> None:
        api = _fake_api(None)
        closer = AutoCloser(api, policy=lambda: ON_ALL, settle_seconds=0)
        assert (await closer.close(THREAD, SWEEP)).close
        api._apply_thread_outcome.assert_awaited_once()


class TestSystemCloser:
    @pytest.mark.asyncio
    async def test_no_op_when_off_or_unregistered(self) -> None:
        closer = AsyncMock()
        set_system_closer(closer)
        try:
            with patch.dict(os.environ, {"CCDB_AUTO_CLOSE": "off"}):
                assert await close_system_thread(1, Closure(KIND_UPGRADE, "r")) is None
            closer.assert_not_awaited()
        finally:
            set_system_closer(None)
        with patch.dict(os.environ, {"CCDB_AUTO_CLOSE": "on"}):
            assert await close_system_thread(1, Closure(KIND_UPGRADE, "r")) is None

    @pytest.mark.asyncio
    async def test_calls_the_closer_and_swallows_errors(self) -> None:
        closer = AsyncMock(side_effect=RuntimeError("boom"))
        set_system_closer(closer)
        try:
            with patch.dict(os.environ, {"CCDB_AUTO_CLOSE": "on"}):
                assert await close_system_thread(1, Closure(KIND_UPGRADE, "r")) is None
            closer.assert_awaited_once()
        finally:
            set_system_closer(None)

    def test_clear_only_removes_its_own(self) -> None:
        first, second = AsyncMock(), AsyncMock()
        set_system_closer(second)
        auto_close.clear_system_closer(first)
        assert auto_close._closer is second
        auto_close.clear_system_closer(second)
        assert auto_close._closer is None


class TestUpgradeHook:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("text", "closes"),
        [
            ("✅ Already up to date.", True),
            ("🕒 Restart reserved: ...", True),
            ("⚠️ no restart can be reserved", False),
        ],
    )
    async def test_only_finished_outcomes_close(self, text: str, closes: bool) -> None:
        from claude_discord.cogs import auto_upgrade

        with patch.object(auto_upgrade, "close_system_thread", AsyncMock()) as close:
            await auto_upgrade._close_if_allowed(7, text)
        assert close.await_count == (1 if closes else 0)
        if closes:
            assert close.await_args.args[1].kind == KIND_UPGRADE


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


def _app(api: Any, closer: AutoCloser, policy: AutoClosePolicy) -> web.Application:
    app = web.Application()

    async def close(request: web.Request) -> web.Response:
        return await handle_auto_close(request, closer)

    async def sweep(request: web.Request) -> web.Response:
        return await handle_sweep_threads(request, api)

    async def closures(request: web.Request) -> web.Response:
        return await handle_list_auto_closures(request, api, policy)

    app.router.add_post("/api/threads/{thread_id}/auto-close", close)
    app.router.add_get("/api/sweep/threads", sweep)
    app.router.add_get("/api/auto-closures", closures)
    return app


async def _client(api: Any, policy: AutoClosePolicy) -> TestClient:
    closer = AutoCloser(api, policy=lambda: policy, settle_seconds=0)
    client = TestClient(TestServer(_app(api, closer, policy)))
    await client.start_server()
    return client


class TestRoutes:
    @pytest.mark.asyncio
    async def test_auto_close_refused_while_sweeps_are_off(self, db_path: str) -> None:
        api = _fake_api(await _console(db_path))
        client = await _client(api, AutoClosePolicy(mode=MODE_ON))
        try:
            resp = await client.post(
                f"/api/threads/{THREAD}/auto-close",
                json={"from_thread": SWEEPER, "reason": "over"},
            )
            assert resp.status == 403
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_auto_close_validates_and_closes(self, db_path: str) -> None:
        console = await _console(db_path)
        api = _fake_api(console)
        client = await _client(api, ON_ALL)
        try:
            url = f"/api/threads/{THREAD}/auto-close"
            assert (await client.post(url, json={"reason": "over"})).status == 400
            self_close = {"from_thread": THREAD, "reason": "over"}
            assert (await client.post(url, json=self_close)).status == 400
            missing = {"from_thread": SWEEPER, "reason": "over", "human_next_step": True}
            resp = await client.post(url, json=missing)
            assert resp.status == 409
            resp = await client.post(url, json={"from_thread": SWEEPER, "reason": "over"})
            assert resp.status == 200
            assert (await resp.json())["status"] == "closed"
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_sweep_lists_candidates_and_stale_idle(self, db_path: str) -> None:
        console = await _console(db_path)
        console.board = [
            {"id": "t:1", "thread_id": "1", "bucket": "candidate"},
            {"id": "t:2", "thread_id": "2", "bucket": "idle", "last_activity_at": "2020-01-01"},
            {"id": "t:3", "thread_id": "3", "bucket": "idle", "last_activity_at": "2999-01-01"},
            {"id": "t:4", "thread_id": "4", "bucket": "candidate", "running": True},
            {"id": "t:5", "thread_id": "5", "bucket": "candidate", "pinned_at": "x"},
            {"id": "t:6", "thread_id": "6", "bucket": "me"},
            {"id": "c:7", "thread_id": None, "bucket": "todo"},
        ]
        client = await _client(_fake_api(console), ON_ALL)
        try:
            body = await (await client.get("/api/sweep/threads")).json()
            assert [t["thread_id"] for t in body["threads"]] == ["1", "2"]
            assert (await client.get("/api/sweep/threads?stale_days=0")).status == 400
        finally:
            await client.close()

    @pytest.mark.asyncio
    async def test_rescue_list(self, db_path: str) -> None:
        console = await _console(db_path)
        api = _fake_api(console)
        closer = AutoCloser(api, policy=lambda: ON_ALL, settle_seconds=0)
        await closer.close(THREAD, SWEEP)
        await console.work_repo.update(thread_item_id(22), {"state": STATE_DONE})
        console.board = [
            {
                "id": thread_item_id(THREAD),
                "thread_id": str(THREAD),
                "bucket": "done",
                "tags": [AUTO_CLOSED_TAG],
            },
            {"id": thread_item_id(22), "thread_id": "22", "bucket": "done", "tags": []},
        ]
        client = await _client(api, ON_ALL)
        try:
            body = await (await client.get("/api/auto-closures")).json()
            assert [c["thread_id"] for c in body["closures"]] == [str(THREAD)]
            assert body["days"] == 30
            assert body["closures"][0]["human_next_step"] is False
            reopened = [{**console.board[0], "bucket": "candidate"}]
            console.board = reopened
            body = await (await client.get("/api/auto-closures")).json()
            assert body["closures"] == []
        finally:
            await client.close()


def test_closure_note_shape() -> None:
    note = auto_close.closure_note(SWEEP, closed_at="2026-11-21T10:00:00+00:00")
    assert note.startswith("[auto-closed 2026-11-21T10:00")
    assert "No human next step" in note
    assert json.dumps(SWEEP.as_dict())


class TestEvidenceRecord:
    @pytest.mark.asyncio
    async def test_api_host_records_evidence_completion(self, db_path: str) -> None:
        from claude_discord.auto_complete import Evidence
        from claude_discord.ext.auto_complete_api import ApiCompletionHost

        console = await _console(db_path)
        host = ApiCompletionHost(_fake_api(console))
        evidence = Evidence(deliverables=("https://example.test/pr/1",), within_scope=True)
        await host.record(THREAD, evidence)
        item = await console.work_repo.get(thread_item_id(THREAD))
        assert item is not None
        assert {AUTO_CLOSED_TAG, f"{AUTO_CLOSED_TAG}:evidence"} <= set(item.tags)
        assert item.note is not None and "https://example.test/pr/1" in item.note

    @pytest.mark.asyncio
    async def test_without_console_nothing_is_recorded(self) -> None:
        from claude_discord.auto_complete import Evidence
        from claude_discord.ext.auto_complete_api import ApiCompletionHost

        host = ApiCompletionHost(_fake_api(None))
        await host.record(THREAD, Evidence(deliverables=("x",)))
