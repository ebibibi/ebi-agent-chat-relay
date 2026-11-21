"""``/console/api/pages``: the signed-in person's side of declarative pages.

Automation defines pages over ``/api/pages`` (see ``ext/pages_api.py``); the same definitions can
be made here with a Console sign-in, which is how the demo seeds one. When a person changes an
item's state, who resolved it is recorded as "person" and never taken from the request, so the
record can tell a click from an automated check.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

from ..database.pages_repo import STATE_OPEN, PageError, PageNotFoundError, PageRepository

RESOLVED_BY_PERSON = "person"


class PageRoutes:
    """Reads the repository from the API server per call: setup attaches it after construction."""

    def __init__(self, api: Any) -> None:
        self.api = api

    async def _run(
        self, action: Callable[[PageRepository], Awaitable[dict[str, Any]]]
    ) -> web.Response:
        repo = getattr(self.api, "page_repo", None)
        if not isinstance(repo, PageRepository):
            return web.json_response({"error": "pages are not available"}, status=404)
        try:
            return web.json_response(await action(repo))
        except json.JSONDecodeError:
            return web.json_response({"error": "invalid JSON"}, status=400)
        except PageError as exc:
            return web.json_response({"error": str(exc)}, status=400)
        except PageNotFoundError as exc:
            return web.json_response({"error": str(exc)}, status=404)

    async def define(self, request: web.Request) -> web.Response:
        async def action(repo: PageRepository) -> dict[str, Any]:
            page = await repo.put_page(request.match_info["slug"], await request.json())
            return {"page": page.as_dict()}

        return await self._run(action)

    async def items(self, request: web.Request) -> web.Response:
        async def action(repo: PageRepository) -> dict[str, Any]:
            state = request.query.get("state", STATE_OPEN)
            items = await repo.list_items(request.match_info["slug"], state)
            return {"items": [i.as_dict() for i in items]}

        return await self._run(action)

    async def add(self, request: web.Request) -> web.Response:
        async def action(repo: PageRepository) -> dict[str, Any]:
            item, _ = await repo.add_item(request.match_info["slug"], await request.json())
            return {"item": item.as_dict()}

        return await self._run(action)

    async def change(self, request: web.Request) -> web.Response:
        async def action(repo: PageRepository) -> dict[str, Any]:
            body = await request.json()
            item = await repo.resolve_item(
                request.match_info["slug"],
                request.match_info["key"],
                {"state": body.get("state") if isinstance(body, dict) else None},
                resolved_by=RESOLVED_BY_PERSON,
            )
            return {"item": item.as_dict()}

        return await self._run(action)
