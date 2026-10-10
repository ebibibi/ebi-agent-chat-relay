"""A sliding one-minute window per key."""

from __future__ import annotations

import time
from collections import defaultdict, deque


class RateLimiter:
    """Sliding one-minute window per identity."""

    def __init__(self, per_minute: int) -> None:
        self._per_minute = per_minute
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, who: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        hits = self._hits[who]
        while hits and now - hits[0] > 60.0:
            hits.popleft()
        if len(hits) >= self._per_minute:
            return False
        hits.append(now)
        return True
