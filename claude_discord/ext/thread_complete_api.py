"""``POST /api/threads/{thread_id}/complete`` — close a thread the user asked to close.

An agent normally only *proposes* completion (``/candidate``, the 🏁 marker):
its own guess that work is over must not hide an unfinished request from the
human. When the user says it in so many words — "mark the finished child
threads done" — that guess is no longer the agent's, and refusing only makes
the human click through every thread by hand. This endpoint is that case.

It is narrower than the Console's Done button on purpose:

- ``user_request`` must quote the user's explicit instruction. It is logged, so
  a completion can always be traced to the words that asked for it.
- ``from_thread`` is the calling thread. It may close itself or a thread it
  spawned (directly or further down the spawn lineage) — never a sibling or an
  unrelated thread.
- Another thread whose turn is still running is refused: it is not finished.

Body (JSON): ``{"from_thread": <int>, "user_request": "<the user's words>"}``.
Returns 200 ``{"status": "done", ...}``; 400 for a malformed body, 403 outside
the caller's family, 409 while the target is running, 503 without a Console.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from aiohttp import web

if TYPE_CHECKING:
    from ..database.lineage_repo import ThreadLineageRepository

__all__ = ["MAX_LINEAGE_DEPTH", "handle_complete_thread", "is_descendant"]

logger = logging.getLogger(__name__)

#: Spawn trees are a few levels deep; the cap only guards against a cycle.
MAX_LINEAGE_DEPTH = 32
#: Long enough for a sentence, short enough to keep the log line readable.
MAX_REQUEST_CHARS = 500


async def is_descendant(
    lineage: ThreadLineageRepository | None, thread_id: int, ancestor_id: int
) -> bool:
    """Whether *ancestor_id* spawned *thread_id*, directly or through its children."""
    if lineage is None:
        return False
    current = thread_id
    for _ in range(MAX_LINEAGE_DEPTH):
        parent = await lineage.parent_of(current)
        if parent is None:
            return False
        if parent == ancestor_id:
            return True
        current = parent
    return False


def _positive_int(value: object) -> int | None:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


async def handle_complete_thread(
    request: web.Request,
    *,
    console: Any,
    lineage: ThreadLineageRepository | None,
    running_thread_ids: Callable[[], set[int]],
    mark_without_console: Callable[[web.Request], Awaitable[web.Response]] | None = None,
) -> web.Response:
    """Validate the request, then mark the thread done like the Console's Done button."""
    thread_id = _positive_int(request.match_info.get("thread_id"))
    if thread_id is None:
        return web.json_response({"error": "Invalid thread_id"}, status=400)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "Body must be a JSON object"}, status=400)

    caller = _positive_int(body.get("from_thread"))
    if caller is None:
        return web.json_response({"error": "from_thread (your thread ID) is required"}, status=400)
    user_request = str(body.get("user_request") or "").strip()
    if not user_request:
        return web.json_response(
            {
                "error": "user_request is required: quote the user's explicit request to "
                "close this thread. Without one, propose with /candidate instead."
            },
            status=400,
        )
    if len(user_request) > MAX_REQUEST_CHARS:
        return web.json_response(
            {"error": f"user_request is longer than {MAX_REQUEST_CHARS} characters"}, status=400
        )

    if thread_id != caller:
        if not await is_descendant(lineage, thread_id, caller):
            return web.json_response(
                {"error": "Only the calling thread or a thread it spawned can be closed"},
                status=403,
            )
        if thread_id in running_thread_ids():
            return web.json_response(
                {"error": "The thread still has a turn running; it is not finished"},
                status=409,
            )

    logger.info(
        "thread %d completed by thread %d on the user's request: %r",
        thread_id,
        caller,
        user_request,
    )
    if console is None:
        if mark_without_console is None:
            return web.json_response({"error": "Console is not running"}, status=503)
        return await mark_without_console(request)

    from ..console.work_repo import WorkItemError, thread_item_id

    try:
        item, workspace = await console.complete_item(thread_item_id(thread_id))
    except WorkItemError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    return web.json_response({"status": "done", "item": item.as_dict(), "workspace": workspace})
