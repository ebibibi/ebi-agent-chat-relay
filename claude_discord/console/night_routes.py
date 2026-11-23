"""``/console/api/night*``: the 🌙 button's text, the quiet hours and the night shift.

Every action acts with the signed-in person's authority, like the rest of the Console.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aiohttp import web

from .night import NightSettings, night_template
from .night_shift import GRACE_SECONDS, NightShift, NightShiftError

if TYPE_CHECKING:
    from .server import ConsoleServer


def _error(message: str, status: int) -> web.Response:
    return web.json_response({"error": message}, status=status)


class NightRoutes:
    def __init__(self, server: ConsoleServer, identity: web.RequestKey[str]) -> None:
        self.server = server
        #: The request key the Console's auth middleware stores the signed-in person under.
        self._identity = identity

    def register(self, router: web.UrlDispatcher, prefix: str) -> None:
        router.add_get(f"{prefix}/night", self.night)
        router.add_get(f"{prefix}/night/shift", self.shift)
        router.add_post(f"{prefix}/night/shift", self.start_shift)
        router.add_delete(f"{prefix}/night/shift", self.cancel_shift)

    def _off(self) -> web.Response:
        return _error("the night shift is not available in this deployment", 404)

    def _shift(self) -> NightShift | None:
        return getattr(self.server, "night_shift", None)

    async def night(self, request: web.Request) -> web.Response:
        """The 🌙 button's text and the quiet hours the Console's notifications follow (#1476)."""
        lang = "en" if request.rel_url.query.get("lang") == "en" else "ja"
        desk = getattr(self.server, "night_desk", None)
        settings = await desk.night_settings() if desk is not None else NightSettings()
        return web.json_response({"template": night_template(lang), "settings": settings.as_dict()})

    async def shift(self, request: web.Request) -> web.Response:
        """The current night shift: what was picked, what was sent, what was taken out."""
        night = self._shift()
        if night is None:
            return self._off()
        return web.json_response({"shift": await night.current(), "grace_seconds": GRACE_SECONDS})

    async def start_shift(self, request: web.Request) -> web.Response:
        """🌙: pick tonight's threads and hold them for the grace period (#1511)."""
        night = self._shift()
        if night is None:
            return self._off()
        body: dict[str, Any] = {}
        if request.can_read_body:
            body, err = await self.server._json(request)
            if err:
                return err
        lang = "en" if body.get("lang") == "en" else "ja"
        try:
            shift = await night.plan(body.get("note"), request[self._identity], lang=lang)
        except NightShiftError as exc:
            return _error(str(exc), 400)
        return web.json_response({"shift": shift, "grace_seconds": GRACE_SECONDS}, status=201)

    async def cancel_shift(self, request: web.Request) -> web.Response:
        """Take one planned thread (``?item=t123``) or all of them out before they are sent."""
        night = self._shift()
        if night is None:
            return self._off()
        item = request.rel_url.query.get("item") or None
        return web.json_response({"shift": await night.cancel(item)})
