"""``/api/pages``: how sessions, scripts and scheduled jobs add a screen to the Console."""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

from ..database.page_files import PageFileError
from ..database.pages_cards import list_cards
from ..database.pages_repo import (
    DEFAULT_ITEM_LIMIT,
    STATE_OPEN,
    PageError,
    PageNotFoundError,
    PageRepository,
)

logger = logging.getLogger(__name__)


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
        group = request.query.get("group")
        items = await pages.list_items(_slug(request), state, _limit(request), target, group=group)
        return web.json_response({"items": [i.as_dict() for i in items]})

    return await _guarded(repo, action)


async def handle_list_cards(request: web.Request, repo: PageRepository | None) -> web.Response:
    """One card per group of items (the content they promote), with every row of the group."""

    async def action(pages: PageRepository) -> web.Response:
        state = request.query.get("state", STATE_OPEN)
        cards = await list_cards(pages, _slug(request), state, _limit(request))
        return web.json_response({"cards": cards})

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
    listener's JSON limit. Before it is kept, videos of long-resolved items are cleared, and an
    upload that would leave too little disk free is refused with 507.
    """

    async def action(pages: PageRepository) -> web.Response:
        slug = _slug(request)
        if await pages.get_page(slug) is None:
            raise PageNotFoundError(f"unknown page: {slug}")
        try:
            freed = await pages.prune_videos()
        except Exception:  # housekeeping must not cost the upload
            logger.exception("Could not clear old page videos")
        else:
            if freed:
                logger.info("Cleared %d bytes of videos from long-resolved page items", freed)
        stored = await pages.files.save(
            slug,
            request.content.iter_chunked(1024 * 1024),
            request.headers.get("Content-Type", ""),
            request.headers.get("X-Filename"),
            request.content_length,
        )
        return web.json_response(stored.as_dict(), status=201)

    return await _guarded(repo, action)
