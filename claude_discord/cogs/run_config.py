"""Configuration dataclass for Claude Code execution.

Bundles all parameters needed to execute Claude Code CLI and stream results
to a Discord thread. Using a dataclass instead of a long positional argument
list makes call sites more readable and extension safer (new fields can be
added without changing every caller).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

import discord

from claude_code_core.backend import SessionBackend
from claude_code_core.frontend import ConversationSurface

from ..claude.types import ImageData
from ..concurrency import SessionRegistry
from ..database.ask_repo import PendingAskRepository
from ..database.lounge_repo import LoungeRepository
from ..database.repository import SessionRepository
from ..discord_ui.status import StatusManager

if TYPE_CHECKING:
    from claude_code_core.account_pool import AccountTurn

    from ..backend_settings import BackendSettings
    from ..collision import FileActivityTracker
    from ..database.inbox_repo import ThreadInboxRepository
    from ..database.repository import UsageStatsRepository
    from ..discord_ui.thread_dashboard import ThreadStatusDashboard
    from ..discord_ui.views import StopView
    from ..worktree import WorktreeManager


@dataclass
class RunConfig:
    """All parameters needed for a single Claude Code execution.

    Required fields:
        runner: A fresh (cloned) ClaudeRunner instance.
        prompt: The user's message or skill invocation.

    Optional fields:
        surface: Frontend-neutral destination. New callers should provide this.
        thread: Compatibility input for Discord callers; it is wrapped in a
                DiscordSurface when surface is omitted.
        session_id: Session ID to resume. None for new sessions.
        repo: Session repository for persisting thread-session mappings.
              Pass None for automated workflows without session persistence.
        status: StatusManager for emoji reactions on the user's message.
        registry: SessionRegistry for concurrency awareness. When provided,
                  the session is registered during execution and a concurrency
                  notice is prepended to the prompt.
        ask_repo: Repository for persisting AskUserQuestion state across restarts.
        lounge_repo: Repository for AI Lounge context injection.
        stop_view: StopView instance to bump after each major message, keeping
                   the Stop button at the bottom of the thread.
        worktree_manager: WorktreeManager for automatic session worktree cleanup.
                          When provided, the worktree for this thread is removed
                          (if clean) after the session ends.
    """

    runner: SessionBackend
    prompt: str
    # Canonical frontend-neutral destination. During the migration, existing
    # callers may still pass ``thread``; __post_init__ wraps it in a
    # DiscordSurface without making every call site change in the same PR.
    surface: ConversationSurface = None  # type: ignore[assignment]
    thread: discord.Thread | discord.TextChannel = None  # type: ignore[assignment]
    session_id: str | None = None
    repo: SessionRepository | None = None
    status: StatusManager | None = None
    registry: SessionRegistry | None = None
    ask_repo: PendingAskRepository | None = None
    lounge_repo: LoungeRepository | None = None
    stop_view: StopView | None = None
    worktree_manager: WorktreeManager | None = None
    # Base64-encoded image data for stream-json base64-type image blocks.
    images: list[ImageData] | None = None
    # When True, inject a system-prompt instruction telling Claude to write
    # requested file paths to .ccdb-attachments so the bot can send them.
    attach_on_request: bool = False
    # Thread inbox — when set, classifies the session's final message after
    # completion and persists the result so the dashboard can surface threads
    # that need the user's attention across bot restarts.
    inbox_repo: ThreadInboxRepository | None = None
    inbox_dashboard: ThreadStatusDashboard | None = None
    usage_repo: UsageStatsRepository | None = None
    # Records which files this session writes, so CollisionWatchCog can notice
    # two live sessions editing the same file without either announcing it.
    file_activity: FileActivityTracker | None = None
    claude_command: str = "claude"
    # When True, a compact guardrail was already injected into --append-system-prompt
    # for this run. Prevents infinite interrupt→rerun loops if compact fires again.
    post_compact_rerun: bool = False
    # True only for the single automatic rerun created by the owner-PR
    # completion gate. Prevents a genuinely blocked PR from causing a loop.
    pr_completion_gate_rerun: bool = False
    # When True, only text responses are shown to Discord. Tool embeds, thinking
    # blocks, session start/complete embeds, and other technical details are hidden.
    # Useful for public channels where non-technical users are watching.
    chat_only: bool = False
    # Discord user to mention when Claude pauses for an explicit button/form action.
    notify_user_id: int | None = None
    # Optional callback invoked once when the session reaches its terminal state,
    # with (final_assistant_text, error). Lets an external caller (e.g. the
    # /api/ingest endpoint) retrieve the session's final reply for write-back to
    # its own system. None for normal interactive sessions. Propagates across the
    # internal compact/AskUserQuestion reruns via dataclasses.replace, and fires
    # exactly once at the true terminal return in run_claude_with_config.
    result_sink: Callable[[str | None, str | None], Awaitable[None]] | None = None

    # Backend/model settings resolver. When provided (interactive chat only),
    # the per-turn footer consults it for the 2-layer Codex-status toggle
    # (status.codex global/thread). Headless flows (scheduler, webhook, API
    # ingest) leave this None, so they never spawn the Codex status probe.
    backend_settings: BackendSettings | None = None
    # Command used to invoke Codex (for the Codex status probe in the footer).
    codex_command: str = "codex"
    # Which frontend created this session mapping. Historical callers remain
    # Discord by default; the Teams host sets this explicitly.
    session_origin: str = "discord"
    # Account-pool observations for this turn (rejections, terminal error).
    # Set only when the backend has a pool; shared by reference across the
    # compact/ask reruns so the router sees the whole turn.
    account_turn: AccountTurn | None = None

    # Prevent accidental field mutation — RunConfig is a value object.
    # Use dataclasses.replace() to create modified copies.
    def __post_init__(self) -> None:
        if not self.prompt and not self.images:
            raise ValueError("RunConfig.prompt must not be empty")
        if self.surface is None:
            if self.thread is None:
                raise ValueError("RunConfig requires surface or thread")
            # Local import avoids a module cycle: DiscordSurface itself uses
            # RunConfig-adjacent UI components.
            from ..surface import DiscordSurface

            self.surface = DiscordSurface(
                self.thread,
                status_manager=self.status,
                working_dir=getattr(self.runner, "working_dir", None),
                interrupt_view=self.stop_view,
            )
        elif self.thread is None:
            # The reverse direction. A caller that passes only a surface still
            # reaches code that is genuinely Discord-only (AskUserQuestion's
            # restorable views, a thread's parent channel), and leaving this
            # None made those blow up on the first scheduled run rather than at
            # construction. On a non-Discord surface it stays None, which is
            # correct: there is no Discord object to hand out.
            native = getattr(self.surface, "native_thread", None)
            if native is not None:
                self.thread = native

    def with_prompt(self, prompt: str) -> RunConfig:
        """Return a new RunConfig with a different prompt (immutable copy)."""
        from dataclasses import replace

        return replace(self, prompt=prompt)
