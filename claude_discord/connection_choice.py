"""Apply the chosen connection (subscription / provider) to a runner before a turn.

Every frontend builds its runner the same way — backend, model, effort — and
then calls :func:`apply_connection` next to ``apply_thread_execution_mode``, so
Discord, the Console, Teams and scheduled tasks all honour the same choice.
"""

from __future__ import annotations

import inspect
from typing import Any

from claude_code_core.child_env import cli_environment
from claude_code_core.connections import CONNECTION_BACKENDS, Connection, resolve


async def apply_connection(
    runner: Any, settings: Any, backend: str, thread_id: int | None
) -> Connection | None:
    """Set ``runner.connection`` from the thread's (or the global) choice."""
    if backend not in CONNECTION_BACKENDS or not hasattr(runner, "connection"):
        return None
    getter = getattr(settings, "chosen_connection", None)
    pending = getter(backend, thread_id) if callable(getter) else None
    # Settings stand-ins (tests, custom frontends) may not implement it as a coroutine.
    chosen = await pending if inspect.isawaitable(pending) else None
    if not isinstance(chosen, str):
        chosen = None
    connection = resolve(backend, chosen, cli_environment())
    runner.connection = connection
    return connection


__all__ = ["apply_connection"]
