"""Assemble the board: threads + overlays + lineage + running turns → items.

Pure functions only. The server gathers the inputs (which needs a bot and a
database); everything that decides what a human sees lives here, where a test
can pin it down without either.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from ..thread_marker import (
    OUTCOME_ACTION,
    OUTCOME_DONE,
    OUTCOME_ERROR,
    OUTCOME_REVIEW,
    OUTCOME_WAITING,
    split_marker_tags,
    thread_outcome,
)
from .work_repo import (
    DEFAULT_PRIORITY,
    STATE_DONE,
    STATE_SOMEDAY,
    WorkItem,
    thread_item_id,
)

# What the item is doing. One value per item, most urgent wins.
STATUS_RUNNING = "running"  # a turn is in flight
STATUS_WAITING = "waiting"  # ❓ the agent asked the human something
STATUS_REVIEW = "review"  # 👀 a deliverable to look at
STATUS_ACTION = "action"  # 📋 a task only the human can do
STATUS_ERROR = "error"  # ⚠️ the last turn failed
STATUS_DONE = "done"  # ✅ finished
STATUS_SOMEDAY = "someday"  # parked by the human
STATUS_TODO = "todo"  # a capture: written down, not handed to an agent
STATUS_IDLE = "idle"  # the agent stopped without saying whose move it is

# Which list the item belongs in. The console's views are these buckets.
BUCKET_ME = "me"  # the ball is on the human
BUCKET_AI = "ai"  # an agent is working
BUCKET_TODO = "todo"
BUCKET_IDLE = "idle"
BUCKET_SNOOZED = "snoozed"
BUCKET_DONE = "done"

_OUTCOME_STATUS = {
    OUTCOME_WAITING: STATUS_WAITING,
    OUTCOME_REVIEW: STATUS_REVIEW,
    OUTCOME_ACTION: STATUS_ACTION,
    OUTCOME_ERROR: STATUS_ERROR,
    OUTCOME_DONE: STATUS_DONE,
}
_ME_STATUSES = {STATUS_WAITING, STATUS_REVIEW, STATUS_ACTION, STATUS_ERROR}


@dataclass(frozen=True)
class ThreadSnapshot:
    """What the chat platform says about one thread right now."""

    thread_id: int
    name: str
    archived: bool = False
    created_at: str | None = None
    last_activity_at: str | None = None
    url: str | None = None


@dataclass(frozen=True)
class SessionInfo:
    """What the session table says about one thread."""

    working_dir: str | None = None
    backend: str | None = None
    model: str | None = None
    last_used_at: str | None = None


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def clean_title(name: str) -> str:
    """The thread name without status markers or lineage tags."""
    _, rest = split_marker_tags(name)
    return rest.strip() or name.strip()


def derive_status(
    *,
    thread_name: str | None,
    running: bool,
    overlay: WorkItem | None,
    last_activity_at: str | None = None,
) -> str:
    """Decide one status for an item.

    A running turn beats everything: whatever the title said, the agent is
    working now. Next the human's own verdict (done / someday) — but only
    until the thread moves on. A verdict older than the thread's last message
    is stale: the conversation continued (in chat, or by a scheduled turn),
    and a question the agent asked since must reach the inbox rather than stay
    filed under Done. Last comes the marker the agent left. A thread without a
    marker is *idle* — the agent stopped without saying whose move it is,
    which the console shows rather than guessing.
    """
    if running:
        return STATUS_RUNNING
    if overlay is not None and overlay.state in (STATE_DONE, STATE_SOMEDAY):
        verdict_at = _parse(overlay.updated_at)
        activity = _parse(last_activity_at)
        if thread_name is None or activity is None or verdict_at is None or activity <= verdict_at:
            return STATUS_DONE if overlay.state == STATE_DONE else STATUS_SOMEDAY
    if thread_name is None:
        return STATUS_TODO
    outcome = thread_outcome(thread_name)
    return _OUTCOME_STATUS.get(outcome or "", STATUS_IDLE)


def break_cycles(parents: dict[str, str | None]) -> dict[str, str | None]:
    """Return *parents* with every link that closes a loop removed.

    Overlay parents are checked on write, but lineage parents are not the
    console's to check, and the two together can still form a loop. An item in
    a loop is reachable from no root, so a tree view would silently lose it.
    The link that closes the loop is cut, which keeps every item visible.
    """
    result = dict(parents)
    for start in list(result):
        seen: list[str] = []
        cursor: str | None = start
        while cursor is not None and cursor in result:
            if cursor in seen:
                result[seen[-1]] = None
                break
            seen.append(cursor)
            cursor = result.get(cursor)
    return result


def derive_bucket(status: str, *, snoozed: bool) -> str:
    if status == STATUS_RUNNING:
        return BUCKET_AI
    if status in (STATUS_DONE, STATUS_SOMEDAY):
        return BUCKET_DONE
    if snoozed:
        return BUCKET_SNOOZED
    if status in _ME_STATUSES:
        return BUCKET_ME
    if status == STATUS_TODO:
        return BUCKET_TODO
    return BUCKET_IDLE


def build_board(
    *,
    threads: Iterable[ThreadSnapshot],
    overlays: Iterable[WorkItem],
    lineage: dict[int, int],
    running: set[int],
    sessions: dict[int, SessionInfo] | None = None,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Merge every source into one flat list of items, sorted for triage.

    Args:
        threads: The threads the platform knows about.
        overlays: Every ``work_items`` row (thread overlays and captures).
        lineage: child thread id → parent thread id, from ``thread_lineage``.
        running: Threads with a turn in flight.
        sessions: Per-thread session details, when known.
        now: Injected for tests.

    The list is flat; ``parent_id`` makes it a tree. A parent that is not on
    the board (an old thread that has scrolled away) is dropped rather than
    left dangling, so a client never has to handle a missing node.
    """
    now = now or datetime.now(UTC)
    sessions = sessions or {}
    by_id = {o.id: o for o in overlays}
    snapshots = {t.thread_id: t for t in threads}
    items: list[dict[str, Any]] = []

    for thread_id, snap in snapshots.items():
        item_id = thread_item_id(thread_id)
        overlay = by_id.get(item_id)
        parent = overlay.parent_id if overlay and overlay.parent_id else None
        if parent is None and thread_id in lineage:
            parent = thread_item_id(lineage[thread_id])
        items.append(
            _item(
                item_id=item_id,
                thread_id=thread_id,
                title=(overlay.title if overlay and overlay.title else clean_title(snap.name)),
                thread_name=snap.name,
                overlay=overlay,
                parent_id=parent,
                running=thread_id in running,
                created_at=snap.created_at,
                last_activity_at=snap.last_activity_at,
                url=snap.url,
                archived=snap.archived,
                session=sessions.get(thread_id),
                now=now,
            )
        )

    for overlay in by_id.values():
        if overlay.thread_id is not None:
            continue
        items.append(
            _item(
                item_id=overlay.id,
                thread_id=None,
                title=overlay.title or "",
                thread_name=None,
                overlay=overlay,
                parent_id=overlay.parent_id,
                running=False,
                created_at=overlay.created_at,
                last_activity_at=overlay.updated_at,
                url=None,
                archived=False,
                session=None,
                now=now,
            )
        )

    present = {i["id"] for i in items}
    parents = {i["id"]: (i["parent_id"] if i["parent_id"] in present else None) for i in items}
    parents = break_cycles(parents)
    for item in items:
        item["parent_id"] = parents[item["id"]]
    items.sort(key=triage_key)
    return items


def _item(
    *,
    item_id: str,
    thread_id: int | None,
    title: str,
    thread_name: str | None,
    overlay: WorkItem | None,
    parent_id: str | None,
    running: bool,
    created_at: str | None,
    last_activity_at: str | None,
    url: str | None,
    archived: bool,
    session: SessionInfo | None,
    now: datetime,
) -> dict[str, Any]:
    status = derive_status(
        thread_name=thread_name,
        running=running,
        overlay=overlay,
        last_activity_at=last_activity_at,
    )
    snoozed_until = overlay.snoozed_until if overlay else None
    snooze_end = _parse(snoozed_until)
    snoozed = snooze_end is not None and snooze_end > now
    due_at = overlay.due_at if overlay else None
    due = _parse(due_at)
    return {
        "id": item_id,
        "thread_id": str(thread_id) if thread_id is not None else None,
        "title": title,
        "thread_name": thread_name,
        "status": status,
        "bucket": derive_bucket(status, snoozed=snoozed),
        "priority": overlay.priority if overlay else DEFAULT_PRIORITY,
        "due_at": due_at,
        "overdue": due is not None and due < now and status not in (STATUS_DONE, STATUS_SOMEDAY),
        "snoozed_until": snoozed_until if snoozed else None,
        "project": overlay.project if overlay else None,
        "note": overlay.note if overlay else None,
        "parent_id": parent_id,
        "running": running,
        "archived": archived,
        "created_at": created_at,
        "last_activity_at": last_activity_at,
        "url": url,
        "working_dir": session.working_dir if session else None,
        "backend": session.backend if session else None,
        "model": session.model if session else None,
    }


def triage_key(item: dict[str, Any]) -> tuple[Any, ...]:
    """Most urgent first: priority, then overdue, then due date, then oldest wait.

    Within the same priority the item that has waited longest comes first: a
    question the agent asked yesterday has cost more than one asked a minute
    ago.
    """
    due = item.get("due_at") or "9999"
    waited = item.get("last_activity_at") or "9999"
    return (item.get("priority", DEFAULT_PRIORITY), not item.get("overdue"), due, waited)
