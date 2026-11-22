"""``/api/pages``: how sessions, scripts and scheduled jobs add a screen to the Console."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

from ..database.page_files import PageFileError, declared_type, size_limit
from ..database.pages_repo import (
    DEFAULT_ITEM_LIMIT,
    STATE_OPEN,
    PageError,
    PageNotFoundError,
    PageRepository,
)


async def _guarded(
    repo: PageRepository | None,
    action: Callable[[PageRepository], Awaitable[web.Response]],
) -> web.Response:
    if repo is None:
        return web.json_response({"error": "Pages are not available"}, status=503)
    try:
        return await action(repo)
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    except PageFileError as exc:
        return web.json_response({"error": str(exc)}, status=exc.status)
    except PageError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except PageNotFoundError as exc:
        return web.json_response({"error": str(exc)}, status=404)


def _slug(request: web.Request) -> str:
    return request.match_info["slug"]


def _limit(request: web.Request) -> int:
    raw = request.query.get("limit", str(DEFAULT_ITEM_LIMIT))
    try:
        return int(raw)
    except ValueError as exc:
        raise PageError("limit must be an integer") from exc


async def handle_list_pages(request: web.Request, repo: PageRepository | None) -> web.Response:
    async def action(pages: PageRepository) -> web.Response:
        return web.json_response({"pages": [p.as_dict() for p in await pages.list_pages()]})

    return await _guarded(repo, action)


async def handle_put_page(request: web.Request, repo: PageRepository | None) -> web.Response:
    async def action(pages: PageRepository) -> web.Response:
        page = await pages.put_page(_slug(request), await request.json())
        return web.json_response({"status": "stored", "page": page.as_dict()})

    return await _guarded(repo, action)


async def handle_delete_page(request: web.Request, repo: PageRepository | None) -> web.Response:
    async def action(pages: PageRepository) -> web.Response:
        await pages.delete_page(_slug(request))
        return web.json_response({"status": "deleted"})

    return await _guarded(repo, action)


async def handle_list_items(request: web.Request, repo: PageRepository | None) -> web.Response:
    async def action(pages: PageRepository) -> web.Response:
        state = request.query.get("state", STATE_OPEN)
        target = request.query.get("target")
        items = await pages.list_items(_slug(request), state, _limit(request), target)
        return web.json_response({"items": [i.as_dict() for i in items]})

    return await _guarded(repo, action)


async def handle_add_item(request: web.Request, repo: PageRepository | None) -> web.Response:
    async def action(pages: PageRepository) -> web.Response:
        item, created = await pages.add_item(_slug(request), await request.json())
        body: dict[str, Any] = {
            "status": "created" if created else "exists",
            "item": item.as_dict(),
        }
        return web.json_response(body, status=201 if created else 200)

    return await _guarded(repo, action)


async def handle_resolve_item(request: web.Request, repo: PageRepository | None) -> web.Response:
    async def action(pages: PageRepository) -> web.Response:
        item = await pages.resolve_item(
            _slug(request), request.match_info["key"], await request.json()
        )
        return web.json_response({"status": "updated", "item": item.as_dict()})

    return await _guarded(repo, action)


async def handle_upload_file(request: web.Request, repo: PageRepository | None) -> web.Response:
    """Keep an image or video for this page's items; answer with the URL an item lists.

    The body is the raw file, ``Content-Type`` its type and ``X-Filename`` its percent-encoded
    name. It is streamed to disk with its own size limit: a video is far larger than the
    listener's JSON limit.
    """

    async def action(pages: PageRepository) -> web.Response:
        slug = _slug(request)
        if await pages.get_page(slug) is None:
            raise PageNotFoundError(f"unknown page: {slug}")
        content_type = request.headers.get("Content-Type", "")
        if request.content_length:
            limit = size_limit(declared_type(content_type))
            if request.content_length > limit:
                raise PageFileError(f"the file is larger than {limit // (1024 * 1024)} MB", 413)
        stored = await pages.files.save(
            slug,
            request.content.iter_chunked(64 * 1024),
            content_type,
            request.headers.get("X-Filename"),
        )
        return web.json_response(stored.as_dict(), status=201)

    return await _guarded(repo, action)
