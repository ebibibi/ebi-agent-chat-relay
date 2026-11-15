"""When a person last did something — the signal a reserved restart waits on.

A restart that waits only for "no session running" fires in the gap between
two turns of a conversation that is still going: the person sends the next
message, it is held in the queue, and the bot restarts under them. Session
counts cannot tell a busy person from a quiet bot; this module can.

Only deliberate human actions count: every human message that passes the
frontend-neutral :class:`~claude_code_core.attention_repo.AttentionRecorder`
(Discord, Ebi AI Console, Teams), plus changes made in the Console. Reading the
Console does not count, so an open browser tab never blocks a restart forever.
On startup the clock is seeded from the latest recorded human message, so a
freshly restarted process does not mistake itself for a quiet one.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

_last_at: float | None = None
_last_source: str | None = None


def note_human_activity(source: str, *, at: float | None = None) -> None:
    """Record that a person acted (``source`` says where, for the log).

    ``at`` (epoch seconds) defaults to now. An older time than the one already
    known is ignored, so seeding from history never moves the clock back.
    """
    global _last_at, _last_source  # noqa: PLW0603
    moment = time.time() if at is None else at
    if _last_at is not None and moment <= _last_at:
        return
    _last_at = moment
    _last_source = source


def last_human_activity() -> float | None:
    """Epoch seconds of the latest human action in this process, or None."""
    return _last_at


def last_human_source() -> str | None:
    return _last_source


def reset_human_activity() -> None:
    """Forget every recorded action (tests, and nothing else)."""
    global _last_at, _last_source  # noqa: PLW0603
    _last_at = None
    _last_source = None
