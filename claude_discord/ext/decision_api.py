"""Decision requests on the control plane (ADR-0024): read with a mark, kept or closed after it.

An agent that marks its thread ❓/👀/📋 may send the request in the body. A bare mark still
works and leaves the thread's open requests alone: a thread may hold several cards (#1438).
A finished thread (🏁/✅) closes every request aimed at it; an agent closes one card whose
matter its chat settled with ``DELETE /api/threads/{id}/decisions/{key}``.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from aiohttp import web

from ..decision_default import policy_from_env
from ..decision_request import (
    KINDS,
    DecisionRequestError,
    DecisionRequestSpec,
    has_request,
    parse_request,
    refusal,
)
from ..thread_marker import OUTCOME_ACTION, OUTCOME_REVIEW, OUTCOME_WAITING

if TYPE_CHECKING:
    from ..database.decision_repo import DecisionRequest, DecisionRequestRepository

logger = logging.getLogger(__name__)

#: The marker each request kind rides on.
OUTCOME_KINDS = {OUTCOME_WAITING: "waiting", OUTCOME_REVIEW: "review", OUTCOME_ACTION: "action"}
assert set(OUTCOME_KINDS.values()) == set(KINDS)


async def read_mark_request(
    request: web.Request, outcome: str
) -> tuple[DecisionRequestSpec | None, web.Response | None]:
    """The request a mark carries, ``(None, None)`` for a bare mark, or a 400 to return."""
    # body_exists, not can_read_body: /candidate reads the body first for its
    # evidence, and aiohttp then reports it as unreadable though text() is cached.
    if not request.body_exists:
        return None, None
    raw = await request.text()
    if not raw.strip():
        return None, None
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        return None, web.json_response({"error": "Invalid JSON"}, status=400)
    if not has_request(body):
        return None, None
    kind = OUTCOME_KINDS.get(outcome)
    if kind is None:
        return None, web.json_response(
            {"error": "A decision request goes with /waiting, /review or /action"}, status=400
        )
    try:
        # Read per request, so the owner's scope list applies without a restart (#1478).
        policy = policy_from_env()
        scopes = policy.scopes if policy is not None else None
        return parse_request(body, kind=kind, scopes=scopes), None
    except DecisionRequestError as exc:
        return None, web.json_response(refusal(exc), status=400)


async def remember_mark(
    repo: DecisionRequestRepository | None,
    thread_id: int,
    outcome: str,
    spec: DecisionRequestSpec | None,
) -> DecisionRequest | None:
    """Keep *spec* as the thread's open request, or close what the new marker supersedes.

    Storing a request may fail loudly: the agent then marks again, which is idempotent.
    Closing is best-effort: the mark has landed, and the board hides a request whose
    thread no longer waits on the person anyway.
    """
    if repo is None:
        return None
    if spec is not None:
        return await repo.put(thread_id, spec, origin="mark", source_thread_id=thread_id)
    if outcome in OUTCOME_KINDS:
        # A bare ❓/👀/📋 is about something else; the thread's cards stay (#1438).
        return None
    try:
        await repo.close(thread_id, reason="finished")
    except Exception:
        logger.exception("decision requests of thread %d were not closed", thread_id)
    return None


async def close_decision(
    repo: DecisionRequestRepository | None, raw_thread: str, key: str, reason: str | None
) -> web.Response:
    """DELETE /api/threads/{thread_id}/decisions/{key}: the agent says a card is settled.

    For a decision the person made in the chat instead of on the card. *reason* is kept
    (``answered`` by default). Closing a card that is not open is not an error.
    """
    if repo is None:
        return web.json_response({"error": "decision requests are not available"}, status=503)
    try:
        thread_id = int(raw_thread)
    except ValueError:
        return web.json_response({"error": "Invalid thread_id"}, status=400)
    if not 0 < len(key) <= 40:
        return web.json_response({"error": "key must be 1-40 characters"}, status=400)
    # A card sent without a key is stored under "": "_" names it in a URL.
    if key == "_":
        key = ""
    why = (reason or "answered").strip()[:40] or "answered"
    closed = await repo.close(thread_id, reason=why, key=key)
    return web.json_response({"status": "closed" if closed else "not open", "closed": closed})
