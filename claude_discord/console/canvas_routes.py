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
        router.add_post(f"{prefix}/canvas/limits", self.set_limits)
        router.add_post(f"{prefix}/canvas/widgets/{{key}}/action", self.action)
        router.add_delete(f"{prefix}/canvas/widgets/{{key}}", self.dismiss)
        router.add_post(f"{prefix}/canvas/session/finish", self.finish_session)

    def _off(self) -> web.Response:
        return _error("canvas mode is not available in this deployment", 404)

    async def read(self, request: web.Request) -> web.Response:
        if self.canvas is None:
            return self._off()
        widgets = [w.as_dict() for w in await self.canvas.repo.list_widgets()]
        return web.json_response(
            {
                "widgets": widgets,
                "secretary": await self.canvas.status(),
                "session": await self.canvas.session(),
            }
        )

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

    async def set_limits(self, request: web.Request) -> web.Response:
        """The person changes the wake limits; they apply from the next look, no restart."""
        if self.canvas is None:
            return self._off()
        body, err = await self.server._json(request)
        if err:
            return err
        try:
            limits = await self.canvas.set_limits(
                body.get("max_wakes_per_day"), body.get("min_interval")
            )
        except ValueError as exc:
            return _error(str(exc), 400)
        return web.json_response(limits)

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

    async def finish_session(self, request: web.Request) -> web.Response:
        """The person ended a focus session: tell the secretary what was settled and what was not.

        The answers themselves already went to each asking thread; this is the summary the
        secretary needs to update its notes and the canvas. The prepared plan is cleared.
        """
        if self.canvas is None:
            return self._off()
        body, err = await self.server._json(request)
        if err:
            return err
        decided, problem = _titles(body.get("decided"), with_answer=True)
        later, problem2 = _titles(body.get("later"), with_answer=False)
        if problem or problem2:
            return _error(problem or problem2 or "", 400)
        await self.canvas.set_session(None)
        item_id = await self.canvas.item_id()
        if item_id is None or not (decided or later):
            return web.json_response({"status": "finished", "told": False})
        response = await self.server._deliver(
            item_id, session_summary(decided, later), request[self._identity], metered=False
        )
        return web.json_response({"status": "finished", "told": response.status < 300})

    async def dismiss(self, request: web.Request) -> web.Response:
        if self.canvas is None:
            return self._off()
        removed = await self.canvas.repo.delete_widget(request.match_info["key"])
        return web.json_response({"status": "removed" if removed else "absent"})


MAX_SESSION_ENTRIES = 40
MAX_SESSION_TEXT = 300


def _titles(raw: Any, *, with_answer: bool) -> tuple[list[tuple[str, str]], str | None]:
    """``(title, answer)`` pairs from a finished session's report, or a problem."""
    if raw is None:
        return [], None
    if not isinstance(raw, list) or len(raw) > MAX_SESSION_ENTRIES:
        return [], f"a list of at most {MAX_SESSION_ENTRIES} entries is expected"
    out = []
    for entry in raw:
        if not isinstance(entry, dict):
            return [], "each entry must be an object"
        title = str(entry.get("title") or "").strip()[:MAX_SESSION_TEXT]
        answer = str(entry.get("answer") or "").strip()[:MAX_SESSION_TEXT] if with_answer else ""
        if not title:
            return [], "each entry needs a title"
        out.append((title, answer))
    return out, None


def session_summary(decided: list[tuple[str, str]], later: list[tuple[str, str]]) -> str:
    """What the secretary is told when a focus session ends."""
    lines = ["🎯 Focus session finished. Each answer already went to its thread."]
    if decided:
        lines.append(f"Decided ({len(decided)}):")
        lines += [f"- {title} → {answer}" for title, answer in decided]
    if later:
        lines.append(f"Left for later ({len(later)}):")
        lines += [f"- {title}" for title, _ in later]
    lines.append("Update your notes and the canvas.")
    return "\n".join(lines)
