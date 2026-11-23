"""The person's night settings and the morning brief, read and written for the Console (#1476).

The night settings are stored as one JSON value (:data:`.night.STATE_NIGHT`) in the Console's
state table; the morning brief reads the board, the open decision requests and the last
night shift and moves nothing.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from .console_state import ConsoleStateRepository
from .morning import build_morning_brief
from .night import (
    STATE_NIGHT,
    NightSettings,
    dump_night_settings,
    load_night_settings,
    parse_night_settings,
)


class NightDesk:
    """Night settings and the morning brief. Holds no state of its own."""

    def __init__(
        self,
        repo: ConsoleStateRepository,
        *,
        board: Callable[[], Awaitable[list[dict[str, Any]]]],
        requests: Callable[[], Awaitable[list[dict[str, Any]]]],
        situation: Callable[[int], Awaitable[dict[str, Any] | None]] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.repo = repo
        self._board = board
        self._requests = requests
        self._situation = situation
        self._clock = clock
        #: The 🌙 night shift (#1511), set by the Console; its last shift shows in the brief.
        self.night_shift: Any = None

    async def night_settings(self) -> NightSettings:
        return load_night_settings(await self.repo.get_state(STATE_NIGHT))

    async def set_night_settings(self, body: Any) -> NightSettings:
        """Store what the person chose; raises NightSettingsError listing every problem."""
        settings = parse_night_settings(body, await self.night_settings())
        await self.repo.set_state(STATE_NIGHT, dump_night_settings(settings))
        return settings

    async def morning(self, *, hours: int, base_url: str = "") -> dict[str, Any]:
        """The morning brief: done / decide / continuing. Reads only."""
        return await build_morning_brief(
            await self._board(),
            await self._requests(),
            situation=self._situation,
            now=self._clock(),
            hours=hours,
            base_url=base_url,
            shift=await self.night_shift.current() if self.night_shift is not None else None,
        )
