"""Concurrency awareness for multiple simultaneous Claude Code sessions.

Layer 1: Every session receives a generic concurrency warning in its prompt.
Layer 2: An in-memory registry tracks active sessions so each one knows
         what others are doing and can avoid conflicts.

See: https://github.com/ebibibi/ebi-agent-chat-relay/issues/52
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Layer 2: Active Session Registry
# ---------------------------------------------------------------------------


@dataclass
class ActiveSession:
    """Tracks a single active Claude Code session."""

    thread_id: int
    description: str
    working_dir: str | None = None


_BASE_CONCURRENCY_NOTICE = """\
[CONCURRENCY NOTICE — MANDATORY] You are one of MULTIPLE AI agent sessions \
running simultaneously via Discord. Your thread ID is {thread_id}. \
Messages marked [this thread] in the AI Lounge are YOUR earlier posts from \
this same thread — not from other sessions. After context compaction you may \
see your own lounge messages; do NOT treat them as another session's work. \
Other sessions ARE active right now. \
You MUST follow these rules to avoid destroying each other's work:

1. **Git — USE A WORKTREE (REQUIRED)**: Run \
`git worktree add ../wt-{thread_id} -b session/{thread_id}` BEFORE making \
any changes. Work ONLY inside your worktree. NEVER modify the main working \
directory directly. Always commit and push before finishing — uncommitted \
changes WILL be lost.
2. **Files**: Another session may be editing the same files RIGHT NOW. \
Check `git status` and recent file modification times before overwriting.
3. **Ports & processes**: Shared network ports or lock files may already be in use.
4. **Resources**: Shared databases, APIs with rate limits, or singleton processes \
may be accessed concurrently.
5. **Working directory does NOT persist between messages**: Each of your \
Discord replies runs in a FRESH process that starts in the base working \
directory. A `cd` only lasts for the current message — it is gone by your next \
reply, and the shell resets. So a relative-path script you set up in one \
message will silently run in the WRONG directory later. ALWAYS use absolute \
paths (for scripts, `os.chdir` to an absolute path at startup); for long jobs \
or large output, write results to an absolute-path log file and read it back.

CRITICAL: If your target repository is the same as another active session's, \
you MUST use a separate worktree or stop and warn the user. \
Do NOT proceed without isolation.\
"""

_OTHER_SESSIONS_HEADER = """
⚠️ ACTIVE SESSIONS RIGHT NOW (you MUST avoid conflicts with these):
"""


class SessionRegistry:
    """Thread-safe registry of active Claude Code sessions.

    Designed to be shared across all Cogs in a single bot instance.
    """

    def __init__(self) -> None:
        self._sessions: dict[int, ActiveSession] = {}
        self._lock = threading.Lock()

    def register(
        self,
        thread_id: int,
        description: str,
        working_dir: str | None = None,
    ) -> None:
        """Register or replace an active session."""
        with self._lock:
            self._sessions[thread_id] = ActiveSession(
                thread_id=thread_id,
                description=description,
                working_dir=working_dir,
            )

    def unregister(self, thread_id: int) -> None:
        """Remove a session from the registry."""
        with self._lock:
            self._sessions.pop(thread_id, None)

    def update(
        self,
        thread_id: int,
        *,
        description: str | None = None,
        working_dir: str | None = None,
    ) -> None:
        """Update fields of an existing session. No-op if not registered."""
        with self._lock:
            session = self._sessions.get(thread_id)
            if session is None:
                return
            if description is not None:
                session.description = description
            if working_dir is not None:
                session.working_dir = working_dir

    def list_active(self) -> list[ActiveSession]:
        """Return all active sessions."""
        with self._lock:
            return list(self._sessions.values())

    def list_others(self, thread_id: int) -> list[ActiveSession]:
        """Return all active sessions except the given thread."""
        with self._lock:
            return [s for s in self._sessions.values() if s.thread_id != thread_id]

    def build_concurrency_notice(self, thread_id: int) -> str:
        """Build the full concurrency notice for a session.

        Combines the base Layer 1 warning with Layer 2 context about
        other active sessions.
        """
        notice = _BASE_CONCURRENCY_NOTICE.format(thread_id=thread_id)
        others = self.list_others(thread_id)
        if others:
            notice += _OTHER_SESSIONS_HEADER
            for s in others:
                line = f"- {s.description}"
                if s.working_dir:
                    line += f" (working in {s.working_dir})"
                notice += line + "\n"
            notice += (
                "\nIf your work targets the same repository as any session above, "
                "you MUST use a git worktree. Do NOT proceed without isolation.\n"
            )
        return notice
