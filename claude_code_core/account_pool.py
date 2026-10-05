"""Account pools: pick which pre-logged-in CLI profile serves a turn.

A *profile* is a configuration directory the operator has already logged in
through the vendor CLI (``CLAUDE_CONFIG_DIR`` for Claude Code, ``CODEX_HOME``
for Codex). A *pool* is the ordered list of profiles for one backend plus the
strategy that chooses among them.

This module is pure: it takes the observed usage, the rejection markers, the
pool's persisted cursor and a clock value, and returns a decision. It performs
no I/O, so every strategy can be tested exhaustively with plain values. The
repository (:mod:`claude_code_core.account_pool_repo`) and the router
(:mod:`claude_code_core.account_router`) do the reading and writing.

The relay never handles tokens. A profile is a directory path; the only thing
selection changes is which directory a child process is told to use.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .types import RateLimitInfo

#: Profile that owns usage rows recorded before pools existed, and every row
#: recorded while no pool is configured. Kept stable so a later pool file can
#: name a profile ``default`` and inherit that history on purpose.
DEFAULT_PROFILE = "default"

STRATEGIES = ("priority", "sticky", "round_robin", "most_headroom")
ASSIGN_MODES = ("session", "turn")

#: Environment variable each backend's CLI reads its configuration directory from.
HOME_ENV_VAR: Mapping[str, str] = {"claude": "CLAUDE_CONFIG_DIR", "codex": "CODEX_HOME"}

#: Windows that limit the whole login, not one model. A rejection in one of
#: these exhausts the profile even when the operator did not list it in
#: ``windows``. Model-scoped windows (``seven_day_opus``, ``seven_day_sonnet``)
#: only count when listed: running out of Opus must not stop Sonnet turns.
ACCOUNT_WIDE_WINDOWS = frozenset({"five_hour", "seven_day"})

#: Scope returned by :func:`parse_limit_error` for a limit on the whole login.
ACCOUNT_SCOPE = "account"

# Terminal error text that means "this login is out of quota". Matched against
# the wording the CLIs actually emit (Claude Code 2.1.289 / codex-cli 0.160.0
# binaries), never a bare "limit reached": that also matches "Context limit
# reached", "Budget limit reached", "Concurrent subagent limit reached" and a
# transient "Rate limit reached for requests", none of which mean the login is
# out of quota.
#
# Claude Code: "You've hit your {label}" where label is the window's name
# ("session limit" = five_hour, "weekly limit" = seven_day, "Opus limit",
# "Sonnet limit", "fast limit"), or plain "You've hit your limit"; "You're out
# of usage credits"; legacy "Claude AI usage limit reached|<epoch>".
# Codex: "You've hit your usage limit." (whole login) or
# "You've hit your usage limit for <model>" (one model), "You hit your spend cap".
_HIT_YOUR = re.compile(
    r"you(?:'|\u2019)?ve hit your ([\w' -]{1,40}?)(?:[.·,!\u00b7]|$| for | \u2014)", re.IGNORECASE
)
_LABEL_SCOPE: Mapping[str, str] = {
    "limit": ACCOUNT_SCOPE,
    "usage limit": ACCOUNT_SCOPE,
    "session limit": "five_hour",
    "weekly limit": "seven_day",
    "opus limit": "seven_day_opus",
    "sonnet limit": "seven_day_sonnet",
    "fast limit": "fast",
}
_ACCOUNT_TEXT = re.compile(
    r"usage limit reached|out of usage credits|org is out of usage|you hit your spend cap",
    re.IGNORECASE,
)
_LEGACY_RESET = re.compile(r"usage limit reached\|(\d{9,11})", re.IGNORECASE)


@dataclass(frozen=True)
class LimitError:
    """A terminal error recognised as a quota limit, and what it limits."""

    #: :data:`ACCOUNT_SCOPE`, a window name (``five_hour``, ``seven_day_opus``…),
    #: or another model-scoped name (``fast``, ``model:<name>``).
    scope: str
    resets_at: int | None = None


def parse_limit_error(error: str | None) -> LimitError | None:
    """Recognise a quota-limit error and its scope; ``None`` for anything else."""
    if not error:
        return None
    legacy = _LEGACY_RESET.search(error)
    if legacy:
        return LimitError(ACCOUNT_SCOPE, int(legacy.group(1)))
    hit = _HIT_YOUR.search(error)
    if hit:
        label = hit.group(1).strip().lower()
        rest = error[hit.end(1) :].lstrip()
        if label == "usage limit" and rest.lower().startswith("for "):
            model = rest[4:].split()[0].strip(".,") if rest[4:].split() else "?"
            return LimitError(f"model:{model}")
        scope = _LABEL_SCOPE.get(label)
        if scope is not None:
            return LimitError(scope)
        # Spend caps and shared budgets ("monthly spend limit", "team's shared
        # budget") stop the whole login.
        if "spend" in label or "budget" in label or "monthly" in label:
            return LimitError(ACCOUNT_SCOPE)
        return None
    if _ACCOUNT_TEXT.search(error):
        return LimitError(ACCOUNT_SCOPE)
    return None


def is_limit_error(error: str | None) -> bool:
    """Return True when a terminal error reads as a quota/usage-limit rejection."""
    return parse_limit_error(error) is not None


def rejection_exhausts(pool: PoolConfig, scope: str) -> bool:
    """Whether a rejection with *scope* takes the whole profile out of the pool.

    True for the whole login, for a window listed in ``pool.windows``, and for
    an account-wide window (five-hour / seven-day) even if unlisted. False for
    model-scoped limits the operator did not list.
    """
    return scope == ACCOUNT_SCOPE or scope in pool.windows or scope in ACCOUNT_WIDE_WINDOWS


@dataclass(frozen=True)
class ProfileSpec:
    """One logged-in configuration directory.

    ``home`` is ``None`` for a profile that inherits the relay's own
    environment (whatever ``CLAUDE_CONFIG_DIR`` / ``CODEX_HOME`` the process
    already has, or the CLI's default directory).
    """

    name: str
    home: str | None = None


@dataclass(frozen=True)
class PoolConfig:
    """Selection settings for one backend's pool."""

    backend: str
    profiles: tuple[ProfileSpec, ...]
    strategy: str = "priority"
    switch_at: float = 0.95
    windows: tuple[str, ...] = ("five_hour", "seven_day")
    assign: str = "session"
    retry_on_exhaustion: bool = False
    cooldown_seconds: int = 3600

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(p.name for p in self.profiles)

    def profile(self, name: str) -> ProfileSpec | None:
        return next((p for p in self.profiles if p.name == name), None)


@dataclass(frozen=True)
class PoolCursor:
    """Pool-wide state the strategies carry between decisions.

    ``rr_next`` is the index ``round_robin`` tries first for the next new
    session. ``sticky`` is the profile ``sticky`` stays on until it is
    exhausted.
    """

    rr_next: int = 0
    sticky: str | None = None


@dataclass(frozen=True)
class Selection:
    """The outcome of :func:`select_profile`."""

    profile: str
    cursor: PoolCursor
    reason: str
    #: Profiles that were exhausted at decision time, in pool order.
    exhausted: tuple[str, ...] = ()
    #: True when every profile was exhausted and ``profile`` is merely the one
    #: that frees up first. The turn will most likely be rejected.
    all_exhausted: bool = False


@dataclass(frozen=True)
class AccountBinding:
    """What a runner needs to run as a profile: its name and the env to inject."""

    backend: str
    profile: str
    env: Mapping[str, str] = field(default_factory=dict)


@dataclass
class AccountTurn:
    """Mutable record of one turn's account-relevant observations.

    Filled by the event processor while the turn streams, read by the router
    afterwards to decide whether the profile is exhausted.
    """

    binding: AccountBinding
    rejections: list[RateLimitInfo] = field(default_factory=list)
    error: str | None = None

    def exhausting_rejections(self, pool: PoolConfig) -> list[RateLimitInfo]:
        """Structured rejections that take the whole profile out (see rejection_exhausts)."""
        return [r for r in self.rejections if rejection_exhausts(pool, r.rate_limit_type)]

    def exhausting_error(self, pool: PoolConfig) -> LimitError | None:
        """The terminal error, if it is a limit that takes the whole profile out."""
        limit = parse_limit_error(self.error)
        return limit if limit is not None and rejection_exhausts(pool, limit.scope) else None

    def exhausts(self, pool: PoolConfig) -> bool:
        return bool(self.exhausting_rejections(pool)) or self.exhausting_error(pool) is not None


def binding_for(pool: PoolConfig, name: str) -> AccountBinding:
    """Return the env binding that makes a child process run as *name*."""
    spec = pool.profile(name)
    if spec is None:
        raise KeyError(f"Unknown {pool.backend} profile: {name!r}")
    env: dict[str, str] = {}
    if spec.home is not None:
        env[HOME_ENV_VAR[pool.backend]] = spec.home
    return AccountBinding(backend=pool.backend, profile=name, env=env)


def unavailable_until(
    pool: PoolConfig,
    usage: Sequence[RateLimitInfo],
    blocked_until: int | None,
    now: float,
) -> int | None:
    """Return when a profile becomes usable again, or ``None`` if it is usable now.

    A profile is exhausted while any of these holds:

    - a window listed in ``pool.windows`` is at or above ``switch_at`` and its
      ``resets_at`` is still in the future;
    - a window reported ``status == "rejected"`` and has not reset yet, when
      that window is listed in ``pool.windows`` or limits the whole login
      (:data:`ACCOUNT_WIDE_WINDOWS`) — a rejected Opus-only window does not
      stop the profile unless the operator listed it;
    - a rejection marker (``blocked_until``) is still in the future.

    A window whose reset time has passed no longer says anything about the
    present, so it is ignored. No data at all means "available".
    """
    until: list[int] = []
    for info in usage:
        if info.resets_at <= now:
            continue
        over = info.rate_limit_type in pool.windows and info.utilization >= pool.switch_at
        rejected = info.status == "rejected" and rejection_exhausts(pool, info.rate_limit_type)
        if over or rejected:
            until.append(info.resets_at)
    if blocked_until is not None and blocked_until > now:
        until.append(blocked_until)
    return max(until) if until else None


def utilization(pool: PoolConfig, usage: Sequence[RateLimitInfo], now: float) -> float:
    """Highest live utilization across the pool's windows (0.0 when unknown)."""
    live = [
        info.utilization
        for info in usage
        if info.rate_limit_type in pool.windows and info.resets_at > now
    ]
    return max(live, default=0.0)


def select_profile(
    pool: PoolConfig,
    usage: Mapping[str, Sequence[RateLimitInfo]],
    blocks: Mapping[str, int],
    now: float,
    *,
    current: str | None = None,
    cursor: PoolCursor | None = None,
) -> Selection:
    """Choose the profile for the next turn.

    Args:
        pool: The backend's pool configuration.
        usage: Latest rate-limit windows per profile name.
        blocks: Rejection markers per profile name (Unix seconds).
        now: Current Unix time.
        current: The profile the thread's session is pinned to, or ``None``
            for a thread without a session (a *new session*).
        cursor: The pool's persisted :class:`PoolCursor`.

    With ``assign == "session"`` a thread keeps its pinned profile while that
    profile is available, whatever the strategy, so the session's prompt
    cache stays warm. The strategy decides only for new sessions and for
    threads whose profile ran out. With ``assign == "turn"`` the strategy
    decides every turn.
    """
    cursor = cursor or PoolCursor()
    names = pool.names
    until = {
        name: unavailable_until(pool, usage.get(name, ()), blocks.get(name), now) for name in names
    }
    available = [name for name in names if until[name] is None]
    exhausted = tuple(name for name in names if until[name] is not None)

    if not available:
        # Nothing has headroom. Run on whichever frees up first (ties: pool
        # order) and say so, rather than refusing the turn outright.
        soonest = min(names, key=lambda n: (until[n] or 0, names.index(n)))
        return Selection(
            profile=soonest,
            cursor=cursor,
            reason="all profiles exhausted; using the one that resets first",
            exhausted=exhausted,
            all_exhausted=True,
        )

    if pool.assign == "session" and current in available:
        return Selection(
            profile=current,  # type: ignore[arg-type]  # narrowed by the membership test
            cursor=cursor,
            reason="session stays on its profile",
            exhausted=exhausted,
        )

    pick, new_cursor, reason = _STRATEGY[pool.strategy](pool, available, usage, now, cursor)
    return Selection(profile=pick, cursor=new_cursor, reason=reason, exhausted=exhausted)


def _priority(
    pool: PoolConfig,
    available: list[str],
    usage: Mapping[str, Sequence[RateLimitInfo]],
    now: float,
    cursor: PoolCursor,
) -> tuple[str, PoolCursor, str]:
    # ``available`` is already in pool order, so the first entry is the
    # highest-priority profile with headroom. A profile that resets becomes
    # available again and immediately wins back new sessions.
    return available[0], cursor, "first available in priority order"


def _sticky(
    pool: PoolConfig,
    available: list[str],
    usage: Mapping[str, Sequence[RateLimitInfo]],
    now: float,
    cursor: PoolCursor,
) -> tuple[str, PoolCursor, str]:
    if cursor.sticky in available:
        return cursor.sticky, cursor, "sticky profile still has headroom"  # type: ignore[return-value]
    # Move to the next available profile *after* the old one, so a reset of
    # an earlier profile does not pull the pool back.
    names = pool.names
    start = names.index(cursor.sticky) + 1 if cursor.sticky in names else 0
    ordered = names[start:] + names[:start]
    pick = next(name for name in ordered if name in available)
    reason = "sticky profile exhausted; switched" if cursor.sticky else "first sticky profile"
    return pick, replace(cursor, sticky=pick), reason


def _round_robin(
    pool: PoolConfig,
    available: list[str],
    usage: Mapping[str, Sequence[RateLimitInfo]],
    now: float,
    cursor: PoolCursor,
) -> tuple[str, PoolCursor, str]:
    names = pool.names
    start = cursor.rr_next % len(names)
    ordered = names[start:] + names[:start]
    pick = next(name for name in ordered if name in available)
    next_index = (names.index(pick) + 1) % len(names)
    return pick, replace(cursor, rr_next=next_index), "next in rotation"


def _most_headroom(
    pool: PoolConfig,
    available: list[str],
    usage: Mapping[str, Sequence[RateLimitInfo]],
    now: float,
    cursor: PoolCursor,
) -> tuple[str, PoolCursor, str]:
    # min() is stable, so ties go to the earlier profile in pool order.
    pick = min(available, key=lambda name: utilization(pool, usage.get(name, ()), now))
    return pick, cursor, "lowest utilization"


_STRATEGY = {
    "priority": _priority,
    "sticky": _sticky,
    "round_robin": _round_robin,
    "most_headroom": _most_headroom,
}
