"""Closing routine threads without a human, and keeping a way back (#1490).

Evidence-based completion (:mod:`claude_discord.auto_complete`) closes a thread
whose *agent* proves the work is over. Two kinds of thread never get that far,
and still pile up waiting for a human to press Done:

- **upgrade** — the thread ccdb itself opens for every release it applies. When
  the upgrade went through there is nothing for anyone to look at.
- **sweep** — a periodic housekeeping job reviews finished-looking and stalled
  threads and closes the ones that are over, after moving any promise, deadline,
  customer matter or payment it finds into the operator's task list.

Closing is cheap to undo, so this module trades caution in *what* closes for a
record of *what was closed*: every automatic close is tagged ``auto-closed`` on
the thread's work item, carries the last key points and whether a human still
had a next step, and stays listed for :attr:`AutoClosePolicy.rescue_days` so it
can be reopened with one tap (or by replying in the thread).

What does not change:

- **Never time-based on its own.** A sweep closes a thread because a reviewer
  judged it over, not because it is old.
- **Measured facts still block.** A running turn, an active wait, an open
  decision request, an unfinished child or a Teams reply draft keeps the thread
  open whatever the caller says.
- **A human next step must land somewhere first.** A sweep close that reports
  one must say where it was recorded (``followup``), or it is refused.

Configuration is env-only and the default is ``off`` — today's behaviour. An
unknown mode means ``off`` and unknown kinds are ignored: a typo must never
widen what closes itself.
"""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "AUTO_CLOSED_TAG",
    "HUMAN_NEXT_TAG",
    "KIND_SWEEP",
    "KIND_UPGRADE",
    "MODE_DRY_RUN",
    "MODE_OFF",
    "MODE_ON",
    "AutoClosePolicy",
    "Closure",
    "ClosureError",
    "CloseFacts",
    "CloseVerdict",
    "clear_system_closer",
    "close_system_thread",
    "closure_note",
    "closure_tags",
    "evaluate_close",
    "parse_closure",
    "set_system_closer",
]

MODE_OFF = "off"
MODE_DRY_RUN = "dry-run"
MODE_ON = "on"
_MODES = frozenset({MODE_OFF, MODE_DRY_RUN, MODE_ON})

#: The thread ccdb opens to apply a release.
KIND_UPGRADE = "upgrade"
#: A housekeeping job's review of candidates and stalled threads.
KIND_SWEEP = "sweep"
KINDS = frozenset({KIND_UPGRADE, KIND_SWEEP})

MODE_ENV_VAR = "CCDB_AUTO_CLOSE"
KINDS_ENV_VAR = "CCDB_AUTO_CLOSE_KINDS"
RESCUE_DAYS_ENV_VAR = "CCDB_AUTO_CLOSE_RESCUE_DAYS"

DEFAULT_KINDS = frozenset({KIND_UPGRADE})
DEFAULT_RESCUE_DAYS = 30
MAX_RESCUE_DAYS = 365

#: Every automatic close carries this tag; a saved view on it is the rescue list.
AUTO_CLOSED_TAG = "auto-closed"
#: Added when the closed thread still had a step for a human (recorded elsewhere).
HUMAN_NEXT_TAG = "auto-closed:human-next"

MAX_REASON_CHARS = 300
MAX_SUMMARY_CHARS = 1200
MAX_FOLLOWUP_CHARS = 500


class ClosureError(ValueError):
    """The closure body is malformed."""


@dataclass(frozen=True)
class AutoClosePolicy:
    """Which routine threads the operator lets close without a human."""

    mode: str = MODE_OFF
    kinds: frozenset[str] = DEFAULT_KINDS
    rescue_days: int = DEFAULT_RESCUE_DAYS

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AutoClosePolicy:
        source = os.environ if env is None else env
        mode = source.get(MODE_ENV_VAR, MODE_OFF).strip().lower() or MODE_OFF
        if mode not in _MODES:
            mode = MODE_OFF
        raw_kinds = source.get(KINDS_ENV_VAR)
        if raw_kinds is None:
            kinds = DEFAULT_KINDS
        else:
            kinds = frozenset(k.strip().lower() for k in raw_kinds.split(",")) & KINDS
        return cls(mode=mode, kinds=kinds, rescue_days=_rescue_days(source))

    def allows(self, kind: str) -> bool:
        return self.mode != MODE_OFF and kind in self.kinds


def _rescue_days(source: Mapping[str, str]) -> int:
    raw = source.get(RESCUE_DAYS_ENV_VAR, "").strip()
    try:
        days = int(raw) if raw else DEFAULT_RESCUE_DAYS
    except ValueError:
        return DEFAULT_RESCUE_DAYS
    return min(max(days, 1), MAX_RESCUE_DAYS)


@dataclass(frozen=True)
class Closure:
    """What is being closed and what a human would need to know to take it back."""

    kind: str
    reason: str
    #: The last key points of the thread, in a sentence or two.
    summary: str = ""
    #: Whether a human still had something to do when it closed.
    human_next_step: bool = False
    #: Where that next step was recorded (a task list path, an issue URL).
    followup: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "reason": self.reason,
            "summary": self.summary,
            "human_next_step": self.human_next_step,
            "followup": self.followup,
        }


@dataclass(frozen=True)
class CloseFacts:
    """What ccdb measured about the thread, the same facts auto-completion reads."""

    running: bool
    teams_reply_draft: bool
    active_waits: int
    open_requests: int
    unfinished_children: tuple[int, ...] = ()


@dataclass(frozen=True)
class CloseVerdict:
    mode: str
    eligible: bool
    close: bool
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "eligible": self.eligible,
            "close": self.close,
            "reasons": list(self.reasons),
        }


def _text(raw: object, field: str, limit: int, *, required: bool = False) -> str:
    if raw is None:
        value = ""
    elif isinstance(raw, str):
        value = raw.strip()
    else:
        raise ClosureError(f"{field} must be a string")
    if required and not value:
        raise ClosureError(f"{field} is required")
    if len(value) > limit:
        raise ClosureError(f"{field} is longer than {limit} characters")
    return value


def parse_closure(body: object, *, kind: str) -> Closure:
    """A :class:`Closure` from a request body; *kind* is decided by the route, not the body."""
    if not isinstance(body, Mapping):
        raise ClosureError("Body must be a JSON object")
    human = body.get("human_next_step", False)
    if not isinstance(human, bool):
        raise ClosureError("human_next_step must be true or false")
    return Closure(
        kind=kind,
        reason=_text(body.get("reason"), "reason", MAX_REASON_CHARS, required=True),
        summary=_text(body.get("summary"), "summary", MAX_SUMMARY_CHARS),
        human_next_step=human,
        followup=_text(body.get("followup"), "followup", MAX_FOLLOWUP_CHARS),
    )


def evaluate_close(policy: AutoClosePolicy, closure: Closure, facts: CloseFacts) -> CloseVerdict:
    """Decide whether *closure* may close the thread now."""
    if not policy.allows(closure.kind):
        return CloseVerdict(
            mode=policy.mode,
            eligible=False,
            close=False,
            reasons=(f"auto-close is not enabled for {closure.kind!r} threads",),
        )
    reasons: list[str] = []
    if closure.human_next_step and not closure.followup:
        reasons.append("a human next step was reported but not where it was recorded (followup)")
    if facts.running:
        reasons.append("the thread has a turn running")
    if facts.teams_reply_draft:
        reasons.append("Teams reply-draft threads are always completed by a human")
    if facts.active_waits:
        reasons.append("the thread has an active wait")
    if facts.open_requests:
        reasons.append("the thread has an open decision request")
    reasons.extend(f"child thread {c} is not finished" for c in facts.unfinished_children)
    eligible = not reasons
    return CloseVerdict(
        mode=policy.mode,
        eligible=eligible,
        close=eligible and policy.mode == MODE_ON,
        reasons=tuple(reasons),
    )


def closure_tags(closure: Closure) -> list[str]:
    tags = [AUTO_CLOSED_TAG, f"{AUTO_CLOSED_TAG}:{closure.kind}"]
    if closure.human_next_step:
        tags.append(HUMAN_NEXT_TAG)
    return tags


def closure_note(closure: Closure, *, closed_at: str) -> str:
    """The record kept on the work item: why it closed and what was left."""
    lines = [f"[auto-closed {closed_at[:16]} · {closure.kind}] {closure.reason}"]
    if closure.summary:
        lines.append(f"Last state: {closure.summary}")
    if closure.human_next_step:
        lines.append(f"Human next step recorded in: {closure.followup}")
    else:
        lines.append("No human next step was left.")
    return "\n".join(lines)


def thread_message(closure: Closure, rescue_days: int) -> str:
    """What the thread itself shows, so a reader there knows how to take it back."""
    head = "🧹 **Closed automatically**"
    if closure.kind == KIND_UPGRADE:
        head += " — the upgrade finished and needs nothing from you."
    else:
        head += f" — {closure.reason}"
    lines = [head]
    if closure.human_next_step:
        lines.append(f"Your next step was kept in: {closure.followup}")
    lines.append(
        f"Reply here, or press Reopen in the Console's Done list (kept {rescue_days} days "
        "under `auto-closed`), to bring it back."
    )
    return "\n".join(lines)


#: The in-process closer, installed by the API server. ``None`` while no server
#: runs (a bare bot, tests): then a system close is a no-op and the thread keeps
#: today's behaviour of waiting for a human.
_SystemCloser = Callable[[int, Closure], Awaitable[CloseVerdict | None]]
_closer: _SystemCloser | None = None


def set_system_closer(closer: _SystemCloser | None) -> None:
    global _closer
    _closer = closer


def clear_system_closer(closer: _SystemCloser) -> None:
    """Uninstall *closer*, unless another server has installed its own since."""
    global _closer
    if _closer == closer:
        _closer = None


async def close_system_thread(thread_id: int, closure: Closure) -> CloseVerdict | None:
    """Close a thread ccdb itself opened, if the operator allows it. Never raises."""
    closer = _closer
    if closer is None or not AutoClosePolicy.from_env().allows(closure.kind):
        return None
    try:
        return await closer(thread_id, closure)
    except Exception:
        import logging

        logging.getLogger(__name__).exception("auto-close of thread %d failed", thread_id)
        return None
