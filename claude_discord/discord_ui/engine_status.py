"""Codex engine status for the per-turn footer.

Fetches Codex usage / rate-limit data via the ``codex app-server`` JSON-RPC
method ``account/rateLimits/read`` — the same call the Codex TUI makes on
startup — and formats it into a compact, Discord-ready line.

No browser automation, no public REST API: we drive the official client's
local stdio backend over JSON-RPC. The call is read-only and incurs no billing
(it is exactly what ``codex`` itself does every time it starts an interactive
session).

The result is cached per ``codex_command`` with a short TTL so that rapid
successive turns do not each spawn an ``app-server`` process.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import shlex
import time
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

# JSON-RPC method exposed by `codex app-server` that returns the same payload
# the TUI shows in its startup banner.
_RATE_LIMITS_METHOD = "account/rateLimits/read"

_DEFAULT_TIMEOUT = 15.0
# Successful results are cached this long; rate-limit windows move slowly
# (5h / weekly) so a short cache avoids spawning app-server every turn.
_DEFAULT_TTL = 90.0
# Failures (codex missing, not logged in) are cached for a shorter window so we
# do not hammer a broken setup on every message, but still recover quickly.
_FAIL_TTL = 30.0


async def fetch_codex_rate_limits(
    codex_command: str = "codex",
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict | None:
    """Return the raw ``account/rateLimits/read`` result, or ``None`` on failure.

    Spawns ``<codex_command> app-server``, performs the ``initialize``
    handshake, then calls the rate-limits method and returns its ``result``
    object. Any error (codex not installed, not logged in, protocol change,
    timeout) yields ``None`` — callers treat that as "Codex status unavailable".

    Security: always ``create_subprocess_exec`` (never a shell). ``codex_command``
    comes from trusted configuration, not user input, but is still split with
    ``shlex`` and passed as discrete argv entries.
    """
    parts = shlex.split(codex_command) if codex_command else ["codex"]
    if not parts:
        parts = ["codex"]

    try:
        proc = await asyncio.create_subprocess_exec(
            *parts,
            "app-server",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (OSError, ValueError):
        logger.debug("Failed to spawn codex app-server", exc_info=True)
        return None

    async def _exchange() -> dict | None:
        assert proc.stdin is not None and proc.stdout is not None
        init = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"clientInfo": {"name": "ccdb-engine-status", "version": "1.0.0"}},
        }
        read = {"jsonrpc": "2.0", "id": 2, "method": _RATE_LIMITS_METHOD, "params": None}
        proc.stdin.write((json.dumps(init) + "\n").encode())
        proc.stdin.write((json.dumps(read) + "\n").encode())
        await proc.stdin.drain()

        while True:
            line = await proc.stdout.readline()
            if not line:  # EOF
                return None
            try:
                msg = json.loads(line)
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if msg.get("id") == 2:
                return msg.get("result") if "result" in msg else None

    try:
        return await asyncio.wait_for(_exchange(), timeout=timeout)
    except (TimeoutError, Exception):
        logger.debug("codex app-server rate-limits exchange failed", exc_info=True)
        return None
    finally:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=3)


def _fmt_pct(snap: dict | None) -> str | None:
    """Return ``"N%"`` from a primary/secondary snapshot, or ``None``."""
    if not isinstance(snap, dict):
        return None
    pct = snap.get("usedPercent")
    if pct is None:
        return None
    try:
        return f"{round(float(pct))}%"
    except (TypeError, ValueError):
        return None


def _window_label(window_duration_mins: object) -> str | None:
    """Return a compact label derived from a rate-limit duration."""
    if isinstance(window_duration_mins, bool) or not isinstance(window_duration_mins, (int, float)):
        return None
    if not float(window_duration_mins).is_integer():
        return None

    minutes = int(window_duration_mins)
    if minutes <= 0:
        return None
    if minutes == 10080:
        return "週次"
    if minutes % 1440 == 0:
        return f"{minutes // 1440}d"
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    return f"{minutes}m"


def _window_segment(snap: dict | None, fallback_label: str) -> str | None:
    """Format one rate-limit window, retaining a legacy positional fallback."""
    pct = _fmt_pct(snap)
    if pct is None:
        return None
    duration = snap.get("windowDurationMins") if isinstance(snap, dict) else None
    label = _window_label(duration) or fallback_label
    return f"{label} {pct}"


def format_codex_status_line(data: dict | None) -> str | None:
    """Format a ``account/rateLimits/read`` result into one Discord line.

    Example: ``🤖 Codex: 5h 1% · 週次 8% · クレジット 0 (prolite)``.
    Returns ``None`` when there is nothing meaningful to show.
    """
    if not isinstance(data, dict):
        return None
    snap = data.get("rateLimits")
    if not isinstance(snap, dict):
        return None

    segments: list[str] = []
    primary = _window_segment(snap.get("primary"), "5h")
    if primary is not None:
        segments.append(primary)
    secondary = _window_segment(snap.get("secondary"), "週次")
    if secondary is not None:
        segments.append(secondary)

    credit_info = snap.get("credits")
    if isinstance(credit_info, dict):
        if credit_info.get("unlimited"):
            segments.append("クレジット 無制限")
        elif credit_info.get("balance") is not None:
            segments.append(f"クレジット {credit_info.get('balance')}")

    if not segments:
        return None

    plan = snap.get("planType")
    suffix = f" ({plan})" if plan else ""
    reached = snap.get("rateLimitReachedType")
    warn = " ⚠ 上限到達" if reached else ""
    return f"\U0001f916 Codex: {' · '.join(segments)}{suffix}{warn}"


class CodexStatusProvider:
    """TTL-cached provider of the formatted Codex status line.

    ``fetcher`` and ``clock`` are injectable for testing. In production the
    fetcher is :func:`fetch_codex_rate_limits` and the clock is
    ``time.monotonic``.
    """

    def __init__(
        self,
        codex_command: str = "codex",
        *,
        ttl: float = _DEFAULT_TTL,
        fail_ttl: float = _FAIL_TTL,
        fetcher: Callable[[str], Awaitable[dict | None]] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._command = codex_command
        self._ttl = ttl
        self._fail_ttl = fail_ttl
        self._fetcher = fetcher or (lambda cmd: fetch_codex_rate_limits(cmd))
        self._clock = clock
        self._cached_line: str | None = None
        self._cached_at: float | None = None
        self._cache_was_success = False
        self._lock = asyncio.Lock()

    def _is_fresh(self) -> bool:
        if self._cached_at is None:
            return False
        ttl = self._ttl if self._cache_was_success else self._fail_ttl
        return (self._clock() - self._cached_at) < ttl

    async def get_line(self, *, force: bool = False) -> str | None:
        """Return the cached status line, refreshing it when stale."""
        async with self._lock:
            if not force and self._is_fresh():
                return self._cached_line
            data = await self._fetcher(self._command)
            line = format_codex_status_line(data)
            self._cached_line = line
            self._cached_at = self._clock()
            self._cache_was_success = line is not None
            return line


# Module-level provider registry keyed by codex command so a single cache is
# shared across all turns/threads for a given command.
_PROVIDERS: dict[str, CodexStatusProvider] = {}


def _provider_for(codex_command: str) -> CodexStatusProvider:
    prov = _PROVIDERS.get(codex_command)
    if prov is None:
        prov = CodexStatusProvider(codex_command)
        _PROVIDERS[codex_command] = prov
    return prov


async def get_codex_status_line(codex_command: str = "codex") -> str | None:
    """Convenience entry point used by the per-turn footer (cached)."""
    return await _provider_for(codex_command).get_line()
