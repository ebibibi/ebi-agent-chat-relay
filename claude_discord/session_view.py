"""Cross-session observability — read-only views of what other sessions are doing.

Concurrent Claude Code sessions already announce themselves in the AI Lounge,
but a session had no way to look *at* another session: the lounge line told it
a thread ID and nothing more.  This module builds the JSON view served by
``GET /api/sessions`` so a session can answer "who else is running, where, and
what did they last say?" before touching a shared repository.

Everything here is pure data assembly — no Discord or database access — so the
merge rules stay testable without a running bot.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from claude_code_core.lounge_repo import LoungeMessage
    from claude_code_core.session_repo import SessionRecord

    from .concurrency import ActiveSession
    from .database.lineage_repo import Lineage

# A session is "running" while a Claude turn is in flight. Persisted records
# without an in-flight turn are history, not agents waiting for work or input.
# The SessionRegistry holds an entry only between turn start and turn end.
STATE_RUNNING = "running"
STATE_HISTORY = "history"


def latest_lounge_by_thread(messages: list[LoungeMessage]) -> dict[int, LoungeMessage]:
    """Map thread_id → that thread's most recent lounge message.

    ``LoungeRepository.get_recent`` returns oldest-first, so a later match
    simply overwrites an earlier one.
    """
    latest: dict[int, LoungeMessage] = {}
    for msg in messages:
        if msg.thread_id is not None:
            latest[msg.thread_id] = msg
    return latest


def build_session_views(
    *,
    records: list[SessionRecord],
    active: list[ActiveSession],
    running_thread_ids: set[int],
    lounge_messages: list[LoungeMessage],
    thread_names: dict[int, str] | None = None,
    lineage: list[Lineage] | None = None,
) -> list[dict[str, Any]]:
    """Merge the three sources of session truth into one ordered view.

    Args:
        records: Persisted sessions (``sessions`` table) — knows when a session
            was created, its working dir, backend and model.
        active: In-memory registry entries — knows what a session is doing
            *right now* (the first 100 chars of the current prompt).
        running_thread_ids: Threads with a Claude turn in flight.
        lounge_messages: Recent AI Lounge messages, oldest first.
        thread_names: Optional thread_id → Discord thread title.
        lineage: Optional spawn links. Turns a flat list of concurrent sessions
            into the tree that produced it, which is the difference between
            "ten sessions are running" and "I started four of them".

    Returns:
        One dict per thread, running sessions first, then recent history.
        A thread present only in the registry (no DB row yet — its session ID
        is minted after the first turn completes) still appears, because that
        is exactly the session most likely to collide with the caller.
    """
    names = thread_names or {}
    links = lineage or []
    parents = {link.thread_id: link for link in links}
    children: dict[int, list[int]] = {}
    for link in links:
        children.setdefault(link.parent_thread_id, []).append(link.thread_id)
    latest_lounge = latest_lounge_by_thread(lounge_messages)
    by_thread: dict[int, ActiveSession] = {s.thread_id: s for s in active}

    views: dict[int, dict[str, Any]] = {}

    for rec in records:
        views[rec.thread_id] = {
            "thread_id": rec.thread_id,
            "session_id": rec.session_id,
            "working_dir": rec.working_dir,
            "backend": rec.backend,
            "model": rec.model,
            "origin": rec.origin,
            "summary": rec.summary,
            "created_at": rec.created_at,
            "last_used_at": rec.last_used_at,
        }

    # Registry entries win on working_dir/description: they describe the turn
    # currently executing, while the DB row may predate a /cd or a worktree.
    for thread_id, session in by_thread.items():
        view = views.setdefault(
            thread_id,
            {
                "thread_id": thread_id,
                "session_id": None,
                "backend": None,
                "model": None,
                "origin": None,
                "summary": None,
                "created_at": None,
                "last_used_at": None,
            },
        )
        view["current_task"] = session.description
        if session.working_dir:
            view["working_dir"] = session.working_dir

    for thread_id, view in views.items():
        view.setdefault("current_task", None)
        view.setdefault("working_dir", None)
        view["thread_name"] = names.get(thread_id)
        link = parents.get(thread_id)
        view["parent_thread_id"] = None if link is None else link.parent_thread_id
        view["family"] = None if link is None else link.family_code
        view["children"] = children.get(thread_id, [])
        view["state"] = STATE_RUNNING if thread_id in running_thread_ids else STATE_HISTORY
        msg = latest_lounge.get(thread_id)
        view["latest_lounge"] = (
            None
            if msg is None
            else {"label": msg.label, "message": msg.message, "posted_at": msg.posted_at}
        )

    # Timestamps are datetime('now','localtime') strings, so lexicographic
    # order matches chronological order; a missing timestamp sorts last.
    newest_first = sorted(views.values(), key=lambda v: v["last_used_at"] or "", reverse=True)
    running = [v for v in newest_first if v["state"] == STATE_RUNNING]
    history = [v for v in newest_first if v["state"] != STATE_RUNNING]
    return running + history
