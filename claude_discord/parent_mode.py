"""Parent mode — a thread that manages child threads without being told (#1489).

The relay already has everything a manager needs: ``/api/spawn`` with
``parent_thread_id`` records the family, ``notify_parent`` reports each child's
outcome back, the family situation panel shows the tree, and a child's decision
requests surface in the parent's panel. What was missing is the instruction:
without it the human has to type "you are the parent, use child threads" on
every important request.

Parent mode is that instruction, switched on per thread or for the whole
deployment. Resolution order:

1. the thread's own override (``on``/``off``), set from the Console;
2. for a thread that was itself spawned as a child: **off** — children are
   workers, and a default that turned every child into a manager would grow the
   tree without bound;
3. the deployment default: the stored Console value, else
   ``CCDB_PARENT_MODE_DEFAULT``, else off (today's behaviour).

The cap on unfinished children per parent (``CCDB_PARENT_MODE_MAX_CHILDREN``,
default :data:`DEFAULT_MAX_CHILDREN`) is enforced by ``/api/spawn``; this module
only stores it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal

from .database.settings_repo import SettingsRepository

__all__ = [
    "DEFAULT_MAX_CHILDREN",
    "MAX_CHILDREN_LIMIT",
    "ParentModeSettings",
    "ParentModeState",
    "build_child_section",
    "build_parent_section",
    "parse_mode",
]

DEFAULT_MAX_CHILDREN = 5
#: Upper bound an operator can set: past this the family is not reviewable.
MAX_CHILDREN_LIMIT = 50

_DEFAULT_KEY = "parent_mode.default"
_THREAD_PREFIX = "parent_mode.thread."
_MAX_CHILDREN_KEY = "parent_mode.max_children"

_TRUE = frozenset({"1", "on", "true", "yes"})
_FALSE = frozenset({"0", "off", "false", "no"})

Source = Literal["thread", "child", "default"]


@dataclass(frozen=True)
class ParentModeState:
    """Whether a thread runs in parent mode, and which setting decided it."""

    enabled: bool
    source: Source


def parse_mode(raw: object) -> bool | None:
    """``on``/``off``/``default`` (or a boolean / null) → True / False / None.

    None means "no override: follow the default". Anything else is refused, so a
    typo in a request cannot silently clear a setting.
    """
    if raw is None or isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        value = raw.strip().lower()
        if value == "default":
            return None
        if value in _TRUE:
            return True
        if value in _FALSE:
            return False
    raise ValueError("mode must be on, off or default")


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in _TRUE


def _valid_max(value: int) -> bool:
    return 1 <= value <= MAX_CHILDREN_LIMIT


class ParentModeSettings:
    """Parent mode settings, stored in the relay's key-value settings table."""

    def __init__(self, repo: SettingsRepository) -> None:
        self._repo = repo

    async def default_enabled(self) -> bool:
        stored = await self._repo.get(_DEFAULT_KEY)
        if stored is not None:
            return stored in _TRUE
        return _env_flag("CCDB_PARENT_MODE_DEFAULT")

    async def set_default(self, enabled: bool) -> None:
        await self._repo.set(_DEFAULT_KEY, "on" if enabled else "off")

    async def thread_override(self, thread_id: int) -> bool | None:
        stored = await self._repo.get(f"{_THREAD_PREFIX}{int(thread_id)}")
        if stored is None:
            return None
        return stored in _TRUE

    async def set_thread(self, thread_id: int, enabled: bool | None) -> None:
        """Set this thread's override; None removes it so the default applies."""
        key = f"{_THREAD_PREFIX}{int(thread_id)}"
        if enabled is None:
            await self._repo.delete(key)
        else:
            await self._repo.set(key, "on" if enabled else "off")

    async def resolve(self, thread_id: int, *, is_child: bool) -> ParentModeState:
        override = await self.thread_override(thread_id)
        if override is not None:
            return ParentModeState(override, "thread")
        if is_child:
            return ParentModeState(False, "child")
        return ParentModeState(await self.default_enabled(), "default")

    async def max_children(self) -> int:
        stored = await self._repo.get(_MAX_CHILDREN_KEY)
        for raw in (stored, os.getenv("CCDB_PARENT_MODE_MAX_CHILDREN")):
            if raw is None:
                continue
            try:
                value = int(raw)
            except ValueError:
                continue
            if _valid_max(value):
                return value
        return DEFAULT_MAX_CHILDREN

    async def set_max_children(self, value: int) -> None:
        if not _valid_max(value):
            raise ValueError(f"max children must be between 1 and {MAX_CHILDREN_LIMIT}")
        await self._repo.set(_MAX_CHILDREN_KEY, str(value))


def build_parent_section(thread_id: int, max_children: int) -> str:
    """The system-prompt section for a thread in parent mode."""
    return (
        "## Parent mode (this thread manages child threads)\n"
        "The user turned on parent mode for this thread: they talk only to you, and you "
        "delegate. Act as the manager without waiting to be told.\n"
        "- Split substantial work into child threads: POST $CCDB_API_URL/api/spawn with "
        f'parent_thread_id: {thread_id} and "notify_parent": true, one child per '
        "independent piece (separate repo, file set or question), each with a "
        "self-contained brief: goal and done condition, what is already decided, scope and "
        "prohibitions, how to report. The relay-thread-manager skill describes the "
        "procedure if it is available.\n"
        "- Do small work yourself: a question you can answer, a lookup, a one-file edit, "
        "anything that takes a few minutes. A child thread costs a full session and the "
        "user's attention; never spawn one for a single command.\n"
        f"- At most {max_children} unfinished children at once; the relay refuses more. "
        "Finish, merge or close children before starting new ones.\n"
        "- Look after the children: each report the relay queues here is your cue to "
        "verify the evidence (PR, CI, deliverable), answer or correct the child, retry "
        "failures, and wait for CI with a registered wait rather than inside your turn. "
        "Do not forward each child's report to the user.\n"
        "- Report to the user once, here, in one combined report: what is done (with "
        "links), what needs their decision, what is still moving. Keep the situation "
        "panel current. A child's decision requests already appear in your panel: do not "
        "copy them; the user answers the child directly.\n"
        "- Publishing, sending, paying and deleting production data still need the "
        "user's explicit approval, whichever thread does the work."
    )


def build_child_section(parent_thread_id: int) -> str:
    """The system-prompt section for a child whose parent runs in parent mode."""
    return (
        "## Child of a managed thread\n"
        f"Thread {parent_thread_id} spawned you and manages this work; it is told each "
        "time you mark this thread finished or waiting on the user. Do the work in your "
        "brief yourself. Do not create child threads of your own: if the work is larger "
        "than your brief, say so in your final reply and let the manager split it. End "
        "every turn that finishes or blocks the work with the matching mark (candidate, "
        "waiting, review or action) and a short result: what changed, the evidence, "
        "what is open."
    )
