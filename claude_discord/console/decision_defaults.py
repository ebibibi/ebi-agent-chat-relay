"""Proceed with a due card's recommendation, inside the owner's approved scopes (#1478).

The loop does one thing: when a card that carries ``if_no_answer`` has passed its time, it
posts a labelled automatic notice into the card's thread — which resumes the agent — and closes
the card as ``defaulted``. It never acts as the person: the notice says it is automatic, and it
is not metered as the person's attention.

A card is left waiting when any of these holds, re-checked on every pass: the owner's scope
file no longer lists its scope (or is gone), someone moved it to Later, its thread is running,
queued, finished or archived, or the board does not know the thread. One card per thread per
pass: the notice starts a turn, and the next card waits for that turn to end.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from ..database.decision_repo import DecisionRequest, DecisionRequestRepository
from ..decision_default import DefaultPolicy

logger = logging.getLogger(__name__)

Deliver = Callable[[str, str], Awaitable[bool]]
INTERVAL_SECONDS = 60
#: At most this many notices per pass, so a backlog does not start every thread at once.
MAX_PER_PASS = 3
_IDLE_STATUSES_EXCLUDED = frozenset({"running", "done", "candidate", "someday"})


def notice(*, title: str, label: str, effect: str, scope_label: str, after: str) -> str:
    """The message posted into the thread. Plain, and explicit that no person answered."""
    return (
        f'⏱️ Nobody answered the card "{title}" by {after}. Proceeding with the '
        f"recommended option **{label}** — {effect}\n"
        f"The owner approved this kind of work to go ahead unanswered: {scope_label}. "
        "This is an automatic default, not the person's answer: do the recommended option, "
        "stay inside that scope, and leave anything beyond it (publishing, sending, payment, "
        "deletion, production changes) for the person."
    )


def _due(request: DecisionRequest, now: datetime) -> bool:
    raw = (request.if_no_answer or {}).get("after")
    if not isinstance(raw, str):
        return False
    try:
        after = datetime.fromisoformat(raw)
    except ValueError:
        return False
    return after.tzinfo is not None and after <= now


def _idle(item: dict[str, Any] | None) -> bool:
    if item is None or item.get("archived") or item.get("running") or item.get("queued"):
        return False
    return item.get("status") not in _IDLE_STATUSES_EXCLUDED


class DecisionDefaulter:
    """Runs :meth:`run_once` over the board; *policy* is re-read every pass."""

    def __init__(
        self,
        repo: DecisionRequestRepository,
        *,
        policy: Callable[[], DefaultPolicy | None],
        deliver: Deliver,
    ) -> None:
        self.repo = repo
        self.policy = policy
        self.deliver = deliver
        self._task: asyncio.Task[None] | None = None

    async def run_once(
        self, items: Sequence[dict[str, Any]], *, now: datetime | None = None
    ) -> int:
        policy = self.policy()
        if policy is None or not policy.scopes:
            return 0
        now = now or datetime.now(UTC)
        by_thread = {str(i.get("thread_id")): i for i in items if i.get("thread_id")}
        done = 0
        touched: set[int] = set()
        for request in await self.repo.list_open():
            if done >= MAX_PER_PASS:
                break
            if request.thread_id in touched or request.deferred_at:
                continue
            scope = (request.if_no_answer or {}).get("scope")
            if scope not in policy.scopes or not _due(request, now):
                continue
            item = by_thread.get(str(request.thread_id))
            if not _idle(item):
                continue
            option = next((o for o in request.options if o.get("recommended")), None)
            if option is None:
                continue
            assert item is not None
            touched.add(request.thread_id)
            text = notice(
                title=request.title,
                label=str(option.get("label", "")),
                effect=str(option.get("effect", "")),
                scope_label=policy.scopes[scope],
                after=str((request.if_no_answer or {}).get("after")),
            )
            try:
                delivered = await self.deliver(str(item["id"]), text)
            except Exception:
                logger.exception("decision default: delivery to %s failed", item.get("id"))
                continue
            if not delivered:
                logger.warning("decision default: %s did not accept the notice", item.get("id"))
                continue
            await self.repo.close(request.thread_id, reason="defaulted", request_id=request.id)
            logger.info(
                "decision default: card %d of thread %d proceeded within scope %s",
                request.id,
                request.thread_id,
                scope,
            )
            done += 1
        return done

    def start(self, board: Callable[[], Awaitable[list[dict[str, Any]]]]) -> None:
        if self._task is not None:
            return

        async def loop() -> None:
            while True:
                try:
                    await self.run_once(await board())
                except Exception:
                    logger.exception("decision default: pass failed")
                await asyncio.sleep(INTERVAL_SECONDS)

        self._task = asyncio.ensure_future(loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
