"""Per-thread execution-environment choice, bounded by the operator's allowlist.

The deployment default and the allowlist live in the environment
(``CCDB_EXECUTION_MODE`` / ``CCDB_EXECUTION_ALLOWED_MODES``); only the choice of
one allowed mode for one thread is stored here, next to the ``/backend``
overrides. A stored choice the operator has since removed from the allowlist is
ignored (the thread falls back to the default) rather than honoured.
"""

from __future__ import annotations

import inspect
import logging
from typing import TYPE_CHECKING, Any

from claude_code_core.execution import MODES, ExecutionConfig

if TYPE_CHECKING:
    from .database.settings_repo import SettingsRepository

logger = logging.getLogger(__name__)

EXECUTION_THREAD_PREFIX = "execution.thread."  # + thread_id


class ExecutionModeNotAllowedError(ValueError):
    """A user asked for a mode the operator did not put on the allowlist."""


class ExecutionSettings:
    """Read and write a thread's execution mode."""

    def __init__(self, repo: SettingsRepository) -> None:
        self.repo = repo

    @staticmethod
    def config() -> ExecutionConfig:
        return ExecutionConfig.from_env()

    async def stored_mode(self, thread_id: int) -> str | None:
        value = await self.repo.get(f"{EXECUTION_THREAD_PREFIX}{thread_id}")
        return value if value in MODES else None

    async def thread_mode(self, thread_id: int | None) -> str | None:
        """The thread's allowed override, or None for the deployment default."""
        if thread_id is None:
            return None
        stored = await self.stored_mode(thread_id)
        if stored is None:
            return None
        if not self.config().is_allowed(stored):
            logger.warning(
                "thread %d asked for execution mode %s, no longer allowed; using the default",
                thread_id,
                stored,
            )
            return None
        return stored

    async def effective_mode(self, thread_id: int | None) -> str:
        return await self.thread_mode(thread_id) or self.config().default_mode

    async def set_thread_mode(self, thread_id: int, mode: str) -> None:
        config = self.config()
        if mode not in MODES:
            raise ExecutionModeNotAllowedError(f"unknown execution mode {mode!r}")
        if not config.is_allowed(mode):
            raise ExecutionModeNotAllowedError(
                f"`{mode}` is not enabled on this deployment "
                f"(allowed: {', '.join(config.allowed_modes)})"
            )
        await self.repo.set(f"{EXECUTION_THREAD_PREFIX}{thread_id}", mode)
        logger.info("execution mode set: thread=%d -> %s", thread_id, mode)

    async def clear_thread_mode(self, thread_id: int) -> bool:
        return await self.repo.delete(f"{EXECUTION_THREAD_PREFIX}{thread_id}")


async def apply_thread_execution_mode(
    runner: Any, repo: SettingsRepository | None, thread_id: int | None
) -> None:
    """Set ``runner.execution_mode`` from the thread's stored, allowed choice.

    A read failure propagates: the thread may have asked for a stricter
    environment than the default, and quietly running it under the default
    would be the wrong direction to fail.
    """
    if repo is None or thread_id is None:
        return
    # Only an async key-value store can hold a choice; anything else (a bare
    # stand-in without a working ``get``) has none to apply.
    if not inspect.iscoroutinefunction(getattr(repo, "get", None)):
        return
    mode = await ExecutionSettings(repo).thread_mode(thread_id)
    if mode is not None:
        runner.execution_mode = mode
