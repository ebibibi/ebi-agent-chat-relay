"""Canvas mode on the control plane (ADR-0025): what the resident secretary calls.

* ``/api/canvas`` … — place, update, remove and reorder widgets (registered data, drawn by
  the Console with a fixed renderer per kind; see :mod:`..canvas_widget`).
* ``/api/secretary/digest`` — what changed since the secretary's cursor.
* ``/api/secretary/notes`` — the secretary's note per thread.
* ``/api/secretary/deliver`` — hand the person's answer, given in the secretary's chat, to
  the thread that asked.

The routes resolve the Console at call time: the Console starts after the REST API, and a
deployment without it answers 503.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from aiohttp import web

from ..canvas_widget import (
    MAX_WIDGETS,
    SESSION_FORMAT,
    WidgetError,
    parse_session,
    parse_widget,
    refusal,
    valid_key,
)
from ..console.canvas_repo import MAX_NOTE

if TYPE_CHECKING:
    from ..console.canvas import CanvasSecretary
    from .api_server import ApiServer

logger = logging.getLogger(__name__)

MAX_DELIVER_CHARS = 4000
#: The secretary's write key, given only in its own charter (ADR-0025).
TOKEN_HEADER = "X-Canvas-Token"


def _error(message: str, status: int) -> web.Response:
    return web.json_response({"error": message}, status=status)


async def _body(request: web.Request) -> tuple[Any, web.Response | None]:
    try:
        return await request.json(), None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, _error("Invalid JSON", 400)


def _thread(raw: Any) -> int | None:
    text = str(raw or "").strip()
    return int(text) if text.isdigit() and len(text) <= 20 else None


class CanvasApi:
    """The control-plane routes of canvas mode. Holds only the API server."""

    def __init__(self, api_server: ApiServer) -> None:
        self._api = api_server

    def register(self, router: web.UrlDispatcher) -> None:
        router.add_get("/api/canvas", self.read)
        router.add_put("/api/canvas/widgets/{key}", self.put_widget)
        router.add_delete("/api/canvas/widgets/{key}", self.delete_widget)
        router.add_post("/api/canvas/order", self.order)
        router.add_put("/api/canvas/session", self.put_session)
        router.add_delete("/api/canvas/session", self.delete_session)
        router.add_get("/api/secretary/digest", self.digest)
        router.add_get("/api/secretary/notes", self.notes)
        router.add_put("/api/secretary/notes/{thread_id}", self.put_note)
        router.add_post("/api/secretary/deliver", self.deliver)

    def _canvas(self) -> CanvasSecretary | None:
        console = getattr(self._api, "_console", None)
        return getattr(console, "canvas", None)

    def _unavailable(self) -> web.Response:
        return _error("canvas mode needs the Ebi AI Console, which is not running", 503)

    async def _refused(self, canvas: CanvasSecretary, request: web.Request) -> web.Response | None:
        """403 unless the caller is the secretary's own conversation (it holds the key)."""
        if await canvas.authorized(request.headers.get(TOKEN_HEADER)):
            return None
        return _error(
            f"only the canvas secretary may change this; send its {TOKEN_HEADER} header", 403
        )

    async def read(self, request: web.Request) -> web.Response:
        canvas = self._canvas()
        if canvas is None:
            return self._unavailable()
        widgets = [w.as_dict() for w in await canvas.repo.list_widgets()]
        return web.json_response({"widgets": widgets, "session": await canvas.session()})

    async def put_widget(self, request: web.Request) -> web.Response:
        canvas = self._canvas()
        if canvas is None:
            return self._unavailable()
        if (refused := await self._refused(canvas, request)) is not None:
            return refused
        key = request.match_info["key"]
        if not valid_key(key):
            return _error(
                "key must be 1-40 of a-z, 0-9, _ . : - (starting with a letter or digit)", 400
            )
        body, err = await _body(request)
        if err:
            return err
        try:
            spec = parse_widget(body)
        except WidgetError as exc:
            return web.json_response(refusal(exc), status=400)
        existing = await canvas.repo.get_widget(key)
        if existing is None and await canvas.repo.count_widgets() >= MAX_WIDGETS:
            return _error(f"the canvas holds at most {MAX_WIDGETS} widgets; remove one first", 400)
        widget, created = await canvas.repo.put_widget(key, spec)
        return web.json_response(
            {"status": "created" if created else "updated", "widget": widget.as_dict()},
            status=201 if created else 200,
        )

    async def delete_widget(self, request: web.Request) -> web.Response:
        canvas = self._canvas()
        if canvas is None:
            return self._unavailable()
        if (refused := await self._refused(canvas, request)) is not None:
            return refused
        removed = await canvas.repo.delete_widget(request.match_info["key"])
        return web.json_response({"status": "removed" if removed else "absent"})

    async def order(self, request: web.Request) -> web.Response:
        canvas = self._canvas()
        if canvas is None:
            return self._unavailable()
        if (refused := await self._refused(canvas, request)) is not None:
            return refused
        body, err = await _body(request)
        if err:
            return err
        keys = body.get("keys") if isinstance(body, dict) else None
        if not isinstance(keys, list) or not all(isinstance(k, str) and valid_key(k) for k in keys):
            return _error("keys must be a list of widget keys", 400)
        moved = await canvas.repo.reorder(keys)
        return web.json_response({"moved": moved})

    async def put_session(self, request: web.Request) -> web.Response:
        """The secretary prepares a focus session: which decisions, in which order, with what."""
        canvas = self._canvas()
        if canvas is None:
            return self._unavailable()
        if (refused := await self._refused(canvas, request)) is not None:
            return refused
        body, err = await _body(request)
        if err:
            return err
        try:
            plan = parse_session(body)
        except WidgetError as exc:
            return web.json_response(
                {"error": "session refused", "problems": exc.problems, "format": SESSION_FORMAT},
                status=400,
            )
        await canvas.set_session(plan)
        return web.json_response({"status": "prepared", "session": await canvas.session()})

    async def delete_session(self, request: web.Request) -> web.Response:
        canvas = self._canvas()
        if canvas is None:
            return self._unavailable()
        if (refused := await self._refused(canvas, request)) is not None:
            return refused
        await canvas.set_session(None)
        return web.json_response({"status": "removed"})

    async def digest(self, request: web.Request) -> web.Response:
        canvas = self._canvas()
        if canvas is None:
            return self._unavailable()
        query = request.rel_url.query
        # Anyone may look; only the secretary moves its own cursor.
        owner = await canvas.authorized(request.headers.get(TOKEN_HEADER))
        return web.json_response(
            await canvas.digest(
                everything=query.get("all") in ("1", "true"),
                peek=not owner or query.get("peek") in ("1", "true"),
            )
        )

    async def notes(self, request: web.Request) -> web.Response:
        canvas = self._canvas()
        if canvas is None:
            return self._unavailable()
        notes = await canvas.repo.notes()
        return web.json_response({"notes": {str(k): v for k, v in notes.items()}})

    async def put_note(self, request: web.Request) -> web.Response:
        canvas = self._canvas()
        if canvas is None:
            return self._unavailable()
        if (refused := await self._refused(canvas, request)) is not None:
            return refused
        thread_id = _thread(request.match_info["thread_id"])
        if thread_id is None:
            return _error("thread_id must be digits", 400)
        body, err = await _body(request)
        if err:
            return err
        note = body.get("note") if isinstance(body, dict) else None
        if not isinstance(note, str):
            return _error("note must be a string (empty deletes it)", 400)
        note = note.strip()
        if len(note) > MAX_NOTE:
            return _error(f"note is {len(note)} characters; at most {MAX_NOTE}", 400)
        await canvas.repo.put_note(thread_id, note)
        return web.json_response({"status": "saved" if note else "deleted"})

    async def deliver(self, request: web.Request) -> web.Response:
        console = getattr(self._api, "_console", None)
        canvas = getattr(console, "canvas", None)
        if console is None or canvas is None:
            return self._unavailable()
        if (refused := await self._refused(canvas, request)) is not None:
            return refused
        body, err = await _body(request)
        if err:
            return err
        if not isinstance(body, dict):
            return _error("send {thread_id, text}", 400)
        thread_id = _thread(body.get("thread_id"))
        text = str(body.get("text") or "").strip()
        if thread_id is None or not text:
            return _error("thread_id (digits) and text are required", 400)
        if len(text) > MAX_DELIVER_CHARS:
            return _error(f"text must be at most {MAX_DELIVER_CHARS} characters", 400)
        return await console.deliver_from_secretary(thread_id, text)
