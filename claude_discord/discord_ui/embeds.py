"""Discord embed builders for Claude Code events."""

from __future__ import annotations

import discord

from ..claude.types import TodoItem, ToolCategory, ToolUseEvent

# Colors
COLOR_INFO = 0x5865F2  # Discord blurple
COLOR_SUCCESS = 0x57F287  # Green
COLOR_ERROR = 0xED4245  # Red
COLOR_TOOL = 0xFEE75C  # Yellow
COLOR_THINKING = 0x9B59B6  # Purple
COLOR_ASK = 0x3498DB  # Blue — question-like
COLOR_TODO = 0xE67E22  # Orange — task list

AUTOCOMPACT_THRESHOLD = 83.5


# Per-backend visual mapping for session embeds. Falls back to claude
# defaults when an unknown / None backend is supplied.
_BACKEND_TITLE: dict[str, str] = {
    "claude": "\U0001f916 Claude Code",  # robot face 🤖
    "codex": "\U0001f300 OpenAI Codex",  # cyclone 🌀
    "local": "\U0001f3e0 Local model",  # house 🏠
}
_BACKEND_COLOR_START: dict[str, int] = {
    "claude": COLOR_INFO,  # Discord blurple
    "codex": 0x10A37F,  # OpenAI teal-green
    "local": 0x6B8E23,  # olive — "stays on your hardware"
}


def _backend_display(backend: str | None, model: str | None) -> tuple[str, int, str]:
    """Return (title_prefix, color, model_suffix) for the given backend."""
    b = backend if isinstance(backend, str) and backend else "claude"
    title = _BACKEND_TITLE.get(b, _BACKEND_TITLE["claude"])
    color = _BACKEND_COLOR_START.get(b, COLOR_INFO)
    suffix = f" · {model}" if model else ""
    return title, color, suffix


CATEGORY_ICON: dict[ToolCategory, str] = {
    ToolCategory.READ: "\U0001f4d6",  # 📖
    ToolCategory.EDIT: "✏️",  # ✏️
    ToolCategory.COMMAND: "\U0001f527",  # 🔧
    ToolCategory.WEB: "\U0001f310",  # 🌐
    ToolCategory.THINK: "\U0001f4ad",  # 💭
    ToolCategory.OTHER: "\U0001f916",  # 🤖
}


def tool_use_embed(
    tool: ToolUseEvent,
    in_progress: bool = True,
    elapsed_s: int | None = None,
) -> discord.Embed:
    """Create an embed for a tool use event.

    Args:
        tool: The tool use event to display.
        in_progress: Whether the tool is still running.
        elapsed_s: Seconds elapsed since the tool started. When provided and
                   the tool is in-progress, elapsed time is shown in the
                   description so the title (command name) stays stable.
    """
    icon = CATEGORY_ICON.get(tool.category, "\U0001f916")
    title = f"{icon} {tool.display_name}{'...' if in_progress else ''}"

    embed = discord.Embed(
        title=title[:256],
        color=COLOR_TOOL if in_progress else COLOR_INFO,
    )
    if in_progress and elapsed_s is not None:
        embed.description = f"⏳ {elapsed_s}s elapsed..."
    return embed


def session_start_embed(
    session_id: str | None = None,
    *,
    backend: str | None = None,
    model: str | None = None,
) -> discord.Embed:
    """Create an embed for session start.

    Title prefix and color reflect which backend (claude/codex) is
    handling this session. The model name appears in the footer.
    """
    title_prefix, color, _ = _backend_display(backend, model)
    embed = discord.Embed(
        title=f"{title_prefix} session started",
        color=color,
    )
    footer_parts: list[str] = []
    if session_id:
        footer_parts.append(f"Session: {session_id[:8]}...")
    if isinstance(model, str) and model:
        footer_parts.append(model)
    if footer_parts:
        embed.set_footer(text=" · ".join(footer_parts))
    return embed


def session_complete_embed(
    cost_usd: float | None = None,
    duration_ms: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cache_read_tokens: int | None = None,
    context_window: int | None = None,
    cache_creation_tokens: int | None = None,
    *,
    backend: str | None = None,
    model: str | None = None,
) -> discord.Embed:
    """Create an embed for session completion."""
    parts: list[str] = []
    if isinstance(backend, str) and backend:
        _model_str = model if isinstance(model, str) else None
        title_prefix, _, model_suffix = _backend_display(backend, _model_str)
        # Strip the leading emoji for inline use; the leading brain chip carries enough.
        backend_tag = title_prefix.split(" ", 1)[-1] if " " in title_prefix else title_prefix
        parts.append(f"\U0001f9e0 {backend_tag}{model_suffix}")
    if duration_ms is not None:
        seconds = duration_ms / 1000
        parts.append(f"⏱️ {seconds:.1f}s")
    if cost_usd is not None:
        parts.append(f"\U0001f4b0 ${cost_usd:.4f}")
    if input_tokens is not None and output_tokens is not None:

        def _fmt(n: int) -> str:
            return f"{n / 1000:.1f}k" if n >= 1000 else str(n)

        token_str = f"\U0001f4ca {_fmt(input_tokens)}↑ {_fmt(output_tokens)}↓"
        if cache_read_tokens:
            total = input_tokens + cache_read_tokens
            hit_pct = int(cache_read_tokens / total * 100) if total else 0
            token_str += f" ({hit_pct}% cache)"
        parts.append(token_str)

    if context_window and input_tokens is not None:
        # Context window usage = prompt tokens only (input + cache reads/creation).
        # Output tokens are NOT included — they are not yet "in" the context window;
        # they will be added as cached input on the next turn.
        # This matches Claude Code's own lrH() calculation in the CLI source.
        context_used = input_tokens + (cache_read_tokens or 0) + (cache_creation_tokens or 0)
        usage_pct = min(100.0, context_used / context_window * 100)
        remaining_pct = max(0.0, AUTOCOMPACT_THRESHOLD - usage_pct)
        ctx_str = f"\U0001f4ca {usage_pct:.0f}% ctx"
        if usage_pct < AUTOCOMPACT_THRESHOLD:
            ctx_str += f" ({remaining_pct:.0f}% until compact)"
        else:
            ctx_str += " ⚠️"
        parts.append(ctx_str)

    description = " | ".join(parts) if parts else None

    embed = discord.Embed(
        title="✅ Done",
        description=description,
        color=COLOR_SUCCESS,
    )

    if context_window and input_tokens is not None:
        context_used = input_tokens + (cache_read_tokens or 0) + (cache_creation_tokens or 0)
        usage_pct = min(100.0, context_used / context_window * 100)
        if usage_pct >= AUTOCOMPACT_THRESHOLD:
            embed.set_footer(
                text=f"⚠️ Context {usage_pct:.0f}% full — auto-compact may run on next turn"
            )

    return embed


_PREVIEW_LINES = 3


def tool_result_preview_embed(tool_title: str, full_content: str) -> discord.Embed:
    """Collapsed embed showing the first _PREVIEW_LINES lines and a hidden-count hint.

    Paired with ToolResultView so the user can expand on demand.
    """
    title = tool_title.rstrip(".")
    embed = discord.Embed(title=title[:256], color=COLOR_INFO)
    if full_content:
        lines = full_content.split("\n")
        preview = "\n".join(lines[:_PREVIEW_LINES])
        hidden = len(lines) - _PREVIEW_LINES
        if hidden > 0:
            preview += f"\n... +{hidden} lines"
        embed.description = f"```\n{preview}\n```"
    return embed


def tool_result_embed(tool_title: str, result_content: str) -> discord.Embed:
    """Create an embed for a completed tool with its result.

    Replaces the in-progress tool embed once the result is available.
    Uses description (4096-char limit) rather than a field (1024-char limit)
    so that up to ~30 lines of output can be shown without truncation.
    """
    # Strip the trailing "..." from in-progress title
    title = tool_title.rstrip(".")
    embed = discord.Embed(
        title=title[:256],
        color=COLOR_INFO,
    )
    if result_content:
        # Reserve 8 chars for code block markers (```\n … \n```)
        max_content = 4096 - 8
        display = result_content[:max_content]
        embed.description = f"```\n{display}\n```"
    return embed


def thinking_embed(thinking_text: str) -> discord.Embed:
    """Create an embed for extended thinking content.

    Uses a plain code block (no spoiler) so the text is always rendered with
    Discord's own code block background/foreground — guaranteed readable in
    both dark and light themes regardless of the embed accent color.

    Note: spoiler + code block combinations (||```text```||) do not apply code
    block styling when revealed inside embed descriptions; the text still picks
    up the embed accent color and can become unreadable.
    """
    # Reserve chars for code block markers: ```\n...\n``` = 8 chars overhead
    max_text = 4096 - 8 - len("\n... (truncated)")
    truncated = thinking_text[:max_text]
    if len(thinking_text) > max_text:
        truncated += "\n... (truncated)"
    return discord.Embed(
        title="\U0001f4ad Thinking",
        description=f"```\n{truncated}\n```",
        color=COLOR_THINKING,
    )


def redacted_thinking_embed() -> discord.Embed:
    """Create a placeholder embed for a redacted_thinking block."""
    return discord.Embed(
        title="\U0001f512 Thinking (redacted)",
        description="Some reasoning was performed but cannot be shown.",
        color=0x95A5A6,  # Muted grey
    )


def error_embed(error: str) -> discord.Embed:
    """Create an embed for errors."""
    return discord.Embed(
        title="❌ Error",
        description=error[:4000],
        color=COLOR_ERROR,
    )


def timeout_embed(seconds: int) -> discord.Embed:
    """Create an embed for session timeout with actionable guidance."""
    return discord.Embed(
        title="⏱️ Session timed out",
        description=(
            f"No response received for {seconds} seconds.\n\n"
            "**What to do:**\n"
            "• Send a message to resume the session\n"
            "• Use `/clear` to start fresh"
        ),
        color=COLOR_ERROR,
    )


def ask_embed(question: str, header: str = "") -> discord.Embed:
    """Create an embed for an AskUserQuestion interactive prompt."""
    title = f"❓ {header}" if header else "❓ The agent needs your input"
    return discord.Embed(
        title=title[:256],
        description=question[:4096],
        color=COLOR_ASK,
    )


def stopped_embed() -> discord.Embed:
    """Create an embed for a manually stopped session."""
    return discord.Embed(
        title="⏹️ Session stopped",
        description=(
            "The session was stopped.\n\n"
            "The session is preserved — send a message to resume, "
            "or use `/clear` to start fresh."
        ),
        color=0xFFA500,  # Orange — not an error, just interrupted
    )


# Status icons for each todo state
_TODO_ICON = {
    "pending": "⬜",
    "in_progress": "\U0001f504",
    "completed": "✅",
}


def todo_embed(todos: list[TodoItem]) -> discord.Embed:
    """Create (or update) a Discord embed showing the current TodoWrite task list.

    Each todo item is rendered as a single line:
      ✅ Task description
      🔄 Active task label (while in_progress)
      ⬜ Pending task

    The embed is posted once and then **edited in-place** as the task list
    changes, so the user sees a single live progress view in the thread.
    """
    lines: list[str] = []
    for item in todos:
        icon = _TODO_ICON.get(item.status, "⬜")
        label = (
            item.active_form if item.status == "in_progress" and item.active_form else item.content
        )
        lines.append(f"{icon} {label}")

    description = "\n".join(lines) if lines else "*(no tasks)*"
    completed = sum(1 for t in todos if t.status == "completed")
    total = len(todos)
    title = f"\U0001f4cb Tasks ({completed}/{total})"

    return discord.Embed(
        title=title,
        description=description[:4096],
        color=COLOR_TODO,
    )
