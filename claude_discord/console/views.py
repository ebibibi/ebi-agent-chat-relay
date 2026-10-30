"""Saved views: a named rule that selects items from the whole board.

A view is a name and a query in the filter language the web client speaks
(``tag:teams-exporter status:waiting,review -p:3``). The server stores the pair
and nothing else — it never evaluates the query — so a new rule keyword needs
no migration, and the list is the same on every device a person signs in from.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import aiosqlite
from aiohttp import web

if TYPE_CHECKING:
    from .server import ConsoleServer

MAX_NAME_CHARS = 40
MAX_QUERY_CHARS = 500
MAX_VIEWS = 30

_SCHEMA = """
CREATE TABLE IF NOT EXISTS console_views (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    query TEXT NOT NULL,
    position INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
"""


class ViewError(ValueError):
    """A change to the saved views that is refused."""


@dataclass(frozen=True)
class SavedView:
    id: str
    name: str
    query: str
    position: int

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "query": self.query, "position": self.position}


def _text(value: object, field: str, limit: int) -> str:
    if not isinstance(value, str):
        raise ViewError(f"{field} must be a string")
    text = " ".join(value.split())
    if not text:
        raise ViewError(f"{field} is required")
    if len(text) > limit:
        raise ViewError(f"{field} must be at most {limit} characters")
    return text


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class SavedViewRepository:
    """CRUD for ``console_views``. One short-lived connection per call."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def init_db(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()

    async def list(self) -> list[SavedView]:
        async with aiosqlite.connect(self._db_path) as db:
            rows = await db.execute_fetchall(
                "SELECT id, name, query, position FROM console_views ORDER BY position, created_at"
            )
        return [SavedView(*row) for row in rows]

    async def add(self, name: object, query: object) -> SavedView:
        clean_name = _text(name, "name", MAX_NAME_CHARS)
        clean_query = _text(query, "query", MAX_QUERY_CHARS)
        views = await self.list()
        if len(views) >= MAX_VIEWS:
            raise ViewError(f"at most {MAX_VIEWS} saved views")
        view = SavedView(
            id=f"v{uuid.uuid4().hex[:10]}",
            name=clean_name,
            query=clean_query,
            position=max((v.position for v in views), default=-1) + 1,
        )
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO console_views (id, name, query, position, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (view.id, view.name, view.query, view.position, _now()),
            )
            await db.commit()
        return view

    async def update(self, view_id: str, changes: dict[str, Any]) -> SavedView:
        unknown = set(changes) - {"name", "query", "position"}
        if unknown:
            raise ViewError(f"unknown fields: {', '.join(sorted(unknown))}")
        current = next((v for v in await self.list() if v.id == view_id), None)
        if current is None:
            raise ViewError("unknown view")
        position = changes.get("position", current.position)
        if isinstance(position, bool) or not isinstance(position, int):
            raise ViewError("position must be an integer")
        updated = SavedView(
            id=current.id,
            name=_text(changes["name"], "name", MAX_NAME_CHARS)
            if "name" in changes
            else current.name,
            query=_text(changes["query"], "query", MAX_QUERY_CHARS)
            if "query" in changes
            else current.query,
            position=position,
        )
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE console_views SET name = ?, query = ?, position = ? WHERE id = ?",
                (updated.name, updated.query, updated.position, updated.id),
            )
            await db.commit()
        return updated

    async def delete(self, view_id: str) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute("DELETE FROM console_views WHERE id = ?", (view_id,))
            await db.commit()
        if cursor.rowcount == 0:
            raise ViewError("unknown view")


class ViewRoutes:
    """``/console/api/views``: list, add, change and delete saved views."""

    def __init__(self, server: ConsoleServer, repo: SavedViewRepository | None) -> None:
        self.server = server
        self.repo = repo

    async def _listing(self) -> web.Response:
        assert self.repo is not None
        return web.json_response({"views": [v.as_dict() for v in await self.repo.list()]})

    async def read(self, request: web.Request) -> web.Response:
        if self.repo is None:
            return web.json_response({"views": []})
        return await self._listing()

    async def add(self, request: web.Request) -> web.Response:
        return await self._change(
            request, lambda body: self._repo().add(body.get("name"), body.get("query"))
        )

    async def patch(self, request: web.Request) -> web.Response:
        view_id = request.match_info["view_id"]
        return await self._change(request, lambda body: self._repo().update(view_id, body))

    async def delete(self, request: web.Request) -> web.Response:
        if self.repo is None:
            return web.json_response({"error": "saved views are not available"}, status=404)
        try:
            await self.repo.delete(request.match_info["view_id"])
        except ViewError as exc:
            return web.json_response({"error": str(exc)}, status=404)
        return await self._listing()

    def _repo(self) -> SavedViewRepository:
        assert self.repo is not None
        return self.repo

    async def _change(self, request: web.Request, change: Any) -> web.Response:
        if self.repo is None:
            return web.json_response({"error": "saved views are not available"}, status=404)
        body, err = await self.server._json(request)
        if err:
            return err
        try:
            await change(body)
        except ViewError as exc:
            status = 404 if str(exc) == "unknown view" else 400
            return web.json_response({"error": str(exc)}, status=status)
        return await self._listing()
