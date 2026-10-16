"""SessionBackend decorator that anonymizes on the way out and restores on the
way back.

Wrapping the backend — rather than editing ClaudeRunner and CodexRunner — means
both CLIs are covered by one implementation, and a backend added tomorrow is
covered for free.

Attribute access is delegated in *both* directions: several Cogs mutate the
runner in place (``runner.images = ...``, ``runner.working_dir = ...``), and
those writes must land on the real runner, not on this shell.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

from ..types import ImageData, MessageType, StreamEvent

if TYPE_CHECKING:
    from ..backend import SessionBackend
    from .gateway import PrivacyGateway

logger = logging.getLogger(__name__)

__all__ = ["AnonymizingBackend"]

_PASSTHROUGH = ("_inner", "_gateway", "_context")


class AnonymizingBackend:
    """Wraps a SessionBackend with the anonymization gateway."""

    # Declared, never assigned: reads and writes are forwarded to the inner
    # backend by ``__getattr__`` / ``__setattr__``. The annotations exist so the
    # wrapper still satisfies the SessionBackend protocol for a type checker.
    command: str
    model: str
    working_dir: str | None
    permission_mode: str
    images: list[ImageData] | None
    api_port: int | None
    timeout_seconds: int
    dangerously_skip_permissions: bool
    allowed_tools: list[str] | None

    def __init__(
        self,
        inner: SessionBackend,
        gateway: PrivacyGateway,
        **context: Any,
    ) -> None:
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_gateway", gateway)
        object.__setattr__(self, "_context", context)

    # ------------------------------------------------------------ delegation

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_inner"), name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in _PASSTHROUGH:
            object.__setattr__(self, name, value)
        else:
            setattr(object.__getattribute__(self, "_inner"), name, value)

    @property
    def inner(self) -> SessionBackend:
        return object.__getattribute__(self, "_inner")

    async def inject_user_message(self, text: str, images: list[ImageData] | None = None) -> bool:
        """Refuse: a message delivered mid-turn would skip the gateway's guard.

        Declared rather than left to ``__getattr__``, which would hand the raw
        text straight to the CLI. The caller runs it as the next turn instead,
        which goes through :meth:`run` and so through the gateway.
        """
        return False

    def unconsumed_messages(self) -> list[Any]:
        return []

    # ------------------------------------------------------------------ run

    async def run(
        self,
        prompt: str,
        session_id: str | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        gateway: PrivacyGateway = object.__getattribute__(self, "_gateway")
        context: dict[str, Any] = dict(object.__getattribute__(self, "_context"))
        context.setdefault("session_id", session_id)
        context.setdefault("backend", getattr(self.inner, "command", ""))

        outcome = await gateway.guard(prompt, **context)
        if not outcome.allowed:
            yield StreamEvent(
                raw={},
                message_type=MessageType.RESULT,
                is_complete=True,
                error=outcome.reason,
            )
            return

        if outcome.warning:
            yield StreamEvent(
                raw={},
                message_type=MessageType.SYSTEM,
                text=f"⚠️ {outcome.warning}",
            )

        async for event in self.inner.run(outcome.text, session_id):
            yield _restore_event(event, gateway)

    # ------------------------------------------------- explicit delegations
    # ``__getattr__`` already forwards these at runtime; spelling them out
    # keeps the protocol satisfied for the type checker.

    async def interrupt(self) -> None:
        await self.inner.interrupt()

    async def kill(self) -> None:
        await self.inner.kill()

    async def inject_tool_result(self, request_id: str, data: dict) -> None:
        await self.inner.inject_tool_result(request_id, data)

    def _build_env(self) -> dict[str, str]:
        return self.inner._build_env()

    def describe_api(self) -> str:
        return self.inner.describe_api()

    def clone(self, **kwargs: object) -> AnonymizingBackend:
        """Clone the inner backend and re-wrap it — the guard must survive."""
        gateway: PrivacyGateway = object.__getattribute__(self, "_gateway")
        context: dict[str, Any] = object.__getattribute__(self, "_context")
        return AnonymizingBackend(self.inner.clone(**kwargs), gateway, **context)


def _restore_event(event: StreamEvent, gateway: PrivacyGateway) -> StreamEvent:
    """Return a copy of ``event`` with aliases turned back into real names.

    Only fields that reach a human are rewritten. ``raw`` is left untouched:
    it is the transport record of what the external model actually said, and
    rewriting it would make the audit trail lie.
    """
    changes: dict[str, Any] = {}
    for attr in ("text", "thinking", "tool_result_content", "error"):
        value = getattr(event, attr, None)
        if isinstance(value, str) and value:
            restored = gateway.restore(value)
            if restored != value:
                changes[attr] = restored

    tool_use = event.tool_use
    if tool_use is not None and tool_use.tool_input:
        restored_input = {
            key: (gateway.restore(val) if isinstance(val, str) else val)
            for key, val in tool_use.tool_input.items()
        }
        if restored_input != tool_use.tool_input:
            changes["tool_use"] = dataclasses.replace(tool_use, tool_input=restored_input)

    if not changes:
        return event
    return dataclasses.replace(event, **changes)
