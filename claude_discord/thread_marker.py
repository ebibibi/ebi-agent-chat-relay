"""Which threads an agent started, readable at a glance in the channel list.

A thread created through ``POST /api/spawn`` — one session starting another —
is indistinguishable from a thread a human opened by posting in the channel.
Discord exposes no per-thread colour, badge or icon, so the **title** is the
only surface available, and the marker therefore has to coexist with a name the
spawning agent chose: it is prepended, never substituted.

The marker is applied where the final name is assembled, not asked of the
caller. An agent that has to remember a convention will eventually forget it,
and the one thread that then looks human-authored is exactly the one worth
noticing.
"""

from __future__ import annotations

import os

# Discord rejects a thread name longer than this.
MAX_THREAD_NAME_LENGTH = 100

# Prepended to agent-spawned thread names unless the operator overrides it.
DEFAULT_SPAWN_MARKER = "\U0001f916"  # 🤖

SPAWN_MARKER_ENV_VAR = "CCDB_SPAWN_THREAD_MARKER"


def spawn_marker() -> str:
    """Return the configured marker, or ``""`` when the operator disabled it.

    An *empty* value is honoured as an explicit opt-out rather than folded back
    into the default: ``os.environ.get(...) or DEFAULT`` would make the marker
    impossible to turn off, and "the setting I wrote does nothing" is worse than
    no setting at all.  An unset variable — and only an unset variable — means
    "use the default", which keeps the feature zero-config for consumers.
    """
    configured = os.environ.get(SPAWN_MARKER_ENV_VAR)
    if configured is None:
        return DEFAULT_SPAWN_MARKER
    return configured.strip()


def mark_spawned_thread_name(name: str, marker: str | None = None) -> str:
    """Return *name* prefixed with the agent-spawn marker, Discord-safe.

    Truncation happens *after* the marker is attached so the head of the string
    survives: cutting the tail costs a few words of a title that was already
    being trimmed, while cutting the head would silently drop the one character
    the feature exists to show.

    Re-marking is a no-op.  A caller that already followed the convention (or a
    name derived from an earlier marked thread, as ``/fork`` does) must not end
    up with ``🤖 🤖``.
    """
    effective = spawn_marker() if marker is None else marker.strip()
    trimmed = name.strip()
    if not effective:
        return trimmed[:MAX_THREAD_NAME_LENGTH]
    if trimmed.startswith(effective):
        return trimmed[:MAX_THREAD_NAME_LENGTH]
    return f"{effective} {trimmed}"[:MAX_THREAD_NAME_LENGTH]
