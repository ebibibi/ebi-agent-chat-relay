"""Long videos on a Console page (#1538): a configurable limit, disk space, ranges, retention.

A 20-minute 1080p recording is about 1.2 GB. It must upload without being held in memory, be
refused clearly when the disk cannot take it, be served in ranges (seek, resume, a proxy that
cuts long responses), and not stay on disk for ever once it has been posted.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import aiosqlite
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.console.auth import ConsoleAuthConfig, ConsoleAuthenticator
from claude_discord.console.server import ConsoleServer
from claude_discord.console.work_repo import WorkItemRepository
from claude_discord.database import page_files
from claude_discord.database.page_files import PageFiles
from claude_discord.database.pages_repo import PageRepository
from claude_discord.ext import pages_api

TOKEN = "t" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}
MB = 1024 * 1024
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
MP4 = b"\x00\x00\x00\x18ftypisom" + bytes(range(256)) * 4

Handler = Callable[[web.Request, PageRepository], Awaitable[web.Response]]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch) -> None:
    for name in (
        page_files.ENV_MAX_VIDEO_MB,
        page_files.ENV_MIN_FREE_MB,
        page_files.ENV_VIDEO_RETAIN_DAYS,
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
async def repo(tmp_path) -> PageRepository:
    pages = PageRepository(str(tmp_path / "sessions.db"))
    await pages.init_db()
    await pages.put_page("posts", {"title": "To post"})
    return pages


@pytest.fixture
async def rest(repo):
    def route(handler: Handler):
        async def wrapped(request: web.Request) -> web.Response:
            return await handler(request, repo)

        return wrapped

    app = web.Application()
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


async def _upload(rest, data, content_type: str = "video/mp4") -> web.Response:
    return await rest.post(
        "/api/pages/posts/files",
        data=data,
        headers={"Content-Type": content_type, "X-Filename": "talk.mp4"},
    )


def _files(repo: PageRepository) -> list[Path]:
    return [p for p in repo.files.root.rglob("*") if p.is_file()]


# -- the limit ----------------------------------------------------------------------------------


def test_the_video_limit_defaults_to_4_gb_and_images_stay_at_10_mb(tmp_path) -> None:
    files = PageFiles(tmp_path)
    assert files.size_limit("video/mp4") == 4 * 1024 * MB
    assert files.size_limit("image/png") == 10 * MB
    assert files.min_free_bytes == 1024 * MB
    assert files.video_retain_days == 30


def test_the_limits_are_read_from_the_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(page_files.ENV_MAX_VIDEO_MB, "8192")
    monkeypatch.setenv(page_files.ENV_MIN_FREE_MB, "0")
    monkeypatch.setenv(page_files.ENV_VIDEO_RETAIN_DAYS, "0")
    files = PageFiles(tmp_path)
    assert files.size_limit("video/webm") == 8 * 1024 * MB
    assert files.size_limit("image/jpeg") == 10 * MB
    assert (files.min_free_bytes, files.video_retain_days) == (0, 0)


@pytest.mark.parametrize("raw", ["lots", "0", "-5", "1.5"])
def test_an_unusable_limit_falls_back_to_the_default(tmp_path, monkeypatch, raw) -> None:
    monkeypatch.setenv(page_files.ENV_MAX_VIDEO_MB, raw)
    assert PageFiles(tmp_path).max_video_bytes == page_files.DEFAULT_MAX_VIDEO_BYTES


async def test_a_video_over_the_limit_is_refused_and_leaves_nothing(rest, repo) -> None:
    repo.files.max_video_bytes = 512
    response = await _upload(rest, MP4)
    assert response.status == 413
    assert "larger than" in (await response.json())["error"]
    assert _files(repo) == []


async def test_a_video_far_over_the_old_200_mb_is_accepted(rest, repo) -> None:
    """The declared length decides early; the stream never sits in memory."""

    async def body() -> AsyncIterator[bytes]:
        yield MP4[:16]
        for _ in range(4):
            yield b"\x00" * MB

    response = await _upload(rest, body())
    assert response.status == 201, await response.text()
    assert (await response.json())["size"] == 16 + 4 * MB


# -- disk space ---------------------------------------------------------------------------------


async def test_an_upload_the_disk_cannot_take_is_refused_with_507(rest, repo) -> None:
    repo.files.min_free_bytes = 1024 * MB
    repo.files._disk_free = lambda _path: 2000 * MB
    assert (await _upload(rest, MP4)).status == 201  # fits beside the reserve
    # The declared length is checked before the first byte is written.
    repo.files._disk_free = lambda _path: 1024 * MB + 100
    response = await _upload(rest, MP4)
    assert response.status == 507
    assert "disk space" in (await response.json())["error"]
    assert len(_files(repo)) == 1


async def test_a_stream_of_unknown_length_stops_when_the_disk_fills(
    rest, repo, monkeypatch
) -> None:
    monkeypatch.setattr(page_files, "_SPACE_CHECK_BYTES", MB)
    free = iter([10_000 * MB, 0])
    repo.files._disk_free = lambda _path: next(free, 0)

    async def body() -> AsyncIterator[bytes]:
        yield MP4[:16]
        for _ in range(3):
            yield b"\x00" * MB

    response = await _upload(rest, body())
    assert response.status == 507
    assert _files(repo) == []


def test_free_space_is_measured_where_the_files_will_go(tmp_path) -> None:
    assert page_files._disk_free(tmp_path / "not" / "yet" / "made") > 0


# -- serving in ranges --------------------------------------------------------------------------


async def test_a_stored_video_is_served_in_ranges(rest, console, repo) -> None:
    url = (await (await _upload(rest, MP4)).json())["url"]
    whole = await console.get(url, headers=AUTH)
    assert whole.status == 200
    assert whole.headers["Accept-Ranges"] == "bytes"
    assert "Content-Encoding" not in whole.headers

    part = await console.get(url, headers={**AUTH, "Range": "bytes=100-199"})
    assert part.status == 206
    assert part.headers["Content-Range"] == f"bytes 100-199/{len(MP4)}"
    assert await part.read() == MP4[100:200]

    tail = await console.get(
        url + "?download=1", headers={**AUTH, "Range": f"bytes={len(MP4) - 8}-"}
    )
    assert tail.status == 206
    assert await tail.read() == MP4[-8:]
    assert tail.headers["Content-Disposition"].startswith("attachment")

    # Ranges change nothing about who may read.
    assert (await console.get(url, headers={"Range": "bytes=0-9"})).status == 401


# -- retention ----------------------------------------------------------------------------------


async def _item(repo: PageRepository, key: str, urls: list[str], resolved_days_ago: int | None):
    await repo.add_item("posts", {"key": key, "body": key, "attachments": urls})
    if resolved_days_ago is None:
        return
    await repo.resolve_item("posts", key, {"state": "done"})
    when = (datetime.now(UTC) - timedelta(days=resolved_days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    async with aiosqlite.connect(repo._db_path) as db:
        await db.execute(
            "UPDATE console_page_items SET resolved_at = ? WHERE page_slug = 'posts' AND key = ?",
            (when, key),
        )
        await db.commit()


async def test_videos_of_long_resolved_items_are_cleared_on_the_next_upload(rest, repo) -> None:
    async def upload(data: bytes, content_type: str) -> str:
        return (await (await _upload(rest, data, content_type)).json())["url"]

    old, recent, open_, shared, image = [
        await upload(MP4, "video/mp4"),
        await upload(MP4, "video/mp4"),
        await upload(MP4, "video/mp4"),
        await upload(MP4, "video/mp4"),
        await upload(PNG, "image/png"),
    ]
    await _item(repo, "old", [old, shared, image], resolved_days_ago=31)
    await _item(repo, "recent", [recent], resolved_days_ago=29)
    await _item(repo, "open", [open_, shared], resolved_days_ago=None)
    before = {p.parent.name for p in _files(repo)}

    assert (await _upload(rest, MP4)).status == 201

    after = {p.parent.name for p in _files(repo)}
    gone = before - after
    assert gone == {old.split("/")[-2]}, "only the old item's own video goes"
    for kept in (recent, open_, shared, image):
        assert kept.split("/")[-2] in after


async def test_a_retention_of_zero_keeps_every_video(repo) -> None:
    repo.files.video_retain_days = 0
    assert await repo.prune_videos(datetime.now(UTC) + timedelta(days=3650)) == 0


async def test_a_failed_cleanup_does_not_cost_the_upload(rest, repo, monkeypatch) -> None:
    async def broken(*_args, **_kwargs) -> int:
        raise RuntimeError("disk on fire")

    monkeypatch.setattr(repo, "prune_videos", broken)
    assert (await _upload(rest, MP4)).status == 201
