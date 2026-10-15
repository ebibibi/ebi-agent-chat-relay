"""Extract human/assistant text from one line of a Codex rollout JSONL file.

Codex has written two shapes of conversation event over time:

* legacy: ``event_msg`` with ``payload.type`` ``user_message`` / ``agent_message``
  and the text in ``payload.message``;
* current: ``event_msg`` with ``payload.type`` ``item_completed`` and
  ``payload.item.type`` ``UserMessage`` / ``AgentMessage`` whose ``content`` is a
  list of text blocks.

Both are read here so every text-only handoff (cross-backend switch, account
switch, stuck-resume recovery) sees the same conversation.  ``response_item``
records are ignored on purpose: their user messages also carry injected
AGENTS.md / environment context that is not part of the conversation.
"""

from __future__ import annotations

import json

_LEGACY_ROLES = {"user_message": "User", "agent_message": "Assistant"}
_ITEM_ROLES = {"UserMessage": "User", "AgentMessage": "Assistant"}
_MARKERS = ('"user_message"', '"agent_message"', '"UserMessage"', '"AgentMessage"')


def _item_text(content: object) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "\n".join(
        block["text"]
        for block in content
        if isinstance(block, dict)
        and str(block.get("type", "")).lower() == "text"
        and isinstance(block.get("text"), str)
    )


def codex_message_from_line(line: str) -> tuple[str, str] | None:
    """Return ``(role, text)`` for a conversation event line, else ``None``.

    *role* is ``"User"`` or ``"Assistant"``.  Lines that cannot be a
    conversation event are rejected before JSON parsing, so multi-megabyte
    image and tool records stay cheap to skip.
    """
    if '"event_msg"' not in line or not any(marker in line for marker in _MARKERS):
        return None
    try:
        record = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict) or record.get("type") != "event_msg":
        return None
    payload = record.get("payload")
    if not isinstance(payload, dict):
        return None

    event_type = payload.get("type")
    if event_type in _LEGACY_ROLES:
        text = payload.get("message")
        role = _LEGACY_ROLES[event_type]
    elif event_type == "item_completed" and isinstance(payload.get("item"), dict):
        item = payload["item"]
        role = _ITEM_ROLES.get(item.get("type"))
        if role is None:
            return None
        text = _item_text(item.get("content"))
    else:
        return None

    if not isinstance(text, str) or not text.strip():
        return None
    return role, text.strip()
