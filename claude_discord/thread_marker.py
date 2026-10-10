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

# Prepended when the agent reports the thread's work finished (safe to close).
DEFAULT_DONE_MARKER = "\u2705"  # ✅

SPAWN_MARKER_ENV_VAR = "CCDB_SPAWN_THREAD_MARKER"
PARENT_MARKER_ENV_VAR = "CCDB_SPAWN_PARENT_MARKER"
DONE_MARKER_ENV_VAR = "CCDB_DONE_THREAD_MARKER"
WAITING_MARKER_ENV_VAR = "CCDB_WAITING_THREAD_MARKER"
ERROR_MARKER_ENV_VAR = "CCDB_ERROR_THREAD_MARKER"
REVIEW_MARKER_ENV_VAR = "CCDB_REVIEW_THREAD_MARKER"
ACTION_MARKER_ENV_VAR = "CCDB_ACTION_THREAD_MARKER"
SCHEDULED_MARKER_ENV_VAR = "CCDB_SCHEDULED_THREAD_MARKER"

# Prepended when the turn ended on a question only the human can answer.
DEFAULT_WAITING_MARKER = "\U00002753"  # ❓
# Prepended when the turn ended on a deliverable the human should look over.
DEFAULT_REVIEW_MARKER = "\U0001f440"  # 👀
# Prepended when the turn ended on a task the human has to do outside the chat.
DEFAULT_ACTION_MARKER = "\U0001f4cb"  # 📋
# Prepended when the turn ended in an error rather than an answer.
DEFAULT_ERROR_MARKER = "\U000026a0\U0000fe0f"  # ⚠️
# Prepended while a scheduled task is waiting to post into the thread.
DEFAULT_SCHEDULED_MARKER = "\U000023f0"  # ⏰

# How the last turn ended. At most one applies: each describes whose move it is.
OUTCOME_DONE = "done"
OUTCOME_WAITING = "waiting"
OUTCOME_REVIEW = "review"
OUTCOME_ACTION = "action"
OUTCOME_ERROR = "error"

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


def done_marker() -> str:
    """Marker for a thread whose work is finished and can be closed."""
    return _marker(DONE_MARKER_ENV_VAR, DEFAULT_DONE_MARKER)


def scheduled_marker() -> str:
    """Marker for a thread a scheduled task will post into later."""
    return _marker(SCHEDULED_MARKER_ENV_VAR, DEFAULT_SCHEDULED_MARKER)


def waiting_marker() -> str:
    """Marker for a thread whose turn ended waiting on the human's answer."""
    return _marker(WAITING_MARKER_ENV_VAR, DEFAULT_WAITING_MARKER)


def review_marker() -> str:
    """Marker for a thread whose turn ended on a deliverable to review."""
    return _marker(REVIEW_MARKER_ENV_VAR, DEFAULT_REVIEW_MARKER)


def action_marker() -> str:
    """Marker for a thread whose turn ended on a task for the human (a todo)."""
    return _marker(ACTION_MARKER_ENV_VAR, DEFAULT_ACTION_MARKER)


def error_marker() -> str:
    """Marker for a thread whose last turn failed."""
    return _marker(ERROR_MARKER_ENV_VAR, DEFAULT_ERROR_MARKER)


def _outcome_markers() -> dict[str, str]:
    return {
        OUTCOME_DONE: done_marker(),
        OUTCOME_WAITING: waiting_marker(),
        OUTCOME_REVIEW: review_marker(),
        OUTCOME_ACTION: action_marker(),
        OUTCOME_ERROR: error_marker(),
    }


def _split_status(name: str) -> tuple[str | None, bool, str]:
    """Strip the leading status markers: ``(outcome, scheduled, rest)``.

    Status markers describe the thread *now* and are toggled independently, so
    they are accepted in any order and always written back in the one order
    :func:`_join_status` uses — otherwise removing one would depend on which
    happened to be applied first.  Each kind is consumed at most once, which
    bounds the loop whatever the name holds.
    """
    outcomes = [(key, tag) for key, tag in _outcome_markers().items() if tag]
    scheduled_tag = scheduled_marker()
    outcome: str | None = None
    scheduled = False
    rest = name.strip()
    while True:
        hit = None
        if outcome is None:
            hit = next(((k, t) for k, t in outcomes if rest.startswith(t)), None)
        if hit is not None:
            outcome, rest = hit[0], rest[len(hit[1]) :].lstrip()
        elif scheduled_tag and not scheduled and rest.startswith(scheduled_tag):
            scheduled, rest = True, rest[len(scheduled_tag) :].lstrip()
        else:
            return outcome, scheduled, rest


def _join_status(outcome: str | None, scheduled: bool, rest: str) -> str:
    """Rebuild a name from its status: outcome first, then scheduled.

    The outcome leads because "is this waiting on me?" is the question a human
    scans the channel list for.  Truncation happens after tagging for the same
    reason as :func:`mark_spawned_thread_name` — the head must survive.
    """
    tags = [
        _outcome_markers().get(outcome, "") if outcome else "",
        scheduled_marker() if scheduled else "",
    ]
    prefix = " ".join(tag for tag in tags if tag)
    return _fit(f"{prefix} {rest}".strip() if prefix else rest)


def set_outcome_thread_name(name: str, outcome: str | None) -> str:
    """Return *name* showing *outcome* (``None`` clears it), idempotent.

    A new outcome replaces the old one: a thread that failed and was then
    finished is finished, not both.  The scheduled marker is left as it is.
    """
    _, scheduled, rest = _split_status(name)
    return _join_status(outcome, scheduled, rest)


def thread_outcome(name: str) -> str | None:
    """Return the outcome *name* currently shows (``OUTCOME_*``), or ``None``."""
    outcome, _, _ = _split_status(name)
    return outcome


def mark_done_thread_name(name: str) -> str:
    """Return *name* tagged as ready to close, Discord-safe and idempotent.

    The marker goes in front of everything, lineage tags included: "can I
    close this?" is the question a human scans the channel list for, so it
    is the first thing the eye should hit.
    """
    return set_outcome_thread_name(name, OUTCOME_DONE)


def unmark_done_thread_name(name: str) -> str:
    """Return *name* without a leading done marker (unchanged when absent)."""
    outcome, scheduled, rest = _split_status(name)
    return _join_status(None if outcome == OUTCOME_DONE else outcome, scheduled, rest)


def mark_scheduled_thread_name(name: str) -> str:
    """Return *name* tagged as waiting on a scheduled task, idempotent.

    Sits behind an outcome marker and in front of lineage tags: it is a
    status like the outcome, not part of the thread's ancestry.
    """
    outcome, _, rest = _split_status(name)
    return _join_status(outcome, True, rest)


def unmark_scheduled_thread_name(name: str) -> str:
    """Return *name* without the scheduled marker (unchanged when absent)."""
    outcome, _, rest = _split_status(name)
    return _join_status(outcome, False, rest)


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

    A leading outcome marker (done, waiting, review, action, error) is dropped, not returned:
    a retitle only happens on a human's turn, and a thread someone is talking
    in again has moved past how its last turn ended.  A scheduled marker is
    kept, leading the tags: the task is still waiting whatever the thread is
    called now.
    """
    _, scheduled, rest = _split_status(name)
    tags: list[str] = [scheduled_marker()] if scheduled else []
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
