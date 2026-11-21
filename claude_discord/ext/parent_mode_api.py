"""Parent mode at the spawn boundary (#1489).

Two things change when a thread in parent mode spawns a child:

- ``notify_parent`` defaults to true, so the manager hears back from every child
  without having to remember the flag (an explicit value still wins);
- the manager cannot hold more than ``max_children`` unfinished children at once.
  A prompt can ask an agent to keep the family small; only the relay can make
  sure it does.

"Unfinished" uses the same reading as auto-completion: a child is finished once
its title carries the candidate or done marker, or a human marked it done.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

from ..parent_mode import ParentModeSettings
from ..thread_marker import OUTCOME_CANDIDATE, OUTCOME_DONE, thread_outcome

__all__ = ["SpawnGate", "check_spawn"]

logger = logging.getLogger(__name__)

_FINISHED = frozenset({OUTCOME_CANDIDATE, OUTCOME_DONE})


class FamilyHost(Protocol):
    """What the gate needs to see a family. :class:`ApiCompletionHost` provides it."""

    async def parent_of(self, thread_id: int) -> int | None: ...
    async def children_of(self, thread_id: int) -> list[int]: ...
    async def thread_title(self, thread_id: int) -> str | None: ...
    async def human_done(self, thread_id: int) -> bool: ...


@dataclass(frozen=True)
class SpawnGate:
    """The parent-mode verdict on one spawn."""

    parent_mode: bool
    refusal: str | None = None
    unfinished: int = 0
    max_children: int = 0


async def _finished(host: FamilyHost, thread_id: int) -> bool:
    title = await host.thread_title(thread_id)
    if title is not None and thread_outcome(title) in _FINISHED:
        return True
    return await host.human_done(thread_id)


async def check_spawn(
    settings: ParentModeSettings, host: FamilyHost, parent_thread_id: int
) -> SpawnGate:
    """Whether *parent_thread_id* runs in parent mode, and whether it may spawn now."""
    is_child = await host.parent_of(parent_thread_id) is not None
    state = await settings.resolve(parent_thread_id, is_child=is_child)
    if not state.enabled:
        return SpawnGate(parent_mode=False)
    cap = await settings.max_children()
    unfinished = 0
    for child in await host.children_of(parent_thread_id):
        if not await _finished(host, child):
            unfinished += 1
    if unfinished < cap:
        return SpawnGate(parent_mode=True, unfinished=unfinished, max_children=cap)
    return SpawnGate(
        parent_mode=True,
        unfinished=unfinished,
        max_children=cap,
        refusal=(
            f"parent mode allows at most {cap} unfinished child threads and this thread "
            f"has {unfinished}. Finish, verify or close a child (mark it candidate/done) "
            "before spawning another, or do this piece yourself."
        ),
    )
