"""Resume turns that a crash (SIGKILL, OOM) cut off, without resuming them forever.

The clean-shutdown path (``ClaudeChatCog.cog_unload``) and this one meet in
``pending_resumes``: a crashed turn is turned into the same kind of row a clean
shutdown would have written, so the startup resume loop, its delete-before-spawn
guarantee and its handling of console and headless deployments apply unchanged.

Three rules decide what happens to a turn found by ``take_orphans``:

1. A thread that already has a pending resume was shut down cleanly (or asked
   for its own restart); that row wins and the crash row is dropped.
2. A turn whose process went silent more than the resume TTL ago (default
   5 minutes, the same window as ``pending_resumes``) is not run again on its
   own — after a long outage the user may have moved on. The thread is told.
3. A turn that already crashed the bot ``max_resumes`` times in a row is not
   run again either; a turn that kills the process every time would otherwise
   restart it in a loop. The thread is told.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from .database.inflight_repo import InflightTurn, InflightTurnRepository

logger = logging.getLogger(__name__)

#: ``pending_resumes.reason`` for a turn recovered after the process died.
CRASH_RESUME_REASON = "crash_recovery"

#: Automatic resumes of the same turn in a row before ccdb stops and asks.
DEFAULT_MAX_CRASH_RESUMES = 2

#: How often a live process refreshes its in-flight rows.
HEARTBEAT_SECONDS = 60.0

_PROMPT_EXCERPT_CHARS = 800


@dataclass(frozen=True)
class CrashNotice:
    """A crashed turn that is not resumed automatically, and why."""

    thread_id: int
    #: ``"stale"`` (silent longer than the TTL) or ``"limit"`` (crashed too often).
    reason: str
    crash_count: int


@dataclass
class CrashRecoveryPlan:
    """What startup recovery decided."""

    #: thread_id -> crash count to carry into the resumed turn.
    resumed: dict[int, int] = field(default_factory=dict)
    notices: list[CrashNotice] = field(default_factory=list)


def crash_resume_prompt(original_prompt: str | None, attempt: int, max_resumes: int) -> str:
    """The prompt that continues a turn the crash cut off."""
    excerpt = (original_prompt or "").strip()
    if len(excerpt) > _PROMPT_EXCERPT_CHARS:
        excerpt = excerpt[:_PROMPT_EXCERPT_CHARS] + "…"
    quoted = (
        f"\n\nThe message that turn was handling:\n> {excerpt.replace(chr(10), chr(10) + '> ')}"
    )
    return (
        "The bot process was killed (for example out of memory) while this turn was "
        "running, so it ended without a clean shutdown. "
        "Check what was already done, report where things stand, then continue. "
        f"This is automatic resume {attempt} of at most {max_resumes}: if this work "
        "may be what is crashing the bot (large memory use, a runaway process), "
        "stop and tell the user instead of repeating it. "
        "⚠️ Before making any code changes, commits, or PRs, re-confirm with the user "
        "that they want you to proceed." + (quoted if excerpt else "")
    )


def crash_notice_text(notice: CrashNotice) -> str:
    """What the thread is told when its crashed turn is not resumed automatically."""
    if notice.reason == "limit":
        why = (
            f"It has stopped the bot {notice.crash_count} times in a row, "
            "so it was not restarted automatically."
        )
    else:
        why = "The bot was down for a while, so it was not restarted automatically."
    return (
        "⚠️ **The bot stopped unexpectedly while this thread was working.** "
        f"{why} Reply here to continue."
    )


async def plan_crash_recovery(
    *,
    inflight: InflightTurnRepository,
    resumes: Any,
    sessions: Any,
    boot_id: str,
    ttl_seconds: float,
    max_resumes: int = DEFAULT_MAX_CRASH_RESUMES,
) -> CrashRecoveryPlan:
    """Turn the rows a dead process left into pending resumes or notices."""
    plan = CrashRecoveryPlan()
    orphans = await inflight.take_orphans(boot_id)
    if not orphans:
        return plan
    already_pending = {p.thread_id for p in await resumes.get_pending()}
    for turn in orphans:
        if turn.thread_id in already_pending:
            continue
        attempt = turn.crash_count + 1
        if attempt > max_resumes:
            plan.notices.append(CrashNotice(turn.thread_id, "limit", turn.crash_count))
        elif turn.silent_seconds > ttl_seconds:
            plan.notices.append(CrashNotice(turn.thread_id, "stale", turn.crash_count))
        else:
            await _mark_resume(turn, resumes, sessions, attempt, max_resumes)
            plan.resumed[turn.thread_id] = attempt
    logger.warning(
        "Crash recovery: %d turn(s) left by a dead process (%d resumed, %d not)",
        len(orphans),
        len(plan.resumed),
        len(plan.notices),
    )
    return plan


async def _mark_resume(
    turn: InflightTurn, resumes: Any, sessions: Any, attempt: int, max_resumes: int
) -> None:
    # The session id saved while the turn streamed is newer than the one it
    # started with (a new thread starts with none).
    session_id = turn.session_id
    try:
        record = await sessions.get(turn.thread_id)
        if record is not None and record.session_id:
            session_id = record.session_id
    except Exception:
        logger.warning("Crash recovery: no session record for thread %d", turn.thread_id)
    await resumes.mark(
        turn.thread_id,
        session_id=session_id,
        reason=CRASH_RESUME_REASON,
        resume_prompt=crash_resume_prompt(turn.prompt, attempt, max_resumes),
    )


class InflightTracker:
    """The cog's handle on ``inflight_turns``: one per process, never raises.

    Bookkeeping must not cost the user a turn, so every database error is
    logged and swallowed — the worst case is the pre-existing behaviour (a
    crashed turn is not resumed).
    """

    def __init__(
        self,
        inflight: InflightTurnRepository,
        *,
        boot_id: str | None = None,
        max_resumes: int = DEFAULT_MAX_CRASH_RESUMES,
    ) -> None:
        self._inflight = inflight
        self.boot_id = boot_id or uuid.uuid4().hex
        self.max_resumes = max_resumes
        self._carried: dict[int, int] = {}
        self._recovered = False

    async def begin(self, thread_id: int, *, session_id: str | None, prompt: str | None) -> None:
        """Record a starting turn, carrying the crash count of a resumed one."""
        try:
            await self._inflight.begin(
                thread_id,
                boot_id=self.boot_id,
                session_id=session_id,
                prompt=prompt,
                crash_count=self._carried.pop(thread_id, 0),
            )
        except Exception:
            logger.warning("Could not record in-flight turn of thread %d", thread_id, exc_info=True)

    async def end(self, thread_id: int) -> None:
        """Forget a finished turn."""
        try:
            await self._inflight.end(thread_id, boot_id=self.boot_id)
        except Exception:
            logger.warning("Could not clear in-flight turn of thread %d", thread_id, exc_info=True)

    async def recover_once(
        self, *, resumes: Any, sessions: Any, ttl_seconds: float
    ) -> CrashRecoveryPlan:
        """Run startup recovery the first time only (``on_ready`` fires on every reconnect)."""
        if self._recovered:
            return CrashRecoveryPlan()
        self._recovered = True
        try:
            plan = await plan_crash_recovery(
                inflight=self._inflight,
                resumes=resumes,
                sessions=sessions,
                boot_id=self.boot_id,
                ttl_seconds=ttl_seconds,
                max_resumes=self.max_resumes,
            )
        except Exception:
            logger.error("Crash recovery failed", exc_info=True)
            return CrashRecoveryPlan()
        self._carried.update(plan.resumed)
        return plan

    async def heartbeat_forever(self, interval: float = HEARTBEAT_SECONDS) -> None:
        """Keep this process's rows fresh until cancelled."""
        while True:
            await asyncio.sleep(interval)
            try:
                await self._inflight.heartbeat(self.boot_id)
            except Exception:
                logger.warning("In-flight heartbeat failed", exc_info=True)
