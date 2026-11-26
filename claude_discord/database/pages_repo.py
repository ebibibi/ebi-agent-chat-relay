"""Declarative Console pages: a screen that automation adds by registering data.

A page is a named list of things only a person can finish (a post to send by hand, a form to
fill in). Nothing here runs in the browser and nothing is specific to one kind of work: the
owner of a page registers text and links, the Console renders them as text, and either a
person or the automation that can observe the outcome resolves each item.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import aiosqlite

from .page_files import PageFiles, StoredFile, kind_of_name, parse_url

logger = logging.getLogger(__name__)

MAX_PAGES = 20
MAX_TITLE_CHARS = 60
MAX_LABEL_CHARS = 20
MAX_ITEM_TITLE_CHARS = 200
MAX_BODY_CHARS = 10_000
MAX_SOURCE_CHARS = 200
MAX_RESOLVER_CHARS = 60
MAX_TARGET_CHARS = 60
MAX_ATTACHMENTS = 10
MAX_ATTACHMENT_NAME_CHARS = 120
#: The largest character limit a page or item may declare (a long-form video description).
MAX_CHAR_LIMIT = 100_000
# An X compose link carries the whole post percent-encoded, about nine bytes per character.
MAX_URL_CHARS = 16_000
DEFAULT_ITEM_LIMIT = 200
MAX_ITEM_LIMIT = 1000
MAX_GROUP_TITLE_CHARS = 200
MAX_ERROR_CHARS = 500
#: The most days a page may let an open item wait before it fades or is skipped.
MAX_DAYS = 365

STATE_OPEN = "open"
STATE_DONE = "done"
STATE_SKIPPED = "skipped"
STATES = frozenset({STATE_OPEN, STATE_DONE, STATE_SKIPPED})

#: Who finishes an item (#1564). "manual": a person. "auto": an automation that posts it and
#: reports the outcome; until it does, nobody is asked to act on the item.
MODE_MANUAL = "manual"
MODE_AUTO = "auto"
MODES = frozenset({MODE_MANUAL, MODE_AUTO})
#: How long an automatic item may stay open without an outcome before a person is asked to
#: finish it. Posting over an API takes minutes; hours of silence mean the automation is gone.
AUTO_RESULT_WAIT = timedelta(hours=6)
#: ``resolved_by`` of an item the page skipped because it waited longer than ``expire_days``.
EXPIRED_BY = "expired"
#: Expiring runs at most this often when the board lists pages.
_EXPIRE_EVERY_SECONDS = 60.0

#: How a body is counted against a limit. "chars" counts code points. "weighted" counts the way
#: networks that budget by display width do: wide (CJK, emoji) characters as 2 and every link as
#: 23, whatever its length. Neither names a network: a page picks the rule its target uses.
COUNT_CHARS = "chars"
COUNT_WEIGHTED = "weighted"
COUNTS = frozenset({COUNT_CHARS, COUNT_WEIGHTED})
WEIGHTED_URL_LENGTH = 23
_NARROW_RANGES = ((0, 4351), (8192, 8205), (8208, 8223), (8242, 8247))
_ZWJ = 0x200D
_INVISIBLE = frozenset({_ZWJ, 0xFE0E, 0xFE0F})
_SKIN_TONES = range(0x1F3FB, 0x1F400)
_FLAG_LETTERS = range(0x1F1E6, 0x1F200)
# A link ends at the first space or non-ASCII character: "https://e.com/a。続き" is a link and text.
_URL = re.compile(r"https?://[\x21-\x7e]+", re.IGNORECASE)

_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_KEY = re.compile(r"[\w.:-]{1,128}")
_LABELS = ("open_label", "done_label", "skip_label")
_DAYS = ("fade_days", "expire_days")
_PAGE_FIELDS = frozenset({"title", "position", "max_chars", "count", *_LABELS, *_DAYS})
_ITEM_FIELDS = frozenset(
    {"key", "title", "body", "url", "source", "created_at"}
    | {"target", "attachments", "max_chars", "count", "link"}
    | {"group", "group_title", "mode"}
)
_ATTACHMENT_FIELDS = frozenset({"url", "name"})
_RESOLVE_FIELDS = frozenset({"state", "result_url", "resolved_by", "mode", "error"})
_TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS console_pages (
    slug TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    open_label TEXT NOT NULL DEFAULT '',
    done_label TEXT NOT NULL DEFAULT '',
    skip_label TEXT NOT NULL DEFAULT '',
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    max_chars INTEGER NOT NULL DEFAULT 0,
    count TEXT NOT NULL DEFAULT '',
    fade_days INTEGER NOT NULL DEFAULT 0,
    expire_days INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS console_page_items (
    page_slug TEXT NOT NULL REFERENCES console_pages(slug) ON DELETE CASCADE,
    key TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    body TEXT NOT NULL DEFAULT '',
    url TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'open',
    resolved_by TEXT NOT NULL DEFAULT '',
    result_url TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    resolved_at TEXT NOT NULL DEFAULT '',
    target TEXT NOT NULL DEFAULT '',
    attachments TEXT NOT NULL DEFAULT '',
    max_chars INTEGER NOT NULL DEFAULT 0,
    count TEXT NOT NULL DEFAULT '',
    link TEXT NOT NULL DEFAULT '',
    group_key TEXT NOT NULL DEFAULT '',
    group_title TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT 'manual',
    error TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (page_slug, key)
);
CREATE INDEX IF NOT EXISTS idx_console_page_items_state
    ON console_page_items(page_slug, state, created_at);
"""
#: Columns added after the first release (#1518), added to an existing database on start.
_ADDED_COLUMNS = {
    "console_pages": (
        ("max_chars", "INTEGER NOT NULL DEFAULT 0"),
        ("count", "TEXT NOT NULL DEFAULT ''"),
        # How long an open item may wait before it fades and is skipped (#1564).
        ("fade_days", "INTEGER NOT NULL DEFAULT 0"),
        ("expire_days", "INTEGER NOT NULL DEFAULT 0"),
    ),
    "console_page_items": (
        ("target", "TEXT NOT NULL DEFAULT ''"),
        ("attachments", "TEXT NOT NULL DEFAULT ''"),
        ("max_chars", "INTEGER NOT NULL DEFAULT 0"),
        ("count", "TEXT NOT NULL DEFAULT ''"),
        # The link to share, kept apart from the body so a person can leave it out (#1530).
        ("link", "TEXT NOT NULL DEFAULT ''"),
        # One card per piece of content, and rows an automation posts (#1564).
        ("group_key", "TEXT NOT NULL DEFAULT ''"),
        ("group_title", "TEXT NOT NULL DEFAULT ''"),
        ("mode", "TEXT NOT NULL DEFAULT 'manual'"),
        ("error", "TEXT NOT NULL DEFAULT ''"),
    ),
}


class PageError(ValueError):
    """A page or item that cannot be stored. The message is written for the caller to act on."""


class PageNotFoundError(LookupError):
    """The page or item a request names does not exist."""


@dataclass(frozen=True)
class Page:
    slug: str
    title: str
    open_label: str
    done_label: str
    skip_label: str
    position: int
    open_count: int
    max_chars: int = 0
    count: str = COUNT_CHARS
    #: Days an open item waits before it is shown faded, and before it is skipped; 0 = never.
    fade_days: int = 0
    expire_days: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "title": self.title,
            "open_label": self.open_label,
            "done_label": self.done_label,
            "skip_label": self.skip_label,
            "position": self.position,
            "open_count": self.open_count,
            "max_chars": self.max_chars,
            "count": self.count,
            "fade_days": self.fade_days,
            "expire_days": self.expire_days,
        }


@dataclass(frozen=True)
class PageItem:
    key: str
    title: str
    body: str
    url: str
    source: str
    state: str
    resolved_by: str
    result_url: str
    created_at: str
    resolved_at: str
    target: str = ""
    attachments: list[dict[str, Any]] = field(default_factory=list)
    #: The limit and counting rule in force (the item's own, else the page's); 0 = no limit.
    limit: int = 0
    count: str = COUNT_CHARS
    #: The body measured by that rule, so every client shows the same number.
    length: int = 0
    #: A link to share with the post, copied on its own: some networks limit posts with links,
    #: so the person decides each time whether to paste it. Not counted in ``length``.
    link: str = ""
    #: The piece of content the item promotes; items sharing it form one card (#1564).
    group: str = ""
    group_title: str = ""
    mode: str = MODE_MANUAL
    #: Why automatic posting failed, kept while the row waits for a person.
    error: str = ""
    #: An automatic item that has not reported within ``AUTO_RESULT_WAIT``: a person finishes it.
    stalled: bool = False
    #: An open item a person should finish that is older than the page's ``fade_days``.
    faded: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "title": self.title,
            "body": self.body,
            "url": self.url,
            "source": self.source,
            "state": self.state,
            "resolved_by": self.resolved_by,
            "result_url": self.result_url,
            "created_at": self.created_at,
            "resolved_at": self.resolved_at,
            "target": self.target,
            "attachments": [dict(a) for a in self.attachments],
            "limit": self.limit,
            "count": self.count,
            "length": self.length,
            "link": self.link,
            "group": self.group,
            "group_title": self.group_title,
            "mode": self.mode,
            "error": self.error,
            "stalled": self.stalled,
            "faded": self.faded,
        }


def _now() -> str:
    return datetime.now(UTC).strftime(_TIME_FORMAT)


def _object(body: Any, allowed: frozenset[str]) -> dict[str, Any]:
    if not isinstance(body, dict):
        raise PageError("the body must be a JSON object")
    unknown = sorted(set(body) - allowed)
    if unknown:
        raise PageError(f"unknown fields: {', '.join(unknown)}")
    return body


def _text(body: dict[str, Any], field: str, limit: int, *, required: bool = False) -> str:
    value = body.get(field, "")
    if not isinstance(value, str):
        raise PageError(f"{field} must be a string")
    text = value.strip()
    if required and not text:
        raise PageError(f"{field} is required")
    if len(text) > limit:
        raise PageError(f"{field} must be at most {limit} characters")
    return text


def _link(body: dict[str, Any], field: str) -> str:
    """Only http(s): this value becomes the target of a link a person clicks."""
    url = _text(body, field, MAX_URL_CHARS)
    return _check_link(url, field) if url else ""


def _timestamp(body: dict[str, Any]) -> str | None:
    value = body.get("created_at")
    if value is None:
        return None
    if not isinstance(value, str):
        raise PageError("created_at must be a string")
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PageError("created_at must be an ISO 8601 time") from exc
    if moment.tzinfo is None:
        raise PageError("created_at must include a timezone")
    return moment.astimezone(UTC).strftime(_TIME_FORMAT)


def count_chars(text: str, rule: str) -> int:
    """How long *text* is under a counting rule (see ``COUNTS``)."""
    if rule != COUNT_WEIGHTED:
        return len(text)
    links = _URL.findall(text)
    total = WEIGHTED_URL_LENGTH * len(links)
    joined = False  # the previous character was a zero-width joiner
    flag_half = False  # a regional indicator is waiting for its pair
    for char in _URL.sub("", text):
        point = ord(char)
        glued, joined = joined, point == _ZWJ
        if point in _INVISIBLE or point in _SKIN_TONES or unicodedata.category(char) == "Mn":
            continue
        if glued:
            continue  # part of an emoji sequence already counted
        if point in _FLAG_LETTERS:
            flag_half = not flag_half
            if not flag_half:
                continue  # the second letter of a flag
        narrow = any(low <= point <= high for low, high in _NARROW_RANGES)
        total += 1 if narrow else 2
    return total


def _limit(body: dict[str, Any]) -> int:
    value = body.get("max_chars", 0)
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_CHAR_LIMIT:
        raise PageError(f"max_chars must be an integer from 0 to {MAX_CHAR_LIMIT}")
    return value


def _count_rule(body: dict[str, Any], default: str) -> str:
    value = body.get("count") or default
    if not isinstance(value, str) or (value not in COUNTS and value != ""):
        raise PageError(f"count must be one of: {', '.join(sorted(COUNTS))}")
    return str(value)


def _days(body: dict[str, Any], field: str) -> int:
    value = body.get(field, 0)
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= MAX_DAYS:
        raise PageError(f"{field} must be an integer from 0 to {MAX_DAYS}")
    return value


def _mode(body: dict[str, Any]) -> str | None:
    value = body.get("mode")
    if value is None:
        return None
    if value not in MODES:
        raise PageError(f"mode must be one of: {', '.join(sorted(MODES))}")
    return str(value)


def _group(body: dict[str, Any]) -> str:
    value = body.get("group", "")
    if value in ("", None):
        return ""
    if not isinstance(value, str) or not _KEY.fullmatch(value):
        raise PageError("group must be 1-128 letters, digits, '_', '.', ':' or '-'")
    return value


def _check_link(url: str, field: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise PageError(f"{field} is not a valid URL") from exc
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise PageError(f"{field} must be an http or https URL or a file uploaded to this page")
    return url


def check_slug(slug: str) -> str:
    if not _SLUG.fullmatch(slug):
        raise PageError("slug must be lowercase letters, digits and hyphens (at most 40)")
    return slug


def _check_key(key: object) -> str:
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        raise PageError("key must be 1-128 letters, digits, '_', '.', ':' or '-'")
    return key


def check_state(state: object) -> str:
    if state not in STATES:
        raise PageError(f"state must be one of: {', '.join(sorted(STATES))}")
    return str(state)


ITEM_COLUMNS = (
    "key, title, body, url, source, state, resolved_by, result_url, created_at, resolved_at,"
    " target, attachments, max_chars, count, link, group_key, group_title, mode, error"
)
_PAGE_COLUMNS = "p.slug, p.title, p.open_label, p.done_label, p.skip_label, p.position"


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime(_TIME_FORMAT)


def make_item(row: tuple[Any, ...], page: Page, now: datetime | None = None) -> PageItem:
    """An item from a row of ``ITEM_COLUMNS``, with what is derived from its page and age."""
    *base, target, attachments, max_chars, count, link, group, group_title, mode, error = row
    rule = count or page.count
    limit = max_chars or page.max_chars
    moment = now or datetime.now(UTC)
    created_at, is_open = base[8], base[5] == STATE_OPEN
    stalled = is_open and mode == MODE_AUTO and created_at < _stamp(moment - AUTO_RESULT_WAIT)
    needs_person = is_open and (mode != MODE_AUTO or stalled)
    fade_cutoff = _stamp(moment - timedelta(days=page.fade_days))
    return PageItem(
        *base,
        target=target,
        attachments=json.loads(attachments) if attachments else [],
        limit=limit,
        count=rule,
        length=count_chars(base[2], rule),
        link=link,
        group=group,
        group_title=group_title,
        mode=mode or MODE_MANUAL,
        error=error,
        stalled=stalled,
        faded=needs_person and page.fade_days > 0 and created_at < fade_cutoff,
    )


class PageRepository:
    """CRUD for ``console_pages`` and their items. One short-lived connection per call.

    Uploaded attachments live in *files_dir*, by default ``page_files`` next to the database.
    """

    def __init__(self, db_path: str, files_dir: Path | None = None) -> None:
        self._db_path = db_path
        self._expired_at = float("-inf")
        self.files = PageFiles(files_dir or Path(db_path).parent / "page_files")
        self._merging: asyncio.Task[int] | None = None  # kept so the task is not collected

    @property
    def db_path(self) -> str:
        return self._db_path

    async def init_db(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
            for table, columns in _ADDED_COLUMNS.items():
                rows = await db.execute_fetchall(f"PRAGMA table_info({table})")
                present = {row[1] for row in rows}
                for name, definition in columns:
                    if name not in present:
                        await db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
            await db.commit()

    async def list_pages(self, now: datetime | None = None) -> list[Page]:
        """Every page with the number of open items a person should finish.

        The board calls this every few seconds, so it is also where open items past their
        page's ``expire_days`` are skipped: at most once a minute, or whenever *now* is given.
        """
        if now is not None or time.monotonic() - self._expired_at >= _EXPIRE_EVERY_SECONDS:
            self._expired_at = time.monotonic()
            await self.expire_stale(now)
        return await self._pages(now)

    async def _pages(self, now: datetime | None = None) -> list[Page]:
        # An automatic item counts once it has waited too long for its outcome.
        cutoff = _stamp((now or datetime.now(UTC)) - AUTO_RESULT_WAIT)
        async with aiosqlite.connect(self._db_path) as db:
            rows = await db.execute_fetchall(
                f"""
                SELECT {_PAGE_COLUMNS},
                       (SELECT COUNT(*) FROM console_page_items i
                         WHERE i.page_slug = p.slug AND i.state = 'open'
                           AND (i.mode != 'auto' OR i.created_at < ?)),
                       p.max_chars, p.count, p.fade_days, p.expire_days
                  FROM console_pages p ORDER BY p.position, p.created_at
                """,  # noqa: S608 (constants)
                (cutoff,),
            )
        return [
            Page(
                *row[:7],
                max_chars=row[7],
                count=row[8] or COUNT_CHARS,
                fade_days=row[9],
                expire_days=row[10],
            )
            for row in rows
        ]

    async def get_page(self, slug: str) -> Page | None:
        return next((p for p in await self._pages() if p.slug == slug), None)

    async def expire_stale(self, now: datetime | None = None) -> int:
        """Skip open items older than their page's ``expire_days``; answers how many.

        A queue a person never empties turns into debt: what was not posted within a week is
        no longer worth posting. Pages without ``expire_days`` are never touched.
        """
        moment = now or datetime.now(UTC)
        total = 0
        async with aiosqlite.connect(self._db_path) as db:
            pages = await db.execute_fetchall(
                "SELECT slug, expire_days FROM console_pages WHERE expire_days > 0"
            )
            for slug, days in pages:
                cursor = await db.execute(
                    "UPDATE console_page_items SET state = ?, resolved_by = ?, resolved_at = ?"
                    " WHERE page_slug = ? AND state = ? AND created_at < ?",
                    (
                        STATE_SKIPPED,
                        EXPIRED_BY,
                        _stamp(moment),
                        slug,
                        STATE_OPEN,
                        _stamp(moment - timedelta(days=days)),
                    ),
                )
                total += max(cursor.rowcount, 0)
            await db.commit()
        return total

    async def _require_page(self, slug: str) -> Page:
        page = await self.get_page(slug)
        if page is None:
            raise PageNotFoundError(f"unknown page: {slug}")
        return page

    async def put_page(self, slug: str, body: Any) -> Page:
        """Create the page or replace its definition; its items are left as they are."""
        check_slug(slug)
        fields = _object(body, _PAGE_FIELDS)
        title = _text(fields, "title", MAX_TITLE_CHARS, required=True)
        labels = [_text(fields, label, MAX_LABEL_CHARS) for label in _LABELS]
        position = fields.get("position", 0)
        if isinstance(position, bool) or not isinstance(position, int):
            raise PageError("position must be an integer")
        max_chars = _limit(fields)
        rule = _count_rule(fields, COUNT_CHARS)
        days = [_days(fields, name) for name in _DAYS]
        if await self.get_page(slug) is None and len(await self._pages()) >= MAX_PAGES:
            raise PageError(f"at most {MAX_PAGES} pages")
        now = _now()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO console_pages (slug, title, open_label, done_label, skip_label,
                                           position, created_at, updated_at, max_chars, count,
                                           fade_days, expire_days)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                    title = excluded.title, open_label = excluded.open_label,
                    done_label = excluded.done_label, skip_label = excluded.skip_label,
                    position = excluded.position, updated_at = excluded.updated_at,
                    max_chars = excluded.max_chars, count = excluded.count,
                    fade_days = excluded.fade_days, expire_days = excluded.expire_days
                """,
                (slug, title, *labels, position, now, now, max_chars, rule, *days),
            )
            await db.commit()
        page = await self.get_page(slug)
        if page is None:
            raise RuntimeError(f"page {slug} was not stored")
        return page

    async def delete_page(self, slug: str) -> None:
        """Remove the page, its items and the files uploaded to it."""
        async with aiosqlite.connect(self._db_path) as db:
            # Explicit: SQLite ignores ON DELETE CASCADE unless each connection turns it on.
            await db.execute("DELETE FROM console_page_items WHERE page_slug = ?", (slug,))
            cursor = await db.execute("DELETE FROM console_pages WHERE slug = ?", (slug,))
            await db.commit()
        if cursor.rowcount == 0:
            raise PageNotFoundError(f"unknown page: {slug}")
        # Off the event loop: a page may hold large videos.
        await asyncio.to_thread(self.files.remove_page, slug)

    async def prune_videos(self, now: datetime | None = None) -> int:
        """Delete videos only resolved items list, resolved over the retention period ago.

        A posted video has done its job, and at a gigabyte each they would fill the disk. A file
        any open or recently resolved item still lists is kept, and so is every image. Answers
        the bytes freed; a retention of 0 days keeps everything.
        """
        days = self.files.video_retain_days
        if days <= 0:
            return 0
        cutoff = ((now or datetime.now(UTC)) - timedelta(days=days)).strftime(_TIME_FORMAT)
        async with aiosqlite.connect(self._db_path) as db:
            rows = await db.execute_fetchall(
                "SELECT state, resolved_at, attachments FROM console_page_items"
                " WHERE attachments != ''"
            )
        keep: set[tuple[str, str]] = set()
        stale: set[tuple[str, str]] = set()
        for state, resolved_at, attachments in rows:
            fresh = state == STATE_OPEN or resolved_at >= cutoff
            for entry in json.loads(attachments):
                stored = parse_url(entry.get("url", ""))
                if stored is None or entry.get("kind") != "video":
                    continue
                (keep if fresh else stale).add(stored[:2])
        doomed = stale - keep
        if not doomed:
            return 0
        return sum(
            [await asyncio.to_thread(self.files.remove, slug, token) for slug, token in doomed]
        )

    async def merge_duplicate_files(self) -> int:
        """Keep one copy per content of files listed before uploads were deduplicated (#1601).

        Each stored attachment is hashed once; the copy registered first becomes the one every
        item lists, the items are rewritten (a row listing the same file twice lists it once),
        and a copy no item lists any more is deleted. Answers the bytes freed. Running it again
        changes nothing.
        """
        async with aiosqlite.connect(self._db_path) as db:
            rows = await db.execute_fetchall(
                "SELECT page_slug, key, attachments FROM console_page_items"
                " WHERE attachments != '' ORDER BY created_at, key"
            )
        digests: dict[tuple[str, str, str], str | None] = {}
        canonical: dict[tuple[str, str], StoredFile] = {}
        dropped: set[tuple[str, str]] = set()
        updates: list[tuple[str, str, str]] = []
        for slug, key, raw in rows:
            entries = json.loads(raw)
            merged: list[dict[str, Any]] = []
            for entry in entries:
                file = await self._canonical(slug, entry, digests, canonical)
                if file is None:
                    merged.append(entry)
                    continue
                stored = parse_url(entry.get("url", ""))
                if stored is not None and file.url != entry.get("url"):
                    dropped.add(stored[:2])
                fresh = {
                    **entry,
                    "url": file.url,
                    "name": file.name,
                    "kind": file.kind,
                    "size": file.size,
                }
                if all(m.get("url") != file.url for m in merged):
                    merged.append(fresh)
            if merged != entries:
                updates.append((json.dumps(merged, ensure_ascii=False), slug, key))
        if updates:
            async with aiosqlite.connect(self._db_path) as db:
                await db.executemany(
                    "UPDATE console_page_items SET attachments = ? WHERE page_slug = ? AND key = ?",
                    updates,
                )
                await db.commit()
        return await self._remove_unlisted(dropped)

    async def _canonical(
        self,
        slug: str,
        entry: dict[str, Any],
        digests: dict[tuple[str, str, str], str | None],
        canonical: dict[tuple[str, str], StoredFile],
    ) -> StoredFile | None:
        """The one stored file with this attachment's bytes, or None for a link or a lost file."""
        stored = parse_url(entry.get("url", ""))
        if stored is None or stored[0] != slug:
            return None
        if stored not in digests:
            digests[stored] = await asyncio.to_thread(self.files.digest, *stored)
        digest = digests[stored]
        if digest is None:
            return None
        if (slug, digest) not in canonical:
            known = self.files.by_digest(slug, digest)
            if known is None:
                known = self.files.describe(*stored)
                if known is None:
                    return None
                self.files.remember(slug, digest, stored[1], known.name)
            canonical[(slug, digest)] = known
        return canonical[(slug, digest)]

    async def _remove_unlisted(self, candidates: set[tuple[str, str]]) -> int:
        """Delete the copies in *candidates* that no item lists; answer the bytes freed."""
        if not candidates:
            return 0
        async with aiosqlite.connect(self._db_path) as db:
            rows = await db.execute_fetchall(
                "SELECT attachments FROM console_page_items WHERE attachments != ''"
            )
        listed = {
            stored[:2]
            for (raw,) in rows
            for entry in json.loads(raw)
            if (stored := parse_url(entry.get("url", ""))) is not None
        }
        doomed = candidates - listed
        return sum([await asyncio.to_thread(self.files.remove, *pair) for pair in doomed])

    def start_merging_duplicate_files(self) -> asyncio.Task[int] | None:
        """Run :meth:`merge_duplicate_files` in the background once at start-up (hashing may
        take a while for long videos); failures are logged, never raised."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return None

        async def run() -> int:
            try:
                freed = await self.merge_duplicate_files()
            except Exception:  # housekeeping must not take the service down
                logger.exception("Could not merge duplicate page files")
                return 0
            if freed:
                logger.info("Merged duplicate page files, freeing %d bytes", freed)
            return freed

        self._merging = loop.create_task(run())
        return self._merging

    async def list_items(
        self,
        slug: str,
        state: str = STATE_OPEN,
        limit: int = DEFAULT_ITEM_LIMIT,
        target: str | None = None,
        *,
        group: str | None = None,
        now: datetime | None = None,
    ) -> list[PageItem]:
        """Items in one state: open ones oldest first, resolved ones latest first.

        *target* or *group*, when given, keeps only the items registered for it.
        """
        check_state(state)
        page = await self._require_page(slug)
        order = "created_at, key" if state == STATE_OPEN else "resolved_at DESC, key"
        where = "page_slug = ? AND state = ?"
        params: tuple[Any, ...] = (slug, state)
        for column, value in (("target", target), ("group_key", group)):
            if value is not None:
                where += f" AND {column} = ?"
                params = (*params, value)
        async with aiosqlite.connect(self._db_path) as db:
            rows = await db.execute_fetchall(
                f"SELECT {ITEM_COLUMNS} FROM console_page_items"  # noqa: S608 (constants)
                f" WHERE {where} ORDER BY {order} LIMIT ?",
                (*params, max(1, min(limit, MAX_ITEM_LIMIT))),
            )
        return [make_item(tuple(row), page, now) for row in rows]

    async def add_item(self, slug: str, body: Any) -> tuple[PageItem, bool]:
        """Register an item. The second value is False when its key already existed.

        Registering again refreshes the text, links, target and attachments but never the
        state: a generator that runs twice must not bring back what a person already finished.
        """
        fields = _object(body, _ITEM_FIELDS)
        key = _check_key(fields.get("key"))
        title = _text(fields, "title", MAX_ITEM_TITLE_CHARS)
        text = _text(fields, "body", MAX_BODY_CHARS)
        if not title and not text:
            raise PageError("an item needs a title or a body")
        url = _link(fields, "url")
        link = _link(fields, "link")
        source = _text(fields, "source", MAX_SOURCE_CHARS)
        target = _text(fields, "target", MAX_TARGET_CHARS)
        max_chars = _limit(fields)
        rule = _count_rule(fields, "")
        group = _group(fields)
        group_title = _text(fields, "group_title", MAX_GROUP_TITLE_CHARS)
        mode = _mode(fields) or MODE_MANUAL
        created_at = _timestamp(fields) or _now()
        await self._require_page(slug)
        attachments = json.dumps(self._attachments(slug, fields.get("attachments")))
        values = (title, text, url, source, target, attachments, max_chars, rule, link)
        values += (group, group_title)
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO console_page_items
                    (page_slug, key, title, body, url, source, target, attachments, max_chars,
                     count, link, group_key, group_title, mode, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(page_slug, key) DO NOTHING
                """,
                (slug, key, *values, mode, created_at),
            )
            created = cursor.rowcount > 0
            # The mode is left alone too: a failed automatic post that fell back to a person
            # must not go back to waiting for the automation when the generator runs again.
            if not created:
                await db.execute(
                    "UPDATE console_page_items SET title = ?, body = ?, url = ?, source = ?,"
                    " target = ?, attachments = ?, max_chars = ?, count = ?, link = ?,"
                    " group_key = ?, group_title = ?"
                    " WHERE page_slug = ? AND key = ?",
                    (*values, slug, key),
                )
            await db.commit()
        return await self._require_item(slug, key), created

    def _attachments(self, slug: str, raw: object) -> list[dict[str, Any]]:
        """Checked attachments: links people may open, or files uploaded to this page."""
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise PageError("attachments must be a list")
        if len(raw) > MAX_ATTACHMENTS:
            raise PageError(f"at most {MAX_ATTACHMENTS} attachments")
        return [self._attachment(slug, entry) for entry in raw]

    def _attachment(self, slug: str, entry: object) -> dict[str, Any]:
        if not isinstance(entry, str | dict):
            raise PageError("attachments: each entry must be a URL or an object with a url")
        fields = _object({"url": entry} if isinstance(entry, str) else entry, _ATTACHMENT_FIELDS)
        url = _text(fields, "url", MAX_URL_CHARS, required=True)
        name = _text(fields, "name", MAX_ATTACHMENT_NAME_CHARS)
        stored = parse_url(url)
        if stored is not None:
            file_slug, token, file_name = stored
            found = self.files.describe(file_slug, token, file_name) if file_slug == slug else None
            if found is None:
                raise PageError("attachments: no such file was uploaded to this page")
            # The size lets the Console say how big a download is before anyone starts it.
            return {
                "url": found.url,
                "name": name or found.name,
                "kind": found.kind,
                "size": found.size,
            }
        _check_link(url, "attachments")
        fallback = unquote(urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]) or url
        name = name or fallback[:MAX_ATTACHMENT_NAME_CHARS]
        return {"url": url, "name": name, "kind": kind_of_name(urlsplit(url).path)}

    async def resolve_item(
        self, slug: str, key: str, body: Any, *, resolved_by: str | None = None
    ) -> PageItem:
        """Move an item to a state. *resolved_by* overrides the body for a signed-in person.

        Going back to open clears who resolved it and the result, so a reopened item does not
        keep claiming an outcome.

        An automation reports a failed post with ``{"state": "open", "mode": "manual",
        "error": "..."}``: the row stays on its card for a person, with the reason. The error is
        cleared when the item is finished, since it no longer describes the row.
        """
        fields = _object(body, _RESOLVE_FIELDS)
        state = check_state(fields.get("state"))
        result_url = _link(fields, "result_url")
        resolver = (
            resolved_by
            if resolved_by is not None
            else _text(fields, "resolved_by", MAX_RESOLVER_CHARS)
        )
        mode = _mode(fields)
        error = _text(fields, "error", MAX_ERROR_CHARS)
        reopened = state == STATE_OPEN
        if error and not reopened:
            raise PageError("error describes a row still open: send it with state open")
        current = await self._require_item(slug, key)
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE console_page_items"
                " SET state = ?, resolved_by = ?, result_url = ?, resolved_at = ?,"
                " mode = ?, error = ?"
                " WHERE page_slug = ? AND key = ?",
                (
                    state,
                    "" if reopened else resolver,
                    "" if reopened else result_url,
                    "" if reopened else _now(),
                    mode or current.mode,
                    error if reopened else "",
                    slug,
                    key,
                ),
            )
            await db.commit()
        return await self._require_item(slug, key)

    async def _require_item(self, slug: str, key: str) -> PageItem:
        page = await self._require_page(slug)
        async with aiosqlite.connect(self._db_path) as db:
            rows = await db.execute_fetchall(
                f"SELECT {ITEM_COLUMNS} FROM console_page_items"  # noqa: S608 (constants)
                " WHERE page_slug = ? AND key = ?",
                (slug, key),
            )
        rows = list(rows)
        if not rows:
            raise PageNotFoundError(f"unknown item: {key}")
        return make_item(tuple(rows[0]), page)
