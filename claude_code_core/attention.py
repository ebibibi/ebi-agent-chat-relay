"""Estimate how much of the *operator's own* attention each day and thread used.

Agent time is cheap and already metered elsewhere. What a person running many
parallel threads actually runs out of is their own time: reading a reply,
deciding, and typing the next instruction. Nothing observes that directly, so
this module estimates it from the one signal every frontend has — when the
human sent a message, where, and how long it was.

The model (all parameters configurable, see :class:`AttentionParams`):

1. One author's messages across *all* threads are sorted in time and merged
   into bursts: a message within ``idle_gap`` of the previous one continues the
   burst, a longer silence starts a new one.
2. A burst costs its span plus a ``lead_in`` — the reading and deciding that
   happened before its first message and leaves no timestamp of its own.
3. A burst's minutes are shared among its messages in proportion to the
   characters written (by message count when the whole burst wrote none), so
   each message carries its own share to its thread and its own local day.
   Summed per thread that is exactly "split the burst across the threads it
   touched by characters"; summed per day it splits a burst that crosses
   midnight instead of handing it whole to one side.

The result is an estimate and says so: every report carries ``estimate: true``
and the parameters it was produced with. This module is pure — no I/O, no
clock — so it can be tested on fixed timestamps.
"""

from __future__ import annotations

import math
import time
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

__all__ = [
    "DEFAULT_ATTACHMENT_WEIGHT",
    "DEFAULT_IDLE_GAP_MINUTES",
    "MAX_YEAR",
    "MIN_YEAR",
    "DEFAULT_LEAD_IN_MINUTES",
    "AttentionConfig",
    "AttentionParams",
    "HumanActivity",
    "MessageShare",
    "SOURCE_BACKFILL",
    "SOURCE_LIVE",
    "build_report",
    "estimate_shares",
    "local_day_bounds_utc",
]

DEFAULT_IDLE_GAP_MINUTES = 10.0
DEFAULT_LEAD_IN_MINUTES = 2.0
DEFAULT_ATTACHMENT_WEIGHT = 50.0

# Days outside this window cannot be converted to UTC with a day of margin on
# either side without overflowing ``datetime``; nothing real lives there.
MIN_YEAR = 1970
MAX_YEAR = 9998

#: Where a row came from. Live rows are messages that reached a session;
#: backfilled rows are every human message history still holds.
SOURCE_LIVE = "live"
SOURCE_BACKFILL = "backfill"
SOURCES = (SOURCE_LIVE, SOURCE_BACKFILL)

GROUP_BY_DAY = "day"
GROUP_BY_THREAD = "thread"
GROUP_BY_CHOICES = (GROUP_BY_DAY, GROUP_BY_THREAD)

_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class HumanActivity:
    """One message a human sent that reached a session. Metadata only — no text.

    ``occurred_at`` must be timezone-aware; it is normalised to UTC on creation.
    """

    frontend: str
    conversation_id: str
    author_id: str
    occurred_at: datetime
    message_id: str
    char_count: int = 0
    attachment_count: int = 0
    parent_id: str | None = None
    thread_title: str | None = None
    source: str = SOURCE_LIVE

    def __post_init__(self) -> None:
        if self.source not in SOURCES:
            raise ValueError(f"HumanActivity.source must be one of {', '.join(SOURCES)}")
        if self.occurred_at.tzinfo is None:
            raise ValueError("HumanActivity.occurred_at must be timezone-aware")
        if self.char_count < 0 or self.attachment_count < 0:
            raise ValueError("HumanActivity counts must not be negative")
        if not self.message_id:
            raise ValueError("HumanActivity.message_id is required for idempotency")
        object.__setattr__(self, "occurred_at", self.occurred_at.astimezone(UTC))


@dataclass(frozen=True)
class AttentionParams:
    """The knobs of the estimate. ``timezone=None`` means the host's local zone."""

    idle_gap_minutes: float = DEFAULT_IDLE_GAP_MINUTES
    lead_in_minutes: float = DEFAULT_LEAD_IN_MINUTES
    timezone: str | None = None
    #: Characters an attachment counts as when splitting a burst, so a pasted
    #: screenshot is not worth nothing next to a typed sentence.
    attachment_weight: float = DEFAULT_ATTACHMENT_WEIGHT

    def __post_init__(self) -> None:
        if not math.isfinite(self.attachment_weight) or self.attachment_weight < 0:
            raise ValueError("attachment_weight must be zero or a positive number")
        if not math.isfinite(self.idle_gap_minutes) or self.idle_gap_minutes <= 0:
            raise ValueError("idle_gap_minutes must be a positive number")
        if not math.isfinite(self.lead_in_minutes) or self.lead_in_minutes < 0:
            raise ValueError("lead_in_minutes must be zero or a positive number")
        if self.timezone is not None:
            try:
                ZoneInfo(self.timezone)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ValueError(f"unknown timezone {self.timezone!r}") from exc

    @property
    def tz(self) -> tzinfo | None:
        """The zone days are bucketed in; ``None`` lets ``astimezone()`` use the host's."""
        return ZoneInfo(self.timezone) if self.timezone else None

    def timezone_label(self) -> str:
        if self.timezone:
            return self.timezone
        return f"local ({datetime.now().astimezone().tzname() or time.tzname[0]})"

    def local_day(self, moment: datetime) -> date:
        return moment.astimezone(self.tz).date()

    def weight(self, activity: HumanActivity) -> float:
        return activity.char_count + self.attachment_weight * activity.attachment_count

    def as_dict(self) -> dict[str, object]:
        return {
            "idle_gap_minutes": self.idle_gap_minutes,
            "lead_in_minutes": self.lead_in_minutes,
            "timezone": self.timezone_label(),
            "attachment_weight_chars": self.attachment_weight,
            "weighting": (
                "characters + attachment_weight_chars per attachment"
                " (message count when a burst has no weight)"
            ),
        }


@dataclass(frozen=True)
class AttentionConfig:
    """Environment-driven configuration. Recording is on unless switched off."""

    enabled: bool = True
    params: AttentionParams = field(default_factory=AttentionParams)

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> AttentionConfig:
        """Read ``CCDB_ATTENTION_*``. Raises ``ValueError`` naming a malformed variable."""
        enabled = env.get("CCDB_ATTENTION_ENABLED", "").strip().lower() not in _FALSE_VALUES
        idle = _float_env(env, "CCDB_ATTENTION_IDLE_GAP_MINUTES", DEFAULT_IDLE_GAP_MINUTES)
        lead = _float_env(env, "CCDB_ATTENTION_LEAD_IN_MINUTES", DEFAULT_LEAD_IN_MINUTES)
        attach = _float_env(env, "CCDB_ATTENTION_ATTACHMENT_WEIGHT", DEFAULT_ATTACHMENT_WEIGHT)
        tz_name = env.get("CCDB_ATTENTION_TIMEZONE", "").strip() or None
        try:
            params = AttentionParams(
                idle_gap_minutes=idle,
                lead_in_minutes=lead,
                timezone=tz_name,
                attachment_weight=attach,
            )
        except ValueError as exc:
            raise ValueError(f"invalid CCDB_ATTENTION_* setting: {exc}") from exc
        return cls(enabled=enabled, params=params)


def _float_env(env: Mapping[str, str], name: str, default: float) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


@dataclass(frozen=True)
class MessageShare:
    """The minutes of attention one message is charged with."""

    activity: HumanActivity
    minutes: float
    burst_index: int


def estimate_shares(
    activities: Iterable[HumanActivity], params: AttentionParams
) -> list[MessageShare]:
    """Charge every message with its share of the burst it belongs to.

    Bursts are formed per author, across all threads. The returned shares are
    in chronological order per author; their sum is the total estimate.
    """
    by_author: dict[str, list[HumanActivity]] = {}
    for activity in activities:
        by_author.setdefault(activity.author_id, []).append(activity)

    shares: list[MessageShare] = []
    burst_index = 0
    for author in sorted(by_author):
        for burst in _bursts(by_author[author], params.idle_gap_minutes):
            shares.extend(_share_burst(burst, params, burst_index))
            burst_index += 1
    return shares


def _bursts(activities: list[HumanActivity], idle_gap_minutes: float) -> list[list[HumanActivity]]:
    ordered = sorted(activities, key=lambda a: (a.occurred_at, a.message_id))
    gap = timedelta(minutes=idle_gap_minutes)
    bursts: list[list[HumanActivity]] = []
    for activity in ordered:
        if bursts and activity.occurred_at - bursts[-1][-1].occurred_at <= gap:
            bursts[-1].append(activity)
        else:
            bursts.append([activity])
    return bursts


def _share_burst(
    burst: list[HumanActivity], params: AttentionParams, burst_index: int
) -> list[MessageShare]:
    span = (burst[-1].occurred_at - burst[0].occurred_at).total_seconds() / 60
    minutes = span + params.lead_in_minutes
    raw = [params.weight(a) for a in burst]
    total = sum(raw)
    # Nothing written or attached at all: every message counts the same.
    weights = [w / total for w in raw] if total > 0 else [1 / len(burst)] * len(burst)
    return [
        MessageShare(activity=a, minutes=minutes * w, burst_index=burst_index)
        for a, w in zip(burst, weights, strict=True)
    ]


def local_day_bounds_utc(
    start: date, end: date, params: AttentionParams
) -> tuple[datetime, datetime]:
    """UTC instants covering local days ``start``..``end`` inclusive."""
    if start.year < MIN_YEAR or end.year > MAX_YEAR:
        raise ValueError(f"days must fall between the years {MIN_YEAR} and {MAX_YEAR}")
    tz = params.tz
    first = datetime.combine(start, datetime.min.time())
    after = datetime.combine(end + timedelta(days=1), datetime.min.time())
    if tz is None:
        return first.astimezone().astimezone(UTC), after.astimezone().astimezone(UTC)
    return (
        first.replace(tzinfo=tz).astimezone(UTC),
        after.replace(tzinfo=tz).astimezone(UTC),
    )


def build_report(
    activities: Iterable[HumanActivity],
    params: AttentionParams,
    *,
    start: date,
    end: date,
    group_by: str = GROUP_BY_DAY,
    author_id: str | None = None,
    include_backfill: bool = True,
) -> dict[str, object]:
    """Aggregate shares into a JSON-ready report for local days ``start``..``end``.

    *activities* may (and should) extend an ``idle_gap`` beyond the range on
    both sides so bursts at the edges are formed correctly; only messages whose
    local day falls inside the range are counted. ``include_backfill=False``
    estimates from live rows only — the messages known to have reached a session.
    """
    if group_by not in GROUP_BY_CHOICES:
        raise ValueError(f"group_by must be one of {', '.join(GROUP_BY_CHOICES)}")
    if end < start:
        raise ValueError("the range ends before it starts")

    pool = [
        a
        for a in activities
        if (author_id is None or a.author_id == author_id)
        and (include_backfill or a.source == SOURCE_LIVE)
    ]
    counted = [
        s
        for s in estimate_shares(pool, params)
        if start <= params.local_day(s.activity.occurred_at) <= end
    ]
    rows = _rows_by_day(counted, params) if group_by == GROUP_BY_DAY else _rows_by_thread(counted)
    return {
        "estimate": True,
        "parameters": params.as_dict(),
        "from": start.isoformat(),
        "to": end.isoformat(),
        "group_by": group_by,
        "author": author_id,
        "include_backfill": include_backfill,
        "sources": dict(sorted(Counter(s.activity.source for s in counted).items())),
        "total_minutes": _round(sum(s.minutes for s in counted)),
        "total_messages": len(counted),
        "rows": rows,
    }


def _rows_by_day(shares: list[MessageShare], params: AttentionParams) -> list[dict[str, object]]:
    minutes: dict[date, float] = {}
    messages: dict[date, int] = {}
    threads: dict[date, set[tuple[str, str]]] = {}
    for share in shares:
        day = params.local_day(share.activity.occurred_at)
        minutes[day] = minutes.get(day, 0.0) + share.minutes
        messages[day] = messages.get(day, 0) + 1
        key = (share.activity.frontend, share.activity.conversation_id)
        threads.setdefault(day, set()).add(key)
    return [
        {
            "day": day.isoformat(),
            "minutes": _round(minutes[day]),
            "messages": messages[day],
            "threads": len(threads[day]),
        }
        for day in sorted(minutes)
    ]


def _rows_by_thread(shares: list[MessageShare]) -> list[dict[str, object]]:
    minutes: dict[tuple[str, str], float] = {}
    messages: dict[tuple[str, str], int] = {}
    latest: dict[tuple[str, str], HumanActivity] = {}
    for share in shares:
        a = share.activity
        key = (a.frontend, a.conversation_id)
        minutes[key] = minutes.get(key, 0.0) + share.minutes
        messages[key] = messages.get(key, 0) + 1
        # The newest title wins: threads are renamed as their work drifts.
        if key not in latest or a.occurred_at >= latest[key].occurred_at:
            latest[key] = a
    ordered = sorted(minutes, key=lambda k: (-minutes[k], k))
    return [
        {
            "frontend": key[0],
            "conversation_id": key[1],
            "parent_id": latest[key].parent_id,
            "title": latest[key].thread_title,
            "minutes": _round(minutes[key]),
            "messages": messages[key],
            "last_active": latest[key].occurred_at.isoformat(),
        }
        for key in ordered
    ]


def _round(value: float) -> float:
    return round(value, 1)
