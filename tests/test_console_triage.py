"""Automatic triage: the model's guesses, what a human pinned, and the Focus ranking."""

from __future__ import annotations

import stat
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.console.auth import ConsoleAuthConfig, ConsoleAuthenticator
from claude_discord.console.server import ConsoleServer
from claude_discord.console.triage import (
    Triager,
    TriageRepository,
    TriageResult,
    apply_triage,
    ask_model,
    build_prompt,
    focus_score,
    parse_reply,
    triage_env,
)
from claude_discord.console.work_repo import WorkItemRepository

NOW = datetime(2026, 10, 12, 3, 0, tzinfo=UTC)
TOKEN = "t" * 40


def _item(item_id: str = "t1", **over):
    base = {
        "id": item_id,
        "title": "Ship the report",
        "status": "idle",
        "bucket": "idle",
        "priority": 2,
        "due_at": None,
        "overdue": False,
        "last_activity_at": "2026-10-12T02:00:00Z",
        "thread_id": "1",
    }
    return {**base, **over}


@pytest.fixture
async def repo(tmp_path) -> TriageRepository:
    r = TriageRepository(str(tmp_path / "sessions.db"))
    await r.init_db()
    return r


# -- parsing ---------------------------------------------------------------


def test_a_reply_wrapped_in_prose_is_parsed_and_clamped() -> None:
    body = '{"priority": 7, "due": null, "reason": "顧客待ち", "next": "返信する"}'
    raw = f"Sure:\n```json\n{body}\n```"
    assert parse_reply(raw, now=NOW) == TriageResult(
        priority=3, due_at=None, reason="顧客待ち", next_step="返信する"
    )


@pytest.mark.parametrize(
    "raw", ["", "no json", '{"priority": "high"}', '{"priority": true}', "[1, 2]", "{bad json}"]
)
def test_an_unusable_reply_is_none(raw: str) -> None:
    assert parse_reply(raw, now=NOW) is None


def test_a_date_only_deadline_is_the_end_of_that_day_and_nonsense_is_dropped() -> None:
    day = parse_reply('{"priority": 1, "due": "2026-10-14"}', now=NOW)
    assert day is not None and day.due_at is not None
    local_end = datetime.fromisoformat("2026-10-14T23:59").astimezone().astimezone(UTC)
    assert day.due_at == local_end.strftime("%Y-%m-%dT%H:%M:%SZ")
    for bad in ("tomorrow", "1999-01-01", "2099-01-01", 5):
        reply = parse_reply(f'{{"priority": 1, "due": "{bad}"}}', now=NOW)
        assert reply is not None and reply.due_at is None


def test_the_prompt_keeps_the_latest_messages_and_states_the_time() -> None:
    messages = [("person", f"message {i} " + "x" * 2000) for i in range(40)]
    prompt = build_prompt(title="  Ship\nit ", status="waiting", messages=messages, now=NOW)
    assert "Item title: Ship it" in prompt
    assert "message 39" in prompt and "message 0 " not in prompt
    assert "2026-10-12" in prompt
    assert len(prompt) < 12000


# -- the board -------------------------------------------------------------


async def test_a_guess_fills_only_what_a_human_left_unset(repo) -> None:
    due = (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    result = TriageResult(priority=0, due_at=due, reason="今日締切", next_step="提出する")
    for item_id in ("t1", "t2", "t3"):
        await repo.save(item_id, result, basis="b", model="haiku")
    await repo.pin("t3", priority=True, due=True)
    items = [_item("t1"), _item("t2", priority=1), _item("t3")]

    one, two, three = apply_triage(items, await repo.all(), now=NOW)

    assert (one["priority"], one["priority_by"], one["due_by"], one["overdue"]) == (
        0,
        "ai",
        "ai",
        True,
    )
    assert one["ai"]["next_step"] == "提出する"
    # Set before triage existed: a non-default priority is a human's.
    assert (two["priority"], "priority_by" in two) == (1, False)
    # Pinned by a human, even at the default value.
    assert (three["priority"], three["due_at"], "ai" in three) == (2, None, True)
    assert items[0]["priority"] == 2  # the input is not changed


def test_priority_outranks_deadline_and_whose_move_it_is() -> None:
    overdue = (NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    p0_idle = focus_score(_item(priority=0), now=NOW)
    p1_waiting_overdue = focus_score(
        _item(priority=1, status="waiting", bucket="me", due_at=overdue), now=NOW
    )
    assert p0_idle is not None and p1_waiting_overdue is not None
    assert p0_idle > p1_waiting_overdue


def test_focus_ranks_the_humans_move_priority_and_deadline() -> None:
    soon = (NOW + timedelta(hours=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    waiting = focus_score(_item(status="waiting", bucket="me"), now=NOW)
    idle = focus_score(_item(), now=NOW)
    urgent_idle = focus_score(_item(priority=0, due_at=soon), now=NOW)
    assert waiting is not None and idle is not None and urgent_idle is not None
    assert urgent_idle > waiting > idle
    for gone in (
        _item(status="running", bucket="ai"),
        _item(status="done", bucket="done"),
        _item(bucket="snoozed"),
    ):
        assert focus_score(gone, now=NOW) is None


# -- the loop --------------------------------------------------------------


async def test_items_are_triaged_once_per_activity_and_failures_back_off(repo) -> None:
    asked: list[str] = []
    answers = {"Ship the report": '{"priority": 1, "reason": "r", "next": "n"}'}

    async def ask(prompt: str) -> str | None:
        title = prompt.split("Item title: ")[1].split("\n")[0]
        asked.append(title)
        return answers.get(title)

    async def read(item):
        return [("person", "please")]

    triager = Triager(repo, ask=ask, read_messages=read, per_pass=5)
    items = [
        _item("t1"),
        _item("t2", title="Broken"),
        _item("t3", status="running"),
        _item("t4", status="done"),
    ]
    assert await triager.run_once(items) == 1
    assert sorted(asked) == ["Broken", "Ship the report"]
    assert (await repo.all())["t1"].priority == 1

    asked.clear()
    assert await triager.run_once(items) == 0
    assert asked == []  # nothing new, and the failure waits before a retry

    items[0] = _item("t1", last_activity_at="2026-10-12T02:30:00Z")
    await triager.run_once(items)
    assert asked == ["Ship the report"]


async def test_a_pass_is_capped_and_newest_first(repo) -> None:
    async def ask(prompt: str) -> str | None:
        return '{"priority": 2}'

    async def read(item):
        return []

    triager = Triager(repo, ask=ask, read_messages=read, per_pass=2)
    items = [_item(f"t{i}", last_activity_at=f"2026-10-1{i}T00:00:00Z") for i in range(5)]
    picked = [i["id"] for i in triager.due(items, {})]
    assert picked == ["t4", "t3"]


@pytest.mark.skipif(sys.platform == "win32", reason="uses a POSIX shell script")
async def test_the_model_runs_without_tools_hooks_or_relay_credentials(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("CCDB_CONSOLE_TOKEN", "secret")
    monkeypatch.setenv("DISCORD_BOT_TOKEN", "secret")
    assert "CCDB_CONSOLE_TOKEN" not in triage_env()
    assert "DISCORD_BOT_TOKEN" not in triage_env()
    fake = tmp_path / "claude"
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\nenv | grep -c CCDB_CONSOLE_TOKEN || true\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    out = await ask_model("-p rm", command=str(fake), model="haiku")
    assert out is not None
    args = out.splitlines()
    assert args[:4] == ["-p", "--model", "haiku", "--tools"]
    assert '{"disableAllHooks": true}' in args
    assert args[args.index("--") + 1] == "-p rm"
    assert args[-1] == "0"


@pytest.mark.skipif(sys.platform == "win32", reason="uses a POSIX shell script")
async def test_a_missing_or_failing_cli_is_none(tmp_path) -> None:
    assert await ask_model("x", command=str(tmp_path / "nope")) is None
    fake = tmp_path / "claude"
    fake.write_text("#!/bin/sh\nexit 3\n")
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    assert await ask_model("x", command=str(fake)) is None


# -- the server ------------------------------------------------------------


async def test_board_shows_guesses_and_a_human_edit_pins_them(tmp_path) -> None:
    db = str(tmp_path / "sessions.db")
    work = WorkItemRepository(db)
    await work.init_db()
    triage = TriageRepository(db)
    await triage.init_db()
    api = MagicMock()
    api.default_channel_id = None
    api.lineage_repo = None
    api.session_repo = None
    api.bot.get_channel.return_value = None
    api.bot.cogs = {}
    api._running_thread_ids.return_value = set()
    console = ConsoleServer(
        api, work, ConsoleAuthenticator(ConsoleAuthConfig(token=TOKEN)), port=0, triage=triage
    )
    auth = {"Authorization": f"Bearer {TOKEN}"}
    write = {**auth, "X-Console-Request": "1"}
    async with TestClient(TestServer(console.app)) as client:
        created = await client.post("/console/api/items", json={"title": "Taxes"}, headers=write)
        item_id = (await created.json())["item"]["id"]
        await triage.save(
            item_id, TriageResult(0, None, "期限が近い", "書類を出す"), basis="b", model="haiku"
        )
        [item] = (await (await client.get("/console/api/board", headers=auth)).json())["items"]
        assert (item["priority"], item["priority_by"], item["focus"] is not None) == (0, "ai", True)
        assert await console.triage_messages(item) == [("note", "")]

        await client.patch(f"/console/api/items/{item_id}", json={"priority": 2}, headers=write)
        [item] = (await (await client.get("/console/api/board", headers=auth)).json())["items"]
        assert (item["priority"], "priority_by" in item) == (2, False)
        assert item["ai"]["reason"] == "期限が近い"


# -- the second axis: can it be done now? -----------------------------------


def test_waiting_is_parsed_apart_from_importance() -> None:
    raw = (
        '{"priority": 0, "next": "②④を設定する", "ready": false,'
        ' "ready_at": "2026-10-13T20:00", "waiting_for": "明日20時のリマインド"}'
    )
    reply = parse_reply(raw, now=NOW)
    assert reply is not None
    assert (reply.priority, reply.ready, reply.waiting_for) == (0, False, "明日20時のリマインド")
    expected = datetime.fromisoformat("2026-10-13T20:00").astimezone().astimezone(UTC)
    assert reply.ready_at == expected.strftime("%Y-%m-%dT%H:%M:%SZ")
    # An answer without the field (an older prompt) counts as doable now.
    plain = parse_reply('{"priority": 2}', now=NOW)
    assert plain is not None and plain.ready is True


async def test_an_urgent_wait_is_not_actionable_until_its_time(repo) -> None:
    later = (NOW + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    await repo.save(
        "t1",
        TriageResult(0, None, "r", "設定する", ready=False, ready_at=later, waiting_for="20時"),
        basis="b",
        model="haiku",
    )
    await repo.save("t2", TriageResult(3, None, "r", "読む"), basis="b", model="haiku")
    await repo.save(
        "t3", TriageResult(1, None, "r", "n", ready=False, waiting_for="先方"), basis="b", model="m"
    )
    rows = await repo.all()
    wait, now_, open_wait = apply_triage([_item("t1"), _item("t2"), _item("t3")], rows, now=NOW)
    assert wait["focus"] > now_["focus"]  # more important…
    assert (wait["actionable"], now_["actionable"], open_wait["actionable"]) == (False, True, False)
    assert wait["ai"]["waiting_for"] == "20時"
    [due] = apply_triage([_item("t1")], rows, now=NOW + timedelta(hours=4))
    assert due["actionable"] is True  # …and doable once its time has come
    [untriaged] = apply_triage([_item("t9")], rows, now=NOW)
    assert untriaged["actionable"] is True


async def test_a_table_from_the_first_version_gains_the_new_columns(tmp_path) -> None:
    import aiosqlite

    db = str(tmp_path / "old.db")
    async with aiosqlite.connect(db) as conn:
        await conn.execute(
            "CREATE TABLE ai_triage (item_id TEXT PRIMARY KEY, priority INTEGER, due_at TEXT,"
            " reason TEXT, next_step TEXT, basis TEXT, model TEXT, updated_at TEXT,"
            " pinned_priority INTEGER NOT NULL DEFAULT 0, pinned_due INTEGER NOT NULL DEFAULT 0)"
        )
        await conn.execute("INSERT INTO ai_triage (item_id, priority, basis) VALUES ('t1', 1, 'x')")
        await conn.commit()
    repo = TriageRepository(db)
    await repo.init_db()
    await repo.init_db()  # twice is harmless
    assert (await repo.all())["t1"].ready is None


def test_a_new_prompt_version_retriages_everything() -> None:
    from claude_discord.console.triage import PROMPT_VERSION, _basis

    assert _basis(_item()).startswith(f"{PROMPT_VERSION}|")


# -- the third axis: where can it be done? ------------------------------------


def test_a_place_is_parsed_and_known_places_are_offered() -> None:
    reply = parse_reply('{"priority": 1, "place": "事務所", "ready": true}', now=NOW)
    assert reply is not None and (reply.place, reply.ready) == ("事務所", True)
    anywhere = parse_reply('{"priority": 1, "place": null}', now=NOW)
    assert anywhere is not None and anywhere.place is None
    prompt = build_prompt(title="t", status="idle", messages=[], now=NOW, places=["自宅", "会社PC"])
    assert "Places this person already uses: 会社PC, 自宅" in prompt


async def test_a_guessed_place_fills_in_and_a_persons_place_wins(repo) -> None:
    from claude_discord.console.triage import known_places

    result = TriageResult(1, None, "r", "n", place="自宅")
    for item_id in ("t1", "t2", "t3"):
        await repo.save(item_id, result, basis="b", model="haiku")
    await repo.pin("t3", place=True)
    rows = await repo.all()
    items = [_item("t1"), _item("t2", place="会社PC"), _item("t3")]
    one, two, three = apply_triage(items, rows, now=NOW)
    assert (one["place"], one["place_by"]) == ("自宅", "ai")
    assert (two["place"], "place_by" in two) == ("会社PC", False)
    assert (three.get("place"), three["ai"]["place"]) == (None, "自宅")
    assert one["actionable"] is True  # a place is not a wait
    assert known_places(items, rows) == ["会社PC", "自宅"]


async def test_setting_a_place_from_the_console_pins_it(tmp_path) -> None:
    db = str(tmp_path / "sessions.db")
    work = WorkItemRepository(db)
    await work.init_db()
    triage = TriageRepository(db)
    await triage.init_db()
    api = MagicMock()
    api.default_channel_id = None
    api.lineage_repo = None
    api.session_repo = None
    api.bot.get_channel.return_value = None
    api.bot.cogs = {}
    api._running_thread_ids.return_value = set()
    console = ConsoleServer(
        api, work, ConsoleAuthenticator(ConsoleAuthConfig(token=TOKEN)), port=0, triage=triage
    )
    auth = {"Authorization": f"Bearer {TOKEN}"}
    write = {**auth, "X-Console-Request": "1"}
    async with TestClient(TestServer(console.app)) as client:
        created = await client.post(
            "/console/api/items", json={"title": "Swap the router", "place": "自宅"}, headers=write
        )
        item_id = (await created.json())["item"]["id"]
        await triage.save(
            item_id, TriageResult(1, None, "r", "n", place="事務所"), basis="b", model="m"
        )
        [item] = (await (await client.get("/console/api/board", headers=auth)).json())["items"]
        assert (item["place"], "place_by" in item) == ("自宅", False)
        await client.patch(f"/console/api/items/{item_id}", json={"place": None}, headers=write)
        [item] = (await (await client.get("/console/api/board", headers=auth)).json())["items"]
        assert item["place"] is None  # cleared by a person: anywhere, not the guess
        bad = await client.patch(
            f"/console/api/items/{item_id}", json={"place": "x" * 41}, headers=write
        )
        assert bad.status == 400
