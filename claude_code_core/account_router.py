"""Route each turn to an account-pool profile and react to quota rejections.

The router is the I/O shell around :func:`claude_code_core.account_pool.select_profile`:
it reads usage, rejection markers and the pool cursor from SQLite, asks the
pure selector for a profile, moves the session transcript when a thread
changes profile, and records the outcome of the turn.

It is frontend-agnostic. A frontend calls :meth:`AccountRouter.plan_turn`
before spawning, sets ``runner.account = plan.binding``, passes an
:class:`~claude_code_core.account_pool.AccountTurn` to its event processing,
and calls :meth:`AccountRouter.finish_turn` afterwards.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from .account_pool import (
    AccountBinding,
    AccountTurn,
    PoolConfig,
    Selection,
    binding_for,
    select_profile,
    unavailable_until,
)
from .account_transcripts import ambient_home, copy_transcript

if TYPE_CHECKING:
    from .account_pool_repo import AccountPoolRepository
    from .session_repo import UsageStatsRepository
    from .types import RateLimitInfo

logger = logging.getLogger(__name__)

#: Shown for a session that predates the pool (it lives in the relay's own login).
RELAY_DEFAULT_LABEL = "relay default"


@dataclass(frozen=True)
class TurnPlan:
    """Where a turn runs and how its session gets there."""

    binding: AccountBinding
    selection: Selection
    #: Session id to resume (unchanged unless the transcript could not move).
    session_id: str | None
    #: Profile the session was on before this turn, when it moved.
    moved_from: str | None = None
    #: Home the session lived in before this turn, when it moved.
    source_home: Path | None = None
    #: True when the session moved but its transcript could not be copied,
    #: so the caller should seed a fresh session with a text handoff.
    needs_handoff: bool = False

    @property
    def profile(self) -> str:
        return self.binding.profile


@dataclass(frozen=True)
class Failover:
    """What happened when a turn was rejected for quota."""

    profile: str
    blocked_until: int
    next_profile: str | None
    retry: bool


@dataclass(frozen=True)
class ProfileStatus:
    """One row of ``/usage``: a profile and what the router knows about it."""

    backend: str
    profile: str
    usage: tuple[RateLimitInfo, ...]
    unavailable_until: int | None


class AccountRouter:
    """Chooses a profile per turn for every backend that has a pool."""

    def __init__(
        self,
        pools: dict[str, PoolConfig],
        repo: AccountPoolRepository,
        usage: UsageStatsRepository,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.pools = pools
        self._repo = repo
        self._usage = usage
        self._clock = clock
        # Read-select-write of the pool cursor must not interleave, or two
        # threads starting together would both take the same round-robin slot.
        self._select_lock = asyncio.Lock()

    def pool_for(self, backend: str) -> PoolConfig | None:
        return self.pools.get(backend)

    def home_of(self, backend: str, profile: str | None) -> Path:
        """Directory a profile's CLI state lives in (ambient for inheriting profiles)."""
        pool = self.pools.get(backend)
        spec = pool.profile(profile) if pool is not None and profile else None
        if spec is not None and spec.home is not None:
            return Path(spec.home)
        return ambient_home(backend)

    async def plan_turn(
        self, *, backend: str, thread_id: int, session_id: str | None
    ) -> TurnPlan | None:
        """Pick the profile for this turn; ``None`` when *backend* has no pool."""
        pool = self.pools.get(backend)
        if pool is None:
            return None

        pin = await self._repo.get_pin_home(thread_id)
        pinned = pin[1] if pin is not None and pin[0] == backend else None
        pinned_home = Path(pin[2]) if pinned is not None and pin and pin[2] else None
        # A profile removed from the pool file can no longer be chosen, but its
        # directory (stored with the pin) is still where the transcript lives.
        current = pinned if session_id and pinned in pool.names else None

        async with self._select_lock:
            usage = await self._usage.get_by_profile()
            blocks = await self._repo.get_blocks()
            cursor = await self._repo.get_cursor(backend)
            selection = select_profile(
                pool, usage, blocks, self._clock(), current=current, cursor=cursor
            )
            if selection.cursor != cursor:
                await self._repo.set_cursor(backend, selection.cursor)

        resume_id = session_id
        moved_from: str | None = None
        source_home: Path | None = None
        needs_handoff = False
        if session_id:
            source_home = pinned_home or self.home_of(
                backend, pinned if pinned in pool.names else None
            )
            target_home = self.home_of(backend, selection.profile)
            if source_home.resolve() != target_home.resolve():
                moved_from = pinned or RELAY_DEFAULT_LABEL
                copied = await asyncio.to_thread(
                    copy_transcript, backend, session_id, source_home, target_home
                )
                if not copied:
                    resume_id = None
                    needs_handoff = True
            else:
                source_home = None

        spec = pool.profile(selection.profile)
        await self._repo.set_pin(
            thread_id, backend, selection.profile, spec.home if spec is not None else None
        )
        logger.info(
            "Account pool %s: thread %d -> %s (%s)%s",
            backend,
            thread_id,
            selection.profile,
            selection.reason,
            f", moved from {moved_from}" if moved_from else "",
        )
        return TurnPlan(
            binding=binding_for(pool, selection.profile),
            selection=selection,
            session_id=resume_id,
            moved_from=moved_from,
            source_home=source_home,
            needs_handoff=needs_handoff,
        )

    async def finish_turn(self, turn: AccountTurn) -> Failover | None:
        """Mark the profile exhausted if the turn was rejected; say what comes next."""
        if not turn.rejected:
            return None
        pool = self.pools.get(turn.binding.backend)
        if pool is None:
            return None
        now = self._clock()
        resets = [r.resets_at for r in turn.rejections if r.resets_at > now]
        until = max(resets) if resets else int(now) + pool.cooldown_seconds
        reason = "rate_limit_event rejected" if turn.rejections else (turn.error or "")[:200]
        await self._repo.set_block(turn.binding.profile, until, reason)
        logger.warning(
            "Account pool %s: profile %s rejected for quota until %d",
            pool.backend,
            turn.binding.profile,
            until,
        )

        # Peek at the next choice without moving the cursor; the real choice
        # is made (and persisted) when the next turn is planned.
        usage = await self._usage.get_by_profile()
        blocks = await self._repo.get_blocks()
        cursor = await self._repo.get_cursor(pool.backend)
        peek = select_profile(pool, usage, blocks, self._clock(), cursor=cursor)
        if peek.all_exhausted or peek.profile == turn.binding.profile:
            return Failover(turn.binding.profile, until, next_profile=None, retry=False)
        return Failover(
            turn.binding.profile, until, next_profile=peek.profile, retry=pool.retry_on_exhaustion
        )

    async def record_usage(self, profile: str, windows: list[RateLimitInfo]) -> None:
        for info in windows:
            await self._usage.upsert(info, profile=profile)

    async def statuses(self) -> list[ProfileStatus]:
        """Every configured profile with its latest usage, in pool order."""
        usage = await self._usage.get_by_profile()
        blocks = await self._repo.get_blocks()
        now = self._clock()
        rows: list[ProfileStatus] = []
        for backend, pool in self.pools.items():
            for name in pool.names:
                profile_usage = tuple(usage.get(name, ()))
                rows.append(
                    ProfileStatus(
                        backend=backend,
                        profile=name,
                        usage=profile_usage,
                        unavailable_until=unavailable_until(
                            pool, profile_usage, blocks.get(name), now
                        ),
                    )
                )
        return rows


def build_account_router(db_path: str) -> AccountRouter | None:
    """Build a router from ``CCDB_ACCOUNT_POOLS_FILE``; ``None`` when unset.

    Raises :class:`~claude_code_core.account_pool_config.AccountPoolConfigError`
    for an invalid file, so a misconfiguration stops startup instead of
    routing turns to the wrong login.
    """
    from .account_pool_config import load_from_env
    from .account_pool_repo import AccountPoolRepository
    from .session_repo import UsageStatsRepository

    pools = load_from_env()
    if not pools:
        return None
    for backend, pool in pools.items():
        logger.info(
            "Account pool %s: strategy=%s assign=%s profiles=%s",
            backend,
            pool.strategy,
            pool.assign,
            ", ".join(pool.names),
        )
    return AccountRouter(pools, AccountPoolRepository(db_path), UsageStatsRepository(db_path))
