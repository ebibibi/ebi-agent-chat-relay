"""Declarative Console pages: a screen that automation adds by registering data.

A page is a named list of things only a person can finish (a post to send by hand, a form to
fill in). Nothing here runs in the browser and nothing is specific to one kind of work: the
owner of a page registers text and links, the Console renders them as text, and either a
person or the automation that can observe the outcome resolves each item.
"""

from __future__ import annotations

import asyncio
import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import aiosqlite

from .page_files import PageFiles, kind_of_name, parse_url

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

STATE_OPEN = "open"
STATE_DONE = "done"
STATE_SKIPPED = "skipped"
STATES = frozenset({STATE_OPEN, STATE_DONE, STATE_SKIPPED})

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
_PAGE_FIELDS = frozenset({"title", "position", "max_chars", "count", *_LABELS})
_ITEM_FIELDS = frozenset(
    {"key", "title", "body", "url", "source", "created_at"}
    | {"target", "attachments", "max_chars", "count", "link"}
)
_ATTACHMENT_FIELDS = frozenset({"url", "name"})
_RESOLVE_FIELDS = frozenset({"state", "result_url", "resolved_by"})
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
    count TEXT NOT NULL DEFAULT ''
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
    ),
    "console_page_items": (
        ("target", "TEXT NOT NULL DEFAULT ''"),
        ("attachments", "TEXT NOT NULL DEFAULT ''"),
        ("max_chars", "INTEGER NOT NULL DEFAULT 0"),
        ("count", "TEXT NOT NULL DEFAULT ''"),
        # The link to share, kept apart from the body so a person can leave it out (#1530).
        ("link", "TEXT NOT NULL DEFAULT ''"),
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
    attachments: list[dict[str, str]] = field(default_factory=list)
    #: The limit and counting rule in force (the item's own, else the page's); 0 = no limit.
    limit: int = 0
    count: str = COUNT_CHARS
    #: The body measured by that rule, so every client shows the same number.
    length: int = 0
    #: A link to share with the post, copied on its own: some networks limit posts with links,
    #: so the person decides each time whether to paste it. Not counted in ``length``.
    link: str = ""

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


_ITEM_COLUMNS = (
    "key, title, body, url, source, state, resolved_by, result_url, created_at, resolved_at,"
    " target, attachments, max_chars, count, link"
)
_PAGE_COLUMNS = "p.slug, p.title, p.open_label, p.done_label, p.skip_label, p.position"


def _item(row: tuple[Any, ...], page: Page) -> PageItem:
    *base, target, attachments, max_chars, count, link = row
    rule = count or page.count
    limit = max_chars or page.max_chars
    return PageItem(
        *base,
        target=target,
        attachments=json.loads(attachments) if attachments else [],
        limit=limit,
        count=rule,
        length=count_chars(base[2], rule),
        link=link,
    )


class PageRepository:
    """CRUD for ``console_pages`` and their items. One short-lived connection per call.

    Uploaded attachments live in *files_dir*, by default ``page_files`` next to the database.
    """

    def __init__(self, db_path: str, files_dir: Path | None = None) -> None:
        self._db_path = db_path
        self.files = PageFiles(files_dir or Path(db_path).parent / "page_files")

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

    async def list_pages(self) -> list[Page]:
        async with aiosqlite.connect(self._db_path) as db:
            rows = await db.execute_fetchall(
                f"""
                SELECT {_PAGE_COLUMNS},
                       (SELECT COUNT(*) FROM console_page_items i
                         WHERE i.page_slug = p.slug AND i.state = 'open'),
                       p.max_chars, p.count
                  FROM console_pages p ORDER BY p.position, p.created_at
                """  # noqa: S608 (constants)
            )
        return [Page(*row[:7], max_chars=row[7], count=row[8] or COUNT_CHARS) for row in rows]

    async def get_page(self, slug: str) -> Page | None:
        return next((p for p in await self.list_pages() if p.slug == slug), None)

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
        if await self.get_page(slug) is None and len(await self.list_pages()) >= MAX_PAGES:
            raise PageError(f"at most {MAX_PAGES} pages")
        now = _now()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO console_pages (slug, title, open_label, done_label, skip_label,
                                           position, created_at, updated_at, max_chars, count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                    title = excluded.title, open_label = excluded.open_label,
                    done_label = excluded.done_label, skip_label = excluded.skip_label,
                    position = excluded.position, updated_at = excluded.updated_at,
                    max_chars = excluded.max_chars, count = excluded.count
                """,
                (slug, title, *labels, position, now, now, max_chars, rule),
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

    async def list_items(
        self,
        slug: str,
        state: str = STATE_OPEN,
        limit: int = DEFAULT_ITEM_LIMIT,
        target: str | None = None,
    ) -> list[PageItem]:
        """Items in one state: open ones oldest first, resolved ones latest first.

        *target*, when given, keeps only the items registered for that target.
        """
        check_state(state)
        page = await self._require_page(slug)
        order = "created_at, key" if state == STATE_OPEN else "resolved_at DESC, key"
        where = "page_slug = ? AND state = ?" + (" AND target = ?" if target is not None else "")
        params: tuple[Any, ...] = (slug, state) if target is None else (slug, state, target)
        async with aiosqlite.connect(self._db_path) as db:
            rows = await db.execute_fetchall(
                f"SELECT {_ITEM_COLUMNS} FROM console_page_items"  # noqa: S608 (constants)
                f" WHERE {where} ORDER BY {order} LIMIT ?",
                (*params, max(1, min(limit, MAX_ITEM_LIMIT))),
            )
        return [_item(tuple(row), page) for row in rows]

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
        created_at = _timestamp(fields) or _now()
        await self._require_page(slug)
        attachments = json.dumps(self._attachments(slug, fields.get("attachments")))
        values = (title, text, url, source, target, attachments, max_chars, rule, link)
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO console_page_items
                    (page_slug, key, title, body, url, source, target, attachments, max_chars,
                     count, link, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(page_slug, key) DO NOTHING
                """,
                (slug, key, *values, created_at),
            )
            created = cursor.rowcount > 0
            if not created:
                await db.execute(
                    "UPDATE console_page_items SET title = ?, body = ?, url = ?, source = ?,"
                    " target = ?, attachments = ?, max_chars = ?, count = ?, link = ?"
                    " WHERE page_slug = ? AND key = ?",
                    (*values, slug, key),
                )
            await db.commit()
        return await self._require_item(slug, key), created

    def _attachments(self, slug: str, raw: object) -> list[dict[str, str]]:
        """Checked attachments: links people may open, or files uploaded to this page."""
        if raw is None:
            return []
        if not isinstance(raw, list):
            raise PageError("attachments must be a list")
        if len(raw) > MAX_ATTACHMENTS:
            raise PageError(f"at most {MAX_ATTACHMENTS} attachments")
        return [self._attachment(slug, entry) for entry in raw]

    def _attachment(self, slug: str, entry: object) -> dict[str, str]:
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
            return {"url": found.url, "name": name or found.name, "kind": found.kind}
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
        """
        fields = _object(body, _RESOLVE_FIELDS)
        state = check_state(fields.get("state"))
        result_url = _link(fields, "result_url")
        resolver = (
            resolved_by
            if resolved_by is not None
            else _text(fields, "resolved_by", MAX_RESOLVER_CHARS)
        )
        await self._require_item(slug, key)
        reopened = state == STATE_OPEN
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE console_page_items"
                " SET state = ?, resolved_by = ?, result_url = ?, resolved_at = ?"
                " WHERE page_slug = ? AND key = ?",
                (
                    state,
                    "" if reopened else resolver,
                    "" if reopened else result_url,
                    "" if reopened else _now(),
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
                f"SELECT {_ITEM_COLUMNS} FROM console_page_items"  # noqa: S608 (constants)
                " WHERE page_slug = ? AND key = ?",
                (slug, key),
            )
        rows = list(rows)
        if not rows:
            raise PageNotFoundError(f"unknown item: {key}")
        return _item(tuple(rows[0]), page)
