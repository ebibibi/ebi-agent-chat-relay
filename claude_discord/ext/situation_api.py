"""``/api/threads/<id>/situation``: what a parent session says about where its work stands.

An entry of ``asks`` may be a decision request (ADR-0024) instead of a string: the same object
a mark carries, plus ``for_thread`` when the parent carries a child's question and ``kind``
(``waiting`` by default). The panel keeps showing each request's title; the request itself
goes to the Console's secretary home, and one the parent leaves out of a later ``asks`` closes.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from aiohttp import web

from ..database.situation_repo import SituationError, ThreadSituationRepository, parse_situation
from ..decision_request import (
    KINDS,
    DecisionRequestError,
    DecisionRequestSpec,
    parse_request,
    refusal,
)

if TYPE_CHECKING:
    from ..database.decision_repo import DecisionRequestRepository


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
    request: web.Request,
    repo: ThreadSituationRepository | None,
    decisions: DecisionRequestRepository | None = None,
) -> web.Response:
    thread_id = _thread_id(request)
    if thread_id is None:
        return web.json_response({"error": "Invalid thread_id"}, status=400)
    if repo is None:
        return web.json_response({"error": "Situations are not available"}, status=503)
    try:
        body = await request.json()
    except json.JSONDecodeError:
        return web.json_response({"error": "Invalid JSON"}, status=400)
    try:
        body, requests = split_asks(body, thread_id)
        fields = parse_situation(body)
    except DecisionRequestError as exc:
        return web.json_response(refusal(exc), status=400)
    except SituationError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    stored = await repo.update(thread_id, fields)
    if decisions is not None and requests is not None:
        await decisions.sync_situation(thread_id, requests)
    return web.json_response(
        {
            "status": "updated",
            "situation": stored.as_dict(),
            "requests": len(requests or []),
        }
    )


def split_asks(
    body: Any, thread_id: int
) -> tuple[Any, list[tuple[int, DecisionRequestSpec]] | None]:
    """Take the decision requests out of ``asks``, leaving their titles for the panel.

    Returns the body to store and the requests — ``None`` when ``asks`` is not written, so
    a write of ``doing`` alone leaves the open requests as they are.
    """
    if not isinstance(body, dict) or not isinstance(body.get("asks"), list):
        return body, None
    asks: list[Any] = []
    requests: list[tuple[int, DecisionRequestSpec]] = []
    problems: list[str] = []
    seen: set[tuple[int, str]] = set()
    for index, entry in enumerate(body["asks"]):
        if not isinstance(entry, dict):
            asks.append(entry)
            continue
        where = f"asks[{index}]"
        entry = dict(entry)
        target = _target(entry.pop("for_thread", None), thread_id)
        kind = entry.pop("kind", "waiting")
        if target is None:
            problems.append(f"{where}.for_thread must be a thread id")
            continue
        if kind not in KINDS:
            problems.append(f"{where}.kind must be one of {', '.join(KINDS)}")
            continue
        try:
            spec = parse_request(entry, kind=kind)
        except DecisionRequestError as exc:
            problems.extend(f"{where}: {problem}" for problem in exc.problems)
            continue
        if (target, spec.key) in seen:
            problems.append(f"{where} repeats a question for the same thread; give it a key")
            continue
        seen.add((target, spec.key))
        requests.append((target, spec))
        asks.append(spec.title)
    if problems:
        raise DecisionRequestError(problems)
    return {**body, "asks": asks}, requests


def _target(value: Any, own: int) -> int | None:
    if value is None:
        return own
    if isinstance(value, bool):
        return None
    try:
        target = int(value)
    except (TypeError, ValueError):
        return None
    return target if target > 0 else None
