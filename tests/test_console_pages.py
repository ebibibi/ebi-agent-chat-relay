"""Declarative Console pages: registered over /api/pages, worked through in the Console."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from unittest.mock import MagicMock

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.console.auth import ConsoleAuthConfig, ConsoleAuthenticator
from claude_discord.console.server import ConsoleServer
from claude_discord.console.work_repo import WorkItemRepository
from claude_discord.database.pages_repo import (
    MAX_PAGES,
    PageError,
    PageNotFoundError,
    PageRepository,
)
from claude_discord.ext import pages_api

TOKEN = "t" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}
WRITE = {**AUTH, "X-Console-Request": "1"}

Handler = Callable[[web.Request, PageRepository], Awaitable[web.Response]]


@pytest.fixture
async def repo(tmp_path) -> PageRepository:
    pages = PageRepository(str(tmp_path / "sessions.db"))
    await pages.init_db()
    return pages


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


@pytest.fixture
async def rest(repo):
    def route(handler: Handler):
        async def wrapped(request: web.Request) -> web.Response:
            return await handler(request, repo)

        return wrapped

    app = web.Application()
    app.router.add_get("/api/pages", route(pages_api.handle_list_pages))
    app.router.add_put("/api/pages/{slug}", route(pages_api.handle_put_page))
    app.router.add_delete("/api/pages/{slug}", route(pages_api.handle_delete_page))
    app.router.add_get("/api/pages/{slug}/items", route(pages_api.handle_list_items))
    app.router.add_post("/api/pages/{slug}/items", route(pages_api.handle_add_item))
    app.router.add_patch("/api/pages/{slug}/items/{key}", route(pages_api.handle_resolve_item))
    async with TestClient(TestServer(app)) as client:
        yield client


async def test_registering_again_never_reopens_a_finished_item(repo) -> None:
    await repo.put_page("posts", {"title": "To post"})
    await repo.add_item("posts", {"key": "a", "body": "first"})
    await repo.resolve_item("posts", "a", {"state": "done", "resolved_by": "watcher"})

    item, created = await repo.add_item("posts", {"key": "a", "body": "second"})

    assert created is False
    assert item.state == "done"
    assert item.body == "second"
    assert item.resolved_by == "watcher"


async def test_open_items_come_oldest_first(repo) -> None:
    await repo.put_page("posts", {"title": "To post"})
    await repo.add_item("posts", {"key": "new", "body": "n", "created_at": "2026-11-02T00:00:00Z"})
    await repo.add_item(
        "posts", {"key": "old", "body": "o", "created_at": "2026-11-01T09:00:00+09:00"}
    )
    assert [i.key for i in await repo.list_items("posts")] == ["old", "new"]


async def test_reopening_clears_the_claimed_outcome(repo) -> None:
    await repo.put_page("posts", {"title": "To post"})
    await repo.add_item("posts", {"key": "a", "body": "x"})
    await repo.resolve_item(
        "posts",
        "a",
        {"state": "done", "resolved_by": "watcher", "result_url": "https://example.com/1"},
    )

    item = await repo.resolve_item("posts", "a", {"state": "open"})

    assert (item.state, item.resolved_by, item.result_url, item.resolved_at) == ("open", "", "", "")


@pytest.mark.parametrize(
    "url", ["javascript:alert(1)", "data:text/html,x", "//example.com/x", "file:///etc/passwd"]
)
async def test_only_http_links_can_be_stored(repo, url: str) -> None:
    await repo.put_page("posts", {"title": "To post"})
    with pytest.raises(PageError, match="http"):
        await repo.add_item("posts", {"key": "a", "body": "x", "url": url})
    await repo.add_item("posts", {"key": "b", "body": "x"})
    with pytest.raises(PageError, match="http"):
        await repo.resolve_item("posts", "b", {"state": "done", "result_url": url})


@pytest.mark.parametrize("slug", ["Posts", "a/b", "-a", "", "a" * 41, "日本語"])
async def test_a_slug_that_is_not_safe_is_refused(repo, slug: str) -> None:
    with pytest.raises(PageError, match="slug"):
        await repo.put_page(slug, {"title": "T"})


async def test_invalid_definitions_are_refused(repo) -> None:
    with pytest.raises(PageError, match="title"):
        await repo.put_page("posts", {})
    with pytest.raises(PageError, match="unknown fields: titel"):
        await repo.put_page("posts", {"title": "T", "titel": "typo"})
    await repo.put_page("posts", {"title": "T"})
    with pytest.raises(PageError, match="key"):
        await repo.add_item("posts", {"key": "a/b", "body": "x"})
    with pytest.raises(PageError, match="title or a body"):
        await repo.add_item("posts", {"key": "a"})
    with pytest.raises(PageError, match="timezone"):
        await repo.add_item("posts", {"key": "a", "body": "x", "created_at": "2026-11-01T00:00:00"})
    with pytest.raises(PageNotFoundError):
        await repo.add_item("missing", {"key": "a", "body": "x"})


async def test_the_number_of_pages_is_bounded_but_an_existing_one_can_change(repo) -> None:
    for index in range(MAX_PAGES):
        await repo.put_page(f"p{index}", {"title": "T"})
    with pytest.raises(PageError, match="at most"):
        await repo.put_page("one-more", {"title": "T"})
    assert (await repo.put_page("p0", {"title": "Renamed"})).title == "Renamed"


async def test_deleting_a_page_removes_its_items(repo) -> None:
    await repo.put_page("posts", {"title": "T"})
    await repo.add_item("posts", {"key": "a", "body": "x"})
    await repo.delete_page("posts")
    await repo.put_page("posts", {"title": "T"})
    assert await repo.list_items("posts") == []
    with pytest.raises(PageNotFoundError):
        await repo.delete_page("missing")


async def test_rest_api_round_trip(rest) -> None:
    assert (await rest.put("/api/pages/posts", json={"title": "To post"})).status == 200
    first = await rest.post(
        "/api/pages/posts/items",
        json={"key": "a", "body": "hello", "url": "https://example.com/compose?text=hello"},
    )
    assert first.status == 201
    again = await rest.post("/api/pages/posts/items", json={"key": "a", "body": "hello"})
    assert again.status == 200
    assert (await again.json())["status"] == "exists"

    pages = (await (await rest.get("/api/pages")).json())["pages"]
    assert [(p["slug"], p["open_count"]) for p in pages] == [("posts", 1)]

    resolved = await rest.patch(
        "/api/pages/posts/items/a",
        json={"state": "done", "resolved_by": "watcher", "result_url": "https://example.com/1"},
    )
    assert (await resolved.json())["item"]["resolved_by"] == "watcher"
    assert (await (await rest.get("/api/pages/posts/items")).json())["items"] == []
    done = await (await rest.get("/api/pages/posts/items?state=done")).json()
    assert done["items"][0]["result_url"] == "https://example.com/1"


async def test_rest_api_reports_bad_requests(rest) -> None:
    assert (await rest.get("/api/pages/missing/items")).status == 404
    assert (await rest.put("/api/pages/posts", data=b"{")).status == 400
    assert (await rest.put("/api/pages/posts", json=["not", "an", "object"])).status == 400
    await rest.put("/api/pages/posts", json={"title": "T"})
    assert (await rest.get("/api/pages/posts/items?limit=x")).status == 400
    assert (await rest.get("/api/pages/posts/items?state=nope")).status == 400


async def test_pages_come_with_the_board_and_a_person_resolves_items(console, repo) -> None:
    await repo.put_page("posts", {"title": "To post"})
    await repo.add_item("posts", {"key": "a", "body": "hello"})

    board = await (await console.get("/console/api/board", headers=AUTH)).json()
    assert [(p["slug"], p["open_count"]) for p in board["pages"]] == [("posts", 1)]
    listed = await (await console.get("/console/api/pages/posts/items", headers=AUTH)).json()
    assert [i["key"] for i in listed["items"]] == ["a"]

    res = await console.patch(
        "/console/api/pages/posts/items/a",
        json={"state": "skipped", "resolved_by": "someone-else", "result_url": "https://x.test"},
        headers=WRITE,
    )
    item = (await res.json())["item"]
    # Who resolved it comes from the route, never from what the browser sent.
    assert (item["state"], item["resolved_by"], item["result_url"]) == ("skipped", "person", "")


async def test_console_routes_are_authenticated_and_csrf_protected(console, repo) -> None:
    await repo.put_page("posts", {"title": "T"})
    await repo.add_item("posts", {"key": "a", "body": "x"})
    assert (await console.get("/console/api/pages/posts/items")).status == 401
    no_csrf = await console.patch(
        "/console/api/pages/posts/items/a", json={"state": "done"}, headers=AUTH
    )
    assert no_csrf.status == 403


async def test_console_reports_bad_changes(console, repo) -> None:
    await repo.put_page("posts", {"title": "T"})
    await repo.add_item("posts", {"key": "a", "body": "x"})
    url = "/console/api/pages/posts/items"
    assert (await console.patch(f"{url}/a", json={"state": "x"}, headers=WRITE)).status == 400
    assert (await console.patch(f"{url}/b", json={"state": "done"}, headers=WRITE)).status == 404
    assert (await console.get(f"{url}?state=x", headers=AUTH)).status == 400
    assert (await console.get("/console/api/pages/none/items", headers=AUTH)).status == 404


async def test_a_signed_in_person_can_define_a_page(console) -> None:
    created = await console.post("/console/api/pages/mine", json={"title": "Mine"}, headers=WRITE)
    assert created.status == 200
    added = await console.post(
        "/console/api/pages/mine/items", json={"key": "a", "body": "x"}, headers=WRITE
    )
    assert (await added.json())["item"]["state"] == "open"
