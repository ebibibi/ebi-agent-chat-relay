"""Turn a live Discord message into a :class:`HumanActivity` row.

Only genuine human messages count. Bots, webhooks and system messages are
excluded here as well as upstream, because every prompt the relay injects
itself (``/api/spawn``, ``/api/ingest``, relays, the scheduler) is posted by
the bot account or a webhook — so "a human typed this" is decided by the
author, never by the content.
"""

from __future__ import annotations

from typing import Any

import discord

from claude_code_core.attention import HumanActivity

from .thread_policy import initial_thread_name

__all__ = ["activity_from_message", "is_human_message"]

FRONTEND = "discord"


def is_human_message(message: Any) -> bool:
    """Whether *message* was written by a person rather than a bot or webhook."""
    author = getattr(message, "author", None)
    if author is None or getattr(author, "bot", False) is True:
        return False
    if getattr(author, "system", False) is True:
        return False
    return getattr(message, "webhook_id", None) is None


def activity_from_message(
    message: discord.Message,
    *,
    opens_thread: bool,
) -> HumanActivity:
    """Describe where *message* lands as a session turn.

    Args:
        message: The inbound human message.
        opens_thread: ``True`` when the message is about to start a new thread.
            Discord gives a thread created from a message that message's id, so
            the row is keyed to the conversation it creates.
    """
    channel = message.channel
    if isinstance(channel, discord.Thread):
        conversation_id = str(channel.id)
        parent_id = str(channel.parent_id) if channel.parent_id else None
        title: str | None = channel.name
    elif opens_thread:
        conversation_id = str(message.id)
        parent_id = str(channel.id)
        # The thread does not exist yet; this is the exact name it is about to
        # be created with (the opening text, as Discord shows it), so the row
        # agrees with the sidebar. Renames are picked up by later messages.
        title = initial_thread_name(message.content)
    else:
        conversation_id = str(channel.id)
        parent_id = None
        title = getattr(channel, "name", None)
    return HumanActivity(
        frontend=FRONTEND,
        conversation_id=conversation_id,
        parent_id=parent_id,
        thread_title=title,
        author_id=str(message.author.id),
        occurred_at=message.created_at,
        char_count=len(message.content or ""),
        attachment_count=len(message.attachments),
        message_id=str(message.id),
    )
