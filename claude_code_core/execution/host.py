"""``host``: today's behaviour — the CLI runs as the relay's user, unwrapped."""

from __future__ import annotations

from .base import Launch
from .config import HOST


class HostEnvironment:
    name = HOST

    async def preflight(self, backend: str, launch: Launch) -> str | None:
        return None

    def transform(self, backend: str, launch: Launch) -> Launch:
        return launch
