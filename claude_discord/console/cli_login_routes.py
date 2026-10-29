"""``/console/api/cli-login``: sign the agent CLIs in from the browser.

``GET`` lists each CLI with its sign-in state and the attempt in progress.
``POST /cli-login/{backend}`` takes ``{"action": "start" | "code" | "cancel"}``
(``code`` also carries ``"code"``). Behind the Console's own authentication
and CSRF header like every other change; see :mod:`.cli_login`.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from aiohttp import web

from .cli_login import BACKENDS, CliLogin, LoginError
from .logsafe import q as _q

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


def _error(message: str, status: int) -> web.Response:
    return web.json_response({"error": message}, status=status)


class CliLoginRoutes:
    def __init__(self, login: CliLogin | None, identity: Callable[[web.Request], str]) -> None:
        self._login = login
        self._identity = identity

    def register(self, router: web.UrlDispatcher, prefix: str) -> None:
        router.add_get(f"{prefix}/cli-login", self.read)
        router.add_post(f"{prefix}/cli-login/{{backend}}", self.change)

    async def read(self, request: web.Request) -> web.Response:
        if self._login is None:
            return web.json_response({"backends": []})
        return web.json_response(await self._login.read())

    async def change(self, request: web.Request) -> web.Response:
        if self._login is None:
            return _error("signing in from the Console is off", 404)
        backend = request.match_info["backend"]
        if backend not in BACKENDS or backend not in self._login.backends:
            return _error(f"{backend} cannot be signed in from here", 404)
        try:
            body: Any = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return _error("invalid JSON", 400)
        if not isinstance(body, dict):
            return _error("the body must be a JSON object", 400)
        action = body.get("action")
        try:
            if action == "start":
                flow = await self._login.start(backend)
            elif action == "code":
                flow = await self._login.submit(backend, body.get("code"))
            elif action == "cancel":
                flow = await self._login.cancel(backend)
            else:
                return _error("action must be start, code or cancel", 400)
        except LoginError as exc:
            return _error(str(exc), 409)
        logger.info("console: %s %s the %s sign-in", _q(self._identity(request)), action, backend)
        return web.json_response({"flow": flow.as_dict() if flow is not None else None})


__all__ = ["CliLoginRoutes"]
