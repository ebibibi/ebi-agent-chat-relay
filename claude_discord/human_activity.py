"""When a person last did something — the signal a reserved restart waits on.

A restart that waits only for "no session running" fires in the gap between
two turns of a conversation that is still going: the person sends the next
message, it is held in the queue, and the bot restarts under them. Session
counts cannot tell a busy person from a quiet bot; this module can.

Only deliberate human actions count: a message from an allowed Discord user,
or a change made in the Ebi AI Console. Reading the Console does not count, so
an open browser tab never blocks a restart forever.
"""

from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

_last_at: float | None = None
_last_source: str | None = None


def note_human_activity(source: str, *, at: float | None = None) -> None:
    """Record that a person just acted (``source`` says where, for the log)."""
    global _last_at, _last_source  # noqa: PLW0603
    _last_at = time.time() if at is None else at
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
