"""The same file attached to several rows is kept once (#1601).

A card's Instagram and TikTok rows usually carry the very same video, and a generator uploads it
once per row. A phone should download it once, so the page keeps one copy per content (sha256):
an upload whose bytes are already on the page answers the stored file's URL, and copies kept
before this change are merged into one, rewriting the items that list them.
"""

from __future__ import annotations

import json
import uuid

import aiosqlite
import pytest

from claude_discord.database import page_files
from claude_discord.database.pages_repo import PageRepository

MP4 = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 64
OTHER = b"\x00\x00\x00\x18ftypisom" + b"\x01" * 64


async def _chunks(data: bytes):
    yield data[:10]
    yield data[10:]


@pytest.fixture
async def repo(tmp_path) -> PageRepository:
    pages = PageRepository(str(tmp_path / "sessions.db"))
    await pages.init_db()
    await pages.put_page("out", {"title": "Out"})
    return pages


def _tokens(repo: PageRepository, slug: str = "out") -> set[str]:
    page = repo.files.root / slug
    return {p.name for p in page.iterdir() if p.is_dir() and not p.name.startswith(".")}


async def test_the_same_bytes_uploaded_twice_are_kept_once(repo) -> None:
    first = await repo.files.save("out", _chunks(MP4), "video/mp4", "instagram")
    second = await repo.files.save("out", _chunks(MP4), "video/mp4", "tiktok")
    assert second.url == first.url and second.name == first.name
    assert len(_tokens(repo)) == 1


async def test_different_bytes_are_different_files(repo) -> None:
    first = await repo.files.save("out", _chunks(MP4), "video/mp4", "a")
    second = await repo.files.save("out", _chunks(OTHER), "video/mp4", "b")
    assert first.url != second.url
    assert len(_tokens(repo)) == 2


async def test_files_are_shared_within_a_page_only(repo) -> None:
    await repo.put_page("other", {"title": "Other"})
    first = await repo.files.save("out", _chunks(MP4), "video/mp4", "a")
    second = await repo.files.save("other", _chunks(MP4), "video/mp4", "a")
    assert first.url != second.url


async def test_a_removed_copy_is_stored_again(repo) -> None:
    first = await repo.files.save("out", _chunks(MP4), "video/mp4", "a")
    _, token, _ = page_files.parse_url(first.url)
    repo.files.remove("out", token)
    second = await repo.files.save("out", _chunks(MP4), "video/mp4", "a")
    assert second.url != first.url
    assert repo.files.describe(*page_files.parse_url(second.url)) is not None


def _place(repo: PageRepository, data: bytes, name: str) -> str:
    """A file kept the old way: one copy per upload, with no content index."""
    token = uuid.uuid4().hex
    folder = repo.files.root / "out" / token
    folder.mkdir(parents=True)
    (folder / name).write_bytes(data)
    return page_files.file_url("out", token, name)


async def _attachments(repo: PageRepository, key: str) -> list[dict]:
    async with aiosqlite.connect(repo._db_path) as db:
        [(raw,)] = await db.execute_fetchall(
            "SELECT attachments FROM console_page_items WHERE key = ?", (key,)
        )
    return json.loads(raw)


async def test_copies_kept_before_are_merged_into_one(repo) -> None:
    ig = _place(repo, MP4, "opening.mp4")
    tiktok = _place(repo, MP4, "opening.mp4")
    threads = _place(repo, OTHER, "threads.mp4")
    for key, url in (("ig", ig), ("tt", tiktok), ("th", threads)):
        await repo.add_item("out", {"key": key, "body": key, "attachments": [url]})

    freed = await repo.merge_duplicate_files()

    assert freed == len(MP4)
    [a] = await _attachments(repo, "ig")
    [b] = await _attachments(repo, "tt")
    [c] = await _attachments(repo, "th")
    assert a["url"] == b["url"] == ig and c["url"] == threads
    assert a["size"] == len(MP4)
    assert len(_tokens(repo)) == 2  # the duplicate copy is gone, the others stay
    # Merged files are indexed: the next upload of the same bytes finds them.
    again = await repo.files.save("out", _chunks(MP4), "video/mp4", "x")
    assert again.url == ig
    # Running it again changes nothing.
    assert await repo.merge_duplicate_files() == 0


async def test_merging_keeps_a_copy_some_item_still_lists_elsewhere(repo) -> None:
    keep = _place(repo, MP4, "a.mp4")
    dup = _place(repo, MP4, "b.mp4")
    await repo.add_item("out", {"key": "one", "body": "x", "attachments": [keep]})
    await repo.add_item("out", {"key": "two", "body": "y", "attachments": [dup, keep]})
    await repo.merge_duplicate_files()
    urls = [a["url"] for a in await _attachments(repo, "two")]
    assert urls == [keep, keep] or urls == [keep]
    assert repo.files.describe(*page_files.parse_url(keep)) is not None
