"""One card for the same question asked across a thread family (#1493).

Children of one parent often need the same answer from the human: the same policy choice,
the same approval. Each child's card shows in the parent's panel, so without this the human
answers the same thing several times. The parent sees the family's open cards, writes one
card of its own that every child's question is answered by, and says which cards it covers:

``GET /api/threads/{id}/decisions?family=1``
    Open requests of the thread and its descendants.
``POST /api/threads/{id}/decisions/{key}/covers`` ``{"request_ids": [...]}``
    The thread's open card *key* covers those requests. Each must be open and belong to a
    descendant (never the thread itself or another family). They are closed as
    ``bundled:<card id>``; the Console then delivers the answer to the parent's card to
    each covered thread as well (``console/server.py``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aiohttp import web

if TYPE_CHECKING:
    from ..database.decision_repo import DecisionRequest, DecisionRequestRepository
    from ..database.lineage_repo import ThreadLineageRepository

__all__ = ["MAX_COVERS", "MAX_DEPTH", "covers", "family_requests", "is_descendant"]

#: More than this in one call is not one question any more.
MAX_COVERS = 20
#: How far down a family is followed; also stops a corrupt lineage cycle.
MAX_DEPTH = 8


async def is_descendant(lineage: ThreadLineageRepository, thread_id: int, ancestor_id: int) -> bool:
    """Whether *thread_id* was spawned, directly or further down, by *ancestor_id*."""
    current = thread_id
    for _ in range(MAX_DEPTH):
        parent = await lineage.parent_of(current)
        if parent is None:
            return False
        if parent == ancestor_id:
            return True
        current = parent
    return False


def _thread_id(raw: str) -> int | None:
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def _summary(request: DecisionRequest) -> dict[str, Any]:
    return {
        "id": request.id,
        "thread_id": str(request.thread_id),
        "key": request.key,
        "kind": request.kind,
        "title": request.title,
        "options": [o.get("label") for o in request.options],
    }


async def family_requests(
    repo: DecisionRequestRepository | None,
    lineage: ThreadLineageRepository | None,
    raw_thread: str,
    family: bool,
) -> web.Response:
    if repo is None:
        return web.json_response({"error": "decision requests are not available"}, status=503)
    thread_id = _thread_id(raw_thread)
    if thread_id is None:
        return web.json_response({"error": "Invalid thread_id"}, status=400)
    found = []
    for request in await repo.list_open():
        if request.thread_id == thread_id or (
            family
            and lineage is not None
            and await is_descendant(lineage, request.thread_id, thread_id)
        ):
            found.append(_summary(request))
    return web.json_response({"requests": found})


async def covers(
    repo: DecisionRequestRepository | None,
    lineage: ThreadLineageRepository | None,
    raw_thread: str,
    key: str,
    body: Any,
) -> web.Response:
    if repo is None or lineage is None:
        return web.json_response({"error": "decision requests are not available"}, status=503)
    thread_id = _thread_id(raw_thread)
    if thread_id is None:
        return web.json_response({"error": "Invalid thread_id"}, status=400)
    if not 0 < len(key) <= 40:
        return web.json_response({"error": "key must be 1-40 characters"}, status=400)
    ids = body.get("request_ids") if isinstance(body, dict) else None
    if (
        not isinstance(ids, list)
        or not ids
        or len(ids) > MAX_COVERS
        or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids)
    ):
        return web.json_response(
            {"error": f"request_ids must be a list of 1-{MAX_COVERS} request ids"}, status=400
        )
    card = await repo.get_open(thread_id, "" if key == "_" else key)
    if card is None:
        return web.json_response(
            {"error": "send your own card with this key first, then cover the others"},
            status=404,
        )
    open_by_id = {r.id: r for r in await repo.list_open()}
    problems = []
    for request_id in ids:
        request = open_by_id.get(request_id)
        if request is None:
            problems.append(f"{request_id}: not an open request")
        elif request.thread_id == thread_id:
            problems.append(f"{request_id}: your own card; send it again with the same key")
        elif not await is_descendant(lineage, request.thread_id, thread_id):
            problems.append(f"{request_id}: not from a thread you spawned")
    if problems:
        return web.json_response({"error": "nothing was covered", "problems": problems}, status=400)
    covered = await repo.bundle(card.id, list(ids))
    return web.json_response({"status": "covered", "request_id": card.id, "covered": covered})
