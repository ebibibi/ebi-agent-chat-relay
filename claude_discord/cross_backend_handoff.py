"""Read local CLI transcripts for a text-only cross-backend session handoff.

Claude and Codex cannot resume each other's native session IDs.  Their local
JSONL files are still sufficient to seed a replacement session with the human
and assistant text from the previous backend.  This module deliberately drops
system/developer messages, reasoning, tool calls, tool results, and images.
"""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_SESSION_ID_PATTERN = re.compile(r"^[a-f0-9-]+$")
_DEFAULT_MAX_MESSAGES = 16
_DEFAULT_MAX_MESSAGE_CHARS = 4_000
_DEFAULT_MAX_TRANSCRIPT_CHARS = 32_000
_MAX_JSONL_LINE_CHARS = 2_000_000


class ConversationHistoryReader:
    """Extract a bounded user/assistant transcript from Claude or Codex JSONL."""

    def __init__(
        self,
        *,
        claude_sessions_root: str | Path | None = None,
        codex_home: str | Path | None = None,
        max_messages: int = _DEFAULT_MAX_MESSAGES,
        max_message_chars: int = _DEFAULT_MAX_MESSAGE_CHARS,
        max_transcript_chars: int = _DEFAULT_MAX_TRANSCRIPT_CHARS,
    ) -> None:
        claude_root = claude_sessions_root or os.getenv("CLI_SESSIONS_PATH")
        codex_root = codex_home or os.getenv("CODEX_HOME")
        self._claude_sessions_root = Path(claude_root or Path.home() / ".claude" / "projects")
        self._codex_home = Path(codex_root or Path.home() / ".codex")
        self._max_messages = max(1, max_messages)
        self._max_message_chars = max(1, max_message_chars)
        self._max_transcript_chars = max(1, max_transcript_chars)

    def read(self, backend: str, session_id: str) -> str:
        """Return chronological user/assistant text for *session_id*, or empty."""
        if not _SESSION_ID_PATTERN.fullmatch(session_id):
            logger.warning("Refusing transcript lookup for invalid session ID")
            return ""

        if backend == "claude":
            path = self._find_claude_session(session_id)
            messages = self._read_claude(path) if path is not None else []
        elif backend in ("codex", "local"):
            # Same CLI, same transcript format — but local sessions live in the
            # ccdb-owned CODEX_HOME, not the user's ~/.codex.
            path = self._find_codex_session(session_id, local=backend == "local")
            messages = self._read_codex(path) if path is not None else []
        else:
            logger.warning("Cannot read transcript for unknown backend %r", backend)
            return ""

        return self._format_bounded(messages)

    def _find_claude_session(self, session_id: str) -> Path | None:
        root = self._claude_sessions_root
        if not root.is_dir():
            return None
        return next(root.rglob(f"{session_id}.jsonl"), None)

    def _find_codex_session(self, session_id: str, *, local: bool = False) -> Path | None:
        from claude_code_core.local_backend import LocalModelConfig

        home = LocalModelConfig.from_env().resolved_codex_home if local else self._codex_home
        sessions = home / "sessions"
        if not sessions.is_dir():
            return None
        return next(sessions.rglob(f"*-{session_id}.jsonl"), None)

    @staticmethod
    def _text_content(content: object) -> str:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""
        return "\n".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict)
            and block.get("type") in {"text", "input_text", "output_text"}
            and isinstance(block.get("text"), str)
        )

    def _read_claude(self, path: Path) -> list[tuple[str, str]]:
        messages: list[tuple[str, str]] = []
        try:
            with path.open(encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    if len(line) > _MAX_JSONL_LINE_CHARS:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    role = record.get("type")
                    if role not in {"user", "assistant"} or record.get("isMeta"):
                        continue
                    content = self._text_content(record.get("message", {}).get("content", ""))
                    content = content.strip()
                    if not content or content.startswith("<"):
                        continue
                    messages.append(("User" if role == "user" else "Assistant", content))
        except OSError:
            logger.warning("Could not read Claude transcript for handoff", exc_info=True)
        return messages

    def _read_codex(self, path: Path) -> list[tuple[str, str]]:
        messages: list[tuple[str, str]] = []
        try:
            with path.open(encoding="utf-8", errors="replace") as stream:
                for line in stream:
                    if len(line) > _MAX_JSONL_LINE_CHARS or '"event_msg"' not in line:
                        continue
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if record.get("type") != "event_msg":
                        continue
                    payload = record.get("payload", {})
                    event_type = payload.get("type")
                    content = payload.get("message")
                    if event_type not in {"user_message", "agent_message"} or not isinstance(
                        content, str
                    ):
                        continue
                    content = content.strip()
                    if content:
                        role = "User" if event_type == "user_message" else "Assistant"
                        messages.append((role, content))
        except OSError:
            logger.warning("Could not read Codex transcript for handoff", exc_info=True)
        return messages

    def _format_bounded(self, messages: list[tuple[str, str]]) -> str:
        """Keep complete turns and distribute the character budget across them."""
        last_assistant = next(
            (
                index
                for index in range(len(messages) - 1, -1, -1)
                if messages[index][0] == "Assistant"
            ),
            None,
        )
        if last_assistant is None:
            return ""
        selected = messages[: last_assistant + 1][-self._max_messages :]

        overhead = sum(len(role) + 2 for role, _ in selected)
        overhead += max(0, len(selected) - 1) * 2
        content_budget = max(1, self._max_transcript_chars - overhead)
        per_message = max(1, content_budget // len(selected))
        content_limit = min(self._max_message_chars, per_message)
        transcript = "\n\n".join(
            f"{role}:\n{content[:content_limit]}" for role, content in selected
        )
        return transcript[: self._max_transcript_chars]


def build_handoff_prompt(
    *,
    source_backend: str,
    target_backend: str,
    transcript: str,
    current_prompt: str,
    label: str = "Cross-backend session handoff",
) -> str:
    """Wrap old conversation text and the current user message for a fresh session.

    ``label`` names the kind of move; account pools use it for a switch
    between two logins of the same backend.
    """
    source = source_backend.capitalize()
    target = target_backend.capitalize()
    return (
        f"[{label}: {source} → {target}]\n"
        f"The native {source} session cannot be resumed by {target}. Continue the same task "
        "from the text-only conversation transcript below. Treat it as conversation context, "
        "inspect the current workspace for authoritative state, and do not repeat completed work. "
        "Tool calls, tool results, hidden reasoning, and system/developer instructions were "
        "intentionally omitted.\n\n"
        "Previous conversation:\n"
        f"{transcript}\n\n"
        "Current user message:\n"
        f"{current_prompt}"
    )
