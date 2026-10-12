"""Buttons on Discord messages that other frontends can press too.

A discord.py button only answers an interaction, and a bot cannot create an
interaction for itself — so a frontend that only *reads* the thread (the Relay
Console) sees "click the button" and has nothing to click. Flows that wait on
a button therefore also register the same answer here, keyed by the message
the button belongs to. The console lists what is registered for each message
it shows and calls :meth:`MessageActions.invoke` when the person clicks.

Registrations live in memory only. They describe a coroutine that is waiting
right now; after a restart nothing is waiting, so there is nothing to persist.
The flow that registered removes the registration once it has its answer —
whichever side the answer came from — so a stale button cannot be pressed.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Rendering hints, a subset of Discord's button styles.
STYLE_PRIMARY = "primary"
STYLE_SUCCESS = "success"
STYLE_DANGER = "danger"
STYLE_SECONDARY = "secondary"
_STYLES = frozenset({STYLE_PRIMARY, STYLE_SUCCESS, STYLE_DANGER, STYLE_SECONDARY})

# Receives who pressed it (the console identity).
ActionCallback = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class MessageAction:
    """One button: ``callback`` runs when it is pressed."""

    label: str
    callback: ActionCallback
    style: str = STYLE_SECONDARY

    def as_dict(self, index: int) -> dict[str, str]:
        style = self.style if self.style in _STYLES else STYLE_SECONDARY
        return {"id": str(index), "label": self.label[:80], "style": style}


@dataclass(frozen=True)
class _Entry:
    channel_id: int
    actions: tuple[MessageAction, ...]


class MessageActions:
    """In-memory registry of pressable actions, by Discord message id."""

    def __init__(self) -> None:
        self._entries: dict[int, _Entry] = {}

    def register(self, channel_id: int, message_id: int, actions: Iterable[MessageAction]) -> None:
        """Offer *actions* on a message, replacing any earlier ones."""
        entry = _Entry(channel_id=channel_id, actions=tuple(actions))
        if entry.actions:
            self._entries[message_id] = entry

    def clear(self, *message_ids: int) -> None:
        """Withdraw the actions on these messages (unknown ids are ignored)."""
        for message_id in message_ids:
            self._entries.pop(message_id, None)

    def for_message(self, channel_id: int, message_id: int) -> list[dict[str, str]]:
        """What the message offers, as the console renders it."""
        entry = self._entries.get(message_id)
        if entry is None or entry.channel_id != channel_id:
            return []
        return [a.as_dict(i) for i, a in enumerate(entry.actions)]

    async def invoke(self, channel_id: int, message_id: int, action_id: str, who: str) -> bool:
        """Press one action. False when it is gone or never belonged to *channel_id*.

        The registration is withdrawn before the callback runs, so a double
        click (or two people at once) answers once.
        """
        entry = self._entries.get(message_id)
        if entry is None or entry.channel_id != channel_id:
            return False
        if not action_id.isdigit() or int(action_id) >= len(entry.actions):
            return False
        action = entry.actions[int(action_id)]
        self._entries.pop(message_id, None)
        logger.info("message action %r on %d pressed by %s", action.label, message_id, who)
        await action.callback(who)
        return True


# The process-wide registry: producers and the console share it without wiring.
message_actions = MessageActions()
