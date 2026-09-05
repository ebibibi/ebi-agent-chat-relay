"""Event processor for Claude Code stream-json output.

Encapsulates all the state and logic for processing a single Claude Code
CLI session: tracking session IDs, streaming text to Discord, posting tool
embeds, handling AskUserQuestion interrupts, and posting the final result.

This class is extracted from the monolithic run_claude_in_thread() function
so that individual event handlers can be tested in isolation.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import Callable, Coroutine
from pathlib import Path

from claude_code_core.approvals import (
    elicitation_form_prompt,
    elicitation_form_result,
    elicitation_url_prompt,
    elicitation_url_result,
    permission_prompt,
    permission_result,
    plan_prompt,
    plan_result,
)
from claude_code_core.frontend import (
    ActivitySpec,
    ChoicePrompt,
    ConversationSurface,
    Mention,
    Notice,
    NoticeLevel,
    StatusKind,
)
from claude_code_core.types import ElicitationRequest

from ..claude.types import AskQuestion, MessageType, SessionState, StreamEvent, ToolUseEvent
from ..collision import extract_written_path
from .run_config import RunConfig

logger = logging.getLogger(__name__)


def _backend_name_from_runner(runner: object) -> str:
    """Best-effort mapping from runner class name to embed backend tag."""
    cls = type(runner).__name__
    if cls == "AnonymizingBackend":
        inner = getattr(runner, "inner", None)
        if inner is not None and inner is not runner:
            return _backend_name_from_runner(inner)
    # LocalCodexRunner subclasses CodexRunner, so it has to be matched first.
    if cls == "LocalCodexRunner":
        return "local"
    if cls == "CodexRunner":
        return "codex"
    if cls == "AgUiBackend":
        return "agui"
    return "claude"


# Marker file prefix. The full name includes the thread ID to prevent
# cross-contamination when multiple sessions share the same working_dir.
_ATTACHMENT_MARKER_PREFIX = ".ccdb-attachments"


def _attachment_marker_name(thread_id: int) -> str:
    return f"{_ATTACHMENT_MARKER_PREFIX}-{thread_id}"


async def _send_attachment_requests(
    surface: ConversationSurface,
    working_dir: str | None,
    thread_id: int,
) -> None:
    """Read .ccdb-attachments-{thread_id} and deliver them through the surface.

    If the marker file does not exist or is empty, this is a no-op.
    Requests move to a durable pending outbox and are acknowledged individually
    after delivery. Failed files remain pending for the next completed turn;
    attachment errors do not fail the session itself.
    """
    if not working_dir:
        logger.debug("_send_attachment_requests: no working_dir, skipping")
        return
    marker = Path(working_dir) / _attachment_marker_name(thread_id)
    if not marker.exists() and not marker.with_name(marker.name + ".pending").exists():
        logger.debug("_send_attachment_requests: %s not found, skipping", marker)
        return
    logger.info("_send_attachment_requests: found %s", marker)
    from ..attachment_outbox import deliver_pending

    await deliver_pending(surface, marker)


# Max characters for tool result display.
# Sized to show ~30 lines of typical output (100 chars/line × 30 = 3000).
# The embed description limit is 4096, so this leaves room for code block markers.
_TOOL_RESULT_MAX_CHARS = 3000


def _truncate_result(content: str) -> str:
    """Truncate tool result content for display."""
    if len(content) <= _TOOL_RESULT_MAX_CHARS:
        return content
    return content[:_TOOL_RESULT_MAX_CHARS] + "\n... (truncated)"


def _completion_fields(event: StreamEvent, runner: object) -> tuple[tuple[str, str], ...]:
    """Build frontend-neutral completion metadata from a terminal event."""
    fields: list[tuple[str, str]] = []
    backend = _backend_name_from_runner(runner)
    model = getattr(runner, "model", None)
    fields.append(("Backend", f"{backend}{f' · {model}' if model else ''}"))
    if event.duration_ms is not None:
        fields.append(("Duration", f"{event.duration_ms / 1000:.1f}s"))
    if event.cost_usd is not None:
        fields.append(("Cost", f"${event.cost_usd:.4f}"))
    if event.input_tokens is not None and event.output_tokens is not None:
        fields.append(("Tokens", f"{event.input_tokens} in · {event.output_tokens} out"))
    return tuple(fields)


_TIMEOUT_PATTERN = re.compile(r"Timed out after (\d+) seconds")


def _error_notice(error: str) -> Notice:
    """Keep timeout guidance semantic so every surface can render it natively."""
    match = _TIMEOUT_PATTERN.search(error)
    if match:
        seconds = match.group(1)
        return Notice(
            level=NoticeLevel.ERROR,
            title="Session timed out",
            body=(
                f"No response received for {seconds} seconds. "
                "Send a message to resume the session or use /clear to start fresh."
            ),
        )
    return Notice(level=NoticeLevel.ERROR, title="Error", body=error)


class EventProcessor:
    """Processes stream-json events and dispatches Discord actions.

    One instance per Claude Code session run. Call process(event) for each
    event from the runner; call finalize() in a finally block to clean up.

    State machine:
    - session_start_sent: prevents duplicate session start embeds
    - assistant_text_sent: prevents duplicate result text posts
    - pending_ask: set when AskUserQuestion detected; caller should drain runner

    Example usage (see _run_helper.run_claude_with_config for the full flow)::

        processor = EventProcessor(config)
        try:
            async for event in config.runner.run(prompt):
                if processor.should_drain:
                    continue
                await processor.process(event)
        finally:
            await processor.finalize()

        if processor.pending_ask and processor.session_id:
            # Handle AskUserQuestion (see run_helper)
            ...

        return processor.session_id
    """

    def __init__(self, config: RunConfig) -> None:
        self._config = config
        self._state = SessionState(
            session_id=config.session_id,
            thread_id=config.surface.thread_key,
        )
        self._streamer = config.surface.open_stream()
        self._interrupt = None

        # Guards against duplicate embeds/messages in the same run.
        self._session_start_sent: bool = False
        self._assistant_text_sent: bool = False

        # Outstanding approval/input prompts. Held so the garbage collector
        # cannot drop a task that the CLI is waiting on.
        self._prompt_tasks: set[asyncio.Task[None]] = set()

        # Set when AskUserQuestion is detected. Caller should drain the runner
        # (skip events) then handle the ask after the stream ends.
        self._pending_ask: list[AskQuestion] | None = None

        # Set when a ScheduleWakeup tool call is detected (harness-style /loop
        # self-pacing). The caller registers a one-shot scheduled task after
        # the session ends so the loop survives in claude -p mode.
        self._pending_wakeup: dict | None = None

        # Set when compact_boundary fires (and post_compact_rerun is False).
        # Triggers interrupt → rerun-with-guardrail in _run_helper.
        self._compact_occurred: bool = False

        # Per-turn usage from the last assistant message. The RESULT message
        # reports cumulative token counts across all API calls, which inflates
        # context utilization (each turn re-sends the full conversation).
        # We track the last turn's values and prefer them over RESULT's.
        self._last_turn_input_tokens: int | None = None
        self._last_turn_output_tokens: int | None = None
        self._last_turn_cache_read_tokens: int | None = None
        self._last_turn_cache_creation_tokens: int | None = None

        # The session's final assistant reply text, captured when the
        # session_complete event is handled. Surfaced via final_assistant_text
        # so an external caller (e.g. /api/ingest) can retrieve the answer.
        self._final_assistant_text: str = ""

        # Set when the terminal RESULT event carries an in-stream error (rather
        # than raising). Surfaced via final_error so result_sink consumers can
        # distinguish a real failure from an empty answer.
        self._final_error: str | None = None

    # ------------------------------------------------------------------
    # Public properties
    # ------------------------------------------------------------------

    @property
    def final_assistant_text(self) -> str:
        """The session's final assistant reply text (empty until completion)."""
        return self._final_assistant_text

    @property
    def final_error(self) -> str | None:
        """In-stream error from the terminal RESULT event, or None on success."""
        return self._final_error

    @property
    def session_id(self) -> str | None:
        """The current session ID, updated as SYSTEM events arrive."""
        return self._state.session_id

    @property
    def pending_ask(self) -> list[AskQuestion] | None:
        """Set when AskUserQuestion was detected. None otherwise."""
        return self._pending_ask

    @property
    def pending_wakeup(self) -> dict | None:
        """ScheduleWakeup tool input ({delaySeconds, prompt, reason}) if called."""
        return self._pending_wakeup

    @property
    def compact_occurred(self) -> bool:
        """True if compact_boundary was detected and runner was interrupted."""
        return self._compact_occurred

    @property
    def should_drain(self) -> bool:
        """True while the runner should be drained (AskUserQuestion or compact detected)."""
        return self._pending_ask is not None or self._compact_occurred

    @property
    def assistant_text_sent(self) -> bool:
        """True if assistant text was already streamed to Discord."""
        return self._assistant_text_sent

    # ------------------------------------------------------------------
    # Public methods
    # ------------------------------------------------------------------

    async def process(self, event: StreamEvent) -> None:
        """Dispatch a single stream event to the appropriate handler."""
        if event.message_type == MessageType.SYSTEM:
            await self._on_system(event)
        elif event.message_type == MessageType.ASSISTANT:
            await self._on_assistant(event)
        elif event.message_type == MessageType.USER:
            await self._on_tool_result(event)
        elif event.message_type == MessageType.PROGRESS:
            await self._on_progress(event)

        elif event.message_type == MessageType.RATE_LIMIT_EVENT:
            await self._on_rate_limit_event(event)
        elif event.message_type == MessageType.STREAM_EVENT:
            await self._on_stream_event(event)

        if event.is_complete:
            await self._on_complete(event)

    async def wait_for_prompts(self) -> None:
        """Await every outstanding approval or input prompt.

        Deliberately *not* called from :meth:`finalize`. When the stream ends
        with a prompt still open, the CLI has usually already given up on it,
        and blocking teardown for the rest of a five-minute elicitation timeout
        would keep the session's status pinned and the thread busy. A caller
        that genuinely needs the answers — a test, or a shutdown path that must
        not lose them — asks for them here.
        """
        while self._prompt_tasks:
            await asyncio.gather(*tuple(self._prompt_tasks), return_exceptions=True)

    async def cancel_prompts(self) -> None:
        """Abandon every outstanding prompt without answering it.

        The counterpart to :meth:`wait_for_prompts`, for a caller tearing the
        session down for good — a bot shutting down, or a session being
        replaced. Nothing is injected: the runner these answers were bound for
        is going away too.
        """
        tasks = tuple(self._prompt_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def finalize(self) -> None:
        """Disable the interrupt affordance and cancel unfinished activities.

        Prompt tasks are left running on purpose: each owns a timeout that
        fails closed, and an approval the user is mid-way through answering
        should still reach the CLI.
        """
        if self._interrupt is not None:
            with contextlib.suppress(Exception):
                await self._interrupt.disable()
            self._interrupt = None
        for activity in self._state.active_tools.values():
            with contextlib.suppress(Exception):
                await activity.cancel()
        self._state.active_tools.clear()
        for task in self._state.active_timers.values():
            if not task.done():
                task.cancel()
        self._state.active_timers.clear()

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    @property
    def _chat_only(self) -> bool:
        """Shorthand for the chat_only flag on the config."""
        return self._config.chat_only

    async def _on_system(self, event: StreamEvent) -> None:
        """Handle SYSTEM events — capture session_id, post start embed, compact notification."""
        # Context compaction notification (skip display in chat_only mode)
        if event.is_compact:
            await self._config.surface.set_status(StatusKind.COMPACTING)
            if not self._chat_only:
                pre = event.compact_pre_tokens
                trigger = event.compact_trigger or "auto"
                label = f"\U0001f5dc\ufe0f Context compacted ({trigger})"
                if pre:
                    label += f" \u2014 was {pre:,} tokens"
                await self._config.surface.send_notice(Notice(level=NoticeLevel.SUBTLE, body=label))

            # Interrupt the runner so _run_helper can rerun with a guardrail.
            # Skip when post_compact_rerun=True (guardrail already active; avoid loops).
            if not self._config.post_compact_rerun:
                self._compact_occurred = True
                await self._config.runner.interrupt()

        # Permission request — show Allow/Deny buttons (always shown, even in chat_only)
        if event.permission_request is not None:
            await self._handle_permission_request(event)
            return

        # MCP elicitation — show form or URL button (always shown, even in chat_only)
        if event.elicitation is not None:
            await self._handle_elicitation(event)
            return

        # Hook lifecycle events (--include-hook-events)
        if event.hook_event is not None:
            await self._handle_hook_lifecycle(event)
            return

        # Out-of-band notice carried by a SYSTEM event that has no session_id —
        # currently the anonymization gateway reporting that it sent anyway.
        # Without this branch the session_id guard below swallows the text, and
        # a warning nobody sees is worse than no warning at all.
        if event.text and not event.session_id:
            await self._config.surface.send_notice(
                Notice(level=NoticeLevel.WARNING, body=event.text)
            )
            return

        if not event.session_id:
            return

        self._state.session_id = event.session_id
        if self._config.repo:
            wd = getattr(self._config.runner, "working_dir", None)
            # For new sessions, save the prompt as the summary so /resume can display it.
            # For resumed sessions (config.session_id is set), pass no summary to keep
            # the existing one via COALESCE in the SQL query.
            backend = _backend_name_from_runner(self._config.runner)
            if self._config.session_id:
                await self._config.repo.save(
                    self._config.surface.thread_key,
                    self._state.session_id,
                    working_dir=wd,
                    origin=self._config.session_origin,
                    backend=backend,
                )
            else:
                summary = self._config.prompt[:100] if self._config.prompt else None
                await self._config.repo.save(
                    self._config.surface.thread_key,
                    self._state.session_id,
                    working_dir=wd,
                    origin=self._config.session_origin,
                    summary=summary,
                    backend=backend,
                )

        # Guard: post session_start_embed only once (Claude can emit multiple SYSTEM events).
        # Skip in chat_only mode — no session start embed.
        if not self._chat_only and not self._config.session_id and not self._session_start_sent:
            backend = _backend_name_from_runner(self._config.runner)
            fields = tuple(
                (name, value)
                for name, value in (
                    ("Session", self._state.session_id),
                    ("Backend", backend),
                    ("Model", self._config.runner.model),
                )
                if value
            )
            await self._config.surface.send_notice(
                Notice(level=NoticeLevel.INFO, title="Session started", fields=fields)
            )
            self._session_start_sent = True

    async def _on_assistant(self, event: StreamEvent) -> None:
        """Handle ASSISTANT events — thinking, streaming text, tool use."""
        # Extended thinking — only post on complete events (not partials).
        # Skip in chat_only mode.
        if event.thinking and not event.is_partial and not self._chat_only:
            await self._config.surface.send_notice(
                Notice(
                    level=NoticeLevel.SUBTLE,
                    title="Thinking",
                    body=event.thinking,
                    monospace_body=True,
                )
            )

        # Redacted thinking — only post on complete events. Skip in chat_only mode.
        if event.has_redacted_thinking and not event.is_partial and not self._chat_only:
            await self._config.surface.send_notice(
                Notice(
                    level=NoticeLevel.SUBTLE,
                    title="Thinking (redacted)",
                    body="Some reasoning was performed but cannot be shown.",
                )
            )

        # Text streaming — compute delta from last partial, edit in place.
        # Always shown — this IS the chat content.
        if event.text:
            await self._handle_text(event)

        # ScheduleWakeup — capture the request so the caller can register a
        # one-shot scheduled task after the session ends (last call wins).
        if event.tool_use and event.tool_use.tool_name == "ScheduleWakeup":
            self._pending_wakeup = dict(event.tool_use.tool_input)

        # Remember files this session writes so collisions with other live
        # sessions can be detected from behaviour, not from announcements.
        if event.tool_use is not None:
            self._record_file_activity(event.tool_use)

        # Tool use — post embed and start live timer. Skip in chat_only mode.
        if event.tool_use:
            if self._chat_only:
                # Still track tool use count and update status, but don't post embeds.
                self._state.tool_use_count += 1
                await self._config.surface.set_status(StatusKind.for_tool(event.tool_use.category))
            else:
                await self._handle_tool_use(event)

        # TodoWrite — post or edit the live todo progress embed. Skip in chat_only mode.
        if event.todo_list is not None and not self._chat_only:
            await self._handle_todo_write(event)

        # ExitPlanMode — show plan embed with Approve/Cancel buttons.
        # Skip in chat_only mode.
        if event.is_plan_approval and not event.is_partial and not self._chat_only:
            await self._handle_plan_approval(event)

        # Track per-turn usage from assistant messages for accurate context stats.
        self._update_last_turn_usage(event)

        # AskUserQuestion — set pending and signal caller to interrupt runner.
        # Always handled — interactive flow is needed regardless of mode.
        if event.ask_questions:
            self._pending_ask = event.ask_questions
            await self._config.runner.interrupt()

    def _update_last_turn_usage(self, event: StreamEvent) -> None:
        """Record the latest known per-turn usage for accurate context stats.

        Called from both ASSISTANT and STREAM_EVENT events. An ``assistant``
        message's own usage is a mid-generation snapshot — for a still-
        streaming block it can under-report output_tokens by an order of
        magnitude. The authoritative final usage for that same turn arrives
        moments later in a ``message_delta`` stream_event (parsed into a
        STREAM_EVENT-typed StreamEvent by claude_code_core.parser), which
        this method also updates from — overwriting the assistant snapshot
        with the true total, since it always arrives after it for the same
        turn and before the next turn's events.
        """
        if event.input_tokens is not None:
            self._last_turn_input_tokens = event.input_tokens
            self._last_turn_output_tokens = event.output_tokens
            self._last_turn_cache_read_tokens = event.cache_read_tokens
            self._last_turn_cache_creation_tokens = event.cache_creation_tokens

    async def _on_stream_event(self, event: StreamEvent) -> None:
        """Handle low-level STREAM_EVENT events — currently only for usage.

        Everything else about these events (text deltas, block boundaries)
        is intentionally not acted on here; live text streaming already
        comes from the coarser ASSISTANT events.
        """
        self._update_last_turn_usage(event)

    async def _on_tool_result(self, event: StreamEvent) -> None:
        """Handle USER events (tool results) — cancel timer, update embed."""
        if not event.tool_result_id:
            return

        # In chat_only mode, no tool activities were opened — just reset status.
        if self._chat_only:
            await self._config.surface.set_status(StatusKind.THINKING)
            return

        await self._config.surface.set_status(StatusKind.THINKING)

        activity = self._state.active_tools.pop(event.tool_result_id, None)
        if activity is None:
            return
        await activity.complete(_truncate_result(event.tool_result_content or ""))

    async def _on_progress(self, event: StreamEvent) -> None:
        """Handle PROGRESS events — reset stall timer, show hook status."""
        if event.hook_event is not None:
            await self._config.surface.set_status(StatusKind.HOOK)
            return
        await self._config.surface.set_status(StatusKind.THINKING)

    async def _on_rate_limit_event(self, event: StreamEvent) -> None:
        """Handle RATE_LIMIT_EVENT — persist latest rate limit info to usage_stats."""
        if self._config.usage_repo is None or event.rate_limit_info is None:
            return
        await self._config.usage_repo.upsert(event.rate_limit_info)

    async def _on_complete(self, event: StreamEvent) -> None:
        """Handle RESULT events — finalize streaming and post a summary notice."""
        import asyncio

        # Prefer per-turn usage (last assistant message) over cumulative RESULT usage.
        # The RESULT sums tokens across ALL API calls, inflating context utilization.
        if self._last_turn_input_tokens is not None:
            event.input_tokens = self._last_turn_input_tokens
            event.output_tokens = self._last_turn_output_tokens
            event.cache_read_tokens = self._last_turn_cache_read_tokens
            event.cache_creation_tokens = self._last_turn_cache_creation_tokens

        # Finalize any in-progress streaming message.
        # Capture the URL before sending session_complete_embed (which would
        # become last_message_id and hide Claude's actual reply).
        last_assistant_url: str | None = self._state.last_assistant_url
        last_assistant_text: str = self._state.accumulated_text

        if self._streamer.has_content:
            await self._streamer.finalize()
            self._assistant_text_sent = True

        if event.error:
            # In-stream failure (e.g. an API 400/429 surfaced via the RESULT
            # error field, not a raised exception). Capture it so result_sink
            # consumers report a real error instead of an empty "done".
            self._final_error = event.error
            await self._config.surface.send_notice(_error_notice(event.error))
            await self._config.surface.set_status(StatusKind.ERROR)
        else:
            # Post final result text only if no assistant text was already sent.
            response_text = event.text
            if response_text and not self._assistant_text_sent:
                await self._config.surface.send_text(response_text)
                last_assistant_text = response_text

            # Capture the final reply so result_sink consumers (e.g. /api/ingest)
            # can retrieve it after the session ends.
            self._final_assistant_text = last_assistant_text

            # Send files listed in the .ccdb-attachments-{thread_id} marker file.
            await _send_attachment_requests(
                self._config.surface,
                self._config.runner.working_dir,
                self._config.surface.thread_key,
            )

            # In chat_only mode, skip session_complete embed, statusline, and inbox.
            # Just set the done status emoji.
            if self._chat_only:
                await self._config.surface.set_status(StatusKind.DONE)
            else:
                fields = _completion_fields(event, self._config.runner)
                await self._config.surface.send_notice(
                    Notice(level=NoticeLevel.SUCCESS, title="Done", fields=fields)
                )
                await self._config.surface.set_status(StatusKind.DONE)

                # Post the per-turn engine status footer.
                #
                # - Claude statusLine (read from ~/.claude/settings.json)
                #   surfaces Anthropic quota windows (5h, 7d).
                # - Codex status line surfaces Codex usage via `codex
                #   app-server` (account/rateLimits/read).
                #
                # When the 2-layer Codex toggle (status.codex) is enabled both
                # are shown after every turn, so the user can compare usage and
                # decide which engine to use. The Codex probe only runs for
                # interactive chat (backend_settings is wired) — headless flows
                # leave it None and incur no extra subprocess.
                api_label_raw = self._config.runner.describe_api()
                api_label = api_label_raw if isinstance(api_label_raw, str) else None
                if self._config.thread is not None and (
                    self._config.backend_settings is not None or api_label is not None
                ):
                    asyncio.create_task(
                        _post_engine_status_footer(
                            thread=self._config.thread,
                            backend=_backend_name_from_runner(self._config.runner),
                            working_dir=self._config.runner.working_dir,
                            model=self._config.runner.model,
                            context_window=event.context_window,
                            input_tokens=event.input_tokens,
                            cache_creation_tokens=event.cache_creation_tokens,
                            cache_read_tokens=event.cache_read_tokens,
                            api_label=api_label,
                            backend_settings=self._config.backend_settings,
                            codex_command=self._config.codex_command,
                            thread_id=self._config.surface.thread_key,
                        ),
                        name=f"statusline-{self._config.surface.thread_key}",
                    )

                # Schedule inbox classification as a background task (non-blocking).
                # Only runs when inbox_repo is wired in (THREAD_INBOX_ENABLED=true).
                if self._config.inbox_repo is not None and last_assistant_text:
                    asyncio.create_task(
                        _classify_and_update_inbox(
                            thread_id=self._config.surface.thread_key,
                            last_text=last_assistant_text,
                            last_message_url=last_assistant_url,
                            inbox_repo=self._config.inbox_repo,
                            dashboard=self._config.inbox_dashboard,
                            claude_command=self._config.claude_command,
                        ),
                        name=f"inbox-classify-{self._config.surface.thread_key}",
                    )

        if event.session_id:
            if self._config.repo:
                await self._config.repo.save(
                    self._config.surface.thread_key,
                    event.session_id,
                    origin=self._config.session_origin,
                    backend=_backend_name_from_runner(self._config.runner),
                )
            self._state.session_id = event.session_id

        # Persist context window stats (requires repo + context_window in event).
        if self._config.repo and event.context_window is not None:
            context_used = (
                (event.input_tokens or 0)
                + (event.cache_read_tokens or 0)
                + (event.cache_creation_tokens or 0)
            )
            await self._config.repo.update_context_stats(
                thread_id=self._config.surface.thread_key,
                context_window=event.context_window,
                context_used=context_used,
            )

        # Reset for potential next streamer
        self._streamer = self._config.surface.open_stream()

    # ------------------------------------------------------------------
    # Text streaming helpers
    # ------------------------------------------------------------------

    async def _handle_text(self, event: StreamEvent) -> None:
        """Stream text through the selected surface, computing partial deltas."""
        assert event.text is not None

        if event.is_partial:
            delta = event.text[len(self._state.partial_text) :]
            self._state.partial_text = event.text
            if delta:
                await self._streamer.append(delta)
        else:
            # Complete text block: flush the streamer with any remaining delta.
            delta = event.text[len(self._state.partial_text) :]
            if self._streamer.has_content:
                if delta:
                    await self._streamer.append(delta)
                await self._streamer.finalize()
                self._streamer = self._config.surface.open_stream()
            else:
                # No streamer content — post the full text block directly.
                # (Current CLI delivers each text block complete, so this is the
                # normal path; capture the last message URL for inbox linking.)
                try:
                    message_id = await self._config.surface.send_text(event.text)
                    if message_id is None:
                        return
                except Exception:
                    logger.warning("Failed to send assistant text", exc_info=True)
                    return
            self._state.partial_text = ""
            self._state.accumulated_text = event.text
            self._assistant_text_sent = True
            await self._bump_stop()

    def _record_file_activity(self, tool_use: ToolUseEvent) -> None:
        """Note a file write for cross-session collision detection (no-op when unwired)."""
        tracker = self._config.file_activity
        if tracker is None:
            return
        path = extract_written_path(tool_use.tool_name, tool_use.tool_input)
        if path is not None:
            tracker.record(self._config.surface.thread_key, path, time.monotonic())

    async def _handle_tool_use(self, event: StreamEvent) -> None:
        """Open a frontend-native activity for a tool call."""
        assert event.tool_use is not None

        # Finalize any in-progress streaming text before the tool embed.
        if self._streamer.has_content:
            await self._streamer.finalize()
            self._streamer = self._config.surface.open_stream()
        self._state.partial_text = ""

        self._state.tool_use_count += 1

        await self._config.surface.set_status(StatusKind.for_tool(event.tool_use.category))

        try:
            activity = await self._config.surface.open_activity(
                ActivitySpec(
                    kind="tool",
                    title=event.tool_use.display_name,
                    category=event.tool_use.category,
                )
            )
        except Exception:
            logger.debug("Failed to open tool activity", exc_info=True)
            return
        self._state.active_tools[event.tool_use.tool_id] = activity

        await self._bump_stop()

    async def _handle_plan_approval(self, event: StreamEvent) -> None:
        """Ask whether the finished plan may be executed (ExitPlanMode)."""
        # ExitPlanMode does not carry a request_id in the current CLI protocol;
        # we use the session_id as a stable identifier for the inject payload.
        request_id = self._state.session_id or "plan"
        prompt = plan_prompt(event.text or "", notify=self._notify_mention())
        self._ask_in_background(
            self._ask_choice(prompt, request_id, plan_result),
            description=f"plan approval (session={request_id})",
            request_id=request_id,
            refusal=plan_result(None),
        )

    async def _handle_permission_request(self, event: StreamEvent) -> None:
        """Ask whether a tool may run.

        When dangerously_skip_permissions is enabled on the runner, auto-approve
        the request immediately instead of asking.  This works around a CLI
        regression (v2.1.78+, upstream #35895) where --dangerously-skip-permissions
        fails to bypass the file-level sensitive-path check on Edit/Write tools,
        causing permission_request events to be emitted even in yolo mode.
        """
        assert event.permission_request is not None
        request = event.permission_request

        # Workaround for CLI v2.1.78+ regression (#35895):
        # Auto-approve when yolo mode is active — the user already opted into
        # unrestricted execution by setting dangerously_skip_permissions=true.
        if self._config.runner.dangerously_skip_permissions:
            await self._config.runner.inject_tool_result(request.request_id, {"approved": True})
            logger.info(
                "Permission auto-approved (yolo mode): %s (request_id=%s)",
                request.tool_name,
                request.request_id,
            )
            return

        prompt = permission_prompt(request, notify=self._notify_mention())
        self._ask_in_background(
            self._ask_choice(prompt, request.request_id, permission_result),
            description=f"permission for {request.tool_name}",
            request_id=request.request_id,
            refusal=permission_result(None),
        )

    async def _handle_elicitation(self, event: StreamEvent) -> None:
        """Collect input an MCP server asked for, by URL confirmation or form."""
        assert event.elicitation is not None
        req = event.elicitation
        if req.mode == "url-mode":
            coro = self._ask_url_elicitation(req)
            refusal = elicitation_url_result(None)
        else:
            coro = self._ask_form_elicitation(req)
            refusal = elicitation_form_result(None)
        self._ask_in_background(
            coro,
            description=f"elicitation from {req.server_name} ({req.mode})",
            request_id=req.request_id,
            refusal=refusal,
        )

    # -- prompt plumbing ------------------------------------------------

    def _notify_mention(self) -> Mention | None:
        """Who to ping, in the protocol's terms rather than Discord's."""
        user_id = self._config.notify_user_id
        return Mention(external_user_id=str(user_id)) if user_id else None

    async def _ask_choice(
        self,
        prompt: ChoicePrompt,
        request_id: str,
        to_payload: Callable[[tuple[str, ...] | None], dict],
    ) -> None:
        answer = await self._config.surface.prompt_choice(prompt)
        await self._config.runner.inject_tool_result(request_id, to_payload(answer))

    async def _ask_url_elicitation(self, request: ElicitationRequest) -> None:
        """Deliver the link, then ask whether the flow finished.

        Two messages where Discord used to post one: the link is a `prompt_url`
        because a surface that can render a link button should, and the
        confirmation is a separate question because that is what unblocks the
        CLI. Splitting them is what lets a surface with no button support still
        show a usable URL.
        """
        if request.url:
            await self._config.surface.prompt_url(
                "Open link", request.url, notify=self._notify_mention()
            )
        answer = await self._config.surface.prompt_choice(
            elicitation_url_prompt(request, notify=self._notify_mention())
        )
        await self._config.runner.inject_tool_result(
            request.request_id, elicitation_url_result(answer)
        )

    async def _ask_form_elicitation(self, request: ElicitationRequest) -> None:
        values = await self._config.surface.prompt_form(
            elicitation_form_prompt(request, notify=self._notify_mention())
        )
        await self._config.runner.inject_tool_result(
            request.request_id, elicitation_form_result(values)
        )

    def _ask_in_background(
        self,
        coro: Coroutine[object, object, None],
        *,
        description: str,
        request_id: str,
        refusal: dict,
    ) -> None:
        """Run a prompt without stalling the event reader.

        The CLI is blocked until the answer is injected, but this loop is not:
        an approval can legitimately take minutes, and awaiting it inline would
        hold up every later event behind it. The prompt owns its own timeout, so
        nothing here runs unbounded.

        ``refusal`` is what gets injected if the prompt itself fails. A surface
        that cannot ask must not leave the session waiting forever, and a
        request nobody could answer is a refused one.
        """
        task = asyncio.create_task(
            self._ask_guarded(coro, description=description, request_id=request_id, refusal=refusal)
        )
        self._prompt_tasks.add(task)
        task.add_done_callback(self._prompt_tasks.discard)
        logger.info("Prompt posted: %s (request_id=%s)", description, request_id)

    async def _ask_guarded(
        self,
        coro: Coroutine[object, object, None],
        *,
        description: str,
        request_id: str,
        refusal: dict,
    ) -> None:
        try:
            await coro
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Prompt failed: %s — refusing", description, exc_info=True)
            with contextlib.suppress(Exception):
                await self._config.runner.inject_tool_result(request_id, refusal)

    async def _handle_todo_write(self, event: StreamEvent) -> None:
        """Replace the current frontend-native todo activity."""
        assert event.todo_list is not None
        if self._state.todo_message is not None:
            with contextlib.suppress(Exception):
                await self._state.todo_message.cancel()
            self._state.todo_message = None

        markers = {"pending": "☐", "in_progress": "◉", "completed": "☑"}
        detail = "\n".join(
            f"{markers.get(item.status, '•')} {item.content}" for item in event.todo_list
        )
        try:
            self._state.todo_message = await self._config.surface.open_activity(
                ActivitySpec(kind="todo", title="Tasks", detail=detail)
            )
        except Exception:
            logger.warning("Failed to post todo activity; will retry", exc_info=True)

    async def _handle_hook_lifecycle(self, event: StreamEvent) -> None:
        """Show hook events as Discord subtext or status updates.

        Event shapes (from --include-hook-events):
          hook_started  — set 🪝 status
          hook_response — show stderr output if non-empty
          hook_progress — set 🪝 status (async hooks)
          hook_execution_start/complete — legacy batch events
        """
        assert event.hook_event is not None
        he = event.hook_event

        if he.lifecycle in ("started", "progress"):
            await self._config.surface.set_status(StatusKind.HOOK)
            return

        if he.lifecycle == "response":
            stderr = he.stderr.strip()
            if stderr and not self._chat_only:
                lines = stderr.splitlines()
                truncated = lines[:6]
                text = "\n".join(truncated)
                if len(lines) > 6:
                    text += f"\n... (+{len(lines) - 6} lines)"
                await self._config.surface.send_notice(
                    Notice(
                        level=NoticeLevel.SUBTLE,
                        body=text,
                        monospace_body=True,
                    )
                )
            return

        # Legacy batch events
        if self._chat_only:
            if he.lifecycle == "start":
                await self._config.surface.set_status(StatusKind.HOOK)
            return

        if he.lifecycle == "start":
            label = f"\U0001fa9d {he.hook_event_name} hooks running ({he.num_hooks})"
            await self._config.surface.set_status(StatusKind.HOOK)
        elif he.lifecycle == "complete":
            dur = he.duration_ms
            label = f"\U0001fa9d {he.hook_event_name} hooks completed"
            if dur:
                label += f" ({dur}ms)"
            await self._config.surface.set_status(StatusKind.THINKING)
        else:
            return

        await self._config.surface.send_notice(Notice(level=NoticeLevel.SUBTLE, body=label))

    async def _bump_stop(self) -> None:
        """Move the surface's interrupt affordance back into view."""
        if self._interrupt is None:
            self._interrupt = await self._config.surface.offer_interrupt(
                self._config.runner.interrupt
            )
        await self._interrupt.bump()


# ---------------------------------------------------------------------------
# Statusline footer helper (module-level so it can be unit-tested)
# ---------------------------------------------------------------------------


async def _render_claude_statusline_text(
    working_dir: str | None,
    model: str,
    context_window: int | None,
    input_tokens: int | None,
    cache_creation_tokens: int | None,
    cache_read_tokens: int | None,
) -> str | None:
    """Render the configured Claude statusLine to Discord-ready text (or None).

    Reads ``statusLine.command`` from ``~/.claude/settings.json`` and executes
    it with the current session state. Returns the (max 3) non-empty output
    lines joined, or ``None`` when no command is configured / it produced
    nothing.
    """
    import os

    from ..discord_ui.statusline import (
        build_statusline_json,
        read_statusline_command,
        render_statusline,
    )

    command = read_statusline_command()
    if not command:
        return None
    cwd = working_dir or os.path.expanduser("~")
    json_input = build_statusline_json(
        cwd=cwd,
        model_id=model,
        model_display_name=model,
        context_size=context_window or 200000,
        input_tokens=input_tokens or 0,
        cache_creation_tokens=cache_creation_tokens or 0,
        cache_read_tokens=cache_read_tokens or 0,
    )
    result = await render_statusline(command, json_input)
    if not result:
        return None
    lines = [line for line in result.splitlines() if line.strip()]
    return "\n".join(lines[:3]) if lines else None


async def _post_statusline_footer(
    thread: object,
    working_dir: str | None,
    model: str,
    context_window: int | None,
    input_tokens: int | None,
    cache_creation_tokens: int | None,
    cache_read_tokens: int | None,
    api_label: str | None = None,
) -> None:
    """Post the current API provider line and the configured statusLine.

    ``api_label`` (e.g. ``"Anthropic API (direct)"``) is always shown when
    provided, so "which API am I using right now" stays visible after every
    session — even when no ``statusLine`` is configured. The statusLine output
    (read from ``~/.claude/settings.json``) is appended below it when present.
    Posts nothing when neither is available.
    """
    statusline_text = await _render_claude_statusline_text(
        working_dir,
        model,
        context_window,
        input_tokens,
        cache_creation_tokens,
        cache_read_tokens,
    )

    parts: list[str] = []
    if api_label:
        parts.append(f"\U0001f517 API: {api_label}")
    if statusline_text:
        parts.append(statusline_text)

    if not parts:
        return

    body = "\n".join(parts)
    with contextlib.suppress(Exception):
        await thread.send(f"```\n{body}\n```")  # type: ignore[union-attr]


async def _post_engine_status_footer(
    thread: object,
    *,
    backend: str,
    working_dir: str | None,
    model: str,
    context_window: int | None,
    input_tokens: int | None,
    cache_creation_tokens: int | None,
    cache_read_tokens: int | None,
    api_label: str | None,
    backend_settings: object | None,
    codex_command: str,
    thread_id: int | None,
) -> None:
    """Post the per-turn engine status footer (Claude statusLine + Codex line).

    The Codex status line is gated by the 2-layer ``status.codex`` toggle:
      - ``off``  → never shown
      - ``auto`` → shown only when it can be fetched (codex installed +
        logged in); silently hidden otherwise
      - ``on``   → always attempted; a short hint is shown when it fails

    The Claude statusLine renders on Claude turns as before. It is *also*
    rendered on Codex turns when the Codex status line is active, so the
    Anthropic quota stays visible for side-by-side comparison.
    """
    from ..backend_settings import BackendSettings
    from ..discord_ui.engine_status import get_codex_status_line

    # Resolve the Codex-status mode (off when no settings resolver is wired,
    # e.g. headless flows).
    mode = "off"
    if isinstance(backend_settings, BackendSettings):
        mode = await backend_settings.codex_status_mode(thread_id)
    show_codex = mode in ("auto", "on")

    # Codex line (account/rateLimits/read via codex app-server), cached.
    codex_line: str | None = None
    if show_codex:
        codex_line = await get_codex_status_line(codex_command)
        if codex_line is None and mode == "on":
            codex_line = "\U0001f916 Codex: 残量取得失敗（codex login 済みか確認）"

    # Render the Claude statusLine on Claude turns, or whenever the Codex line
    # is being shown (so both engines appear together). On non-Claude turns the
    # session model id is not a Claude model, so suppress the model label.
    render_claude_sl = backend == "claude" or codex_line is not None
    statusline_text: str | None = None
    if render_claude_sl:
        statusline_text = await _render_claude_statusline_text(
            working_dir,
            model if backend == "claude" else "",
            context_window,
            input_tokens,
            cache_creation_tokens,
            cache_read_tokens,
        )

    parts: list[str] = []
    if api_label and backend == "claude":
        parts.append(f"\U0001f517 API: {api_label}")
    if statusline_text:
        parts.append(statusline_text)
    if codex_line:
        parts.append(codex_line)

    if not parts:
        return

    body = "\n".join(parts)
    with contextlib.suppress(Exception):
        await thread.send(f"```\n{body}\n```")  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Inbox classification helper (module-level so it can be unit-tested)
# ---------------------------------------------------------------------------


async def _classify_and_update_inbox(
    thread_id: int,
    last_text: str,
    last_message_url: str | None,
    inbox_repo: object,
    dashboard: object | None,
    claude_command: str,
) -> None:
    """Classify the session's final message and persist the inbox entry.

    Runs as a background asyncio task so it does not block the main flow.
    Type annotations use ``object`` to avoid a circular import; the actual
    types are ThreadInboxRepository and ThreadStatusDashboard.
    """
    from ..database.inbox_repo import ThreadInboxRepository
    from ..discord_ui.inbox_classifier import classify
    from ..discord_ui.thread_dashboard import ThreadStatusDashboard

    assert isinstance(inbox_repo, ThreadInboxRepository)

    try:
        result = await classify(last_text, claude_command=claude_command)
        logger.debug("inbox classify thread_id=%d result=%s", thread_id, result)

        if result == "done":
            # Proactively remove from inbox — no action needed
            await inbox_repo.remove(thread_id)
        else:
            confidence = "high" if result == "waiting" else "low"
            await inbox_repo.upsert(
                thread_id=thread_id,
                status=result,  # type: ignore[arg-type]
                confidence=confidence,
                last_message_url=last_message_url,
            )

        if isinstance(dashboard, ThreadStatusDashboard):
            await dashboard.refresh_inbox(inbox_repo)

    except Exception:
        logger.warning("inbox classify task failed for thread_id=%d", thread_id, exc_info=True)
