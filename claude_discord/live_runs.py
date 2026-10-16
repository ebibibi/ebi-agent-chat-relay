"""The agent process each conversation is running right now (issue #1129).

A human who writes while the agent works expects the agent to read it, the way
Claude Code's own UI takes input mid-turn. Frontends call :func:`try_inject`
first and fall back to queueing the message as the next turn when it returns
False.

Entries are published by ``run_claude_with_config`` once its session slot is
held and the runner that owns the subprocess is known (the clone, not the
template the caller built), and withdrawn when that run ends.

Two rules keep a delivered message from going where the human did not mean it:

- **Order.** While a message for the conversation waits to run as its own
  turn, later messages queue behind it instead of overtaking it into the
  running turn (:func:`queued`).
- **Settings.** A message is only delivered to a run that uses the backend and
  execution environment the conversation is set to now. After ``/backend
  local`` or ``/sandbox``, the next message must not reach the old process.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from claude_code_core.types import ImageData

logger = logging.getLogger(__name__)

# ``execution_mode`` argument meaning "do not compare".
ANY_MODE = object()


@dataclass
class LiveRun:
    """One running turn: the backend that owns the process, and its queue slot."""

    runner: Any
    slot: Any = None
    processor: Any = None
    backend: str | None = None


_live: dict[int, LiveRun] = {}
_waiting: Counter[int] = Counter()


def register(thread_key: int, run: LiveRun) -> None:
    _live[thread_key] = run


def unregister(thread_key: int, run: LiveRun) -> None:
    """Withdraw *run*; a later run of the same conversation is left in place."""
    if _live.get(thread_key) is run:
        del _live[thread_key]


def get(thread_key: int) -> LiveRun | None:
    return _live.get(thread_key)


def hold(thread_key: int) -> Callable[[], None]:
    """Hold later messages back while a message waits to run as its own turn.

    Call when the message is queued. Returns the release, to call once the
    turn before it is over and it is about to run; calling it again is a no-op.
    """
    _waiting[thread_key] += 1
    held = True

    def release() -> None:
        nonlocal held
        if held:
            held = False
            _waiting[thread_key] -= 1
            if _waiting[thread_key] <= 0:
                del _waiting[thread_key]

    return release


@contextmanager
def queued(thread_key: int) -> Iterator[Callable[[], None]]:
    """:func:`hold` for one block: leaving it releases, so a cancelled waiter cannot stick."""
    release = hold(thread_key)
    try:
        yield release
    finally:
        release()


async def try_inject(
    thread_key: int,
    text: str,
    images: list[ImageData] | None = None,
    *,
    backend: str | None = None,
    execution_mode: Any = ANY_MODE,
) -> bool:
    """Hand *text* to the agent running in *thread_key*; False if none can take it.

    *backend* and *execution_mode* are what the conversation is set to now;
    a run started under other settings is not given the message.
    """
    run = _live.get(thread_key)
    if run is None:
        return False
    if _waiting.get(thread_key, 0) > 0:
        return False  # An earlier message is waiting; this one goes after it.
    inject = getattr(run.runner, "inject_user_message", None)
    if inject is None:
        return False  # Codex, pi, AG-UI: no input channel into a running turn.
    if backend is not None and run.backend is not None and backend != run.backend:
        return False
    if execution_mode is not ANY_MODE and (
        getattr(run.runner, "execution_mode", None) != execution_mode
    ):
        return False
    if run.slot is not None and getattr(run.slot, "pause_requested", False):
        return False  # About to yield its slot; the message would race the pause.
    if run.processor is not None and getattr(run.processor, "should_drain", False):
        return False  # Being stopped for a question or a compaction.
    accepted = bool(await inject(text, images))
    if accepted:
        logger.info("Delivered a message into the running turn of %s", thread_key)
    return accepted
