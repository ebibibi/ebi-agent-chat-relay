"""Evidence-based auto-completion — measuring and finishing (#1477).

:mod:`claude_discord.auto_complete` holds the rules; this module feeds them what
ccdb can measure and applies the verdict.

The completion is applied **after the agent's turn ends**, not when the agent
calls ``/candidate``. The call happens mid-turn, and the agent's own final
message arrives after it: a Done recorded before that message is older than the
thread's last activity, which the board reads as "the conversation moved on" and
undoes. So :meth:`AutoCompleter.schedule` waits for the turn to finish, checks
that the 🏁 is still there (a human reply removes it, and starts a new turn),
measures everything again, leaves a note with the evidence and the way to reopen,
and only then completes.

A restart in between drops the pending completion. That is the safe direction:
the thread simply stays a candidate, as it would without this feature.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from ..auto_complete import (
    MODE_OFF,
    AutoCompletePolicy,
    ChildFacts,
    Evidence,
    ThreadFacts,
    Verdict,
    evaluate,
)
from ..thread_marker import OUTCOME_CANDIDATE, OUTCOME_DONE, thread_outcome

__all__ = ["ApiCompletionHost", "AutoCompleter", "CompletionHost", "auto_completion_pending"]

logger = logging.getLogger(__name__)

#: How often the pending completion looks at the thread.
POLL_SECONDS = 5.0
#: Status timestamps have one-second resolution: the note must be strictly older.
SETTLE_SECONDS = 1.5
#: A turn longer than this is not "about to end"; the thread stays a candidate.
IDLE_TIMEOUT_SECONDS = 3 * 3600.0
#: How long to wait for a 🏁 rename held back by the chat platform's rate limit.
TITLE_TIMEOUT_SECONDS = 15 * 60.0

_FINISHED_OUTCOMES = frozenset({OUTCOME_CANDIDATE, OUTCOME_DONE})

#: Threads with a completion scheduled, across completers: the turn-end owner
#: ping reads it so a thread about to close itself does not ask for a reply.
_PENDING_IDS: set[int] = set()


def auto_completion_pending(thread_id: int) -> bool:
    """Whether *thread_id* is waiting for its turn to end to complete itself."""
    return thread_id in _PENDING_IDS


class CompletionHost(Protocol):
    """What the completer needs from the server. Tests provide a fake."""

    def running_thread_ids(self) -> set[int]: ...
    async def thread_title(self, thread_id: int) -> str | None: ...
    async def human_done(self, thread_id: int) -> bool: ...
    async def parent_of(self, thread_id: int) -> int | None: ...
    async def children_of(self, thread_id: int) -> list[int]: ...
    async def active_waits(self, thread_id: int) -> int: ...
    async def open_requests(self, thread_id: int) -> int: ...
    async def teams_reply_draft(self, thread_id: int) -> bool: ...
    async def post_note(self, thread_id: int, text: str) -> None: ...
    async def record(self, thread_id: int, evidence: Evidence) -> None: ...
    async def complete(self, thread_id: int) -> None: ...


def completion_note(evidence: Evidence) -> str:
    """The message left in the thread: what was delivered, how it was checked, how to reopen."""
    lines = ["✅ **Completed automatically** — the evidence below met the completion policy."]
    lines.append("Deliverables:")
    lines.extend(f"- {d}" for d in evidence.deliverables)
    if evidence.verification:
        lines.append("Verification:")
        lines.extend(f"- {v}" for v in evidence.verification)
    lines.append(
        "Reply in this thread, or press Reopen in the Console's Done list "
        "(listed under `auto-closed`), to reopen it."
    )
    return "\n".join(lines)


class AutoCompleter:
    """Assess a candidate now; complete it once its turn has ended."""

    def __init__(
        self,
        host: CompletionHost,
        *,
        policy: Callable[[], AutoCompletePolicy] = AutoCompletePolicy.from_env,
        poll_seconds: float = POLL_SECONDS,
        settle_seconds: float = SETTLE_SECONDS,
        idle_timeout_seconds: float = IDLE_TIMEOUT_SECONDS,
        title_timeout_seconds: float = TITLE_TIMEOUT_SECONDS,
    ) -> None:
        self._host = host
        self._policy = policy
        self._poll = poll_seconds
        self._settle = settle_seconds
        self._idle_timeout = idle_timeout_seconds
        self._title_timeout = title_timeout_seconds
        self._pending: dict[int, asyncio.Task[None]] = {}

    def policy(self) -> AutoCompletePolicy:
        return self._policy()

    async def facts(self, thread_id: int) -> ThreadFacts:
        host = self._host
        running = host.running_thread_ids()
        children = []
        for child in await host.children_of(thread_id):
            title = await host.thread_title(child)
            finished = (title is not None and thread_outcome(title) in _FINISHED_OUTCOMES) or (
                await host.human_done(child)
            )
            children.append(
                ChildFacts(
                    thread_id=child,
                    finished=finished,
                    running=child in running,
                    active_waits=await host.active_waits(child),
                    open_requests=await host.open_requests(child),
                )
            )
        return ThreadFacts(
            is_child=await host.parent_of(thread_id) is not None,
            teams_reply_draft=await host.teams_reply_draft(thread_id),
            active_waits=await host.active_waits(thread_id),
            open_requests=await host.open_requests(thread_id),
            children=tuple(children),
        )

    async def assess(self, thread_id: int, evidence: Evidence | None) -> Verdict:
        """The verdict for a candidate mark. Measures nothing while the feature is off."""
        policy = self._policy()
        if policy.mode == MODE_OFF:
            return evaluate(policy, evidence, ThreadFacts(False, False, 0, 0))
        verdict = evaluate(policy, evidence, await self.facts(thread_id))
        logger.info(
            "auto-complete %s for thread %d: eligible=%s reasons=%s",
            policy.mode,
            thread_id,
            verdict.eligible,
            list(verdict.reasons),
        )
        return verdict

    def schedule(self, thread_id: int, evidence: Evidence) -> None:
        """Complete *thread_id* after its turn, if it is still a finished candidate then."""
        previous = self._pending.pop(thread_id, None)
        if previous is not None:
            previous.cancel()
        task = asyncio.create_task(self._finish(thread_id, evidence))
        self._pending[thread_id] = task
        _PENDING_IDS.add(thread_id)
        task.add_done_callback(lambda t: self._forget(thread_id, t))

    def _forget(self, thread_id: int, task: asyncio.Task[None]) -> None:
        if self._pending.get(thread_id) is task:
            del self._pending[thread_id]
            _PENDING_IDS.discard(thread_id)

    async def join(self) -> None:
        """Wait for every pending completion (tests and shutdown)."""
        while self._pending:
            await asyncio.gather(*self._pending.values(), return_exceptions=True)

    def cancel_all(self) -> None:
        for task in self._pending.values():
            task.cancel()

    async def _finish(self, thread_id: int, evidence: Evidence) -> None:
        try:
            if await self._ready(thread_id):
                await self._complete_if_still_eligible(thread_id, evidence)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("auto-complete of thread %d failed; it stays a candidate", thread_id)

    async def _ready(self, thread_id: int) -> bool:
        """Wait for the turn to end and the 🏁 to show; False when that never happens."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._idle_timeout
        while thread_id in self._host.running_thread_ids():
            if loop.time() > deadline:
                logger.info("auto-complete: thread %d kept running; left a candidate", thread_id)
                return False
            await asyncio.sleep(self._poll)
        deadline = loop.time() + self._title_timeout
        while True:
            if thread_id in self._host.running_thread_ids():
                logger.info("auto-complete: thread %d resumed; cancelled", thread_id)
                return False
            title = await self._host.thread_title(thread_id)
            outcome = thread_outcome(title) if title is not None else None
            if outcome == OUTCOME_CANDIDATE:
                return True
            if outcome is not None:
                logger.info("auto-complete: thread %d now shows %s; cancelled", thread_id, outcome)
                return False
            if loop.time() > deadline:
                logger.info("auto-complete: thread %d lost its candidate mark", thread_id)
                return False
            await asyncio.sleep(self._poll)

    async def _complete_if_still_eligible(self, thread_id: int, evidence: Evidence) -> None:
        verdict = evaluate(self._policy(), evidence, await self.facts(thread_id))
        if not verdict.complete:
            logger.info(
                "auto-complete: thread %d no longer qualifies: %s",
                thread_id,
                list(verdict.reasons),
            )
            return
        await self._host.post_note(thread_id, completion_note(evidence))
        try:
            await self._host.record(thread_id, evidence)
        except Exception:
            # The record helps a later rescue; it must not hold back a verified completion.
            logger.exception("auto-complete: could not record thread %d for rescue", thread_id)
        await asyncio.sleep(self._settle)
        await self._host.complete(thread_id)
        logger.info("thread %d completed automatically: %s", thread_id, evidence.as_dict())


class ApiCompletionHost:
    """:class:`CompletionHost` over the API server's repositories and chat platform.

    Every attribute is read at call time: the server's repositories are attached
    after construction.
    """

    def __init__(self, api: Any) -> None:
        self._api = api

    def running_thread_ids(self) -> set[int]:
        return self._api._running_thread_ids()

    async def thread_title(self, thread_id: int) -> str | None:
        conversations = self._api.console_conversations
        if conversations is not None:
            conversation = await conversations.get(thread_id)
            if conversation is not None:
                return conversation.name
        bot = self._api.bot
        channel = bot.get_channel(thread_id)
        if channel is None:
            try:
                channel = await bot.fetch_channel(thread_id)
            except Exception:
                return None
        name = getattr(channel, "name", None)
        return name if isinstance(name, str) else None

    async def human_done(self, thread_id: int) -> bool:
        console = self._api._console
        if console is None:
            return False
        from ..console.work_repo import STATE_DONE, thread_item_id

        item = await console.work_repo.get(thread_item_id(thread_id))
        return item is not None and item.state == STATE_DONE

    async def parent_of(self, thread_id: int) -> int | None:
        lineage = self._api.lineage_repo
        return None if lineage is None else await lineage.parent_of(thread_id)

    async def children_of(self, thread_id: int) -> list[int]:
        lineage = self._api.lineage_repo
        return [] if lineage is None else await lineage.children_of(thread_id)

    async def active_waits(self, thread_id: int) -> int:
        repo = self._api.wait_repo
        return 0 if repo is None else len(await repo.list_active(thread_id))

    async def open_requests(self, thread_id: int) -> int:
        repo = self._api.decision_repo
        if repo is None:
            return 0
        # A deferred card is still unanswered, so it counts.
        return sum(
            1 for r in await repo.list_open() if thread_id in (r.thread_id, r.source_thread_id)
        )

    async def teams_reply_draft(self, thread_id: int) -> bool:
        from ..teams_exporter import TEAMS_EXPORTER_TAG, source_tags

        repo = self._api.session_repo
        if repo is None:
            return False
        record = await repo.get(thread_id)
        return record is not None and TEAMS_EXPORTER_TAG in source_tags(record.summary)

    async def post_note(self, thread_id: int, text: str) -> None:
        conversations = self._api.console_conversations
        if conversations is not None and await conversations.get(thread_id) is not None:
            await conversations.append(thread_id, author="ccdb", is_bot=True, content=text)
            return
        bot = self._api.bot
        channel = bot.get_channel(thread_id) or await bot.fetch_channel(thread_id)
        import discord

        # The evidence is the agent's text: it must not ping anyone.
        await channel.send(text, allowed_mentions=discord.AllowedMentions.none())

    async def record(self, thread_id: int, evidence: Evidence) -> None:
        """Keep the rescue record an automatic close leaves (#1490)."""
        from ..auto_close import KIND_EVIDENCE, MAX_SUMMARY_CHARS, Closure
        from .auto_close_api import record_closure

        summary = "; ".join(evidence.deliverables)[:MAX_SUMMARY_CHARS]
        closure = Closure(
            kind=KIND_EVIDENCE,
            reason="the evidence met the completion policy",
            summary=summary,
        )
        await record_closure(self._api._console, thread_id, closure)

    async def complete(self, thread_id: int) -> None:
        console = self._api._console
        if console is not None:
            from ..console.work_repo import thread_item_id

            await console.complete_item(thread_item_id(thread_id))
        else:
            await self._api._apply_thread_outcome(thread_id, OUTCOME_DONE)
        # This path bypasses the mark endpoint, so tell an opted-in parent here (#820).
        await self._api.notify_parent_of_outcome(
            thread_id, OUTCOME_DONE, await self.thread_title(thread_id) or ""
        )


async def handle_candidate(
    request: Any,
    completer: AutoCompleter,
    mark: Callable[[], Awaitable[Any]],
) -> Any:
    """``POST /api/threads/{id}/candidate`` with optional evidence.

    Malformed evidence is refused before the mark, so a rejected request never
    leaves a bare 🏁 behind. Facts are measured *before* the mark too: marking
    a candidate closes the thread's own decision requests, and an open one must
    still count against completing.
    """
    from aiohttp import web

    from ..auto_complete import EvidenceError, parse_evidence

    evidence: Evidence | None = None
    if request.body_exists:
        raw = await request.text()
        if raw.strip():
            try:
                evidence = parse_evidence(json.loads(raw))
            except json.JSONDecodeError:
                return web.json_response({"error": "Invalid JSON"}, status=400)
            except EvidenceError as exc:
                return web.json_response({"error": str(exc)}, status=400)
    try:
        thread_id = int(request.match_info["thread_id"])
    except (KeyError, ValueError):
        return await mark()
    policy = completer.policy()
    verdict = None
    if evidence is not None or policy.mode != MODE_OFF:
        verdict = await completer.assess(thread_id, evidence)
    response = await mark()
    if verdict is None or response.status not in (200, 202):
        return response
    scheduled = verdict.complete and evidence is not None
    if scheduled:
        completer.schedule(thread_id, evidence)  # type: ignore[arg-type]
    body = json.loads(response.body)
    body["auto_complete"] = {**verdict.as_dict(), "scheduled": scheduled}
    return web.json_response(body, status=response.status)
