"""Discord-side glue for account pools.

Keeps the chat cog's diff small: planning a turn (including the text handoff
when a transcript cannot move), the one-line notices a thread sees, the Codex
usage probe, and the ``/usage`` rendering all live here.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import TYPE_CHECKING

from claude_code_core.account_pool import AccountBinding
from claude_code_core.types import RateLimitInfo

from .cross_backend_handoff import ConversationHistoryReader, build_handoff_prompt

if TYPE_CHECKING:
    from claude_code_core.account_router import AccountRouter, Failover, ProfileStatus, TurnPlan

logger = logging.getLogger(__name__)

_WINDOW_NAMES = {300: "five_hour", 10080: "seven_day"}


async def plan_account_turn(
    router: AccountRouter | None,
    *,
    backend: str,
    thread_id: int,
    session_id: str | None,
    prompt: str,
) -> tuple[TurnPlan | None, str | None, str]:
    """Return ``(plan, session_id, prompt)`` for this turn.

    Without a router, or for a backend that has no pool, the session id and
    prompt pass through untouched.
    """
    if router is None:
        return None, session_id, prompt
    plan = await router.plan_turn(backend=backend, thread_id=thread_id, session_id=session_id)
    if plan is None:
        return None, session_id, prompt
    if plan.needs_handoff and session_id and plan.source_home is not None:
        reader = ConversationHistoryReader(
            claude_sessions_root=plan.source_home / "projects",
            codex_home=plan.source_home,
        )
        transcript = await asyncio.to_thread(reader.read, backend, session_id)
        if transcript:
            prompt = build_handoff_prompt(
                source_backend=f"{backend} ({plan.moved_from})",
                target_backend=f"{backend} ({plan.profile})",
                transcript=transcript,
                current_prompt=prompt,
                label="Account switch session handoff",
            )
    return plan, plan.session_id, prompt


def plan_notice(plan: TurnPlan | None) -> str | None:
    """A subtle one-liner when the thread moved or every profile is exhausted."""
    if plan is None:
        return None
    if plan.selection.all_exhausted:
        return (
            f"-# ⚠️ Every {plan.binding.backend} account is at its limit — "
            f"trying `{plan.profile}`, which resets first."
        )
    if plan.moved_from is None:
        return None
    how = (
        "fresh session seeded with the conversation text"
        if plan.needs_handoff
        else "session transcript copied, resuming"
    )
    return f"-# 🔀 Account `{plan.moved_from}` → `{plan.profile}` ({how})."


def failover_notice(failover: Failover) -> str:
    """Tell the thread which profile ran out and what happens next."""
    reset = _format_reset(failover.blocked_until)
    head = f"-# ⛽ Account `{failover.profile}` hit its usage limit (available again {reset})."
    if failover.next_profile is None:
        return f"{head} No other account has headroom right now."
    if failover.retry:
        return f"{head} Retrying this turn on `{failover.next_profile}`."
    return f"{head} Your next message will use `{failover.next_profile}`."


def _format_reset(epoch: int) -> str:
    return f"<t:{int(epoch)}:R>"


def codex_windows(data: dict | None) -> list[RateLimitInfo]:
    """Map a ``codex app-server`` rate-limit payload onto usage windows."""
    if not isinstance(data, dict):
        return []
    snap = data.get("rateLimits")
    if not isinstance(snap, dict):
        return []
    reached = bool(snap.get("rateLimitReachedType"))
    windows: list[RateLimitInfo] = []
    for key in ("primary", "secondary"):
        window = snap.get(key)
        if not isinstance(window, dict):
            continue
        try:
            used = float(window.get("usedPercent", 0)) / 100.0
            minutes = int(window.get("windowDurationMins", 0))
            resets_at = int(window.get("resetsAt", 0))
        except (TypeError, ValueError):
            continue
        windows.append(
            RateLimitInfo(
                rate_limit_type=_WINDOW_NAMES.get(minutes, f"{minutes}m"),
                status="rejected" if reached and used >= 1.0 else "allowed",
                utilization=used,
                resets_at=resets_at,
            )
        )
    return windows


async def refresh_codex_usage(
    router: AccountRouter, binding: AccountBinding, codex_command: str
) -> None:
    """Read one Codex profile's quota through ``codex app-server`` and store it.

    ``codex exec --json`` carries no rate-limit events, so this probe is the
    only per-profile utilization source for Codex. Read-only, no billing.
    """
    from .discord_ui.engine_status import fetch_codex_rate_limits

    env = {**os.environ, **binding.env}
    try:
        data = await fetch_codex_rate_limits(codex_command, env=env)
        windows = codex_windows(data)
        if windows:
            await router.record_usage(binding.profile, windows)
    except Exception:
        logger.warning("Codex usage probe failed for profile %s", binding.profile, exc_info=True)


def usage_lines(statuses: list[ProfileStatus], *, now: float | None = None) -> list[str]:
    """Render ``/usage`` for an account pool: one block per profile."""
    from .cogs.session_manage import _format_countdown, _progress_bar

    current = time.time() if now is None else now
    lines: list[str] = []
    backend: str | None = None
    for status in statuses:
        if status.backend != backend:
            backend = status.backend
            lines.append(f"__**{backend}**__")
        state = (
            f"⛔ exhausted, available again {_format_reset(status.unavailable_until)}"
            if status.unavailable_until is not None
            else "✅ available"
        )
        lines.append(f"**{status.profile}** — {state}")
        live = [u for u in status.usage if u.resets_at > current]
        if not live:
            lines.append("-# no usage reported yet")
        for info in live:
            pct = round(info.utilization * 100)
            lines.append(
                f"`{_progress_bar(info.utilization)}` **{pct}%** {info.rate_limit_type}"
                f" — {_format_countdown(info.resets_at)}"
            )
        lines.append("")
    return lines
