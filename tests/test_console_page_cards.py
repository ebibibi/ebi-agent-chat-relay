"""One card per piece of content on a posting page (#1564).

Items may share a ``group`` (the content they promote) and the Console shows each group as one
card with a row per target. An automation registers its own rows as ``auto`` and reports the
outcome on the same card; a failure falls back to a row a person finishes. Open rows fade and
are skipped after the days the page sets. Nothing here names a network.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import aiosqlite
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.console.auth import ConsoleAuthConfig, ConsoleAuthenticator
from claude_discord.console.server import ConsoleServer
from claude_discord.console.work_repo import WorkItemRepository
from claude_discord.database.pages_cards import list_cards
from claude_discord.database.pages_repo import PageError, PageRepository
from claude_discord.ext import pages_api

TOKEN = "t" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}
WRITE = {**AUTH, "X-Console-Request": "1"}
T0 = datetime(2026, 11, 24, 0, 0, tzinfo=UTC)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture
async def repo(tmp_path) -> PageRepository:
    pages = PageRepository(str(tmp_path / "sessions.db"))
    await pages.init_db()
    await pages.put_page("out", {"title": "📣 配信", "fade_days": 3, "expire_days": 7})
    return pages


async def _video(repo: PageRepository, group: str = "yt-abc", **extra) -> None:
    """A video promoted on four targets: two posted by automation, two by hand."""
    base = {"group": group, "group_title": "Regions explained", "source": "promote"}
    for key, target, mode in (
        ("auto-a", "Net A", "auto"),
        ("auto-b", "Net B", "auto"),
        ("hand-c", "Net C", "manual"),
        ("hand-d", "Net D", "manual"),
    ):
        await repo.add_item(
            "out",
            {"key": f"{group}:{key}", "target": target, "mode": mode, "body": f"text {key}"}
            | base
            | extra,
        )


# -- the fields ------------------------------------------------------------------------------


async def test_items_carry_a_group_title_and_mode(repo) -> None:
    await _video(repo)
    items = await repo.list_items("out")
    first = items[0].as_dict()
    assert first["group"] == "yt-abc"
    assert first["group_title"] == "Regions explained"
    assert first["mode"] == "auto"
    assert first["error"] == ""
    assert [i.mode for i in items] == ["auto", "auto", "manual", "manual"]


async def test_an_item_without_the_new_fields_is_a_manual_item_of_its_own(repo) -> None:
    item, _ = await repo.add_item("out", {"key": "plain", "body": "hello"})
    data = item.as_dict()
    assert (data["group"], data["group_title"], data["mode"]) == ("", "", "manual")
    assert data["stalled"] is False and data["faded"] is False


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"group": "has space"}, "group"),
        ({"group": "x" * 129}, "group"),
        ({"group_title": "x" * 201}, "group_title"),
        ({"mode": "robot"}, "mode"),
        ({"error": "boom"}, "unknown fields"),
    ],
)
async def test_invalid_card_fields_are_refused(repo, body: dict, message: str) -> None:
    with pytest.raises(PageError, match=message):
        await repo.add_item("out", {"key": "k", "body": "b", **body})


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"fade_days": -1}, "fade_days"),
        ({"expire_days": 400}, "expire_days"),
        ({"fade_days": True}, "fade_days"),
    ],
)
async def test_invalid_page_days_are_refused(repo, body: dict, message: str) -> None:
    with pytest.raises(PageError, match=message):
        await repo.put_page("other", {"title": "x", **body})


async def test_a_page_reports_its_days(repo) -> None:
    page = await repo.get_page("out")
    assert page is not None
    assert (page.as_dict()["fade_days"], page.as_dict()["expire_days"]) == (3, 7)
    plain = await repo.put_page("plain", {"title": "Plain"})
    assert (plain.fade_days, plain.expire_days) == (0, 0)


async def test_registering_again_keeps_the_mode_a_failure_set(repo) -> None:
    await _video(repo)
    await repo.resolve_item(
        "out", "yt-abc:auto-a", {"state": "open", "mode": "manual", "error": "token expired"}
    )
    await _video(repo)  # the generator runs twice
    item = next(i for i in await repo.list_items("out") if i.key == "yt-abc:auto-a")
    assert (item.mode, item.error) == ("manual", "token expired")


# -- the outcome of an automatic post -------------------------------------------------------


async def test_an_automatic_post_is_reported_done_with_its_url(repo) -> None:
    await _video(repo)
    item = await repo.resolve_item(
        "out",
        "yt-abc:auto-a",
        {"state": "done", "resolved_by": "poster", "result_url": "https://a.example/p/1"},
    )
    assert (item.state, item.resolved_by, item.result_url) == (
        "done",
        "poster",
        "https://a.example/p/1",
    )


async def test_a_failed_automatic_post_falls_back_to_a_manual_row(repo) -> None:
    await _video(repo)
    item = await repo.resolve_item(
        "out", "yt-abc:auto-b", {"state": "open", "mode": "manual", "error": "401 from the API"}
    )
    assert (item.state, item.mode, item.error) == ("open", "manual", "401 from the API")
    # Finishing it by hand clears the error: it no longer describes the row.
    done = await repo.resolve_item("out", "yt-abc:auto-b", {"state": "done"})
    assert done.error == ""


async def test_an_error_needs_an_open_row(repo) -> None:
    await _video(repo)
    with pytest.raises(PageError, match="error"):
        await repo.resolve_item("out", "yt-abc:auto-a", {"state": "done", "error": "x"})
    with pytest.raises(PageError, match="mode"):
        await repo.resolve_item("out", "yt-abc:auto-a", {"state": "open", "mode": "robot"})


async def test_open_count_leaves_out_rows_automation_is_still_posting(repo) -> None:
    await _video(repo)
    page = await repo.get_page("out")
    assert page is not None and page.open_count == 2  # the two manual rows


async def test_an_automatic_row_that_never_reports_needs_a_person(repo) -> None:
    await _video(repo, created_at=_iso(T0))
    later = T0 + timedelta(hours=7)
    items = {i.key: i for i in await repo.list_items("out", now=later)}
    assert items["yt-abc:auto-a"].stalled is True
    assert items["yt-abc:hand-c"].stalled is False
    page = next(p for p in await repo.list_pages(now=later) if p.slug == "out")
    assert page.open_count == 4


# -- fading and expiring --------------------------------------------------------------------


async def test_open_rows_fade_after_the_page_days(repo) -> None:
    await _video(repo, created_at=_iso(T0))
    items = {i.key: i for i in await repo.list_items("out", now=T0 + timedelta(days=3, hours=1))}
    assert items["yt-abc:hand-c"].faded is True
    items = {i.key: i for i in await repo.list_items("out", now=T0 + timedelta(days=2))}
    assert items["yt-abc:hand-c"].faded is False


async def test_open_rows_are_skipped_after_the_page_days(repo) -> None:
    await _video(repo, created_at=_iso(T0))
    await repo.resolve_item("out", "yt-abc:auto-a", {"state": "done", "resolved_by": "poster"})
    expired = await repo.expire_stale(now=T0 + timedelta(days=7, minutes=1))
    assert expired == 3
    skipped = await repo.list_items("out", "skipped")
    assert {i.key for i in skipped} == {"yt-abc:auto-b", "yt-abc:hand-c", "yt-abc:hand-d"}
    assert {i.resolved_by for i in skipped} == {"expired"}
    done = await repo.list_items("out", "done")
    assert [i.key for i in done] == ["yt-abc:auto-a"]


async def test_a_page_without_days_never_fades_or_expires(repo) -> None:
    await repo.put_page("plain", {"title": "Plain"})
    await repo.add_item("plain", {"key": "old", "body": "x", "created_at": _iso(T0)})
    assert await repo.expire_stale(now=T0 + timedelta(days=400)) == 0
    [item] = await repo.list_items("plain", now=T0 + timedelta(days=400))
    assert item.state == "open" and item.faded is False


async def test_listing_pages_expires_stale_rows_on_the_way(repo) -> None:
    await _video(repo, created_at=_iso(T0 - timedelta(days=30)))
    pages = await repo.list_pages()
    assert next(p for p in pages if p.slug == "out").open_count == 0


# -- cards ----------------------------------------------------------------------------------


async def test_a_group_is_one_card_with_its_rows_in_registration_order(repo) -> None:
    await _video(repo)
    await repo.add_item("out", {"key": "lonely", "body": "no group"})
    cards = await list_cards(repo, "out")
    assert [c["group"] for c in cards] == ["yt-abc", ""]
    video = cards[0]
    assert video["title"] == "Regions explained"
    assert [i["target"] for i in video["items"]] == ["Net A", "Net B", "Net C", "Net D"]
    assert [i["key"] for i in cards[1]["items"]] == ["lonely"]


async def test_an_open_card_keeps_its_finished_rows(repo) -> None:
    await _video(repo)
    await repo.resolve_item("out", "yt-abc:auto-a", {"state": "done", "resolved_by": "poster"})
    await repo.resolve_item("out", "yt-abc:hand-d", {"state": "skipped"})
    [card] = await list_cards(repo, "out")
    assert [i["state"] for i in card["items"]] == ["done", "open", "open", "skipped"]
    assert await list_cards(repo, "out", "done") == []


async def test_a_card_moves_to_done_when_no_row_is_open(repo) -> None:
    await _video(repo)
    for key in ("auto-a", "auto-b", "hand-c"):
        await repo.resolve_item("out", f"yt-abc:{key}", {"state": "done"})
    await repo.resolve_item("out", "yt-abc:hand-d", {"state": "skipped"})
    assert await list_cards(repo, "out") == []
    [card] = await list_cards(repo, "out", "done")
    assert len(card["items"]) == 4
    assert await list_cards(repo, "out", "skipped") == []


async def test_a_card_whose_rows_were_all_skipped_is_on_the_skipped_tab(repo) -> None:
    await _video(repo, created_at=_iso(T0))
    await repo.expire_stale(now=T0 + timedelta(days=8))
    [card] = await list_cards(repo, "out", "skipped")
    assert card["group"] == "yt-abc"


async def test_open_cards_are_oldest_first_and_limited(repo) -> None:
    await _video(repo, group="new", created_at=_iso(T0 + timedelta(hours=2)))
    await _video(repo, group="old", created_at=_iso(T0))
    cards = await list_cards(repo, "out", now=T0 + timedelta(hours=3))
    assert [c["group"] for c in cards] == ["old", "new"]
    assert [c["group"] for c in await list_cards(repo, "out", limit=1)] == ["old"]


async def test_a_card_title_falls_back_to_its_first_item(repo) -> None:
    await repo.add_item("out", {"key": "a", "group": "g", "title": "First", "body": "x"})
    await repo.add_item("out", {"key": "b", "group": "g", "body": "y"})
    [card] = await list_cards(repo, "out")
    assert card["title"] == "First"


async def test_items_can_be_listed_for_one_group(repo) -> None:
    await _video(repo, group="one")
    await _video(repo, group="two")
    items = await repo.list_items("out", group="two")
    assert {i.group for i in items} == {"two"} and len(items) == 4


async def test_a_database_from_before_cards_keeps_working(tmp_path) -> None:
    path = str(tmp_path / "old.db")
    async with aiosqlite.connect(path) as db:
        await db.executescript(
            """
            CREATE TABLE console_pages (slug TEXT PRIMARY KEY, title TEXT NOT NULL,
                open_label TEXT NOT NULL DEFAULT '', done_label TEXT NOT NULL DEFAULT '',
                skip_label TEXT NOT NULL DEFAULT '', position INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE console_page_items (page_slug TEXT NOT NULL, key TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL DEFAULT 'open', resolved_by TEXT NOT NULL DEFAULT '',
                result_url TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
                resolved_at TEXT NOT NULL DEFAULT '', PRIMARY KEY (page_slug, key));
            INSERT INTO console_pages VALUES ('old', 'Old', '', '', '', 0, 'x', 'x');
            INSERT INTO console_page_items (page_slug, key, body, created_at)
                VALUES ('old', 'k', 'kept', '2026-01-01T00:00:00Z');
            """
        )
        await db.commit()
    repo = PageRepository(path)
    await repo.init_db()
    [card] = await list_cards(repo, "old")
    assert card["items"][0]["body"] == "kept"
    assert card["items"][0]["mode"] == "manual"


# -- the HTTP side ---------------------------------------------------------------------------


@pytest.fixture
async def rest(repo):
    def route(handler):
        async def wrapped(request: web.Request) -> web.Response:
            return await handler(request, repo)

        return wrapped

    app = web.Application()
    app.router.add_get("/api/pages/{slug}/cards", route(pages_api.handle_list_cards))
    app.router.add_get("/api/pages/{slug}/items", route(pages_api.handle_list_items))
    app.router.add_post("/api/pages/{slug}/items", route(pages_api.handle_add_item))
    app.router.add_patch("/api/pages/{slug}/items/{key}", route(pages_api.handle_resolve_item))
    async with TestClient(TestServer(app)) as client:
        yield client


async def test_the_rest_api_registers_and_reports_an_automatic_post(rest) -> None:
    row = {
        "key": "yt-abc:net-a",
        "group": "yt-abc",
        "group_title": "Regions explained",
        "target": "Net A",
        "mode": "auto",
        "body": "text",
    }
    assert (await rest.post("/api/pages/out/items", json=row)).status == 201
    ok = await rest.patch(
        "/api/pages/out/items/yt-abc:net-a",
        json={"state": "done", "resolved_by": "poster", "result_url": "https://a.example/1"},
    )
    assert ok.status == 200
    resp = await rest.get("/api/pages/out/cards?state=done")
    [card] = (await resp.json())["cards"]
    assert card["items"][0]["result_url"] == "https://a.example/1"
    resp = await rest.get("/api/pages/out/items?group=yt-abc&state=done")
    assert len((await resp.json())["items"]) == 1


async def test_the_rest_api_refuses_a_bad_card_state(rest) -> None:
    resp = await rest.get("/api/pages/out/cards?state=nope")
    assert resp.status == 400
    resp = await rest.get("/api/pages/missing/cards")
    assert resp.status == 404


@pytest.fixture
async def console(tmp_path, repo):
    work = WorkItemRepository(str(tmp_path / "sessions.db"))
    await work.init_db()
    api = MagicMock()
    api.default_channel_id = None
    api.lineage_repo = None
    api.session_repo = None
    api.bot.get_channel.return_value = None
    api.bot.cogs = {}
    api._running_thread_ids.return_value = set()
    api.page_repo = repo
    server = ConsoleServer(api, work, ConsoleAuthenticator(ConsoleAuthConfig(token=TOKEN)), port=0)
    async with TestClient(TestServer(server.app)) as client:
        yield client


async def test_the_console_lists_cards(console, repo) -> None:
    await _video(repo)
    resp = await console.get("/console/api/pages/out/cards?state=open", headers=AUTH)
    assert resp.status == 200
    [card] = (await resp.json())["cards"]
    assert len(card["items"]) == 4


async def test_a_person_cannot_change_a_rows_mode(console, repo) -> None:
    await _video(repo)
    resp = await console.patch(
        "/console/api/pages/out/items/yt-abc:auto-a",
        json={"state": "done", "mode": "manual", "error": "x"},
        headers=WRITE,
    )
    assert resp.status == 200
    item = (await resp.json())["item"]
    assert (item["mode"], item["error"], item["resolved_by"]) == ("auto", "", "person")
