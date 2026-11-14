"""Decision requests on the control plane (ADR-0024): read with a mark, kept or closed after it.

An agent that marks its thread ❓/👀/📋 may send the request in the body. A bare mark still
works as before — it only stops showing an older request, which the new marker supersedes.
A finished thread (🏁/✅) closes every request aimed at it.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from aiohttp import web

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
    if not request.can_read_body:
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
        return parse_request(body, kind=kind), None
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
    try:
        if outcome in OUTCOME_KINDS:
            await repo.close(thread_id, reason="superseded", origin="mark")
        else:
            await repo.close(thread_id, reason="finished")
    except Exception:
        logger.exception("decision requests of thread %d were not closed", thread_id)
    return None
