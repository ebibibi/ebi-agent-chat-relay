"""``/api/restart`` — reserve, inspect and cancel a restart (see :mod:`..restart_reservation`).

- ``POST /api/restart``: body ``{"reason": "...", "requested_by": "...",
  "resume_thread_id": N, "resume_prompt": "..."}`` (all optional; the resume pair
  continues that thread after the restart). Holds the slot queue and restarts
  once nothing is running. 202 when reserved, 200 with the pending reservation
  when one already exists, 503 when no session limit is configured.
- ``GET /api/restart``: the reservation's state and the running/waiting counts.
- ``DELETE /api/restart``: cancel; the queue resumes. 200, 404 when nothing is
  reserved, 409 when the restart is already under way.
"""

from __future__ import annotations

import json
from typing import Any

from aiohttp import web

from ..restart_reservation import (
    RESERVED_RESTART_REASON,
    RestartReservation,
    build_restart_action,
    get_restart_reservation,
    persist_queued_runs,
    set_restart_reservation,
)
from ..session_slots import get_session_slots

__all__ = ["handle_cancel_restart", "handle_get_restart", "handle_reserve_restart"]

_MAX_TEXT = 500


def _reservation_for(bot: Any, resume_repo: Any) -> RestartReservation | None:
    reservation = get_restart_reservation()
    if reservation is not None:
        return reservation
    slots = get_session_slots()
    if slots is None:
        return None

    async def _persist() -> int:
        written = await persist_queued_runs(slots, resume_repo)
        if resume_repo is not None and created.resume_thread_id is not None:
            await resume_repo.mark(
                created.resume_thread_id,
                reason=RESERVED_RESTART_REASON,
                resume_prompt=created.resume_prompt,
            )
            written += 1
        return written

    created = RestartReservation(slots, build_restart_action(bot), persist=_persist)
    set_restart_reservation(created)
    return created


def _no_limit() -> web.Response:
    return web.json_response(
        {"error": "no session limit is configured, so there is no queue to hold"}, status=503
    )


async def handle_reserve_restart(request: web.Request, bot: Any, resume_repo: Any) -> web.Response:
    body: object = {}
    if request.can_read_body:
        try:
            body = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)
    if not isinstance(body, dict):
        return web.json_response({"error": "body must be a JSON object"}, status=400)
    reason = str(body.get("reason") or "")[:_MAX_TEXT]
    requested_by = str(body.get("requested_by") or "")[:_MAX_TEXT]
    resume_thread_id: int | None = None
    if body.get("resume_thread_id") not in (None, ""):
        try:
            resume_thread_id = int(body["resume_thread_id"])
        except (TypeError, ValueError):
            return web.json_response({"error": "resume_thread_id must be an integer"}, status=400)
    resume_prompt = str(body.get("resume_prompt") or "")[:4000] or None

    reservation = _reservation_for(bot, resume_repo)
    if reservation is None:
        return _no_limit()
    created = reservation.reserve(
        requested_by=requested_by,
        reason=reason,
        resume_thread_id=resume_thread_id,
        resume_prompt=resume_prompt,
    )
    return web.json_response(
        {"status": "reserved" if created else "already_reserved", **reservation.status()},
        status=202 if created else 200,
    )


async def handle_get_restart(request: web.Request) -> web.Response:
    reservation = get_restart_reservation()
    if reservation is None:
        slots = get_session_slots()
        if slots is None:
            return _no_limit()
        snapshot = slots.snapshot()
        return web.json_response(
            {
                "reserved": False,
                "phase": "idle",
                "running": sum(1 for i in snapshot if i.running),
                "waiting": sum(1 for i in snapshot if not i.running),
            }
        )
    return web.json_response(reservation.status())


async def handle_cancel_restart(request: web.Request) -> web.Response:
    reservation = get_restart_reservation()
    if reservation is None or not reservation.reserved:
        return web.json_response({"error": "no restart is reserved"}, status=404)
    if not reservation.cancel():
        return web.json_response({"error": "the restart is already under way"}, status=409)
    return web.json_response({"status": "cancelled", **reservation.status()})
