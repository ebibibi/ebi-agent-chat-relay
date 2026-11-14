"""``/console/api/canvas/*``: what the browser in canvas mode reads and presses (ADR-0025).

The person's side of canvas mode. The secretary's side is the control plane
(:mod:`..ext.canvas_api`). Every action here acts with the signed-in person's authority,
like the rest of the Console: start or restart the secretary, pause its automatic wakes,
press a widget's button (sent to the secretary as the person's message), dismiss a widget.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from aiohttp import web

if TYPE_CHECKING:
    from .canvas import CanvasSecretary
    from .server import ConsoleServer

logger = logging.getLogger(__name__)


def _error(message: str, status: int) -> web.Response:
    return web.json_response({"error": message}, status=status)


def button_message(title: str, label: str, say: str) -> str:
    """What a widget button sends the secretary: the person's words, and where they came from."""
    return f"{say}\n\n-# 🧩 {label} — {title}"


class CanvasRoutes:
    def __init__(
        self, server: ConsoleServer, canvas: CanvasSecretary | None, identity: web.RequestKey[str]
    ) -> None:
        self.server = server
        self.canvas = canvas
        #: The request key the Console's auth middleware stores the signed-in person under.
        self._identity = identity

    def register(self, router: web.UrlDispatcher, prefix: str) -> None:
        router.add_get(f"{prefix}/canvas", self.read)
        router.add_post(f"{prefix}/canvas/secretary", self.start)
        router.add_post(f"{prefix}/canvas/pause", self.pause)
        router.add_post(f"{prefix}/canvas/widgets/{{key}}/action", self.action)
        router.add_delete(f"{prefix}/canvas/widgets/{{key}}", self.dismiss)

    def _off(self) -> web.Response:
        return _error("canvas mode is not available in this deployment", 404)

    async def read(self, request: web.Request) -> web.Response:
        if self.canvas is None:
            return self._off()
        widgets = [w.as_dict() for w in await self.canvas.repo.list_widgets()]
        return web.json_response({"widgets": widgets, "secretary": await self.canvas.status()})

    async def start(self, request: web.Request) -> web.Response:
        if self.canvas is None:
            return self._off()
        body: dict[str, Any] = {}
        if request.can_read_body:
            body, err = await self.server._json(request)
            if err:
                return err
        restart = body.get("restart") is True
        try:
            key = await self.canvas.start_secretary(request[self._identity], restart=restart)
        except RuntimeError as exc:
            return _error(str(exc), 503)
        except Exception:
            logger.exception("console: the canvas secretary could not start")
            return _error("could not start the secretary", 502)
        return web.json_response({"item_id": f"t{key}"}, status=201)

    async def pause(self, request: web.Request) -> web.Response:
        if self.canvas is None:
            return self._off()
        body, err = await self.server._json(request)
        if err:
            return err
        paused = body.get("paused")
        if not isinstance(paused, bool):
            return _error("paused must be a boolean", 400)
        await self.canvas.set_paused(paused)
        return web.json_response({"paused": paused})

    async def action(self, request: web.Request) -> web.Response:
        if self.canvas is None:
            return self._off()
        body, err = await self.server._json(request)
        if err:
            return err
        index = body.get("index")
        widget = await self.canvas.repo.get_widget(request.match_info["key"])
        if widget is None:
            return _error("no such widget", 404)
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or not 0 <= index < len(widget.actions)
        ):
            return _error("index must name one of the widget's buttons", 400)
        item_id = await self.canvas.item_id()
        if item_id is None:
            return _error("the secretary has not been started", 409)
        action = widget.actions[index]
        text = button_message(widget.title, action["label"], action["say"])
        # The person pressed it: it is their message, metered like a reply.
        return await self.server._deliver(item_id, text, request[self._identity], metered=True)

    async def dismiss(self, request: web.Request) -> web.Response:
        if self.canvas is None:
            return self._off()
        removed = await self.canvas.repo.delete_widget(request.match_info["key"])
        return web.json_response({"status": "removed" if removed else "absent"})
