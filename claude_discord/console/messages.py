"""Thread messages as the console shows them.

A thread holds three kinds of message, and the console treats them differently:

- ``human``: what a person wrote, including replies sent from the console
  (the bot posts those on the person's behalf, tagged with a footer).
- ``agent``: what the agent says to the person — answers, questions, errors.
- ``activity``: the relay's own bookkeeping — tool calls, thinking, session
  start/finish embeds, status lines, notification pings, rename notices and
  automatic continuation prompts. Hidden by default in the web client.

The classification is a set of rules over what ccdb itself posts, so it lives
next to the console rather than in the generic ``/api/threads`` serializer.
"""

from __future__ import annotations

import re
from typing import Any

from ..discord_ui.embeds import COLOR_ASK, COLOR_ERROR

KIND_HUMAN = "human"
KIND_AGENT = "agent"
KIND_ACTIVITY = "activity"

MAX_CONTENT_CHARS = 4000
MAX_EMBED_TEXT_CHARS = 1000

# Embed colours that carry something the person must read.
_AGENT_EMBED_COLORS = frozenset({COLOR_ASK, COLOR_ERROR})
# "-# ..." is Discord subtext: ccdb uses it for every status note.
_SUBTEXT = re.compile(r"^-# ")
# "[WAIT FINISHED — automatic continuation]", "[MESSAGE FROM ANOTHER CLAUDE SESSION — …]".
_AUTOMATIC_PROMPT = re.compile(r"^\[[A-Z][A-Z0-9 _/#-]*(?:—[^\]\n]*)?\]")
# "🟡 <@123> The agent has finished — your reply is needed here."
_PING = re.compile(r"^\S{1,2} <@!?\d+> ")
# The footer after each turn: a single code block with the API / usage lines.
_FOOTER = re.compile(r"^```\n(?:\U0001f517 API:|[^\n]*\U0001f9e0)[\s\S]*```$")
# Console replies end with "-# 🖥️ via Relay Console (who)".
_CONSOLE_REPLY = re.compile(r"\n*-# \U0001f5a5️? via Relay Console \(([^)\n]*)\)\s*$")
# Message types that are a message, not a system notice (rename, pin, …).
_CONVERSATION_TYPES = frozenset({"default", "reply"})


def _message_type(message: Any) -> str:
    kind = getattr(message, "type", None)
    if kind is None:
        return "default"
    return str(getattr(kind, "name", kind))


def _embeds(message: Any) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for embed in getattr(message, "embeds", None) or []:
        title = str(getattr(embed, "title", None) or "")
        description = str(getattr(embed, "description", None) or "")
        colour = getattr(embed, "colour", None) or getattr(embed, "color", None)
        value = getattr(colour, "value", colour)
        if not title and not description:
            continue
        out.append(
            {
                "title": title[:256],
                "description": description[:MAX_EMBED_TEXT_CHARS],
                "color": value if isinstance(value, int) else None,
            }
        )
    return out


def _attachments(message: Any) -> list[dict[str, object]]:
    return [
        {
            "filename": str(getattr(a, "filename", "") or "file"),
            "url": str(getattr(a, "url", "") or ""),
            "size": getattr(a, "size", None),
        }
        for a in getattr(message, "attachments", None) or []
    ]


def classify(
    *,
    is_bot: bool,
    content: str,
    message_type: str = "default",
    embeds: list[dict[str, object]] | None = None,
    attachments: list[dict[str, object]] | None = None,
) -> str:
    """Return ``human``, ``agent`` or ``activity`` for one message."""
    if not is_bot:
        return KIND_HUMAN
    if message_type not in _CONVERSATION_TYPES:
        return KIND_ACTIVITY
    if attachments:
        return KIND_AGENT
    text = content.strip()
    if not text:
        if any(e.get("color") in _AGENT_EMBED_COLORS for e in embeds or []):
            return KIND_AGENT
        return KIND_ACTIVITY
    if _CONSOLE_REPLY.search(text):
        return KIND_HUMAN
    if _SUBTEXT.match(text) or _AUTOMATIC_PROMPT.match(text) or _PING.match(text):
        return KIND_ACTIVITY
    if _FOOTER.match(text):
        return KIND_ACTIVITY
    return KIND_AGENT


def serialize_message(message: Any) -> dict[str, object]:
    """Reduce a discord.Message to what the console client renders."""
    content = str(getattr(message, "content", "") or "")
    author: Any = getattr(message, "author", None)
    is_bot = bool(getattr(author, "bot", False))
    author_name = str(getattr(author, "display_name", None) or author or "unknown")
    embeds = _embeds(message)
    attachments = _attachments(message)
    kind = classify(
        is_bot=is_bot,
        content=content,
        message_type=_message_type(message),
        embeds=embeds,
        attachments=attachments,
    )
    via_console = _CONSOLE_REPLY.search(content) if is_bot else None
    if via_console:
        # Show who wrote it, and the text without the relay footer.
        author_name = via_console.group(1) or author_name
        content = content[: via_console.start()]
    created_at: Any = getattr(message, "created_at", None)
    return {
        "id": getattr(message, "id", None),
        "author": author_name,
        "is_bot": is_bot,
        "kind": kind,
        "content": content[:MAX_CONTENT_CHARS],
        "truncated": len(content) > MAX_CONTENT_CHARS,
        "embeds": embeds,
        "attachments": attachments,
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else None,
        "jump_url": getattr(message, "jump_url", None),
    }


def with_kind(message: dict[str, Any]) -> dict[str, object]:
    """Add ``kind`` (and empty embeds/attachments) to a stored console message.

    Console conversations keep plain text only, so the text rules decide.
    """
    return {
        **message,
        "kind": classify(
            is_bot=bool(message.get("is_bot")), content=str(message.get("content") or "")
        ),
        "embeds": [],
        "attachments": [],
    }
