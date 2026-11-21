"""Declarative Console pages: a screen that automation adds by registering data.

A page is a named list of things only a person can finish (a post to send by hand, a form to
fill in). Nothing here runs in the browser and nothing is specific to one kind of work: the
owner of a page registers text and links, the Console renders them as text, and either a
person or the automation that can observe the outcome resolves each item.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import aiosqlite

MAX_PAGES = 20
MAX_TITLE_CHARS = 60
MAX_LABEL_CHARS = 20
MAX_ITEM_TITLE_CHARS = 200
MAX_BODY_CHARS = 10_000
MAX_SOURCE_CHARS = 200
MAX_RESOLVER_CHARS = 60
# An X compose link carries the whole post percent-encoded, about nine bytes per character.
MAX_URL_CHARS = 16_000
DEFAULT_ITEM_LIMIT = 200
MAX_ITEM_LIMIT = 1000

STATE_OPEN = "open"
STATE_DONE = "done"
STATE_SKIPPED = "skipped"
STATES = frozenset({STATE_OPEN, STATE_DONE, STATE_SKIPPED})

_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_KEY = re.compile(r"[\w.:-]{1,128}")
_LABELS = ("open_label", "done_label", "skip_label")
_PAGE_FIELDS = frozenset({"title", "position", *_LABELS})
_ITEM_FIELDS = frozenset({"key", "title", "body", "url", "source", "created_at"})
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
    updated_at TEXT NOT NULL
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
    PRIMARY KEY (page_slug, key)
);
CREATE INDEX IF NOT EXISTS idx_console_page_items_state
    ON console_page_items(page_slug, state, created_at);
"""


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

    def as_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "title": self.title,
            "open_label": self.open_label,
            "done_label": self.done_label,
            "skip_label": self.skip_label,
            "position": self.position,
            "open_count": self.open_count,
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
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except ValueError as exc:
        raise PageError(f"{field} is not a valid URL") from exc
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise PageError(f"{field} must be an http or https URL")
    return url


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


def _item(row: tuple[Any, ...]) -> PageItem:
    return PageItem(*row)


_ITEM_COLUMNS = (
    "key, title, body, url, source, state, resolved_by, result_url, created_at, resolved_at"
)


class PageRepository:
    """CRUD for ``console_pages`` and their items. One short-lived connection per call."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def init_db(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()

    async def list_pages(self) -> list[Page]:
        async with aiosqlite.connect(self._db_path) as db:
            rows = await db.execute_fetchall(
                """
                SELECT p.slug, p.title, p.open_label, p.done_label, p.skip_label, p.position,
                       (SELECT COUNT(*) FROM console_page_items i
                         WHERE i.page_slug = p.slug AND i.state = 'open')
                  FROM console_pages p ORDER BY p.position, p.created_at
                """
            )
        return [Page(*row) for row in rows]

    async def get_page(self, slug: str) -> Page | None:
        return next((p for p in await self.list_pages() if p.slug == slug), None)

    async def put_page(self, slug: str, body: Any) -> Page:
        """Create the page or replace its definition; its items are left as they are."""
        check_slug(slug)
        fields = _object(body, _PAGE_FIELDS)
        title = _text(fields, "title", MAX_TITLE_CHARS, required=True)
        labels = [_text(fields, label, MAX_LABEL_CHARS) for label in _LABELS]
        position = fields.get("position", 0)
        if isinstance(position, bool) or not isinstance(position, int):
            raise PageError("position must be an integer")
        if await self.get_page(slug) is None and len(await self.list_pages()) >= MAX_PAGES:
            raise PageError(f"at most {MAX_PAGES} pages")
        now = _now()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO console_pages (slug, title, open_label, done_label, skip_label,
                                           position, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(slug) DO UPDATE SET
                    title = excluded.title, open_label = excluded.open_label,
                    done_label = excluded.done_label, skip_label = excluded.skip_label,
                    position = excluded.position, updated_at = excluded.updated_at
                """,
                (slug, title, *labels, position, now, now),
            )
            await db.commit()
        page = await self.get_page(slug)
        if page is None:
            raise RuntimeError(f"page {slug} was not stored")
        return page

    async def delete_page(self, slug: str) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            # Explicit: SQLite ignores ON DELETE CASCADE unless each connection turns it on.
            await db.execute("DELETE FROM console_page_items WHERE page_slug = ?", (slug,))
            cursor = await db.execute("DELETE FROM console_pages WHERE slug = ?", (slug,))
            await db.commit()
        if cursor.rowcount == 0:
            raise PageNotFoundError(f"unknown page: {slug}")

    async def list_items(
        self, slug: str, state: str = STATE_OPEN, limit: int = DEFAULT_ITEM_LIMIT
    ) -> list[PageItem]:
        """Items in one state: open ones oldest first, resolved ones latest first."""
        check_state(state)
        if await self.get_page(slug) is None:
            raise PageNotFoundError(f"unknown page: {slug}")
        order = "created_at, key" if state == STATE_OPEN else "resolved_at DESC, key"
        async with aiosqlite.connect(self._db_path) as db:
            rows = await db.execute_fetchall(
                f"SELECT {_ITEM_COLUMNS} FROM console_page_items"  # noqa: S608 (constants)
                f" WHERE page_slug = ? AND state = ? ORDER BY {order} LIMIT ?",
                (slug, state, max(1, min(limit, MAX_ITEM_LIMIT))),
            )
        return [_item(tuple(row)) for row in rows]

    async def add_item(self, slug: str, body: Any) -> tuple[PageItem, bool]:
        """Register an item. The second value is False when its key already existed.

        Registering again refreshes the text and link but never the state: a generator that
        runs twice must not bring back what a person already finished.
        """
        fields = _object(body, _ITEM_FIELDS)
        key = _check_key(fields.get("key"))
        title = _text(fields, "title", MAX_ITEM_TITLE_CHARS)
        text = _text(fields, "body", MAX_BODY_CHARS)
        if not title and not text:
            raise PageError("an item needs a title or a body")
        url = _link(fields, "url")
        source = _text(fields, "source", MAX_SOURCE_CHARS)
        created_at = _timestamp(fields) or _now()
        if await self.get_page(slug) is None:
            raise PageNotFoundError(f"unknown page: {slug}")
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                """
                INSERT INTO console_page_items
                    (page_slug, key, title, body, url, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(page_slug, key) DO NOTHING
                """,
                (slug, key, title, text, url, source, created_at),
            )
            created = cursor.rowcount > 0
            if not created:
                await db.execute(
                    "UPDATE console_page_items SET title = ?, body = ?, url = ?, source = ?"
                    " WHERE page_slug = ? AND key = ?",
                    (title, text, url, source, slug, key),
                )
            await db.commit()
        return await self._require_item(slug, key), created

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
        async with aiosqlite.connect(self._db_path) as db:
            rows = await db.execute_fetchall(
                f"SELECT {_ITEM_COLUMNS} FROM console_page_items"  # noqa: S608 (constants)
                " WHERE page_slug = ? AND key = ?",
                (slug, key),
            )
        rows = list(rows)
        if not rows:
            raise PageNotFoundError(f"unknown item: {key}")
        return _item(tuple(rows[0]))
