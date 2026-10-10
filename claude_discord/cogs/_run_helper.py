"""Shared helper for running Claude Code CLI and streaming results to a Discord thread.

Both ClaudeChatCog and SkillCommandCog need to run Claude and post results.
This module is the thin orchestration layer that:
1. Builds ephemeral system context (lounge + concurrency notice) via --append-system-prompt
2. Delegates event processing to EventProcessor
3. Handles AskUserQuestion flow (recursive resume)

Primary API:
    run_claude_with_config(config: RunConfig) -> str | None

Legacy shim:
    run_claude_in_thread(thread, runner, repo, prompt, session_id, ...) -> str | None
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from dataclasses import replace
from typing import TYPE_CHECKING

import discord

from claude_code_core.frontend import Notice, NoticeLevel

from ..discord_ui.ask_handler import collect_ask_answers
from ..discord_ui.embeds import error_embed, timeout_embed
from ..discord_ui.slot_views import SlotWaitView, waiting_embed
from ..lounge import build_lounge_prompt
from ..pr_completion_gate import GitHubPrCompletionGate, build_completion_prompt
from ..session_slots import (
    SessionSlotScheduler,
    SlotEntry,
    SlotPriority,
    configure_session_slots,
    get_session_slots,
)
from ..thread_marker import OUTCOME_ERROR, OUTCOME_WAITING
from ..thread_status import schedule_thread_outcome
from .event_processor import EventProcessor
from .run_config import RunConfig

if TYPE_CHECKING:
    from claude_code_core.types import AskQuestion

    from ..database.wait_repo import WaitRepository

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global session slot limiter
# ---------------------------------------------------------------------------
_pr_completion_gate: GitHubPrCompletionGate | None = None

# Prompt for the automatic continuation of a session paused to free its slot.
# The interrupted turn is already in the transcript, so --resume plus this
# prompt picks the work up where it stopped.
PAUSE_RESUME_PROMPT = (
    "[Resumed automatically] This session was paused mid-turn so another "
    "session could use its slot. Continue the interrupted work from where it "
    "stopped. Do not redo steps that already completed."
)


def configure_session_limit(
    max_concurrent: int, *, allowed_user_ids: set[int] | None = None
) -> None:
    """Set the process-wide concurrent session limit.

    Called once from ``setup_bridge()`` during startup.  All subsequent calls to
    ``run_claude_with_config()`` — regardless of which Cog invokes them — will
    honour the limit, through one reorderable queue (``session_slots.py``).
    """
    configure_session_slots(max_concurrent, allowed_user_ids=allowed_user_ids)


def configure_pr_completion_gate(owner: str | None) -> None:
    """Enable the owner-PR completion gate, or disable it with an empty owner."""
    global _pr_completion_gate  # noqa: PLW0603
    _pr_completion_gate = GitHubPrCompletionGate(owner) if owner else None


# ---------------------------------------------------------------------------
# ScheduleWakeup → one-shot scheduled task bridge
# ---------------------------------------------------------------------------
# Harness-driven models (e.g. /loop dynamic pacing) call a ScheduleWakeup tool
# expecting to be re-invoked after a delay.  In claude -p mode no harness
# exists, so ccdb honours the request by registering a one-shot task in the
# SQLite scheduler that resumes the session in the same thread.
_wakeup_task_repo = None

# Bounds mirror the harness runtime clamp for ScheduleWakeup.delaySeconds.
_WAKEUP_MIN_DELAY_SECONDS = 60
_WAKEUP_MAX_DELAY_SECONDS = 3600

# Sentinel emitted for autonomous loops; meaningless outside the harness.
_AUTONOMOUS_LOOP_SENTINEL = "<<autonomous-loop-dynamic>>"
_AUTONOMOUS_LOOP_PROMPT = (
    "Continue your autonomous /loop iteration based on the previous context. "
    "If the loop's goal is complete, summarize and stop scheduling wakeups."
)


def configure_wakeup_scheduler(task_repo) -> None:
    """Set the process-wide TaskRepository used to honour ScheduleWakeup calls.

    Called once from ``setup_bridge()`` when the scheduler is enabled.  When
    unset, ScheduleWakeup tool calls are logged and ignored.
    """
    global _wakeup_task_repo  # noqa: PLW0603
    _wakeup_task_repo = task_repo


# Max characters for tool result display (re-exported for backward compat).
TOOL_RESULT_MAX_CHARS = 3000

# Injected via --append-system-prompt after context compaction to prevent
# Claude from auto-executing "pending tasks" from the compacted summary.
_POST_COMPACT_GUARDRAIL = (
    "⚠️ POST-COMPACT GUARDRAIL (MANDATORY): Context was just compacted. "
    "You MUST follow these rules:\n"
    "1. Do NOT automatically execute any external actions "
    "(posting to Teams/Slack/Discord/email, calling external APIs, creating resources, etc.) "
    "based on 'in progress' or 'pending' tasks in the compacted context summary.\n"
    "2. Treat every such pending task as needing fresh authorization from the user.\n"
    "3. Respond ONLY to what is explicitly requested in the user's current message.\n"
    "4. If relevant, briefly mention what you were doing before compaction.\n"
    "These rules override any implied continuation in the compacted summary."
)

_TIMEOUT_PATTERN = re.compile(r"Timed out after (\d+) seconds")


def _make_error_embed(error: str) -> discord.Embed:
    """Return a timeout_embed for timeout errors, error_embed otherwise."""
    m = _TIMEOUT_PATTERN.match(error)
    if m:
        return timeout_embed(int(m.group(1)))
    return error_embed(error)


def _truncate_result(content: str) -> str:
    """Truncate tool result content for display (re-exported for backward compat)."""
    if len(content) <= TOOL_RESULT_MAX_CHARS:
        return content
    return content[:TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"


def _build_done_marker_section(config: RunConfig) -> str | None:
    """Tell the agent how to signal that this thread can be closed.

    Only offered where it can work: a surface that renames threads, a backend
    that exposes the control-plane API, and a done marker the operator has
    not disabled.
    """
    from ..thread_marker import done_marker

    marker = done_marker()
    if not marker or not config.surface.capabilities.supports_thread_rename:
        return None
    if getattr(config.runner, "api_port", None) is None:
        return None
    return (
        "## Finishing a Thread\n"
        "When the user's request in this thread is fully finished — changes committed "
        "and pushed/merged, pipelines green, deliverables sent, nothing waiting on you "
        "or on the user — end your final reply by telling the user, in their language, "
        "that this thread can be closed, and mark it:\n"
        '  `curl -s -X POST "$CCDB_API_URL/api/threads/$DISCORD_THREAD_ID/done"`\n'
        f"This prefixes the thread title with {marker}. Do NOT mark it when you are asking "
        "a question, waiting for a pipeline or a decision, or leaving work pending. "
        "If the user replies later, the marker is removed automatically." + _waiting_marker_hint()
    )


def _build_wait_section(config: RunConfig) -> str | None:
    """Tell the agent to end its turn while CI runs — only where a watcher will resume it."""
    from .. import waits

    if not _waits_available(config):
        return None
    return waits.build_wait_section()


def _waits_available(config: RunConfig) -> bool:
    """A watcher runs and this turn's agent can reach the control plane to register."""
    from .. import waits

    return waits.watcher_active() and getattr(config.runner, "api_port", None) is not None


def _waiting_marker_hint() -> str:
    """The counterpart of done: the turn ends because the human has to move next.

    Split three ways so the channel list says *what* is expected, not just that
    something is: a reply, a look at a deliverable, or a task outside the chat.
    """
    from ..thread_marker import action_marker, review_marker, waiting_marker

    kinds = [
        ("waiting", waiting_marker(), "a reply is enough — a question, a choice, a go/no-go"),
        ("review", review_marker(), "a deliverable (draft, slides, attachment) to look over"),
        (
            "action",
            action_marker(),
            "a task they must do themselves outside this chat — a manual step in a UI, "
            "sign-in/MFA, an approval in a portal, a payment, anything physical",
        ),
    ]
    lines = [
        f"  {marker} {desc}:\n"
        f'    `curl -s -X POST "$CCDB_API_URL/api/threads/$DISCORD_THREAD_ID/{path}"`'
        for path, marker, desc in kinds
        if marker
    ]
    if not lines:
        return ""
    return (
        "\nWhen instead your turn ends because only the user can move the work forward, "
        "say plainly what you need from them and mark the thread with the ONE that fits:\n"
        + "\n".join(lines)
        + "\nThe marker is removed when the user replies. Do not use any of these while "
        "you are the one who still has work to do."
    )


async def _build_system_context(config: RunConfig) -> str | None:
    """Build ephemeral system context from AI Lounge and concurrency notice.

    Returns a string to inject as backend-specific developer/system instructions, or
    None if no context is available. Keeping it separate from the user message prevents
    this ephemeral metadata from accumulating in session history, which would otherwise
    cause "Prompt is too long" errors over long conversations.
    """
    parts: list[str] = []

    # Layer 3: AI Lounge context (recent messages + invitation).
    if config.lounge_repo is not None:
        try:
            recent = await config.lounge_repo.get_recent(limit=10)
            lounge_context = build_lounge_prompt(
                recent, current_thread_id=config.surface.thread_key
            )
            parts.append(lounge_context)
            logger.debug("Lounge context built (%d recent message(s))", len(recent))
        except Exception:
            logger.warning("Failed to fetch lounge context — skipping", exc_info=True)

    # Layer 1 + 2: Register session and build concurrency notice.
    if config.registry is not None:
        config.registry.register(
            config.surface.thread_key, config.prompt[:100], config.runner.working_dir
        )
        others = config.registry.list_others(config.surface.thread_key)
        notice = config.registry.build_concurrency_notice(config.surface.thread_key)
        parts.append(notice)
        logger.info(
            "Concurrency notice built for thread %d (%d other active session(s), dir=%s)",
            config.surface.thread_key,
            len(others),
            config.runner.working_dir or "(default)",
        )
    else:
        logger.debug(
            "No session registry — concurrency notice skipped for thread %d",
            config.surface.thread_key,
        )

    # File delivery marker: always injected so Claude knows the per-thread
    # marker name, even when it discovers the mechanism from session history
    # or CLAUDE.md rather than from an explicit "send me the file" request.
    from .event_processor import _attachment_marker_name

    wd = config.runner.working_dir or "your current working directory"
    marker = _attachment_marker_name(config.surface.thread_key)
    parts.append(
        "## File Delivery\n"
        "Discord cannot open local filesystem paths. Never describe a local path as a clickable "
        "link, and never use a local path or file:// URI as a user-facing Markdown link. "
        "For every file the user asks to view, open, download, or receive, deliver it as a real "
        "Discord attachment using the marker below and refer to it as an attached file in the "
        "final response. This Discord-specific rule overrides general instructions to prefer "
        "clickable local-file links.\n"
        "When you need to send files to Discord, use your Bash tool to append "
        "each file's ABSOLUTE path (one path per line, UTF-8) to:\n"
        f"  {wd}/{marker}\n"
        f"Example: `echo /absolute/path/to/file >> {wd}/{marker}`\n"
        "The bot will attach those files when this session ends.\n"
        "When local instructions require Discord attachment for a substantial "
        "written deliverable, save the final text as a Markdown file and append "
        "that file path here. Otherwise, only include files the user explicitly "
        "asked to receive."
    )

    done_section = _build_done_marker_section(config)
    if done_section:
        parts.append(done_section)

    wait_section = _build_wait_section(config)
    if wait_section:
        parts.append(wait_section)

    # Post-compact guardrail: prevent auto-execution of "pending tasks" from summary.
    if config.post_compact_rerun:
        parts.append(_POST_COMPACT_GUARDRAIL)
        logger.info("Post-compact guardrail injected for thread %d", config.surface.thread_key)

    return "\n\n".join(parts) if parts else None


async def _cleanup_session_worktree(config: RunConfig) -> None:
    """Remove the session worktree for this thread if it is clean.

    Runs git operations in a thread pool to avoid blocking the event loop.
    Logs the outcome but never raises — cleanup failures are non-fatal.
    """
    import asyncio

    assert config.worktree_manager is not None  # caller ensures this

    try:
        result = await asyncio.to_thread(
            config.worktree_manager.cleanup_for_thread,
            config.surface.thread_key,
        )
        if result.removed:
            logger.info(
                "Cleaned up session worktree for thread %d: %s",
                config.surface.thread_key,
                result.path,
            )
        elif result.reason == "worktree directory does not exist":
            # Normal case — Claude didn't create a worktree
            pass
        else:
            logger.warning(
                "Could not clean up worktree for thread %d (%s): %s",
                config.surface.thread_key,
                result.path,
                result.reason,
            )
            # Notify the Discord thread if there are uncommitted changes
            if "uncommitted changes" in result.reason:
                with contextlib.suppress(Exception):
                    await config.surface.send_notice(
                        Notice(
                            level=NoticeLevel.WARNING,
                            title="Worktree not cleaned up",
                            body=(
                                f"`{result.path}` has uncommitted changes. Please commit or "
                                f"stash them, then run:\n```\ngit worktree remove "
                                f"{result.path}\n```"
                            ),
                        )
                    )
    except Exception:
        logger.exception(
            "Unexpected error during worktree cleanup for thread %d", config.surface.thread_key
        )


async def _schedule_wakeup(config: RunConfig, wakeup: dict) -> None:
    """Register a one-shot scheduled task for a ScheduleWakeup request.

    The task resumes the session in the same thread after the requested delay
    (clamped to the harness range).  Failures are logged and reported to the
    thread but never break the just-finished session.
    """
    repo = _wakeup_task_repo
    if repo is None:
        logger.warning(
            "ScheduleWakeup requested in thread %d but scheduler is disabled — ignoring",
            config.surface.thread_key,
        )
        return

    try:
        delay = int(wakeup.get("delaySeconds", 0))
    except (TypeError, ValueError):
        delay = 0
    delay = max(_WAKEUP_MIN_DELAY_SECONDS, min(_WAKEUP_MAX_DELAY_SECONDS, delay))

    prompt = str(wakeup.get("prompt") or "").strip()
    if not prompt or prompt == _AUTONOMOUS_LOOP_SENTINEL:
        prompt = _AUTONOMOUS_LOOP_PROMPT
    reason = str(wakeup.get("reason") or "").strip()

    name = f"wakeup-thread-{config.surface.thread_key}"
    channel_id = getattr(config.thread, "parent_id", None) or config.surface.thread_key

    try:
        # Replace any previous wakeup for this thread — last call wins.
        await repo.delete_by_name(name)
        await repo.create(
            name=name,
            prompt=prompt,
            interval_seconds=delay,
            channel_id=channel_id,
            working_dir=getattr(config.runner, "working_dir", None),
            run_immediately=False,
            thread_id=config.surface.thread_key,
            one_shot=True,
        )
    except Exception:
        logger.exception("Failed to schedule wakeup task for thread %d", config.surface.thread_key)
        with contextlib.suppress(Exception):
            await config.surface.send_notice(
                Notice(level=NoticeLevel.WARNING, body="Wakeup could not be scheduled")
            )
        return

    label = f"⏰ Wakeup scheduled in {delay}s"
    if reason:
        label += f" — {reason}"
    logger.info("Wakeup scheduled for thread %d in %ds", config.surface.thread_key, delay)
    with contextlib.suppress(discord.HTTPException):
        await config.surface.send_notice(Notice(level=NoticeLevel.SUBTLE, body=label))


async def _get_pr_completion_prompt(
    config: RunConfig,
    *,
    session_id: str | None,
    final_error: str | None,
) -> str | None:
    """Return one automatic continuation prompt for owner PRs left open.

    GitHub availability must not turn a successful model response into a failed
    Discord turn, so lookup failures are visible but fail open. The rerun flag
    provides a hard one-continuation limit when a PR is genuinely blocked.
    """
    gate = _pr_completion_gate
    if (
        gate is None
        or config.pr_completion_gate_rerun
        or session_id is None
        or final_error is not None
    ):
        return None

    # A thread that registered a wait has done exactly what the gate asks for;
    # continuing it now would only register the same wait again.
    wait_repo = _waits_repo_if_available(config)
    if wait_repo is not None:
        try:
            if config.surface.thread_key in await wait_repo.active_thread_ids():
                return None
        except Exception:
            logger.warning("Could not read waits for the PR completion gate", exc_info=True)

    try:
        prs = await gate.find_for_thread(config.surface.thread_key)
    except Exception:
        logger.warning(
            "PR completion gate unavailable for thread %d",
            config.surface.thread_key,
            exc_info=True,
        )
        with contextlib.suppress(Exception):
            await config.surface.send_notice(
                Notice(
                    level=NoticeLevel.WARNING,
                    title="PR completion gate unavailable",
                    body=(
                        "GitHub could not be checked; this turn is being returned "
                        "without enforcement."
                    ),
                )
            )
        return None

    if not prs:
        return None

    with contextlib.suppress(Exception):
        await config.surface.send_notice(
            Notice(
                level=NoticeLevel.WARNING,
                title="Open owner PR detected — continuing",
                body=(
                    f"{len(prs)} non-draft PR(s) from session/{config.surface.thread_key} "
                    "are still open. The same agent will finish or report a concrete blocker."
                ),
            )
        )
    return build_completion_prompt(prs, waits_available=_waits_available(config))


def _waits_repo_if_available(config: RunConfig) -> WaitRepository | None:
    from .. import waits

    return waits.watcher_repo() if _waits_available(config) else None


async def run_claude_with_config(config: RunConfig) -> str | None:
    """Execute Claude Code CLI and stream results to a Discord thread.

    This is the primary entry point. All Cogs should create a RunConfig and
    pass it here, rather than using the legacy run_claude_in_thread() shim.

    Returns:
        The final session_id, or None if the run failed.
    """
    system_context = await _build_system_context(config)
    runner = (
        config.runner.clone(append_system_prompt=system_context)
        if system_context
        else config.runner
    )
    # Inject per-invocation images (not inherited by runner.clone()).
    if config.images:
        runner.images = config.images

    # Keep stop_view in sync with the runner that will own the live subprocess.
    # When system_context is present a fresh clone is created above, making the
    # original config.runner a "dead" runner with no process.  Without this
    # update the Stop button would send SIGINT to that dead runner and have no
    # effect.  See: https://github.com/ebibibi/ebi-agent-chat-relay/issues/174
    if runner is not config.runner:
        if config.stop_view is not None:
            config.stop_view.update_runner(runner)

        # Update config.runner to point to the clone so that EventProcessor
        # calls interrupt() on the runner that actually owns the subprocess.
        # Without this, compact_boundary and AskUserQuestion interrupt the
        # original (process-less) runner — a no-op that leaves Claude running
        # invisibly.  See: https://github.com/ebibibi/ebi-agent-chat-relay/issues/306
        config = replace(config, runner=runner)

    processor = EventProcessor(config)

    # --- Session slot limiter (reorderable queue) ---
    slots = get_session_slots()
    slot = await _acquire_slot(config, slots) if slots is not None else None
    if slot is not None:
        slot.on_pause = runner.interrupt
    if config.resumed_from_pause:
        # Deferred only while queueing; later compact/ask reruns queue normally.
        config = replace(config, resumed_from_pause=False)

    try:
        async for event in runner.run(config.prompt, session_id=config.session_id):
            if processor.should_drain and not event.is_complete:
                continue
            await processor.process(event)
    except Exception as exc:
        logger.exception("Error running Claude CLI for thread %d", config.surface.thread_key)
        with contextlib.suppress(Exception):
            detail = f"{type(exc).__name__}: {exc}"
            await config.surface.send_notice(
                Notice(
                    level=NoticeLevel.ERROR,
                    title="Error",
                    body=f"An unexpected error occurred.\n```\n{detail}\n```",
                )
            )
        if config.status:
            with contextlib.suppress(Exception):
                await config.status.set_error()
        await _show_outcome(config, OUTCOME_ERROR)
        await _emit_result_sink(config, None, f"{type(exc).__name__}: {exc}")
        return processor.session_id
    finally:
        if slots is not None and slot is not None:
            slots.release(slot)
        await processor.finalize()
        if config.registry is not None:
            config.registry.unregister(config.surface.thread_key)
        if config.worktree_manager is not None:
            await _cleanup_session_worktree(config)

    # Paused to free the slot: queue the continuation as deferred so it
    # resumes on its own once the sessions it yielded to are through.
    if slot is not None and slot.pause_requested:
        return await _requeue_paused(config, processor)

    # After compact_boundary, rerun with a guardrail to prevent Claude from
    # auto-executing "pending tasks" from the compacted context summary.
    if processor.compact_occurred:
        session_id = processor.session_id or config.session_id
        logger.info(
            "Compact detected for session %s — rerunning with post-compact guardrail", session_id
        )
        guardrail_config = replace(config, session_id=session_id, post_compact_rerun=True)
        return await run_claude_with_config(guardrail_config)

    # Honour a ScheduleWakeup tool call by registering a one-shot scheduled
    # task that resumes this session after the requested delay.  Done before
    # the AskUserQuestion flow — a wakeup and a pending ask are mutually
    # exclusive in practice (the ask interrupts the turn).
    if processor.pending_wakeup is not None:
        await _schedule_wakeup(config, processor.pending_wakeup)

    # After the stream ends, handle pending AskUserQuestion by showing Discord
    # UI and resuming the session with the user's answer.
    if processor.pending_ask and processor.session_id:
        await _show_outcome(config, OUTCOME_WAITING)
        if config.thread is None:
            # No Discord UI to collect the answer on. The questions go into the
            # conversation, and the human's next message resumes the session.
            await _post_questions(config, processor.pending_ask)
            await _emit_result_sink(config, processor.final_assistant_text, processor.final_error)
            return processor.session_id
        answer_prompt = await collect_ask_answers(
            config.thread,
            processor.pending_ask,
            processor.session_id,
            ask_repo=config.ask_repo,
            notify_user_id=config.notify_user_id,
        )
        if answer_prompt:
            await _show_outcome(config, None)
            logger.info(
                "Resuming session %s after AskUserQuestion answer",
                processor.session_id,
            )
            return await run_claude_with_config(config.with_prompt(answer_prompt))

    # A PR created by this Discord thread is an intermediate artifact, not a
    # terminal outcome. Resume the same agent once so green owner PRs are
    # merged and verified instead of being delegated back to the user.
    session_id = processor.session_id or config.session_id
    completion_prompt = await _get_pr_completion_prompt(
        config,
        session_id=session_id,
        final_error=processor.final_error,
    )
    if completion_prompt is not None:
        completion_config = replace(
            config,
            prompt=completion_prompt,
            session_id=session_id,
            pr_completion_gate_rerun=True,
        )
        return await run_claude_with_config(completion_config)

    # Terminal path. The compact/ask reruns above delegate to a nested
    # run_claude_with_config call, which reaches its own terminal return — so the
    # sink fires exactly once, from the outermost completed run. An in-stream
    # RESULT error (e.g. API 400/429) is reported as an error, not an empty
    # "done", so the caller can tell a failure from an empty answer.
    if processor.final_error:
        await _show_outcome(config, OUTCOME_ERROR)
    await _emit_result_sink(config, processor.final_assistant_text, processor.final_error)
    return processor.session_id


async def _show_outcome(config: RunConfig, outcome: str | None) -> None:
    """Show how the turn ended on whatever the conversation lives in.

    A Discord thread gets the marker in its title. A surface that keeps its
    own title (the Relay Console) exposes ``set_outcome``; any other surface
    has nowhere to show it.
    """
    if config.thread is not None:
        schedule_thread_outcome(config.thread, outcome)
        return
    set_outcome = getattr(config.surface, "set_outcome", None)
    if set_outcome is None:
        return
    try:
        await set_outcome(outcome)
    except Exception:
        logger.warning(
            "Could not show outcome %r on %d", outcome, config.surface.thread_key, exc_info=True
        )


async def _post_questions(config: RunConfig, questions: list[AskQuestion]) -> None:
    """Write AskUserQuestion's questions into a conversation that has no buttons."""
    blocks = []
    for q in questions:
        lines = [f"**{q.header}**"] if q.header else []
        lines.append(q.question)
        lines.extend(
            f"- {o.label}" + (f" — {o.description}" if o.description else "") for o in q.options
        )
        blocks.append("\n".join(lines))
    with contextlib.suppress(Exception):
        await config.surface.send_text("\n\n".join(blocks) + "\n\n_Reply with your answer._")


def _slot_label(config: RunConfig) -> str:
    name = getattr(config.thread, "name", None)
    return name if isinstance(name, str) and name else str(config.surface.thread_key)


async def _acquire_slot(config: RunConfig, slots: SessionSlotScheduler) -> SlotEntry:
    """Take a session slot, showing queue controls while the run waits."""
    thread_key = config.surface.thread_key
    priority = SlotPriority.DEFERRED if config.resumed_from_pause else SlotPriority.NORMAL
    acquire_coro = slots.acquire(
        thread_key,
        label=_slot_label(config),
        priority=priority,
        resumed_from_pause=config.resumed_from_pause,
    )
    if not slots.would_wait(priority):
        return await acquire_coro

    acquire = asyncio.ensure_future(acquire_coro)
    # Let the entry join the queue first so the message shows its position.
    await asyncio.sleep(0)

    view: SlotWaitView | None = None
    message: discord.Message | None = None
    if isinstance(config.thread, discord.abc.Messageable):
        view = SlotWaitView(slots, thread_key)
        with contextlib.suppress(Exception):
            message = await config.thread.send(
                embed=waiting_embed(slots, thread_key, resumed=config.resumed_from_pause),
                view=view,
            )
    else:
        with contextlib.suppress(Exception):
            await config.surface.send_notice(
                Notice(
                    level=NoticeLevel.SUBTLE,
                    body=(
                        f"\u23f3 Waiting for a free session slot\u2026 "
                        f"({slots.max_slots} max sessions running)"
                    ),
                )
            )
    try:
        return await acquire
    finally:
        if view is not None:
            await view.close(message, resumed=config.resumed_from_pause)


async def _requeue_paused(config: RunConfig, processor: EventProcessor) -> str | None:
    """Continue a paused session once a slot is free again."""
    session_id = processor.session_id or config.session_id
    logger.info("Thread %d paused to free its slot; re-queued", config.surface.thread_key)
    resume_config = replace(
        config,
        # Without a session there is nothing to resume, so the original
        # request runs again from the start.
        prompt=PAUSE_RESUME_PROMPT if session_id else config.prompt,
        session_id=session_id,
        resumed_from_pause=True,
    )
    return await run_claude_with_config(resume_config)


async def _emit_result_sink(config: RunConfig, text: str | None, error: str | None) -> None:
    """Invoke config.result_sink once with the run's terminal outcome.

    A sink failure must never break the Claude run, so all exceptions are
    suppressed and logged.
    """
    if config.result_sink is None:
        return
    try:
        await config.result_sink(text, error)
    except Exception:
        logger.exception("result_sink callback failed for thread %d", config.surface.thread_key)


async def run_claude_in_thread(
    thread: discord.Thread | discord.TextChannel,
    runner,
    repo,
    prompt: str,
    session_id: str | None,
    status=None,
    registry=None,
    ask_repo=None,
    lounge_repo=None,
) -> str | None:
    """Backward-compatible shim. Prefer run_claude_with_config() for new code."""
    config = RunConfig(
        thread=thread,
        runner=runner,
        prompt=prompt,
        session_id=session_id,
        repo=repo,
        status=status,
        registry=registry,
        ask_repo=ask_repo,
        lounge_repo=lounge_repo,
    )
    return await run_claude_with_config(config)
