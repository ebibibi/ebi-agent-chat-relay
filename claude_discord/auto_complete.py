"""Evidence-based auto-completion — the pure rules (#1477).

An agent normally only *proposes* completion (``/candidate``, the 🏁 marker), and
the thread waits for a human to confirm it. When the work is demonstrably over —
a deliverable exists, the checks passed, nothing is left waiting — that wait is
pure queueing: the human confirms what the record already shows.

This module decides whether a candidate may become *done* on its own. Three
things are deliberate:

- **Evidence, never time.** Nothing here looks at how long a candidate has been
  sitting. Silence is not approval: something left unread for two days is
  exactly the thing a human has not checked.
- **Measured where ccdb can measure.** Waits, open decision requests and child
  threads come from ccdb's own tables (:class:`ThreadFacts`), not from the
  agent's account of them. The agent's word is used only for what ccdb cannot
  see: what it delivered, how it verified it, and whether it stayed in scope.
- **External effects are never approved here.** A send, a publication, a payment
  or a production deletion listed in ``external_effects`` keeps the thread a
  candidate, so a human sees it.

Configuration is env-only and the default is ``off`` — today's behaviour. An
unknown value also means ``off``: a typo must never widen what closes itself.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

__all__ = [
    "MODE_DRY_RUN",
    "MODE_OFF",
    "MODE_ON",
    "SCOPE_ALL",
    "SCOPE_CHILDREN",
    "AutoCompletePolicy",
    "ChildFacts",
    "Evidence",
    "EvidenceError",
    "ThreadFacts",
    "Verdict",
    "evaluate",
    "parse_evidence",
]

MODE_OFF = "off"
#: Evaluate and report the verdict, but leave the thread a candidate.
MODE_DRY_RUN = "dry-run"
MODE_ON = "on"
_MODES = frozenset({MODE_OFF, MODE_DRY_RUN, MODE_ON})

#: Only threads another thread spawned (a parent delegated them a bounded task).
SCOPE_CHILDREN = "children"
#: Any thread, except the kinds that always need a human (Teams reply drafts).
SCOPE_ALL = "all"
_SCOPES = frozenset({SCOPE_CHILDREN, SCOPE_ALL})

MODE_ENV_VAR = "CCDB_AUTO_COMPLETE"
SCOPE_ENV_VAR = "CCDB_AUTO_COMPLETE_SCOPE"
REQUIRE_VERIFICATION_ENV_VAR = "CCDB_AUTO_COMPLETE_REQUIRE_VERIFICATION"

#: Bounds on what an agent may send; the evidence is logged, so keep it readable.
MAX_ENTRIES = 50
MAX_ENTRY_CHARS = 2000

_FALSE = frozenset({"0", "false", "no", "off"})


class EvidenceError(ValueError):
    """The ``evidence`` object is malformed (wrong types or too large)."""


@dataclass(frozen=True)
class AutoCompletePolicy:
    """What the operator allows to close without a human."""

    mode: str = MODE_OFF
    scope: str = SCOPE_CHILDREN
    require_verification: bool = True

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AutoCompletePolicy:
        source = os.environ if env is None else env
        mode = source.get(MODE_ENV_VAR, MODE_OFF).strip().lower() or MODE_OFF
        scope = source.get(SCOPE_ENV_VAR, SCOPE_CHILDREN).strip().lower() or SCOPE_CHILDREN
        if mode not in _MODES or scope not in _SCOPES:
            return cls()
        require = source.get(REQUIRE_VERIFICATION_ENV_VAR, "1").strip().lower() not in _FALSE
        return cls(mode=mode, scope=scope, require_verification=require)


@dataclass(frozen=True)
class Evidence:
    """What the agent says it delivered and checked. ccdb cannot see these itself."""

    deliverables: tuple[str, ...] = ()
    verification: tuple[str, ...] = ()
    #: The agent's explicit statement that it stayed within what it was asked to do.
    within_scope: bool = False
    #: Sends, publications, payments, production deletions — each keeps a human in the loop.
    external_effects: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "deliverables": list(self.deliverables),
            "verification": list(self.verification),
            "within_scope": self.within_scope,
            "external_effects": list(self.external_effects),
        }


@dataclass(frozen=True)
class ChildFacts:
    """One spawned child thread, as ccdb sees it."""

    thread_id: int
    #: Done by a human, or itself a completion candidate.
    finished: bool
    running: bool = False
    active_waits: int = 0
    open_requests: int = 0


@dataclass(frozen=True)
class ThreadFacts:
    """What ccdb measured about the thread asking to be completed."""

    is_child: bool
    teams_reply_draft: bool
    active_waits: int
    open_requests: int
    children: tuple[ChildFacts, ...] = ()


@dataclass(frozen=True)
class Verdict:
    """The outcome. ``eligible`` ignores the mode; ``complete`` is what ccdb will do."""

    mode: str
    eligible: bool
    complete: bool
    reasons: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "eligible": self.eligible,
            "complete": self.complete,
            "reasons": list(self.reasons),
        }


def _strings(raw: object, field: str) -> tuple[str, ...]:
    if raw is None:
        return ()
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        raise EvidenceError(f"evidence.{field} must be a list of strings")
    if len(raw) > MAX_ENTRIES:
        raise EvidenceError(f"evidence.{field} has more than {MAX_ENTRIES} entries")
    values: list[str] = []
    for entry in raw:
        if not isinstance(entry, str):
            raise EvidenceError(f"evidence.{field} must be a list of strings")
        if len(entry) > MAX_ENTRY_CHARS:
            raise EvidenceError(f"an evidence.{field} entry is longer than {MAX_ENTRY_CHARS}")
        if entry.strip():
            values.append(entry.strip())
    return tuple(values)


def parse_evidence(body: object) -> Evidence | None:
    """The ``evidence`` of a ``/candidate`` body, or ``None`` when it carries none."""
    if not isinstance(body, Mapping):
        return None
    raw = body.get("evidence")
    if raw is None or raw == {}:
        return None
    if not isinstance(raw, Mapping):
        raise EvidenceError("evidence must be a JSON object")
    within = raw.get("within_scope", False)
    if not isinstance(within, bool):
        raise EvidenceError("evidence.within_scope must be true or false")
    return Evidence(
        deliverables=_strings(raw.get("deliverables"), "deliverables"),
        verification=_strings(raw.get("verification"), "verification"),
        within_scope=within,
        external_effects=_strings(raw.get("external_effects"), "external_effects"),
    )


def _evidence_reasons(policy: AutoCompletePolicy, evidence: Evidence | None) -> list[str]:
    if evidence is None:
        return ["no evidence was sent with the candidate mark"]
    reasons: list[str] = []
    if not evidence.deliverables:
        reasons.append("no deliverable is listed")
    if policy.require_verification and not evidence.verification:
        reasons.append("no verification result is listed")
    if not evidence.within_scope:
        reasons.append("the agent did not state that the work stayed within its scope")
    if evidence.external_effects:
        reasons.append("an external effect (send/publish/payment/deletion) needs a human")
    return reasons


def _child_reasons(children: tuple[ChildFacts, ...]) -> list[str]:
    reasons: list[str] = []
    for child in children:
        if child.running:
            reasons.append(f"child thread {child.thread_id} is still running")
        elif not child.finished:
            reasons.append(f"child thread {child.thread_id} is not finished")
        if child.active_waits:
            reasons.append(f"child thread {child.thread_id} has an active wait")
        if child.open_requests:
            reasons.append(f"child thread {child.thread_id} has an open decision request")
    return reasons


def _fact_reasons(policy: AutoCompletePolicy, facts: ThreadFacts) -> list[str]:
    reasons: list[str] = []
    if facts.teams_reply_draft:
        reasons.append("Teams reply-draft threads are always completed by a human")
    if policy.scope == SCOPE_CHILDREN and not facts.is_child:
        reasons.append("policy covers child threads only, and this thread has no parent")
    if facts.active_waits:
        reasons.append("the thread has an active wait")
    if facts.open_requests:
        reasons.append("the thread has an open decision request")
    reasons.extend(_child_reasons(facts.children))
    return reasons


def evaluate(policy: AutoCompletePolicy, evidence: Evidence | None, facts: ThreadFacts) -> Verdict:
    """Decide whether a completion candidate may be marked done without a human."""
    if policy.mode == MODE_OFF:
        return Verdict(mode=MODE_OFF, eligible=False, complete=False, reasons=("disabled",))
    reasons = tuple(_evidence_reasons(policy, evidence) + _fact_reasons(policy, facts))
    eligible = not reasons
    return Verdict(
        mode=policy.mode,
        eligible=eligible,
        complete=eligible and policy.mode == MODE_ON,
        reasons=reasons,
    )
