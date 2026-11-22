"""Console pages as a manual posting queue for any network (#1518).

A page item can name where it goes (a free label), carry images or videos, and declare a
character limit. Nothing here knows a network by name: the same fields serve X, Instagram,
TikTok or a video site a deployment has never heard of.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from unittest.mock import MagicMock
from urllib.parse import quote

import aiosqlite
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.console.auth import ConsoleAuthConfig, ConsoleAuthenticator
from claude_discord.console.server import ConsoleServer
from claude_discord.console.work_repo import WorkItemRepository
from claude_discord.database import page_files
from claude_discord.database.pages_repo import (
    PageError,
    PageRepository,
    count_chars,
)
from claude_discord.ext import pages_api

TOKEN = "t" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}
WRITE = {**AUTH, "X-Console-Request": "1"}

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64
MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64
MOV = b"\x00\x00\x00\x14ftypqt  " + b"\x00" * 64
WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 64
HTML = b"<html><script>alert(1)</script></html>"

Handler = Callable[[web.Request, PageRepository], Awaitable[web.Response]]


@pytest.fixture
async def repo(tmp_path) -> PageRepository:
    pages = PageRepository(str(tmp_path / "sessions.db"))
    await pages.init_db()
    return pages


@pytest.fixture
async def rest(repo):
    def route(handler: Handler):
        async def wrapped(request: web.Request) -> web.Response:
            return await handler(request, repo)

        return wrapped

    app = web.Application()
    app.router.add_put("/api/pages/{slug}", route(pages_api.handle_put_page))
    app.router.add_delete("/api/pages/{slug}", route(pages_api.handle_delete_page))
    app.router.add_get("/api/pages/{slug}/items", route(pages_api.handle_list_items))
    app.router.add_post("/api/pages/{slug}/items", route(pages_api.handle_add_item))
    app.router.add_post("/api/pages/{slug}/files", route(pages_api.handle_upload_file))
    async with TestClient(TestServer(app)) as client:
        yield client


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


async def _upload(rest, data: bytes, content_type: str, name: str = "clip") -> web.Response:
    return await rest.post(
        "/api/pages/posts/files",
        data=data,
        headers={"Content-Type": content_type, "X-Filename": name},
    )


# -- the posting fields ---------------------------------------------------------------------


async def test_an_item_names_its_target_and_carries_links(repo) -> None:
    await repo.put_page("posts", {"title": "To post", "max_chars": 280, "count": "weighted"})
    item, _ = await repo.add_item(
        "posts",
        {
            "key": "a",
            "body": "hello",
            "target": "Instagram · @shop",
            "attachments": [
                "https://cdn.example.com/banner.png",
                {"url": "https://example.com/v/clip.mp4", "name": "Teaser"},
            ],
        },
    )
    data = item.as_dict()
    assert data["target"] == "Instagram · @shop"
    assert data["attachments"] == [
        {"url": "https://cdn.example.com/banner.png", "name": "banner.png", "kind": "image"},
        {"url": "https://example.com/v/clip.mp4", "name": "Teaser", "kind": "video"},
    ]
    # The page's limit and counting rule apply unless the item says otherwise.
    assert (data["limit"], data["count"], data["length"]) == (280, "weighted", 5)


async def test_an_item_can_override_the_page_limit(repo) -> None:
    await repo.put_page("posts", {"title": "To post", "max_chars": 280, "count": "weighted"})
    item, _ = await repo.add_item(
        "posts", {"key": "a", "body": "あいう", "max_chars": 2, "count": "chars"}
    )
    assert (item.limit, item.length) == (2, 3)


async def test_a_page_without_a_limit_reports_none(repo) -> None:
    await repo.put_page("posts", {"title": "To post"})
    item, _ = await repo.add_item("posts", {"key": "a", "body": "hello"})
    assert (item.limit, item.length) == (0, 5)
    page = (await repo.list_pages())[0].as_dict()
    assert (page["max_chars"], page["count"]) == (0, "chars")


def test_weighted_counting_weighs_wide_characters_and_urls() -> None:
    assert count_chars("abc", "chars") == 3
    assert count_chars("あいう", "chars") == 3
    assert count_chars("あいう", "weighted") == 6
    assert count_chars("abc", "weighted") == 3
    # A link counts as a fixed length however long it is.
    url = "https://example.com/" + "x" * 100
    assert count_chars(f"see {url}", "weighted") == 4 + 23
    assert count_chars("👍🏽", "chars") == 2
    # Text right after a link is still counted (a link ends at the first non-ASCII character).
    assert count_chars("https://example.com/a。続き", "weighted") == 23 + 6
    # An emoji sequence counts once, as one wide character.
    assert count_chars("👍🏽", "weighted") == 2
    assert count_chars("👨‍👩‍👧", "weighted") == 2
    assert count_chars("🇯🇵", "weighted") == 2


async def test_registering_again_refreshes_the_posting_fields(repo) -> None:
    await repo.put_page("posts", {"title": "To post"})
    await repo.add_item("posts", {"key": "a", "body": "x", "target": "X"})
    await repo.resolve_item("posts", "a", {"state": "done"})
    item, created = await repo.add_item(
        "posts", {"key": "a", "body": "x", "target": "Threads", "attachments": []}
    )
    assert (created, item.state, item.target) == (False, "done", "Threads")


async def test_an_item_keeps_its_link_apart_from_the_body(repo) -> None:
    await repo.put_page("posts", {"title": "To post", "max_chars": 10, "count": "weighted"})
    item, _ = await repo.add_item(
        "posts", {"key": "a", "body": "hello", "link": "https://example.com/article"}
    )
    data = item.as_dict()
    assert data["link"] == "https://example.com/article"
    # The body is counted on its own: the link is pasted only if the person chooses to.
    assert data["length"] == 5
    plain, _ = await repo.add_item("posts", {"key": "b", "body": "x"})
    assert plain.as_dict()["link"] == ""


async def test_registering_again_moves_a_link_out_of_the_body(repo) -> None:
    await repo.put_page("posts", {"title": "To post"})
    await repo.add_item("posts", {"key": "a", "body": "read https://example.com/a"})
    item, created = await repo.add_item(
        "posts", {"key": "a", "body": "read", "link": "https://example.com/a"}
    )
    assert (created, item.state, item.body, item.link) == (
        False,
        "open",
        "read",
        "https://example.com/a",
    )


async def test_items_can_be_listed_for_one_target(repo) -> None:
    await repo.put_page("posts", {"title": "To post"})
    await repo.add_item("posts", {"key": "a", "body": "x", "target": "X"})
    await repo.add_item("posts", {"key": "b", "body": "y", "target": "TikTok"})
    await repo.add_item("posts", {"key": "c", "body": "z"})
    assert [i.key for i in await repo.list_items("posts", target="TikTok")] == ["b"]
    assert [i.key for i in await repo.list_items("posts")] == ["a", "b", "c"]


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"target": 1}, "target"),
        ({"target": "x" * 61}, "target"),
        ({"attachments": "https://example.com/a.png"}, "attachments"),
        ({"attachments": ["javascript:alert(1)"]}, "http"),
        ({"attachments": ["/etc/passwd"]}, "http"),
        ({"attachments": [{"url": "https://e.com/a.png", "extra": 1}]}, "unknown"),
        ({"attachments": ["https://e.com/a.png"] * 11}, "at most"),
        ({"attachments": [42]}, "each entry"),
        ({"max_chars": -1}, "max_chars"),
        ({"max_chars": True}, "max_chars"),
        ({"count": "bytes"}, "count"),
        ({"count": ["chars"]}, "count"),
        ({"link": "javascript:alert(1)"}, "link"),
        ({"link": "/relative"}, "link"),
        ({"link": 1}, "link"),
    ],
)
async def test_invalid_posting_fields_are_refused(repo, body: dict, message: str) -> None:
    await repo.put_page("posts", {"title": "To post"})
    with pytest.raises(PageError, match=message):
        await repo.add_item("posts", {"key": "a", "body": "x", **body})


async def test_invalid_page_limits_are_refused(repo) -> None:
    with pytest.raises(PageError, match="max_chars"):
        await repo.put_page("posts", {"title": "T", "max_chars": "280"})
    with pytest.raises(PageError, match="count"):
        await repo.put_page("posts", {"title": "T", "count": "x"})


async def test_a_database_from_before_the_posting_fields_keeps_working(tmp_path) -> None:
    db = str(tmp_path / "old.db")
    async with aiosqlite.connect(db) as conn:
        await conn.executescript(
            """
            CREATE TABLE console_pages (
                slug TEXT PRIMARY KEY, title TEXT NOT NULL,
                open_label TEXT NOT NULL DEFAULT '', done_label TEXT NOT NULL DEFAULT '',
                skip_label TEXT NOT NULL DEFAULT '', position INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE console_page_items (
                page_slug TEXT NOT NULL, key TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL DEFAULT '', source TEXT NOT NULL DEFAULT '',
                state TEXT NOT NULL DEFAULT 'open', resolved_by TEXT NOT NULL DEFAULT '',
                result_url TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL,
                resolved_at TEXT NOT NULL DEFAULT '', PRIMARY KEY (page_slug, key));
            INSERT INTO console_pages VALUES ('x-posts', 'X', '', '', '', 0, 't', 't');
            INSERT INTO console_page_items (page_slug, key, body, created_at)
                VALUES ('x-posts', 'old', 'kept', '2026-11-01T00:00:00Z');
            """
        )
        await conn.commit()
    pages = PageRepository(db)
    await pages.init_db()
    await pages.init_db()  # twice: the upgrade must be idempotent
    [item] = await pages.list_items("x-posts")
    assert (item.key, item.body, item.target, item.attachments) == ("old", "kept", "", [])
    assert item.link == ""


# -- stored attachments -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("data", "content_type", "kind", "served"),
    [
        (PNG, "image/png", "image", "image/png"),
        (JPEG, "image/jpeg", "image", "image/jpeg"),
        (MP4, "video/mp4", "video", "video/mp4"),
        (MOV, "video/quicktime", "video", "video/quicktime"),
        (WEBM, "video/webm", "video", "video/webm"),
    ],
)
async def test_an_uploaded_image_or_video_can_be_attached_and_served(
    rest, console, repo, data: bytes, content_type: str, kind: str, served: str
) -> None:
    await rest.put("/api/pages/posts", json={"title": "To post"})
    stored = await _upload(rest, data, content_type, "%E5%91%8A%E7%9F%A5")
    assert stored.status == 201
    file = await stored.json()
    assert file["kind"] == kind
    assert file["url"].startswith("/console/api/pages/posts/files/")

    added = await rest.post(
        "/api/pages/posts/items", json={"key": "a", "body": "x", "attachments": [file["url"]]}
    )
    assert added.status == 201
    [attachment] = (await added.json())["item"]["attachments"]
    assert attachment["kind"] == kind and attachment["name"] == file["name"]

    shown = await console.get(file["url"], headers=AUTH)
    assert shown.status == 200
    assert await shown.read() == data
    assert shown.headers["Content-Type"] == served
    assert shown.headers["X-Content-Type-Options"] == "nosniff"
    assert "sandbox" in shown.headers["Content-Security-Policy"]
    assert shown.headers["Content-Disposition"].startswith("inline")
    saved = await console.get(file["url"] + "?download=1", headers=AUTH)
    assert saved.headers["Content-Disposition"].startswith("attachment")
    # Signed-in only.
    assert (await console.get(file["url"])).status == 401


@pytest.mark.parametrize(
    ("data", "content_type", "status"),
    [
        (HTML, "text/html", 415),
        (HTML, "image/png", 400),  # claims to be an image, is not
        (PNG, "video/mp4", 400),  # an image is not the video it claims to be
        (b"", "image/png", 400),
        (b"PK\x03\x04", "application/zip", 415),
    ],
)
async def test_uploads_are_checked_by_their_bytes(
    rest, repo, data: bytes, content_type: str, status: int
) -> None:
    await rest.put("/api/pages/posts", json={"title": "To post"})
    response = await _upload(rest, data, content_type)
    assert response.status == status
    assert not any(p.is_file() for p in repo.files.root.rglob("*"))


async def test_uploads_are_bounded_in_size(rest, repo, monkeypatch) -> None:
    monkeypatch.setattr(page_files, "MAX_IMAGE_BYTES", 100)
    await rest.put("/api/pages/posts", json={"title": "To post"})
    response = await _upload(rest, PNG + b"\x00" * 200, "image/png")
    assert response.status == 413
    assert not any(p.is_file() for p in repo.files.root.rglob("*"))


async def test_a_long_non_ascii_name_is_shortened_to_fit_the_disk(rest, repo) -> None:
    await rest.put("/api/pages/posts", json={"title": "To post"})
    name = quote("告知" * 60 + ".png")
    response = await _upload(rest, PNG, "image/png", name)
    assert response.status == 201
    stored = await response.json()
    assert stored["name"].endswith(".png")
    assert len(stored["name"].encode()) <= 200


async def test_an_upload_needs_an_existing_page(rest) -> None:
    assert (await _upload(rest, PNG, "image/png")).status == 404


async def test_an_attachment_must_be_a_file_of_the_same_page(rest, repo) -> None:
    await rest.put("/api/pages/posts", json={"title": "To post"})
    await rest.put("/api/pages/other", json={"title": "Other"})
    file = await (await _upload(rest, PNG, "image/png")).json()
    elsewhere = file["url"].replace("/pages/posts/", "/pages/other/")
    missing = file["url"][:-3] + "zzz"
    for url in (elsewhere, missing, "/console/api/pages/posts/files/../../x"):
        added = await rest.post(
            "/api/pages/other/items" if url == elsewhere else "/api/pages/posts/items",
            json={"key": "a", "body": "x", "attachments": [url]},
        )
        assert added.status == 400, url


async def test_a_stored_file_that_is_not_listed_is_not_served(console, repo) -> None:
    await repo.put_page("posts", {"title": "To post"})
    base = "/console/api/pages/posts/files"
    for url in (f"{base}/{'0' * 32}/a.png", f"{base}/nothex/a.png", f"{base}/{'0' * 32}/..%2Fx"):
        assert (await console.get(url, headers=AUTH)).status == 404, url


async def test_deleting_a_page_removes_its_files(rest, repo) -> None:
    await rest.put("/api/pages/posts", json={"title": "To post"})
    await _upload(rest, PNG, "image/png")
    assert any(p.is_file() for p in repo.files.root.rglob("*"))
    assert (await rest.delete("/api/pages/posts")).status == 200
    assert not any(p.is_file() for p in Path(repo.files.root).rglob("*"))


# -- what a person does -----------------------------------------------------------------------


async def test_a_person_can_paste_the_post_url_when_marking_done(console, repo) -> None:
    await repo.put_page("posts", {"title": "To post"})
    await repo.add_item("posts", {"key": "a", "body": "x"})
    url = "/console/api/pages/posts/items/a"
    bad = await console.patch(
        url, json={"state": "done", "result_url": "javascript:x"}, headers=WRITE
    )
    assert bad.status == 400
    res = await console.patch(
        url,
        json={"state": "done", "result_url": "https://example.com/p/1", "resolved_by": "bot"},
        headers=WRITE,
    )
    item = (await res.json())["item"]
    assert (item["resolved_by"], item["result_url"]) == ("person", "https://example.com/p/1")


async def test_the_console_lists_one_target(console, repo) -> None:
    await repo.put_page("posts", {"title": "To post"})
    await repo.add_item("posts", {"key": "a", "body": "x", "target": "X"})
    await repo.add_item("posts", {"key": "b", "body": "y", "target": "TikTok"})
    listed = await console.get("/console/api/pages/posts/items?target=TikTok", headers=AUTH)
    assert [i["key"] for i in (await listed.json())["items"]] == ["b"]


async def test_the_rest_api_lists_one_target(rest) -> None:
    await rest.put("/api/pages/posts", json={"title": "To post"})
    await rest.post("/api/pages/posts/items", json={"key": "a", "body": "x", "target": "X"})
    await rest.post("/api/pages/posts/items", json={"key": "b", "body": "y", "target": "Y"})
    listed = await (await rest.get("/api/pages/posts/items?target=X")).json()
    assert [i["key"] for i in listed["items"]] == ["a"]


async def test_the_console_may_play_stored_videos(console) -> None:
    response = await console.get("/", headers=AUTH)
    csp = response.headers["Content-Security-Policy"]
    media = next(d for d in csp.split(";") if d.strip().startswith("media-src")).split()
    assert "blob:" in media and "*" not in media and "https:" not in media
