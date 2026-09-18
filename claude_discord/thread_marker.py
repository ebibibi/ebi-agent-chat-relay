"""Which threads an agent started, and which agent started them.

A thread created through ``POST /api/spawn`` is indistinguishable from a thread
a human opened by posting in the channel, and Discord exposes no per-thread
colour, badge or icon — the **title** is the only surface available.  Worse,
once several sessions each spawn several children, "an agent started this" is
no longer enough: the channel list becomes a flat pile of identical markers and
the tree that produced it is invisible.

So the title carries two things: that the thread was spawned, and *which family
it belongs to*.  The family code is derived from the spawning thread's own ID
rather than allocated, which means it can always be recomputed from the ID
alone — no counter to keep, and a lost database costs the lineage *records*
without ever costing the lineage *display*.

The marker is prepended, never substituted: the spawning agent keeps naming its
own thread.
"""

from __future__ import annotations

import hashlib
import os

# Discord rejects a thread name longer than this.
MAX_THREAD_NAME_LENGTH = 100

# Prepended to agent-spawned thread names unless the operator overrides it.
DEFAULT_SPAWN_MARKER = "\U0001f916"  # 🤖
# Prepended to the thread that did the spawning, in front of its own name.
DEFAULT_PARENT_MARKER = "\U0001f333"  # 🌳

SPAWN_MARKER_ENV_VAR = "CCDB_SPAWN_THREAD_MARKER"
PARENT_MARKER_ENV_VAR = "CCDB_SPAWN_PARENT_MARKER"

# Unambiguous alphabet: no 0/O, no 1/I/L. A code is read off a screen and typed
# back into an API call by a human as often as by an agent.
_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
_CODE_LENGTH = 2


def _marker(env_var: str, default: str) -> str:
    """Return the configured marker, or ``""`` when the operator disabled it.

    An *empty* value is honoured as an explicit opt-out rather than folded back
    into the default: ``os.environ.get(...) or DEFAULT`` would make the marker
    impossible to turn off, and "the setting I wrote does nothing" is worse than
    no setting at all.  An unset variable — and only an unset variable — means
    "use the default", which keeps the feature zero-config for consumers.
    """
    configured = os.environ.get(env_var)
    if configured is None:
        return default
    return configured.strip()


def spawn_marker() -> str:
    """Marker for a thread that *was* spawned."""
    return _marker(SPAWN_MARKER_ENV_VAR, DEFAULT_SPAWN_MARKER)


def parent_marker() -> str:
    """Marker for a thread that *did* the spawning."""
    return _marker(PARENT_MARKER_ENV_VAR, DEFAULT_PARENT_MARKER)


def family_code(thread_id: int) -> str:
    """Return the stable two-character family code for *thread_id*.

    Derived, not allocated: any component holding the ID can recompute the code
    without consulting a registry, so parent and child agree even across a
    restart or a lost database.

    Discord snowflakes are sequential, so consecutive threads would otherwise
    receive adjacent — near-identical — codes; hashing spreads them, which is
    what makes two families distinguishable at a glance in the channel list.
    """
    digest = hashlib.sha256(str(int(thread_id)).encode("utf-8")).digest()
    value = int.from_bytes(digest[:4], "big")
    code = ""
    for _ in range(_CODE_LENGTH):
        value, index = divmod(value, len(_CODE_ALPHABET))
        code += _CODE_ALPHABET[index]
    return code


def _fit(name: str) -> str:
    return name[:MAX_THREAD_NAME_LENGTH]


def mark_spawned_thread_name(
    name: str,
    marker: str | None = None,
    parent_thread_id: int | None = None,
) -> str:
    """Return *name* tagged as agent-spawned, Discord-safe.

    With a *parent_thread_id* the tag carries that parent's family code
    (``🤖K2 …``), which is what makes the child recognisable as **this** agent's
    child rather than merely some agent's child.  Without one the tag is the
    bare marker, so a caller that does not know its own thread still produces a
    thread nobody mistakes for human-authored.

    Truncation happens *after* the tag is attached so the head of the string
    survives: cutting the tail costs a few words of a title that was already
    being trimmed, while cutting the head would drop the very characters the
    feature exists to show.

    Re-marking is a no-op.  A caller that already followed the convention (or a
    name derived from an earlier marked thread, as ``/fork`` does) must not end
    up with ``🤖 🤖``.
    """
    effective = spawn_marker() if marker is None else marker.strip()
    trimmed = name.strip()
    if not effective:
        return _fit(trimmed)
    tag = effective if parent_thread_id is None else f"{effective}{family_code(parent_thread_id)}"
    if trimmed.startswith(tag):
        return _fit(trimmed)
    return _fit(f"{tag} {trimmed}")


def mark_parent_thread_name(
    name: str,
    thread_id: int,
    marker: str | None = None,
) -> str:
    """Return *name* tagged as a thread that spawns, using its own family code.

    The tag goes *after* an existing spawn tag rather than at the very front, so
    a thread that is both a child and a parent reads in the order it happened:
    ``🤖K2 🌳P9 …`` — spawned by family K2, root of family P9.  Prepending
    instead would put its children's code in front of its own parent's and
    invert the tree at a glance.

    Idempotent, and deliberately so: the second spawn from the same thread must
    not rename it again.  Discord allows a thread two renames per ten minutes,
    so a tag re-applied per spawn would start failing mid-fan-out and leave the
    parent untagged exactly when it has the most children.
    """
    effective = parent_marker() if marker is None else marker.strip()
    trimmed = name.strip()
    if not effective:
        return _fit(trimmed)
    tag = f"{effective}{family_code(thread_id)}"
    if tag in trimmed:
        return _fit(trimmed)
    spawn_tag_end = _spawn_tag_end(trimmed)
    if spawn_tag_end:
        head, rest = trimmed[:spawn_tag_end], trimmed[spawn_tag_end:].strip()
        return _fit(f"{head} {tag} {rest}")
    return _fit(f"{tag} {trimmed}")


def _spawn_tag_end(name: str) -> int:
    """Index just past a leading ``🤖XX`` tag, or 0 when there is none."""
    marker = spawn_marker()
    if not marker or not name.startswith(marker):
        return 0
    end = len(marker)
    while end < len(name) and name[end] in _CODE_ALPHABET:
        end += 1
    return end


def _tag_end(name: str, marker: str) -> int:
    """Index just past a leading *marker* plus its family code, or 0."""
    if not marker or not name.startswith(marker):
        return 0
    end = len(marker)
    while end < len(name) and name[end] in _CODE_ALPHABET:
        end += 1
    return end


def split_marker_tags(name: str) -> tuple[str, str]:
    """Split *name* into its leading lineage tags and the title behind them.

    ``"🤖K2 🌳P9 Fix the build"`` becomes
    ``("🤖K2 🌳P9", "Fix the build")``.  A name carrying no tag
    yields ``("", name)``, so a caller can always rebuild the name by joining
    the two halves — which is the point: re-titling a thread must not be the
    moment its lineage quietly disappears from the channel list.
    """
    rest = name.strip()
    tags: list[str] = []
    for marker in (spawn_marker(), parent_marker()):
        end = _tag_end(rest, marker)
        if end:
            tags.append(rest[:end])
            rest = rest[end:].lstrip()
    return " ".join(tags), rest


def retag_thread_name(old_name: str, new_title: str) -> str:
    """Return *new_title* wearing whatever lineage tags *old_name* carried.

    Tags already present in *new_title* are not doubled: the suggested title is
    written by a model that has seen the old name, so it sometimes copies the
    marker back in.
    """
    prefix, _ = split_marker_tags(old_name)
    _, title = split_marker_tags(new_title.strip())
    if not prefix:
        return _fit(title)
    return _fit(f"{prefix} {title}".strip())
