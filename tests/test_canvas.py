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
    text = charter("ebi")
    for path in ("/api/secretary/digest", "/api/secretary/notes", "/api/canvas/widgets"):
        assert path in text
    assert "{" not in wake_message(3).replace("{}", "")


def test_config_reads_the_environment_and_clamps() -> None:
    config = CanvasConfig.from_env(
        {"CCDB_CANVAS_MIN_INTERVAL": "5", "CCDB_CANVAS_MAX_WAKES_PER_DAY": "x"}
    )
    assert config.min_interval == 60
    assert config.max_wakes_per_day == 24
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
    resp = await client.put("/api/canvas/widgets/now", json=_widget("markdown"))
    assert resp.status == 201
    resp = await client.put("/api/canvas/widgets/now", json=_widget("metric"))
    assert resp.status == 200 and (await resp.json())["widget"]["kind"] == "metric"
    await client.put("/api/canvas/widgets/b", json=_widget("table"))
    assert (await client.post("/api/canvas/order", json={"keys": ["b", "now"]})).status == 200
    keys = [w["key"] for w in (await (await client.get("/api/canvas")).json())["widgets"]]
    assert keys == ["b", "now"]
    assert (await (await client.delete("/api/canvas/widgets/b")).json())["status"] == "removed"


async def test_a_refused_widget_says_why(api) -> None:
    client, server, console, _ = api
    server._console = console
    resp = await client.put("/api/canvas/widgets/x", json={"kind": "html", "title": "t"})
    body = await resp.json()
    assert resp.status == 400 and body["problems"] and "format" in body
    assert (await client.put("/api/canvas/widgets/Bad Key", json=_widget("markdown"))).status in (
        400,
        404,
    )


async def test_notes_digest_and_deliver(api) -> None:
    client, server, console, _ = api
    server._console = console
    assert (await client.put("/api/secretary/notes/12", json={"note": "hi"})).status == 200
    notes = (await (await client.get("/api/secretary/notes")).json())["notes"]
    assert notes["12"]["note"] == "hi"
    assert (await client.put("/api/secretary/notes/12", json={"note": "x" * 2000})).status == 400
    assert (await client.get("/api/secretary/digest?peek=1")).status == 200
    resp = await client.post("/api/secretary/deliver", json={"thread_id": "12", "text": "A"})
    assert resp.status == 202
    console.deliver_from_secretary.assert_awaited_once_with(12, "A")
    bad = await client.post("/api/secretary/deliver", json={"thread_id": "x", "text": "A"})
    assert bad.status == 400
