"""The agent process each conversation is running right now (issue #1129).

A human who writes while the agent works expects the agent to read it, the way
Claude Code's own UI takes input mid-turn. Frontends call :func:`try_inject`
first and fall back to queueing the message as the next turn when it returns
False.

Entries are published by ``run_claude_with_config`` once its session slot is
held and the runner that owns the subprocess is known (the clone, not the
template the caller built), and withdrawn when that run ends.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from claude_code_core.types import ImageData

logger = logging.getLogger(__name__)


@dataclass
class LiveRun:
    """One running turn: the backend that owns the process, and its queue slot."""

    runner: Any
    slot: Any = None
    processor: Any = None


_live: dict[int, LiveRun] = {}


def register(thread_key: int, run: LiveRun) -> None:
    _live[thread_key] = run


def unregister(thread_key: int, run: LiveRun) -> None:
    """Withdraw *run*; a later run of the same conversation is left in place."""
    if _live.get(thread_key) is run:
        del _live[thread_key]


def get(thread_key: int) -> LiveRun | None:
    return _live.get(thread_key)


async def try_inject(thread_key: int, text: str, images: list[ImageData] | None = None) -> bool:
    """Hand *text* to the agent running in *thread_key*; False if none can take it."""
    run = _live.get(thread_key)
    if run is None:
        return False
    inject = getattr(run.runner, "inject_user_message", None)
    if inject is None:
        return False  # Codex, pi, AG-UI: no input channel into a running turn.
    if run.slot is not None and getattr(run.slot, "pause_requested", False):
        return False  # About to yield its slot; the message would race the pause.
    if run.processor is not None and getattr(run.processor, "should_drain", False):
        return False  # Being stopped for a question or a compaction.
    accepted = bool(await inject(text, images))
    if accepted:
        logger.info("Delivered a message into the running turn of %s", thread_key)
    return accepted
