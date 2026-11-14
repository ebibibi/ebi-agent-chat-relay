"""``/api/threads/<id>/situation``: what a parent session says about where its work stands."""

from __future__ import annotations

import json

from aiohttp import web

from ..database.situation_repo import SituationError, ThreadSituationRepository, parse_situation


def _thread_id(request: web.Request) -> int | None:
    try:
        return int(request.match_info["thread_id"])
    except (KeyError, ValueError):
        return None


async def handle_get_situation(
    request: web.Request, repo: ThreadSituationRepository | None
) -> web.Response:
    thread_id = _thread_id(request)
    if thread_id is None:
        return web.json_response({"error": "Invalid thread_id"}, status=400)
    if repo is None:
        return web.json_response({"error": "Situations are not available"}, status=503)
    found = await repo.get(thread_id)
    return web.json_response({"situation": found.as_dict() if found else None})


async def handle_put_situation(
    request: web.Request, repo: ThreadSituationRepository | None
) -> web.Response:
    thread_id = _thread_id(request)
    if thread_id is None:
        return web.json_response({"error": "Invalid thread_id"}, status=400)
    if repo is None:
        return web.json_response({"error": "Situations are not available"}, status=503)
    try:
        fields = parse_situation(await request.json())
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    except SituationError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    stored = await repo.update(thread_id, fields)
    return web.json_response({"status": "updated", "situation": stored.as_dict()})
