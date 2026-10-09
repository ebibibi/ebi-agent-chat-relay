"""``/api/waits`` — register, list and cancel waits (see :mod:`claude_discord.waits`).

- ``POST /api/waits``: body is a wait spec. ``cwd`` defaults to the thread's
  session working directory when that still exists, so ``gh pr checks 12``
  resolves the repository the session is working in. 201 when created, 200 with
  the live wait when the same thread already waits on the same argv, 400 / 429.
- ``GET /api/waits[?thread_id=N]``: active waits.
- ``DELETE /api/waits/{id}[?thread_id=N]``: cancel; with ``thread_id`` only if
  that thread owns it. 200 / 404.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from aiohttp import web

from ..database.wait_repo import WaitLimitError
from ..waits import WaitSpecError, allowed_cwd, parse_wait_spec

if TYPE_CHECKING:
    from claude_code_core.session_repo import SessionRepository

    from ..database.wait_repo import WaitRepository

__all__ = ["handle_cancel_wait", "handle_create_wait", "handle_list_waits"]


async def handle_create_wait(
    request: web.Request,
    repo: WaitRepository,
    session_repo: SessionRepository | None,
) -> web.Response:
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    if isinstance(body, dict) and not body.get("cwd") and session_repo is not None:
        body = {**body, "cwd": await _session_dir(session_repo, body.get("thread_id"))}
    try:
        spec = parse_wait_spec(body)
    except WaitSpecError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    try:
        wait, created = await repo.create(spec, now=time.time())
    except WaitLimitError as exc:
        return web.json_response({"error": str(exc)}, status=429)
    return web.json_response(
        {"status": "waiting" if created else "already_waiting", "wait": wait.to_dict()},
        status=201 if created else 200,
    )


async def _session_dir(session_repo: SessionRepository, raw_thread_id: object) -> str | None:
    """The thread's session working dir, if it still exists (worktrees get removed)."""
    try:
        thread_id = int(raw_thread_id)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    record = await session_repo.get(thread_id)
    return allowed_cwd(getattr(record, "working_dir", None) if record else None)


async def handle_list_waits(request: web.Request, repo: WaitRepository) -> web.Response:
    thread_id, err = _optional_thread_id(request)
    if err is not None:
        return err
    waits = await repo.list_active(thread_id=thread_id)
    return web.json_response({"waits": [w.to_dict() for w in waits]})


async def handle_cancel_wait(request: web.Request, repo: WaitRepository) -> web.Response:
    try:
        wait_id = int(request.match_info["id"])
    except (KeyError, ValueError):
        return web.json_response({"error": "Invalid wait id"}, status=400)
    thread_id, err = _optional_thread_id(request)
    if err is not None:
        return err
    if not await repo.cancel(wait_id, thread_id=thread_id):
        return web.json_response({"error": "No active wait with that id"}, status=404)
    return web.json_response({"status": "cancelled", "id": wait_id})


def _optional_thread_id(request: web.Request) -> tuple[int | None, web.Response | None]:
    raw = request.query.get("thread_id")
    if raw is None or raw == "":
        return None, None
    try:
        return int(raw), None
    except ValueError:
        return None, web.json_response({"error": "thread_id must be an integer"}, status=400)
