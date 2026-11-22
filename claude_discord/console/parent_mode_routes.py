"""Console routes for parent mode (#1489).

``GET/POST /api/defaults/parent-mode``
    The deployment default and the cap on unfinished children:
    ``{"enabled": bool, "max_children": int}``. POST takes either field.

``GET/POST /api/items/{item_id}/parent-mode``
    One conversation: ``{"mode": "on"|"off"|"default", "enabled": bool,
    "source": "thread"|"child"|"default", "default_enabled": bool,
    "is_child": bool}``. POST takes ``{"mode": ...}``; ``default`` removes the
    override so the conversation follows the deployment default again.

Authentication, the CSRF header and the change rate limit are applied by the
Console middleware before these handlers run.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

from ..parent_mode import MAX_CHILDREN_LIMIT, ParentModeSettings, parse_mode

__all__ = ["ParentModeRoutes"]

logger = logging.getLogger(__name__)

KeyResolver = Callable[[str], Awaitable[tuple[int | None, web.Response | None]]]
ParentLookup = Callable[[int], Awaitable[int | None]]


def _error(message: str, status: int) -> web.Response:
    return web.json_response({"error": message}, status=status)


class ParentModeRoutes:
    def __init__(
        self,
        backend_settings: Callable[[], Any],
        thread_key: KeyResolver,
        parent_of: ParentLookup,
        identity: Callable[[web.Request], str],
    ) -> None:
        self._backend_settings = backend_settings
        self._thread_key = thread_key
        self._parent_of = parent_of
        self._identity = identity

    def register(self, router: web.UrlDispatcher, prefix: str) -> None:
        router.add_get(f"{prefix}/defaults/parent-mode", self.defaults)
        router.add_post(f"{prefix}/defaults/parent-mode", self.defaults)
        router.add_get(f"{prefix}/items/{{item_id}}/parent-mode", self.item)
        router.add_post(f"{prefix}/items/{{item_id}}/parent-mode", self.item)

    def _settings(self) -> ParentModeSettings | None:
        owner = self._backend_settings()
        return None if owner is None else ParentModeSettings(owner.repo)

    async def _body(self, request: web.Request) -> tuple[dict[str, Any], web.Response | None]:
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}, _error("body must be JSON", 400)
        if not isinstance(body, dict):
            return {}, _error("body must be a JSON object", 400)
        return body, None

    async def defaults(self, request: web.Request) -> web.Response:
        settings = self._settings()
        if settings is None:
            return _error("this deployment has no settings store", 503)
        if request.method == "POST":
            body, err = await self._body(request)
            if err:
                return err
            if "enabled" in body:
                if not isinstance(body["enabled"], bool):
                    return _error("enabled must be a boolean", 400)
                await settings.set_default(body["enabled"])
            if "max_children" in body:
                value = body["max_children"]
                if isinstance(value, bool) or not isinstance(value, int):
                    return _error("max_children must be an integer", 400)
                try:
                    await settings.set_max_children(value)
                except ValueError as exc:
                    return _error(str(exc), 400)
            logger.info(
                "console: %r changed the parent mode defaults: %s",
                self._identity(request),
                {k: body[k] for k in ("enabled", "max_children") if k in body},
            )
        return web.json_response(
            {
                "enabled": await settings.default_enabled(),
                "max_children": await settings.max_children(),
                "max_children_limit": MAX_CHILDREN_LIMIT,
            }
        )

    async def item(self, request: web.Request) -> web.Response:
        settings = self._settings()
        if settings is None:
            return _error("this deployment has no settings store", 503)
        key, err = await self._thread_key(request.match_info["item_id"])
        if err:
            return err
        assert key is not None
        if request.method == "POST":
            body, err = await self._body(request)
            if err:
                return err
            try:
                mode = parse_mode(body.get("mode"))
            except ValueError as exc:
                return _error(str(exc), 400)
            if "mode" not in body:
                return _error("mode is required", 400)
            await settings.set_thread(key, mode)
            logger.info(
                "console: %r set parent mode of %s to %s",
                self._identity(request),
                key,
                {True: "on", False: "off", None: "default"}[mode],
            )
        override = await settings.thread_override(key)
        is_child = await self._parent_of(key) is not None
        state = await settings.resolve(key, is_child=is_child)
        return web.json_response(
            {
                "mode": {True: "on", False: "off", None: "default"}[override],
                "enabled": state.enabled,
                "source": state.source,
                "default_enabled": await settings.default_enabled(),
                "is_child": is_child,
            }
        )
