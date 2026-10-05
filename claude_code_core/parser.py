"""Parser for Claude Code CLI stream-json output.

Each line of stdout is a JSON object. This module parses them into StreamEvent objects.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .types import (
    TOOL_CATEGORIES,
    AskOption,
    AskQuestion,
    ContentBlockType,
    ElicitationRequest,
    HookEvent,
    MessageType,
    PermissionRequest,
    RateLimitInfo,
    StreamEvent,
    TodoItem,
    ToolCategory,
    ToolUseEvent,
)

logger = logging.getLogger(__name__)


def parse_line(line: str) -> StreamEvent | None:
    """Parse a single line of stream-json output into a StreamEvent.

    Returns None if the line is empty or unparseable.
    """
    line = line.strip()
    if not line:
        return None

    try:
        data: dict[str, Any] = json.loads(line)
    except json.JSONDecodeError:
        logger.warning("Failed to parse stream-json line: %s", line[:200])
        return None

    msg_type_str = data.get("type", "")
    try:
        msg_type = MessageType(msg_type_str)
    except ValueError:
        logger.debug("Unknown message type: %s", msg_type_str)
        return None

    event = StreamEvent(message_type=msg_type)

    if msg_type == MessageType.SYSTEM:
        _parse_system(data, event)
    elif msg_type == MessageType.ASSISTANT:
        _parse_assistant(data, event)
    elif msg_type == MessageType.USER:
        _parse_user(data, event)
    elif msg_type == MessageType.RESULT:
        _parse_result(data, event)
    elif msg_type == MessageType.PROGRESS:
        _parse_progress(data, event)
    elif msg_type == MessageType.RATE_LIMIT_EVENT:
        _parse_rate_limit_event(data, event)
    elif msg_type == MessageType.STREAM_EVENT:
        _parse_stream_event(data, event)

    return event


def _parse_system(data: dict[str, Any], event: StreamEvent) -> None:
    """Parse system message (contains session_id on init)."""
    event.session_id = data.get("session_id")
    subtype = data.get("subtype", "")
    if subtype == "init":
        logger.info("Session initialized: %s", event.session_id)
    elif subtype == "compact_boundary":
        event.is_compact = True
        metadata = data.get("compactMetadata", {})
        event.compact_trigger = metadata.get("trigger")
        event.compact_pre_tokens = metadata.get("preTokens")
        logger.info(
            "Context compaction (%s) — %s tokens before compact",
            event.compact_trigger,
            event.compact_pre_tokens,
        )
    elif subtype == "permission_request":
        event.permission_request = PermissionRequest(
            request_id=data.get("request_id", ""),
            tool_name=data.get("tool_name", ""),
            tool_input=data.get("tool_input", {}),
        )
        logger.info("Permission request: %s", data.get("tool_name"))
    elif subtype == "elicitation":
        event.elicitation = ElicitationRequest(
            request_id=data.get("request_id", ""),
            server_name=data.get("server_name", ""),
            mode=data.get("mode", "form-mode"),
            message=data.get("message", ""),
            url=data.get("url", ""),
            schema=data.get("schema", {}),
        )
        logger.info("MCP elicitation: %s (%s)", data.get("server_name"), data.get("mode"))
    elif subtype == "stop_hook_summary":
        event.stop_hook_has_output = bool(data.get("hasOutput", False))
        logger.info("Stop hook summary (hasOutput=%s)", event.stop_hook_has_output)
    elif subtype == "hook_started":
        event.hook_event = HookEvent(
            hook_event_name=data.get("hook_event", ""),
            hook_name=data.get("hook_name", ""),
            lifecycle="started",
        )
    elif subtype == "hook_response":
        event.hook_event = HookEvent(
            hook_event_name=data.get("hook_event", ""),
            hook_name=data.get("hook_name", ""),
            lifecycle="response",
            stderr=data.get("stderr", ""),
            outcome=data.get("outcome", ""),
            exit_code=data.get("exit_code"),
        )
    elif subtype == "hook_progress":
        event.hook_event = HookEvent(
            hook_event_name=data.get("hook_event", ""),
            hook_name=data.get("hook_name", ""),
            lifecycle="progress",
        )
    elif subtype in ("hook_execution_start", "hook_execution_complete"):
        lifecycle = "start" if subtype == "hook_execution_start" else "complete"
        num_hooks_str = data.get("num_hooks", "0")
        duration_str = data.get("total_duration_ms", "0")
        event.hook_event = HookEvent(
            hook_event_name=data.get("hook_event", ""),
            hook_name=data.get("hook_name", ""),
            lifecycle=lifecycle,
            num_hooks=int(num_hooks_str) if num_hooks_str.isdigit() else 0,
            duration_ms=int(duration_str) if duration_str.isdigit() else 0,
        )


def _parse_assistant(data: dict[str, Any], event: StreamEvent) -> None:
    """Parse assistant message (text blocks, tool_use blocks, and thinking blocks).

    Each ``assistant``-type message the CLI emits is a fully-assembled single
    content block (one thinking, text, or tool_use block with complete input).
    Token-level partials are delivered separately as ``stream_event`` messages,
    which ccdb intentionally ignores. We therefore treat every ``assistant``
    message we receive as a complete, actionable block.

    Historically ``is_partial`` was derived from ``stop_reason`` (null ⇒ still
    generating). Current Claude Code CLI versions no longer populate
    ``stop_reason`` on the ``assistant`` message — it moved to the trailing
    ``message_delta`` stream_event — so it is ``None`` here even for finished
    blocks. Keeping the old heuristic mis-flagged *every* block as partial,
    which silently disabled all ``not is_partial``-gated handlers in the event
    processor: extended-thinking display and — critically — plan-approval
    (ExitPlanMode). With the approval UI suppressed, the CLI blocked forever
    waiting for an answer that could never be given, so the session appeared
    stuck "running". An explicit ``stop_reason`` (e.g. "end_turn"/"tool_use"),
    when present, is still respected as a completion signal.
    """
    message = data.get("message", {})
    content = message.get("content", [])
    event.is_partial = False

    text_parts: list[str] = []
    thinking_parts: list[str] = []
    for block in content:
        block_type = block.get("type", "")

        if block_type == ContentBlockType.TEXT.value:
            text = block.get("text", "")
            if text:
                text_parts.append(text)

        elif block_type == ContentBlockType.TOOL_USE.value:
            tool_name = block.get("name", "unknown")
            category = TOOL_CATEGORIES.get(tool_name, ToolCategory.OTHER)
            tool_input = block.get("input", {})
            event.tool_use = ToolUseEvent(
                tool_id=block.get("id", ""),
                tool_name=tool_name,
                tool_input=tool_input,
                category=category,
            )
            if tool_name == "AskUserQuestion":
                event.ask_questions = _parse_ask_questions(tool_input)
            elif tool_name == "TodoWrite":
                event.todo_list = _parse_todo_items(tool_input)
            elif tool_name == "ExitPlanMode":
                event.is_plan_approval = True

        elif block_type == ContentBlockType.THINKING.value:
            thinking_text = block.get("thinking", "")
            if thinking_text:
                thinking_parts.append(thinking_text)

        elif block_type == "redacted_thinking":
            event.has_redacted_thinking = True

    if text_parts:
        event.text = "\n".join(text_parts)
    if thinking_parts:
        event.thinking = "\n".join(thinking_parts)

    # Extract per-turn usage from the assistant message.
    # Unlike the cumulative usage in RESULT, this reflects the actual token
    # counts for this single API call — essential for accurate context tracking.
    usage = message.get("usage", {})
    if usage:
        event.input_tokens = usage.get("input_tokens")
        event.output_tokens = usage.get("output_tokens")
        event.cache_read_tokens = usage.get("cache_read_input_tokens")
        event.cache_creation_tokens = usage.get("cache_creation_input_tokens")


def _parse_user(data: dict[str, Any], event: StreamEvent) -> None:
    """Parse user message (tool_result blocks with content)."""
    message = data.get("message", {})
    content = message.get("content", [])

    for block in content:
        if not isinstance(block, dict):
            continue
        if block.get("type") == ContentBlockType.TOOL_RESULT.value:
            event.tool_result_id = block.get("tool_use_id", "")
            # Extract tool result content
            result_content = block.get("content", "")
            if isinstance(result_content, str) and result_content:
                event.tool_result_content = result_content
            elif isinstance(result_content, list):
                # Content can be a list of blocks (e.g. [{type: "text", text: "..."}])
                text_parts = []
                for part in result_content:
                    if isinstance(part, dict) and part.get("type") == "text":
                        text_parts.append(part.get("text", ""))
                if text_parts:
                    event.tool_result_content = "\n".join(text_parts)
            break


def _parse_result(data: dict[str, Any], event: StreamEvent) -> None:
    """Parse result message (session complete)."""
    event.is_complete = True
    event.session_id = data.get("session_id")
    event.cost_usd = data.get("cost_usd")
    event.duration_ms = data.get("duration_ms")

    usage = data.get("usage", {})
    if usage:
        event.input_tokens = usage.get("input_tokens")
        event.output_tokens = usage.get("output_tokens")
        event.cache_read_tokens = usage.get("cache_read_input_tokens")
        event.cache_creation_tokens = usage.get("cache_creation_input_tokens")

    # Extract context window size from modelUsage (any model key).
    model_usage = data.get("modelUsage", {})
    for model_info in model_usage.values():
        if isinstance(model_info, dict) and "contextWindow" in model_info:
            event.context_window = model_info["contextWindow"]
            break

    # Final text from result
    result_text = data.get("result", "")
    if result_text:
        event.text = result_text

    # Check for errors.
    # Two error shapes from the CLI:
    #   {"type":"result","subtype":"error","error":"..."} — explicit error subtype
    #   {"type":"result","subtype":"success","is_error":true,"result":"API Error: ..."} — API-level
    #     error reported as a "successful" result with is_error flag (e.g. 400 from Anthropic API)
    #   {"type":"result","subtype":"error_during_execution","is_error":true,
    #    "result":"","errors":["No conversation found with session ID: ..."]} — the CLI
    #     failed before producing any text (e.g. --resume with a session ID that does
    #     not exist).  There is no `result` text, so the errors[] array is the only
    #     human-readable signal.  Without this branch the run ends silently and the
    #     user sees no reply at all.
    subtype = data.get("subtype", "")
    errors_list = [str(e) for e in data.get("errors", []) if e]
    if subtype == "error":
        event.error = data.get("error", "Unknown error")
    elif subtype.startswith("error") and not result_text:
        event.error = "\n".join(errors_list) if errors_list else f"CLI reported {subtype}"
        event.text = ""
    elif data.get("is_error") and result_text:
        # API-level error (e.g. "API Error: 400 ...") surfaced via is_error flag.
        # Promote it to event.error so the handler shows an error display,
        # not a normal session-complete display.
        event.error = result_text
        event.text = ""  # suppress duplicate display via result text path


def _parse_progress(data: dict[str, Any], event: StreamEvent) -> None:
    """Parse progress message — extract hook_progress data if present."""
    progress_data = data.get("data", {})
    if not isinstance(progress_data, dict):
        return
    if progress_data.get("type") == "hook_progress":
        event.hook_event = HookEvent(
            hook_event_name=progress_data.get("hookEvent", ""),
            hook_name=progress_data.get("hookName", ""),
            command=progress_data.get("command", ""),
            status_message=progress_data.get("statusMessage", ""),
        )


def _parse_rate_limit_event(data: dict[str, Any], event: StreamEvent) -> None:
    """Parse rate_limit_event message into a RateLimitInfo dataclass.

    Claude Code 2.1.289 reports the triggering window at the top level
    (``rateLimitType``/``status``/``resetsAt``) but puts utilization only in
    ``unifiedWindows`` — one entry per window. The top-level utilization falls
    back to that window's entry, and every window is exposed as
    ``rate_limit_windows`` so per-window headroom is not lost.
    """
    info = data.get("rate_limit_info", {})
    if not info:
        return
    rate_limit_type = info.get("rateLimitType", "")
    status = info.get("status", "")
    overage = bool(info.get("isUsingOverage", False))
    unified = info.get("unifiedWindows")
    windows: list[RateLimitInfo] = []
    if isinstance(unified, dict):
        for name, window in unified.items():
            if not isinstance(window, dict):
                continue
            try:
                windows.append(
                    RateLimitInfo(
                        rate_limit_type=str(name),
                        status=status if name == rate_limit_type else "allowed",
                        utilization=float(window.get("utilization", 0.0)),
                        resets_at=int(window.get("resetsAt", 0)),
                        is_using_overage=overage,
                    )
                )
            except (TypeError, ValueError):
                continue
    same = next((w for w in windows if w.rate_limit_type == rate_limit_type), None)
    utilization = info.get("utilization")
    event.rate_limit_info = RateLimitInfo(
        rate_limit_type=rate_limit_type,
        status=status,
        utilization=(
            float(utilization)
            if utilization is not None
            else (same.utilization if same is not None else 0.0)
        ),
        resets_at=int(info.get("resetsAt", same.resets_at if same is not None else 0)),
        is_using_overage=overage,
    )
    if same is None and rate_limit_type:
        windows.append(event.rate_limit_info)
    else:
        windows = [event.rate_limit_info if w is same else w for w in windows]
    event.rate_limit_windows = windows


def _parse_stream_event(data: dict[str, Any], event: StreamEvent) -> None:
    """Parse a low-level ``stream_event`` wrapper.

    Only ``message_delta`` carries usage, and it is the *final* usage for
    that message — unlike the ``assistant`` message's own ``usage`` field,
    which is a mid-generation snapshot (e.g. output_tokens=1 while the block
    is still streaming). ``message_delta`` arrives immediately before
    ``message_stop``, after all of that message's content blocks, so its
    usage is what a caller tracking "this turn's real token count" wants —
    see EventProcessor._last_turn_input_tokens.
    """
    inner = data.get("event", {})
    if not isinstance(inner, dict) or inner.get("type") != "message_delta":
        return
    usage = inner.get("usage", {})
    if not isinstance(usage, dict) or not usage:
        return
    event.input_tokens = usage.get("input_tokens")
    event.output_tokens = usage.get("output_tokens")
    event.cache_read_tokens = usage.get("cache_read_input_tokens")
    event.cache_creation_tokens = usage.get("cache_creation_input_tokens")


def _parse_ask_questions(tool_input: dict[str, Any]) -> list[AskQuestion]:
    """Parse AskUserQuestion input, including JSON-encoded nested values."""

    def _as_list(value: Any) -> list[Any]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                return []
        return value if isinstance(value, list) else []

    def _as_dict(value: Any) -> dict[str, Any] | None:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                return None
        return value if isinstance(value, dict) else None

    result: list[AskQuestion] = []
    for raw_question in _as_list(tool_input.get("questions", [])):
        question = _as_dict(raw_question)
        if question is None:
            continue
        options = [
            AskOption(
                label=option.get("label", ""),
                description=option.get("description", ""),
            )
            for option in (
                _as_dict(raw_option) for raw_option in _as_list(question.get("options", []))
            )
            if option is not None and option.get("label")
        ]
        result.append(
            AskQuestion(
                question=question.get("question", ""),
                header=question.get("header", ""),
                multi_select=bool(question.get("multiSelect", False)),
                options=options,
            )
        )
    return result


def _parse_todo_items(tool_input: dict[str, Any]) -> list[TodoItem]:
    """Parse TodoWrite tool input into a list of TodoItem objects."""
    todos_raw = tool_input.get("todos", [])
    result: list[TodoItem] = []
    for t in todos_raw:
        if not isinstance(t, dict):
            continue
        content = t.get("content", "")
        if not content:
            continue
        result.append(
            TodoItem(
                content=content,
                status=t.get("status", "pending"),
                active_form=t.get("activeForm", ""),
            )
        )
    return result
