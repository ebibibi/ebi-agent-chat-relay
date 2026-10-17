"""Antigravity (``agy``) quota for the usage strip.

``agy`` streams no quota events, and its ``/credits`` panel is interactive
only. The numbers behind that panel come from the Cloud Code endpoint the CLI
itself calls on start-up, ``v1internal:fetchAvailableModels``: every model
carries ``quotaInfo.remainingFraction`` and ``quotaInfo.resetTime``. Models
share a handful of pools (Gemini models drain one, the partner models —
Anthropic and OpenAI — another), so the strip shows one window per pool.

Authentication reuses the OAuth token the CLI keeps on disk. Refreshing it is
the CLI's job: when the stored token has expired, ``agy models`` (a free,
read-only listing) is run once so the CLI writes a fresh one. The token is
only ever sent to Google and never logged.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path

import aiohttp

from claude_code_core.types import RateLimitInfo

logger = logging.getLogger(__name__)

MODELS_URL = "https://cloudcode-pa.googleapis.com/v1internal:fetchAvailableModels"
DEFAULT_TOKEN_FILE = "~/.gemini/antigravity-cli/antigravity-oauth-token"
#: Refresh a little before the stored expiry so the request never races it.
EXPIRY_MARGIN_SECONDS = 60.0
REQUEST_TIMEOUT_SECONDS = 20.0
REFRESH_TIMEOUT_SECONDS = 60.0

#: Window name per quota pool, keyed by ``modelProvider``.
_POOLS = {"MODEL_PROVIDER_GOOGLE": "gemini"}
_PARTNER_POOL = "partner"


def token_file() -> Path:
    return Path(os.getenv("CCDB_AGY_TOKEN_FILE") or DEFAULT_TOKEN_FILE).expanduser()


def _parse_time(value: object) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def antigravity_windows(data: dict | None) -> list[RateLimitInfo]:
    """One window per quota pool; the most drained model in a pool speaks for it."""
    models = (data or {}).get("models")
    if not isinstance(models, dict):
        return []
    pools: dict[str, tuple[float, int]] = {}
    for model in models.values():
        if not isinstance(model, dict) or model.get("isInternal"):
            continue
        quota = model.get("quotaInfo")
        if not isinstance(quota, dict):
            continue
        resets_at = _parse_time(quota.get("resetTime"))
        remaining = quota.get("remainingFraction", 0.0)
        # Models with no reset time are unmetered helpers (tab completion).
        if resets_at is None or isinstance(remaining, bool):
            continue
        if not isinstance(remaining, int | float):
            continue
        pool = _POOLS.get(str(model.get("modelProvider")), _PARTNER_POOL)
        used = min(1.0, max(0.0, 1.0 - float(remaining)))
        pools[pool] = max(pools.get(pool, (used, resets_at)), (used, resets_at))
    return [
        RateLimitInfo(
            rate_limit_type=pool,
            status="rejected" if used >= 1.0 else "allowed",
            utilization=used,
            resets_at=resets_at,
        )
        for pool, (used, resets_at) in sorted(pools.items())
    ]


def _read_token(path: Path, now: float) -> str | None:
    """The stored access token, or ``None`` if it is missing or expired."""
    try:
        token = json.loads(path.read_text(encoding="utf-8")).get("token") or {}
    except (OSError, ValueError, AttributeError):
        return None
    access = token.get("access_token") if isinstance(token, dict) else None
    expiry = _parse_time(token.get("expiry")) if isinstance(token, dict) else None
    if not isinstance(access, str) or not access:
        return None
    if expiry is not None and expiry - EXPIRY_MARGIN_SECONDS <= now:
        return None
    return access


async def _refresh_with_cli(command: str) -> None:
    proc = await asyncio.create_subprocess_exec(
        command,
        "models",
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        await asyncio.wait_for(proc.wait(), REFRESH_TIMEOUT_SECONDS)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise


async def fetch_antigravity_quota(command: str, *, path: Path | None = None) -> dict | None:
    """The ``fetchAvailableModels`` answer, or ``None`` when it cannot be read."""
    path = path or token_file()
    access = _read_token(path, time.time())
    if access is None:
        await _refresh_with_cli(command)
        access = _read_token(path, time.time())
    if access is None:
        logger.warning("console: no usable Antigravity token at %s", path)
        return None
    # The endpoint answers 403 to a generic client User-Agent.
    headers = {
        "Authorization": f"Bearer {access}",
        "Content-Type": "application/json",
        "User-Agent": "antigravity",
    }
    timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT_SECONDS)
    async with (
        aiohttp.ClientSession(timeout=timeout) as session,
        session.post(MODELS_URL, data=b"{}", headers=headers) as resp,
    ):
        if resp.status != 200:
            logger.warning("console: Antigravity quota request failed with HTTP %d", resp.status)
            return None
        data = await resp.json(content_type=None)
    return data if isinstance(data, dict) else None


__all__ = ["antigravity_windows", "fetch_antigravity_quota", "token_file"]
