"""``/console/api/restart``: see a pending restart and answer it.

A restart reserved by a deploy waits for a quiet moment (see
:mod:`claude_discord.restart_reservation`). The Console shows it so the person
using the bot knows why it will restart, and lets them choose:

- ``POST /restart/now``: restart as soon as the running turns finish.
- ``POST /restart/later``: not before ``minutes`` from now (default 60).

``GET /restart`` returns the reservation's state, or ``{"reserved": false}``.
Behind the Console's own authentication and CSRF header like every other change.
Answering here does not count as using the bot, so choosing "now" is not
undone by the click itself.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from aiohttp import web

from ..restart_reservation import get_restart_reservation
from .logsafe import q as _q

logger = logging.getLogger(__name__)

DEFAULT_LATER_MINUTES = 60
MAX_LATER_MINUTES = 24 * 60


def _error(message: str, status: int) -> web.Response:
    return web.json_response({"error": message}, status=status)


def restart_state() -> dict[str, object]:
    """What the Console shows: the pending reservation, or nothing reserved."""
    reservation = get_restart_reservation()
    if reservation is None or not reservation.reserved:
        return {"reserved": False}
    return reservation.status()


class RestartRoutes:
    def __init__(self, identity: Callable[[web.Request], str]) -> None:
        self._identity = identity

    def register(self, router: web.UrlDispatcher, prefix: str) -> None:
        router.add_get(f"{prefix}/restart", self.read)
        router.add_post(f"{prefix}/restart/now", self.now)
        router.add_post(f"{prefix}/restart/later", self.later)

    async def read(self, request: web.Request) -> web.Response:
        return web.json_response(restart_state())

    async def now(self, request: web.Request) -> web.Response:
        reservation = get_restart_reservation()
        who = self._identity(request)
        if reservation is None or not reservation.restart_now(requested_by=f"console:{who}"):
            return _error("no restart is waiting", 404)
        logger.info("console: %s chose to restart now", _q(who))
        return web.json_response(restart_state())

    async def later(self, request: web.Request) -> web.Response:
        minutes: object = DEFAULT_LATER_MINUTES
        if request.can_read_body:
            try:
                body = await request.json()
            except json.JSONDecodeError:
                return _error("invalid JSON", 400)
            if not isinstance(body, dict):
                return _error("body must be a JSON object", 400)
            minutes = body.get("minutes", DEFAULT_LATER_MINUTES)
        if isinstance(minutes, bool) or not isinstance(minutes, int):
            return _error("minutes must be an integer", 400)
        if not 1 <= minutes <= MAX_LATER_MINUTES:
            return _error(f"minutes must be 1..{MAX_LATER_MINUTES}", 400)
        reservation = get_restart_reservation()
        who = self._identity(request)
        if reservation is None or not reservation.postpone(
            minutes * 60, requested_by=f"console:{who}"
        ):
            return _error("no restart is waiting", 404)
        logger.info("console: %s postponed the restart by %d min", _q(who), minutes)
        return web.json_response(restart_state())
