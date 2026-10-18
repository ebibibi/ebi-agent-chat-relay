"""The Focus guide: what to do now and why, what to decide together, and what was not recorded.

The Focus view ranks items; this module explains the ranking to a person.
It reads only what the board already carries (status, priority, deadline,
lineage, the triage model's guesses) and asks no model, so it costs nothing
per poll and gives the same answer twice.

Four parts:

* ``next`` — the top candidates, each with structured reasons (codes the
  client words in its own language) and whether acting lets an agent resume.
* ``batches`` — two or more decisions in one family, gathered under the root:
  one sitting with the parent settles all of them.
* ``calm`` — nothing is the human's to do right now. Saying so is the point:
  an empty list reads as "something is broken", not as "you can stop".
* ``drift`` — only when an Ebi Workspace is configured: threads marked done whose
  Workspace task is still open, or whose records predate the conversation. The
  board writes these back itself, so what reaches a person is what it could not.
  ``None`` means "no Workspace", which the client shows as nothing at all,
  so the guide works the same for someone who never set one up.

The client still decides place: ``next`` is ordered by importance only and
the client skips items bound to somewhere the person is not.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

#: Candidates returned; the client shows the first that fits where the person is.
MAX_NEXT = 5
MAX_DRIFT = 5
#: A thread finished longer ago than this is history, not drift worth raising.
DRIFT_WINDOW = timedelta(days=3)
DUE_SOON = timedelta(hours=24)

#: Statuses where the next move is the human's, with the reason each one gives.
_STATUS_REASON = {"waiting": "asked", "review": "review", "action": "action", "error": "error"}


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _ranked(item: Mapping[str, Any]) -> bool:
    return item.get("focus") is not None and item.get("actionable") is not False


def _order(item: Mapping[str, Any]) -> tuple[Any, ...]:
    """The Focus view's order: importance, then the nearer deadline, then the older wait."""
    return (-int(item["focus"]), item.get("due_at") or "9999", item.get("last_activity_at") or "")


def reasons(item: Mapping[str, Any], *, now: datetime) -> list[str]:
    """Why this item ranks where it does, most compelling first."""
    out = []
    due = _parse(item.get("due_at"))
    if item.get("overdue") or (due is not None and due < now):
        out.append("overdue")
    elif due is not None and due - now < DUE_SOON:
        out.append("due_soon")
    if int(item.get("priority", 2)) <= 1:
        out.append("priority")
    status_reason = _STATUS_REASON.get(str(item.get("status")))
    if status_reason:
        out.append(status_reason)
    return out


def _candidate(item: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
    ai = item.get("ai") or {}
    return {
        "id": item["id"],
        "title": item.get("title") or "",
        "reasons": reasons(item, now=now),
        # An agent stopped to hand this over: once the human acts, it carries on.
        "unblocks": bool(item.get("thread_id")) and item.get("status") in _STATUS_REASON,
        "next_step": ai.get("next_step") or None,
        "why": ai.get("reason") or None,
        "place": item.get("place"),
    }


def _root(item_id: str, parents: Mapping[str, str | None]) -> str:
    seen = {item_id}
    cursor = item_id
    while (parent := parents.get(cursor)) and parent in parents and parent not in seen:
        seen.add(parent)
        cursor = parent
    return cursor


def _batches(items: list[dict[str, Any]], ranked: list[dict[str, Any]]) -> list[dict[str, Any]]:
    parents = {i["id"]: i.get("parent_id") for i in items}
    titles = {i["id"]: i.get("title") or "" for i in items}
    families: dict[str, list[dict[str, Any]]] = {}
    for item in ranked:
        families.setdefault(_root(item["id"], parents), []).append(item)
    out = []
    for root, members in families.items():
        if len(members) < 2:
            continue
        out.append({"root_id": root, "title": titles[root], "items": [m["id"] for m in members]})
    # ``ranked`` is already ordered, so a family's first member is its most important one.
    first = {i["id"]: n for n, i in enumerate(ranked)}
    out.sort(key=lambda b: first[b["items"][0]])
    return out


def _drift(
    items: Iterable[dict[str, Any]], records: Mapping[str, Mapping[str, Any]], *, now: datetime
) -> list[dict[str, Any]]:
    out = []
    for item in items:
        path = item.get("workspace_path")
        record = records.get(path) if path else None
        activity = _parse(item.get("last_activity_at"))
        if (
            record is None
            # A candidate is written back when a person confirms it; only done lags.
            or item.get("status") != "done"
            or activity is None
            or now - activity > DRIFT_WINDOW
        ):
            continue
        names = {" ".join(str(item.get(k) or "").split()) for k in ("title", "note")} - {""}
        task = next(
            (t for t in record.get("tasks", []) if not t.get("done") and t.get("text") in names),
            None,
        )
        base = {"id": item["id"], "title": item.get("title") or "", "workspace_path": path}
        if task is not None:
            out.append({**base, "kind": "task_open", "task": task["text"]})
            continue
        # A guessed binding (working directory, parent) is too loose to accuse a record of
        # being stale: the thread may never have been that project's work at all.
        updated = _parse(record.get("updated_at"))
        if item.get("workspace_by") != "auto" and updated is not None and updated < activity:
            out.append({**base, "kind": "record_stale"})
    return out[:MAX_DRIFT]


def build_guide(
    items: Iterable[dict[str, Any]],
    *,
    records: Mapping[str, Mapping[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The guide for the board *items* (already triaged and Workspace-bound).

    *records* maps a Workspace path to ``{"tasks": [...], "updated_at": iso}``;
    pass ``None`` when no Workspace is configured.
    """
    now = now or datetime.now(UTC)
    items = list(items)
    ranked = sorted((i for i in items if _ranked(i)), key=_order)
    counts = {
        "me": len(ranked),
        "running": sum(1 for i in items if i.get("status") == "running"),
        "waiting": sum(
            1 for i in items if i.get("focus") is not None and i.get("actionable") is False
        ),
        "candidate": sum(1 for i in items if i.get("status") == "candidate"),
    }
    by_activity = sorted(items, key=lambda i: i.get("last_activity_at") or "", reverse=True)
    return {
        "next": [_candidate(i, now=now) for i in ranked[:MAX_NEXT]],
        "batches": _batches(items, ranked),
        "calm": not ranked,
        "counts": counts,
        "drift": None if records is None else _drift(by_activity, records, now=now),
    }
