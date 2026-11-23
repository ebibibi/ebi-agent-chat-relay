"""The night and the morning on the control plane (#1476).

* ``/api/secretary/morning`` — the morning brief: done / decide / continuing.
* ``/api/secretary/night`` — the person's quiet hours and morning delivery choice.

The paths keep their ``secretary`` prefix so existing schedules keep working. The routes
resolve the Console at call time: the Console starts after the REST API, and a deployment
without it answers 503.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from aiohttp import web

from ..console.morning import clamp_hours, render_markdown
from ..console.night import NightSettingsError

if TYPE_CHECKING:
    from ..console.night_desk import NightDesk
    from .api_server import ApiServer

MAX_BASE_URL = 200


def _error(message: str, status: int) -> web.Response:
    return web.json_response({"error": message}, status=status)


def _base_url(raw: str | None) -> str | None:
    """The Console's address for links in the brief: empty, or an http(s) URL."""
    text = (raw or "").strip()
    if not text:
        return ""
    if len(text) > MAX_BASE_URL or not text.startswith(("https://", "http://")):
        return None
    if any(c.isspace() or c in '<>"()[]' for c in text):
        return None
    return text


class NightApi:
    """The control-plane routes of the night and the morning. Holds only the API server."""

    def __init__(self, api_server: ApiServer) -> None:
        self._api = api_server

    def register(self, router: web.UrlDispatcher) -> None:
        router.add_get("/api/secretary/morning", self.morning)
        router.add_get("/api/secretary/night", self.night)
        router.add_put("/api/secretary/night", self.put_night)

    def _desk(self) -> NightDesk | None:
        console = getattr(self._api, "_console", None)
        return getattr(console, "night_desk", None)

    def _unavailable(self) -> web.Response:
        return _error("this needs the Ebi AI Console, which is not running", 503)

    async def morning(self, request: web.Request) -> web.Response:
        """The morning brief. ``format=markdown`` answers one message ready to post."""
        desk = self._desk()
        if desk is None:
            return self._unavailable()
        query = request.rel_url.query
        base = _base_url(query.get("base"))
        if base is None:
            return _error("base must be an http(s) URL of at most 200 characters", 400)
        brief = await desk.morning(hours=clamp_hours(query.get("hours")), base_url=base)
        lang = "en" if query.get("lang") == "en" else "ja"
        if query.get("format") == "markdown":
            return web.Response(text=render_markdown(brief, lang), content_type="text/markdown")
        return web.json_response({**brief, "markdown": render_markdown(brief, lang)})

    async def night(self, request: web.Request) -> web.Response:
        desk = self._desk()
        if desk is None:
            return self._unavailable()
        return web.json_response((await desk.night_settings()).as_dict())

    async def put_night(self, request: web.Request) -> web.Response:
        """Store the person's choice. A field left out keeps its value; null clears it."""
        desk = self._desk()
        if desk is None:
            return self._unavailable()
        body: Any
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return _error("Invalid JSON", 400)
        try:
            settings = await desk.set_night_settings(body)
        except NightSettingsError as exc:
            return web.json_response(
                {"error": "settings refused", "problems": exc.problems}, status=400
            )
        return web.json_response(settings.as_dict())
