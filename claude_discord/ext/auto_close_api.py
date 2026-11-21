"""Closing routine threads and listing them for rescue — the server side (#1490).

:mod:`claude_discord.auto_close` holds the rules; this module measures what
ccdb can see, applies a close and serves three routes:

- ``POST /api/threads/{thread_id}/auto-close`` — a housekeeping job closes a
  thread it reviewed (kind ``sweep``).
- ``GET /api/sweep/threads`` — what such a job should review: completion
  candidates and threads that stopped without saying whose move it is.
- ``GET /api/auto-closures`` — the rescue list: everything closed
  automatically within the rescue window, with the record kept for it.

A close records itself on the thread's work item *before* it marks the item
done, so a crash in between leaves an open item with a note, never a closed one
without. It does not mirror into the bound Workspace entry: an automatic close
says the thread is over, not that the task it served is finished.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from aiohttp import web

from ..auto_close import (
    AUTO_CLOSED_TAG,
    HUMAN_NEXT_TAG,
    KIND_SWEEP,
    AutoClosePolicy,
    CloseFacts,
    CloseVerdict,
    Closure,
    ClosureError,
    closure_note,
    closure_tags,
    evaluate_close,
    parse_closure,
    thread_message,
)
from ..thread_marker import OUTCOME_CANDIDATE, OUTCOME_DONE, thread_outcome
from .auto_complete_api import ApiCompletionHost

__all__ = [
    "AutoCloser",
    "handle_auto_close",
    "handle_list_auto_closures",
    "handle_sweep_threads",
]

logger = logging.getLogger(__name__)

#: Status timestamps have one-second resolution: the done verdict must be newer
#: than the note the close posts, or the board reads it as stale.
SETTLE_SECONDS = 1.5
DEFAULT_STALE_DAYS = 7
MAX_STALE_DAYS = 365
_MAX_NOTE_CHARS = 4000
_FINISHED = frozenset({OUTCOME_CANDIDATE, OUTCOME_DONE})


def _now() -> datetime:
    return datetime.now(UTC)


class AutoCloser:
    """Measure, record and close one thread on behalf of a routine."""

    def __init__(
        self,
        api: Any,
        *,
        policy: Callable[[], AutoClosePolicy] = AutoClosePolicy.from_env,
        settle_seconds: float = SETTLE_SECONDS,
    ) -> None:
        self._api = api
        self._host = ApiCompletionHost(api)
        self._policy = policy
        self._settle = settle_seconds

    def policy(self) -> AutoClosePolicy:
        return self._policy()

    async def facts(self, thread_id: int) -> CloseFacts:
        host = self._host
        running = host.running_thread_ids()
        unfinished: list[int] = []
        for child in await host.children_of(thread_id):
            title = await host.thread_title(child)
            finished = (title is not None and thread_outcome(title) in _FINISHED) or (
                await host.human_done(child)
            )
            if child in running or not finished:
                unfinished.append(child)
        return CloseFacts(
            running=thread_id in running,
            teams_reply_draft=await host.teams_reply_draft(thread_id),
            active_waits=await host.active_waits(thread_id),
            open_requests=await host.open_requests(thread_id),
            unfinished_children=tuple(unfinished),
        )

    async def close(self, thread_id: int, closure: Closure) -> CloseVerdict:
        policy = self._policy()
        verdict = evaluate_close(policy, closure, await self.facts(thread_id))
        logger.info(
            "auto-close %s of thread %d (%s): close=%s reasons=%s",
            policy.mode,
            thread_id,
            closure.kind,
            verdict.close,
            list(verdict.reasons),
        )
        if not verdict.close:
            return verdict
        await self._host.post_note(thread_id, thread_message(closure, policy.rescue_days))
        await self._record(thread_id, closure)
        await asyncio.sleep(self._settle)
        await self._complete(thread_id)
        logger.info("thread %d closed automatically: %s", thread_id, closure.as_dict())
        return verdict

    async def _record(self, thread_id: int, closure: Closure) -> None:
        console = self._api._console
        if console is None:
            return
        from ..console.work_repo import thread_item_id

        repo = console.work_repo
        item_id = thread_item_id(thread_id)
        current = await repo.get(item_id)
        record = closure_note(closure, closed_at=_now().isoformat())
        note = f"{current.note}\n\n{record}" if current is not None and current.note else record
        tags = sorted({*(current.tags if current is not None else ()), *closure_tags(closure)})
        # Keep the newest record when a long note would overflow: it is the one that matters.
        await repo.update(item_id, {"note": note[-_MAX_NOTE_CHARS:], "tags": tags})

    async def _complete(self, thread_id: int) -> None:
        console = self._api._console
        if console is not None:
            from ..console.work_repo import thread_item_id

            await console.complete_item(thread_item_id(thread_id), mirror_workspace=False)
            return
        await self._api._apply_thread_outcome(thread_id, OUTCOME_DONE)


def _positive_int(value: object) -> int | None:
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _days(request: web.Request, name: str, default: int, limit: int) -> int | None:
    raw = request.rel_url.query.get(name)
    if raw is None or raw == "":
        return default
    number = _positive_int(raw)
    return None if number is None or number > limit else number


async def handle_auto_close(request: web.Request, closer: AutoCloser) -> web.Response:
    """``POST /api/threads/{thread_id}/auto-close`` for a housekeeping job.

    Body: ``{"from_thread": <int>, "reason": "...", "summary": "...",
    "human_next_step": false, "followup": "<where it was recorded>"}``.
    200 when closed (or judged in dry-run), 403 when the operator has not
    enabled sweeps, 409 when a measured fact or a missing ``followup`` keeps it open.
    """
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
    if caller == thread_id:
        return web.json_response(
            {"error": "A sweep closes other threads; close your own with /candidate"}, status=400
        )
    try:
        closure = parse_closure(body, kind=KIND_SWEEP)
    except ClosureError as exc:
        return web.json_response({"error": str(exc)}, status=400)
    if not closer.policy().allows(KIND_SWEEP):
        return web.json_response(
            {"error": "Sweep closing is not enabled (CCDB_AUTO_CLOSE / CCDB_AUTO_CLOSE_KINDS)"},
            status=403,
        )
    verdict = await closer.close(thread_id, closure)
    logger.info("auto-close requested by thread %d for thread %d", caller, thread_id)
    status = 200 if verdict.eligible else 409
    return web.json_response(
        {"status": "closed" if verdict.close else "kept", "auto_close": verdict.as_dict()},
        status=status,
    )


def _summary(item: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "id",
        "thread_id",
        "title",
        "status",
        "bucket",
        "last_activity_at",
        "parent_id",
        "project",
        "workspace_path",
        "note",
        "tags",
        "url",
    )
    return {k: item.get(k) for k in keys}


def _older_than(ts: str | None, cutoff: datetime) -> bool:
    if not ts:
        return False
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return False
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed < cutoff


async def handle_sweep_threads(request: web.Request, api: Any) -> web.Response:
    """``GET /api/sweep/threads?stale_days=7`` — threads a sweep should review.

    Completion candidates of any age, plus threads that stopped without a
    marker and have been quiet for ``stale_days``. Running and pinned threads
    are left out; so are threads already done.
    """
    console = api._console
    if console is None:
        return web.json_response({"error": "Console is not running"}, status=503)
    stale_days = _days(request, "stale_days", DEFAULT_STALE_DAYS, MAX_STALE_DAYS)
    if stale_days is None:
        return web.json_response({"error": f"stale_days must be 1-{MAX_STALE_DAYS}"}, status=400)
    cutoff = _now() - timedelta(days=stale_days)
    picked: list[dict[str, Any]] = []
    for item in await console._board_items():
        if not item.get("thread_id") or item.get("running") or item.get("pinned_at"):
            continue
        bucket = item.get("bucket")
        stale_idle = bucket == "idle" and _older_than(item.get("last_activity_at"), cutoff)
        if bucket == "candidate" or stale_idle:
            picked.append(_summary(item))
    return web.json_response({"stale_days": stale_days, "threads": picked})


async def handle_list_auto_closures(
    request: web.Request, api: Any, policy: AutoClosePolicy
) -> web.Response:
    """``GET /api/auto-closures?days=N`` — the rescue list, newest first."""
    console = api._console
    if console is None:
        return web.json_response({"error": "Console is not running"}, status=503)
    days = _days(request, "days", policy.rescue_days, 365)
    if days is None:
        return web.json_response({"error": "days must be 1-365"}, status=400)
    cutoff = _now() - timedelta(days=days)
    closed_at = {
        w.id: w.updated_at
        for w in await console.work_repo.list_all()
        if AUTO_CLOSED_TAG in w.tags and not _older_than(w.updated_at, cutoff)
    }
    entries = []
    for item in await console._board_items():
        if item.get("id") not in closed_at or item.get("bucket") != "done":
            continue
        entry = _summary(item)
        entry["closed_at"] = closed_at[item["id"]]
        entry["human_next_step"] = HUMAN_NEXT_TAG in (item.get("tags") or [])
        entries.append(entry)
    entries.sort(key=lambda e: e["closed_at"] or "", reverse=True)
    return web.json_response({"days": days, "closures": entries})
