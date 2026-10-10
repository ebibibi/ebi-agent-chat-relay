"""How much each backend has left, and when an exhausted one comes back.

Feeds ``GET /console/api/usage``, which the web client shows as a strip that
is always on screen. Sources are the ones ``/usage`` already reads:

* Claude: the ``rate_limit_event`` windows recorded after every turn
  (``usage_stats`` / ``account_usage_stats``). A database read, so cheap.
* Codex: ``codex exec --json`` carries no rate-limit events, so the read-only
  ``codex app-server`` probe is the only source. It spawns a process, so its
  answer is cached and never run once per poll.
* Account pools: every profile from :meth:`AccountRouter.statuses`, with the
  router's own view of whether the profile is blocked.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from typing import TYPE_CHECKING, Any

from claude_code_core.types import RateLimitInfo

from ..account_turns import codex_windows

if TYPE_CHECKING:
    from claude_code_core.account_router import AccountRouter
    from claude_code_core.session_repo import UsageStatsRepository

logger = logging.getLogger(__name__)

#: How long a Codex probe answer is reused. The board polls every few seconds;
#: quota moves on the scale of minutes.
CODEX_CACHE_SECONDS = 120.0
#: A failed probe is retried sooner, but not on every poll.
CODEX_FAIL_CACHE_SECONDS = 60.0

CodexFetcher = Callable[[str], Awaitable[dict | None]]


def _window(info: RateLimitInfo, now: float) -> dict[str, Any]:
    # A window whose reset time has passed is back to zero; the stored row is
    # only refreshed by the next turn, so showing it as-is would be stale.
    reset = info.resets_at <= now
    return {
        "type": info.rate_limit_type,
        "utilization": 0.0 if reset else round(max(0.0, info.utilization), 4),
        "resets_at": info.resets_at,
        "status": "allowed" if reset else info.status,
        "reset": reset,
    }


def _blocked_until(windows: Iterable[RateLimitInfo], now: float) -> int | None:
    """When the backend is usable again, or ``None`` if it is usable now."""
    blocking = [
        w.resets_at
        for w in windows
        if w.resets_at > now and (w.status == "rejected" or w.utilization >= 1.0)
    ]
    return max(blocking) if blocking else None


def backend_entry(
    backend: str,
    windows: Iterable[RateLimitInfo],
    *,
    now: float,
    profile: str | None = None,
    unavailable_until: int | None = None,
    **extra: Any,
) -> dict[str, Any]:
    """One row of the strip: a backend (or pool profile) and its windows."""
    rows = list(windows)
    until = unavailable_until if unavailable_until is not None else _blocked_until(rows, now)
    if until is not None and until <= now:
        until = None
    return {
        "backend": backend,
        "profile": profile,
        "available": until is None,
        "unavailable_until": until,
        "windows": [_window(w, now) for w in rows],
        **extra,
    }


def _reset_credits(data: dict | None) -> int | None:
    grant = (data or {}).get("rateLimitResetCredits")
    if not isinstance(grant, dict):
        return None
    count = grant.get("availableCount")
    return count if isinstance(count, int) and not isinstance(count, bool) else None


def _plan(data: dict | None) -> str | None:
    snap = (data or {}).get("rateLimits")
    plan = snap.get("planType") if isinstance(snap, dict) else None
    return plan if isinstance(plan, str) else None


class UsageReader:
    """Builds the ``/usage`` payload. Every source is optional."""

    def __init__(
        self,
        *,
        usage_repo: UsageStatsRepository | None = None,
        account_router: AccountRouter | None = None,
        codex_command: str | None = None,
        fetch_codex: CodexFetcher | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._usage = usage_repo
        self._router = account_router
        self._codex_command = codex_command
        self._fetch_codex = fetch_codex
        self._clock = clock
        self._codex_cache: tuple[float, dict | None] | None = None
        self._codex_lock = asyncio.Lock()

    async def read(self) -> dict[str, Any]:
        now = self._clock()
        pooled: set[str] = set()
        backends: list[dict[str, Any]] = []
        if self._router is not None:
            pooled = set(self._router.pools)
            backends.extend(await self._pool_entries(now))
        if "claude" not in pooled and self._usage is not None:
            backends.append(await self._claude_entry(now))
        if "codex" not in pooled and self._codex_command:
            backends.append(await self._codex_entry(now))
        return {"now": int(now), "backends": backends}

    async def _pool_entries(self, now: float) -> list[dict[str, Any]]:
        assert self._router is not None
        try:
            statuses = await self._router.statuses()
        except Exception:
            logger.exception("console: could not read account pool usage")
            return []
        return [
            backend_entry(
                s.backend,
                s.usage,
                now=now,
                profile=s.profile,
                unavailable_until=s.unavailable_until,
            )
            for s in statuses
        ]

    async def _claude_entry(self, now: float) -> dict[str, Any]:
        assert self._usage is not None
        try:
            windows = await self._usage.get_latest()
        except Exception:
            logger.exception("console: could not read Claude usage")
            return backend_entry("claude", [], now=now, error="unavailable")
        return backend_entry("claude", windows, now=now)

    async def _codex_entry(self, now: float) -> dict[str, Any]:
        data = await self._codex_data()
        if data is None:
            return backend_entry("codex", [], now=now, error="unavailable")
        return backend_entry(
            "codex",
            codex_windows(data),
            now=now,
            plan=_plan(data),
            reset_credits=_reset_credits(data),
        )

    async def _codex_data(self) -> dict | None:
        async with self._codex_lock:
            mono = time.monotonic()
            if self._codex_cache is not None:
                fetched_at, data = self._codex_cache
                ttl = CODEX_CACHE_SECONDS if data is not None else CODEX_FAIL_CACHE_SECONDS
                if mono - fetched_at < ttl:
                    return data
            data = await self._probe_codex()
            self._codex_cache = (mono, data)
            return data

    async def _probe_codex(self) -> dict | None:
        assert self._codex_command
        fetch = self._fetch_codex
        if fetch is None:
            from ..discord_ui.engine_status import fetch_codex_rate_limits

            fetch = fetch_codex_rate_limits
        try:
            data = await fetch(self._codex_command)
        except Exception:
            logger.warning("console: Codex usage probe failed", exc_info=True)
            return None
        return data if isinstance(data, dict) else None


__all__ = ["UsageReader", "backend_entry"]
