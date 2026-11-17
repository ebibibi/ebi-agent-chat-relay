"""Canvas mode (ADR-0025): widgets, the secretary's memory, its watcher and its API."""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.canvas_widget import KINDS, WidgetError, parse_widget
from claude_discord.console.canvas import CanvasConfig, CanvasSecretary, compare, fingerprint
from claude_discord.console.canvas_charter import charter, wake_message
from claude_discord.console.canvas_repo import CanvasRepository
from claude_discord.database.models import init_db
from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.ext.api_server import ApiServer

GOOD: dict[str, dict[str, Any]] = {
    "decision": {"thread_id": "1571155713332478073"},
    "diff": {"diff": "- old\n+ new"},
    "table": {"columns": ["a", "b"], "rows": [["1", "2"], ["3"]]},
    "markdown": {"text": "**bold** <script>alert(1)</script>"},
    "timeline": {"entries": [{"time": "21:00", "label": "Answer A", "state": "now", "minutes": 5}]},
    "agents": {"thread_ids": []},
    "metric": {"value": "3", "label": "to decide", "percent": 40},
    "schedule": {"lanes": {"you": [{"start": "21:00", "end": "21:30", "label": "Decide"}]}},
}


def _widget(kind: str, **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "title": f"A {kind}", "data": GOOD[kind], **extra}


# -- widgets ------------------------------------------------------------------
@pytest.mark.parametrize("kind", KINDS)
def test_every_kind_has_a_valid_example(kind: str) -> None:
    spec = parse_widget(_widget(kind))
    assert spec.kind == kind
    assert spec.size == "m"


def test_markup_is_kept_as_text_not_interpreted() -> None:
    spec = parse_widget(_widget("markdown"))
    assert "<script>" in spec.data["text"]  # drawn by the sanitising renderer, never as HTML


def test_a_bad_widget_lists_every_problem() -> None:
    with pytest.raises(WidgetError) as caught:
        parse_widget({"kind": "chart", "title": "", "data": [], "html": "<b>", "size": "xl"})
    problems = " | ".join(caught.value.problems)
    for expected in ("unknown fields: html", "kind must be", "title is required", "size", "data"):
        assert expected in problems


def test_table_rows_cannot_be_wider_than_the_columns() -> None:
    with pytest.raises(WidgetError):
        parse_widget(
            {"kind": "table", "title": "t", "data": {"columns": ["a"], "rows": [["1", "2"]]}}
        )


def test_actions_are_buttons_with_a_label_and_words() -> None:
    spec = parse_widget(
        _widget("markdown", actions=[{"label": "Later", "say": "Leave X for later"}])
    )
    assert spec.actions == ({"label": "Later", "say": "Leave X for later"},)
    with pytest.raises(WidgetError):
        parse_widget(_widget("markdown", actions=[{"label": "x" * 40, "say": ""}]))


def test_decision_needs_a_thread_id() -> None:
    with pytest.raises(WidgetError):
        parse_widget({"kind": "decision", "title": "t", "data": {"thread_id": "abc"}})


# -- what changed -------------------------------------------------------------
def _item(item_id: str, status: str = "waiting", **extra: Any) -> dict[str, Any]:
    return {
        "id": item_id,
        "thread_id": item_id[1:],
        "title": f"Thread {item_id}",
        "status": status,
        "running": False,
        "last_activity_at": "2026-11-14T10:00:00Z",
        **extra,
    }


def test_compare_names_status_requests_activity_new_and_gone() -> None:
    old = fingerprint([_item("t1"), _item("t2"), _item("t3")], [{"id": 5, "thread_id": "2"}])
    new = fingerprint(
        [
            _item("t1", "done"),
            _item("t2"),
            _item("t3", last_activity_at="2026-11-14T11:00:00Z"),
            _item("t4"),
        ],
        [{"id": 6, "thread_id": "1"}],
    )
    changes = compare(old, new)
    assert ("t1", "status waiting → done") in changes
    assert ("t1", "decision request opened") in changes
    assert ("t2", "decision request closed") in changes
    assert ("t3", "new activity") in changes
    assert ("t4", "new (waiting)") in changes


def test_a_running_threads_streaming_is_not_a_change() -> None:
    old = fingerprint([_item("t1", "running", running=True)], [])
    new = fingerprint(
        [_item("t1", "running", running=True, last_activity_at="2026-11-14T12:00:00Z")], []
    )
    assert compare(old, new) == []


def test_the_secretary_is_not_watched() -> None:
    assert "t9" not in fingerprint([_item("t9")], [], exclude="t9")


# -- the watcher ----------------------------------------------------------------
class FakeHost:
    def __init__(self) -> None:
        self.spawned: list[dict[str, Any]] = []
        self.replies: list[tuple[int, str, str]] = []

    async def spawn(self, **kwargs: Any) -> int:
        self.spawned.append(kwargs)
        return 900 + len(self.spawned)

    async def owns(self, key: int) -> bool:
        return True

    async def reply(self, key: int, prompt: str, author: str, **_: Any) -> bool:
        self.replies.append((key, prompt, author))
        return False


@pytest.fixture
async def repo():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    store = CanvasRepository(path)
    await store.init_db()
    yield store
    os.unlink(path)


class Board:
    def __init__(self) -> None:
        self.items = [_item("t1"), _item("t2", "running", running=True)]
        self.requests: list[dict[str, Any]] = []

    async def board(self) -> list[dict[str, Any]]:
        return [dict(i) for i in self.items]

    async def open_requests(self) -> list[dict[str, Any]]:
        return list(self.requests)


def _secretary(repo: CanvasRepository, board: Board, host: FakeHost, clock: list[datetime]):
    async def messages(item: dict[str, Any]) -> list[tuple[str, str]]:
        return [("person", "please"), ("agent", f"latest in {item['id']}" + "x" * 900)]

    async def situation(thread_id: int) -> dict[str, Any] | None:
        return {"doing": ["moving"], "decided": ["A"]} if thread_id == 1 else None

    return CanvasSecretary(
        repo,
        board=board.board,
        requests=board.open_requests,
        messages=messages,
        situation=situation,
        host=host,  # type: ignore[arg-type]
        config=CanvasConfig(min_interval=600, max_wakes_per_day=2),
        clock=lambda: clock[0],
    )


async def test_nothing_is_read_until_the_secretary_is_started(repo) -> None:
    board, host = Board(), FakeHost()
    board.board = AsyncMock(side_effect=AssertionError("read"))  # type: ignore[method-assign]
    secretary = _secretary(repo, board, host, [datetime(2026, 11, 14, 12, tzinfo=UTC)])
    assert await secretary.tick() == "no secretary"


async def test_start_pins_the_ai_and_is_idempotent(repo) -> None:
    host = FakeHost()
    secretary = _secretary(repo, Board(), host, [datetime(2026, 11, 14, 12, tzinfo=UTC)])
    key = await secretary.start_secretary("ebi")
    assert await secretary.start_secretary("ebi") == key
    assert len(host.spawned) == 1
    assert host.spawned[0]["ai"] == ("claude", "sonnet", None)
    assert "ebi" in host.spawned[0]["prompt"]
    assert await secretary.start_secretary("ebi", restart=True) != key


async def test_changes_wake_the_idle_secretary_within_the_limits(repo) -> None:
    board, host = Board(), FakeHost()
    clock = [datetime(2026, 11, 14, 12, tzinfo=UTC)]
    secretary = _secretary(repo, board, host, clock)
    key = await secretary.start_secretary("ebi")
    board.items.append({**_item(f"t{key}", "running"), "running": True})
    assert await secretary.tick() == "running"  # the charter turn runs; baseline taken
    board.items[-1] = {**_item(f"t{key}", "idle"), "context": {"used": 30000}}
    assert await secretary.tick() == "nothing changed"
    assert (await repo.day_usage("2026-11-14"))["turns"] == 1

    board.items[0] = _item("t1", "done")
    # The charter turn just read everything: no wake straight after it.
    assert await secretary.tick() == "too soon"
    clock[0] += timedelta(minutes=11)
    assert await secretary.tick() == "woken"
    assert host.replies[-1][0] == key and "1 thread" in host.replies[-1][1]

    board.items[-1] = {**_item(f"t{key}", "idle")}
    board.requests = [{"id": 3, "thread_id": "2"}]
    secretary._secretary_running = False
    assert await secretary.tick() == "too soon"
    clock[0] += timedelta(minutes=11)
    await secretary.set_paused(True)
    assert await secretary.tick() == "paused"
    await secretary.set_paused(False)
    assert await secretary.tick() == "woken"
    secretary._secretary_running = False
    board.items[0] = _item("t1", "waiting")
    clock[0] += timedelta(minutes=11)
    assert await secretary.tick() == "daily cap"


async def test_digest_reads_only_what_changed_and_moves_the_cursor(repo) -> None:
    board, host = Board(), FakeHost()
    secretary = _secretary(repo, board, host, [datetime(2026, 11, 14, 12, tzinfo=UTC)])
    await secretary.start_secretary("ebi")
    await secretary.tick()
    board.items[0] = _item("t1", "review")
    board.requests = [
        {
            "id": 7,
            "thread_id": "1",
            "kind": "review",
            "card": {"title": "Look", "context": "c", "options": [], "deadline": "d"},
        }
    ]
    await repo.put_note(1, "about the site")
    await secretary.tick()

    peek = await secretary.digest(peek=True)
    assert [t["id"] for t in peek["threads"]] == ["t1"]
    entry = peek["threads"][0]
    assert entry["note"] == "about the site"
    assert entry["requests"][0]["title"] == "Look"
    assert entry["situation"] == {"doing": ["moving"], "decided": ["A"]}
    assert len(entry["recent"][-1]["text"]) == 500
    assert (await secretary.digest())["threads"]
    assert (await secretary.digest())["threads"] == []  # the cursor moved

    everything = await secretary.digest(everything=True)
    assert {t["id"] for t in everything["threads"]} == {"t1", "t2"}
    assert "recent" not in everything["threads"][0]


def test_charter_and_wake_point_to_the_api() -> None:
    text = charter("ebi", "k3y")
    assert "X-Canvas-Token: k3y" in text
    for path in ("/api/secretary/digest", "/api/secretary/notes", "/api/canvas/widgets"):
        assert path in text
    assert "{" not in wake_message(3).replace("{}", "")


def test_config_reads_the_environment_and_clamps() -> None:
    config = CanvasConfig.from_env(
        {"CCDB_CANVAS_MIN_INTERVAL": "5", "CCDB_CANVAS_MAX_WAKES_PER_DAY": "x"}
    )
    assert config.min_interval == 60
    assert config.max_wakes_per_day == 8
    assert CanvasConfig.from_env({"CCDB_CANVAS_SECRETARY_MODEL": "opus"}).model == "opus"


# -- the control plane -----------------------------------------------------------
@pytest.fixture
async def api():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(path)
    notifications = NotificationRepository(path)
    await notifications.init_db()
    bot = MagicMock()
    bot.cogs = {}
    server = ApiServer(repo=notifications, bot=bot, default_channel_id=1, host="127.0.0.1", port=0)
    store = CanvasRepository(path)
    await store.init_db()
    console = MagicMock()
    console.canvas = _secretary(store, Board(), FakeHost(), [datetime(2026, 11, 14, tzinfo=UTC)])
    await console.canvas.start_secretary("ebi")
    console.key = {"X-Canvas-Token": await store.get_state("token")}
    console.deliver_from_secretary = AsyncMock(
        return_value=web.json_response({"status": "delivered"}, status=202)
    )
    client = TestClient(TestServer(server.app))
    await client.start_server()
    yield client, server, console, store
    await client.close()
    os.unlink(path)


async def test_without_the_console_the_canvas_answers_503(api) -> None:
    client, _, _, _ = api
    assert (await client.get("/api/canvas")).status == 503


async def test_widgets_are_placed_updated_ordered_and_removed(api) -> None:
    client, server, console, store = api
    server._console = console
    resp = await client.put(
        "/api/canvas/widgets/now", json=_widget("markdown"), headers=console.key
    )
    assert resp.status == 201
    resp = await client.put("/api/canvas/widgets/now", json=_widget("metric"), headers=console.key)
    assert resp.status == 200 and (await resp.json())["widget"]["kind"] == "metric"
    await client.put("/api/canvas/widgets/b", json=_widget("table"), headers=console.key)
    assert (
        await client.post("/api/canvas/order", json={"keys": ["b", "now"]}, headers=console.key)
    ).status == 200
    keys = [w["key"] for w in (await (await client.get("/api/canvas")).json())["widgets"]]
    assert keys == ["b", "now"]
    assert (await (await client.delete("/api/canvas/widgets/b", headers=console.key)).json())[
        "status"
    ] == "removed"


async def test_a_refused_widget_says_why(api) -> None:
    client, server, console, _ = api
    server._console = console
    resp = await client.put(
        "/api/canvas/widgets/x", json={"kind": "html", "title": "t"}, headers=console.key
    )
    body = await resp.json()
    assert resp.status == 400 and body["problems"] and "format" in body
    bad_key = await client.put(
        "/api/canvas/widgets/Bad Key", json=_widget("markdown"), headers=console.key
    )
    assert bad_key.status in (
        400,
        404,
    )


async def test_notes_digest_and_deliver(api) -> None:
    client, server, console, _ = api
    server._console = console
    k = console.key
    assert (
        await client.put("/api/secretary/notes/12", json={"note": "hi"}, headers=k)
    ).status == 200
    notes = (await (await client.get("/api/secretary/notes")).json())["notes"]
    assert notes["12"]["note"] == "hi"
    long = await client.put("/api/secretary/notes/12", json={"note": "x" * 2000}, headers=k)
    assert long.status == 400
    assert (await client.get("/api/secretary/digest?peek=1")).status == 200
    resp = await client.post(
        "/api/secretary/deliver", json={"thread_id": "12", "text": "A"}, headers=k
    )
    assert resp.status == 202
    console.deliver_from_secretary.assert_awaited_once_with(12, "A", None)
    bad = await client.post(
        "/api/secretary/deliver", json={"thread_id": "x", "text": "A"}, headers=k
    )
    assert bad.status == 400


async def test_only_the_secretary_may_write_or_move_its_cursor(api) -> None:
    client, server, console, store = api
    server._console = console
    wrong = {"X-Canvas-Token": "guess"}
    for call in (
        client.put("/api/canvas/widgets/now", json=_widget("markdown")),
        client.put("/api/canvas/widgets/now", json=_widget("markdown"), headers=wrong),
        client.delete("/api/canvas/widgets/now"),
        client.post("/api/canvas/order", json={"keys": []}),
        client.put("/api/secretary/notes/12", json={"note": "planted"}),
        client.post("/api/secretary/deliver", json={"thread_id": "12", "text": "yes"}),
    ):
        assert (await call).status == 403
    console.deliver_from_secretary.assert_not_awaited()
    await store.add_changes([("t1", "new (waiting)")])
    assert (await (await client.get("/api/secretary/digest")).json())["threads"]
    assert (await store.get_state("cursor")) == "0", "a stranger's read does not move the cursor"
    await client.get("/api/secretary/digest", headers=console.key)
    assert (await store.get_state("cursor")) != "0"
    old = console.key["X-Canvas-Token"]
    await console.canvas.start_secretary("ebi", restart=True)
    assert not await console.canvas.authorized(old), "a restart rotates the key"


# -- today's timetable and focus sessions -------------------------------------------
def test_schedule_lanes_and_times_are_checked() -> None:
    spec = parse_widget(
        {
            "kind": "schedule",
            "title": "今日",
            "data": {
                "lanes": {
                    "you": [
                        {"start": "21:00", "end": "21:10", "label": "決めごと3件", "thread_id": "5"}
                    ],
                    "ai": [{"start": "20:30", "label": "字幕", "sub": "62%"}],
                }
            },
        }
    )
    assert spec.data["lanes"]["you"][0]["end"] == "21:10"
    assert spec.data["lanes"]["ai"][0]["end"] is None
    assert spec.data["lanes"]["external"] == []
    with pytest.raises(WidgetError) as caught:
        parse_widget(
            {
                "kind": "schedule",
                "title": "t",
                "data": {
                    "lanes": {
                        "me": [],
                        "you": [
                            {"start": "9:00", "label": "x"},
                            {"start": "10:00", "end": "09:00", "label": "y"},
                        ],
                    }
                },
            }
        )
    text = " | ".join(caught.value.problems)
    assert "unknown lanes: me" in text and "HH:MM" in text and "after its start" in text


def test_a_session_plan_orders_decisions_with_material() -> None:
    from claude_discord.canvas_widget import parse_session

    plan = parse_session(
        {
            "title": "GA v2",
            "minutes": 15,
            "items": [
                {
                    "thread_id": "11",
                    "group": "GA v2",
                    "why": "deadline 11/17",
                    "materials": [
                        {"kind": "table", "title": "中身", "data": GOOD["table"]},
                        {"kind": "timeline", "title": "記録", "data": GOOD["timeline"]},
                    ],
                },
                {"thread_id": "12"},
            ],
        }
    )
    assert [i["thread_id"] for i in plan["items"]] == ["11", "12"]
    assert plan["items"][0]["materials"][1]["kind"] == "timeline"
    with pytest.raises(WidgetError) as caught:
        parse_session(
            {
                "minutes": 0,
                "items": [{"thread_id": "x", "materials": [{"kind": "decision", "title": "d"}]}],
            }
        )
    text = " | ".join(caught.value.problems)
    assert "minutes" in text and "thread_id" in text and "material kind" in text


def test_the_session_summary_tells_the_secretary_what_was_settled() -> None:
    from claude_discord.console.canvas_routes import _titles, session_summary

    decided, problem = _titles([{"title": "hccjp", "answer": "A 残さない"}], with_answer=True)
    later, _ = _titles([{"title": "販売ページ"}], with_answer=False)
    assert problem is None
    text = session_summary(decided, later)
    assert "hccjp → A 残さない" in text and "販売ページ" in text
    assert _titles([{"title": ""}], with_answer=False)[1]
    assert _titles("x", with_answer=False)[1]


async def test_only_the_secretary_prepares_a_session(api) -> None:
    client, server, console, _ = api
    server._console = console
    plan = {"items": [{"thread_id": "11"}], "minutes": 10}
    assert (await client.put("/api/canvas/session", json=plan)).status == 403
    resp = await client.put("/api/canvas/session", json=plan, headers=console.key)
    assert resp.status == 200
    read = await (await client.get("/api/canvas")).json()
    assert read["session"]["minutes"] == 10 and read["session"]["prepared_at"]
    bad = await client.put("/api/canvas/session", json={"items": []}, headers=console.key)
    assert bad.status == 400 and "format" in await bad.json()
    assert (await client.delete("/api/canvas/session", headers=console.key)).status == 200
    assert (await (await client.get("/api/canvas")).json())["session"] is None


def test_a_partial_board_after_a_restart_is_not_hundreds_of_changes() -> None:
    """Measured in production: the first board after a restart lacked the chat threads."""
    partial = fingerprint([_item("t1")], [])
    full = fingerprint([_item("t1")] + [_item(f"t{i}") for i in range(100, 700)], [])
    assert compare(partial, full) == []
    assert compare(full, partial) == []
    full_with_change = fingerprint(
        [_item("t1", "done")] + [_item(f"t{i}") for i in range(100, 700)], []
    )
    assert compare(partial, full_with_change) == [("t1", "status waiting → done")]


# -- fresh turns and live limits (2026-11-15 trial: 24 wakes read about 4.1M tokens) ------------
async def test_every_secretary_turn_gets_a_compact_brief_not_the_whole_conversation(repo) -> None:
    board, host = Board(), FakeHost()
    secretary = _secretary(repo, board, host, [datetime(2026, 11, 14, 12, tzinfo=UTC)])
    long_history = [("ebi", False, f"question {i} " + "x" * 2000) for i in range(200)]
    long_history += [("agent", True, "-# ⌨️ Running: curl ..."), ("agent", True, "answer")]

    async def history(key: int, limit: int) -> list[tuple[str, bool, str]]:
        return long_history[-limit:]

    secretary._history = history
    key = await secretary.start_secretary("ebi")
    assert await secretary.fresh_turn(key + 1, "hi") is None, "other threads resume as usual"
    first = host.spawned[0]["prompt"]
    assert await secretary.fresh_turn(key, first) is None, "the opening charter runs as written"
    brief = await secretary.fresh_turn(key, "🔔 2 thread(s) changed.")
    assert brief is not None
    assert "fresh session" in brief and "X-Canvas-Token" in brief
    assert brief.endswith("🔔 2 thread(s) changed.")
    assert "Running: curl" not in brief and "- you: answer" in brief
    assert len(brief) < 12000, "the same size on the hundredth turn as on the second"


async def test_wake_limits_change_live_from_the_canvas(repo) -> None:
    board, host = Board(), FakeHost()
    clock = [datetime(2026, 11, 14, 12, tzinfo=UTC)]
    secretary = _secretary(repo, board, host, clock)
    assert (await secretary.limits())["max_wakes_per_day"] == 2  # the test config
    await secretary.set_limits(1, 60)
    assert await secretary.limits() == {"max_wakes_per_day": 1, "min_interval": 60}
    assert (await secretary.status())["usage"]["max_wakes"] == 1
    with pytest.raises(ValueError):
        await secretary.set_limits(-1, 60)
    with pytest.raises(ValueError):
        await secretary.set_limits(8, 10)
    await secretary.start_secretary("ebi")
    await secretary.tick()
    board.items[0] = _item("t1", "done")
    clock[0] += timedelta(minutes=2)
    assert await secretary.tick() == "woken"
    board.items[0] = _item("t1", "waiting")
    clock[0] += timedelta(minutes=2)
    secretary._secretary_running = False
    assert await secretary.tick() == "daily cap"


async def test_the_open_screens_ai_runs_the_secretarys_wakes_until_it_lapses(
    repo, monkeypatch
) -> None:
    from claude_discord.console import canvas as canvas_module
    from claude_discord.turn_ai import TurnAI

    class Host(FakeHost):
        def __init__(self) -> None:
            super().__init__()
            self.ais: list = []

        async def reply(self, key: int, prompt: str, author: str, **kw) -> bool:
            self.ais.append(kw.get("ai"))
            return await super().reply(key, prompt, author)

    board, host = Board(), Host()
    clock = [datetime(2026, 11, 14, 12, tzinfo=UTC)]
    secretary = _secretary(repo, board, host, clock)
    await secretary.set_limits(10, 60)
    await secretary.start_secretary("ebi")
    await secretary.tick()
    chosen = TurnAI(backend="codex", model="gpt-6-astra")
    secretary.set_screen_ai(chosen)
    board.items[0] = _item("t1", "done")
    clock[0] += timedelta(minutes=2)
    assert await secretary.tick() == "woken"
    assert host.ais[-1] == chosen
    now = [1000.0]
    monkeypatch.setattr(canvas_module.time, "monotonic", lambda: now[0])
    secretary.set_screen_ai(chosen)
    now[0] += canvas_module.SCREEN_AI_SECONDS + 1
    assert secretary.screen_ai() is None, "the screen closed: back to the thread's own AI"


async def test_the_secretary_names_the_card_its_answer_settles(api) -> None:
    client, server, console, _ = api
    server._console = console
    resp = await client.post(
        "/api/secretary/deliver",
        json={"thread_id": "12", "text": "A", "key": "buy-or-build"},
        headers=console.key,
    )
    assert resp.status == 202
    console.deliver_from_secretary.assert_awaited_once_with(12, "A", "buy-or-build")
    bad = await client.post(
        "/api/secretary/deliver",
        json={"thread_id": "12", "text": "A", "key": "x" * 41},
        headers=console.key,
    )
    assert bad.status == 400
