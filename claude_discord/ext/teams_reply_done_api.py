"""``POST /api/teams/reply/done`` — the person says they answered a Teams thread.

The browser extension asks ccdb to draft a reply in a thread of its own. Once
the person has posted their answer in Teams, that thread is finished, and the
extension is the place where they know it: it has the "I answered" button.
Pressing it closes the reply threads the way the Console's Done button does,
so the person does not have to find each one again.

The ingest token is enough because the reach is narrow: only threads that this
server spawned *for the named Teams thread* can be closed, never an arbitrary
thread ID. The press itself is the human's explicit completion, which is what
synced reply-draft threads wait for.

- Every reply thread filed under that Teams thread is closed, not only the
  newest: an older draft for the same question is just as answered.
- A thread whose turn is still running is refused (409) and nothing is closed.
  A draft being written is not finished, and closing half the set would leave
  the person guessing.
- 404 when this server never drafted a reply for that Teams thread.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from aiohttp import web

__all__ = ["handle_reply_done"]

logger = logging.getLogger(__name__)

#: Closes one thread; returns an error message, or None when it is done.
CloseThread = Callable[[int], Awaitable[str | None]]


async def handle_reply_done(
    thread_ids: list[int],
    *,
    running_thread_ids: Callable[[], set[int]],
    close: CloseThread,
) -> web.Response:
    """Close every reply thread in *thread_ids*, or none of them."""
    if not thread_ids:
        return web.json_response(
            {"error": "No reply thread exists for this Teams thread"}, status=404
        )
    running = running_thread_ids()
    busy = [tid for tid in thread_ids if tid in running]
    if busy:
        return web.json_response(
            {
                "error": "A reply is still being written; close it after it finishes",
                "running": [str(tid) for tid in busy],
            },
            status=409,
        )

    closed: list[str] = []
    failed: list[dict[str, str]] = []
    for thread_id in thread_ids:
        error = await close(thread_id)
        if error is None:
            closed.append(str(thread_id))
        else:
            logger.warning("teams reply done: thread %d not closed: %s", thread_id, error)
            failed.append({"thread_id": str(thread_id), "error": error})
    logger.info("teams reply done: closed %s, failed %d", closed, len(failed))
    status = 200 if closed else 502
    return web.json_response(
        {"status": "done" if not failed else "partial", "closed": closed, "failed": failed},
        status=status,
    )
