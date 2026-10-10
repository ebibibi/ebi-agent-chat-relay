"""What a console conversation is doing right now, for the live view.

A chat platform shows a running turn as it happens: tool embeds, a streaming
answer, a Stop button. The console stores none of that (see ``surface.py``);
this keeps it in memory while the turn runs so a client can poll it. Nothing
here survives a restart, and nothing needs to: once the turn ends, the
transcript has the answer.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

#: Activities kept per conversation. The view shows the latest few, not a log.
MAX_ACTIVITIES = 30
#: The streaming answer is shown as a tail; the whole of it reaches the transcript.
MAX_DRAFT_CHARS = 4000


@dataclass
class LiveActivity:
    title: str
    detail: str | None = None
    done: bool = False
    ok: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {"title": self.title, "detail": self.detail, "done": self.done, "ok": self.ok}


@dataclass
class LiveState:
    status: str | None = None
    activities: deque[LiveActivity] = field(default_factory=lambda: deque(maxlen=MAX_ACTIVITIES))
    draft: str = ""
    stop: Callable[[], Awaitable[None]] | None = None
    updated_at: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.updated_at = time.time()

    def as_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "activities": [a.as_dict() for a in self.activities],
            "draft": self.draft[-MAX_DRAFT_CHARS:],
            "can_stop": self.stop is not None,
            "updated_at": int(self.updated_at),
        }


class LiveBoard:
    """Per-conversation live state, keyed by thread key."""

    def __init__(self) -> None:
        self._states: dict[int, LiveState] = {}

    def state(self, thread_key: int) -> LiveState:
        return self._states.setdefault(thread_key, LiveState())

    def get(self, thread_key: int) -> LiveState | None:
        return self._states.get(thread_key)

    def clear(self, thread_key: int) -> None:
        """The turn ended: what was live is now in the transcript."""
        self._states.pop(thread_key, None)

    async def stop(self, thread_key: int) -> bool:
        """Press Stop on the running turn. False when there is nothing to stop."""
        state = self._states.get(thread_key)
        if state is None or state.stop is None:
            return False
        on_stop, state.stop = state.stop, None
        await on_stop()
        return True
