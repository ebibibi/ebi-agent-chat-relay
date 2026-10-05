"""Execution environments: where and how a CLI backend's subprocess runs.

The operator picks a default mode and an allowlist in the deployment
environment (``CCDB_EXECUTION_MODE`` / ``CCDB_EXECUTION_ALLOWED_MODES``); a thread
may pick a mode from that allowlist. ``host`` — the default — spawns exactly as
before. See ``docs/execution-environments.md`` and ADR-0008.

Runners call :func:`prepare_launch` immediately before
``asyncio.create_subprocess_exec`` and spawn whatever it returns.
"""

from __future__ import annotations

from typing import Any

from ..types import MessageType, StreamEvent
from .base import ExecutionRefusedError, Launch, PreparedLaunch, agent_state_paths
from .config import (
    BWRAP,
    CONTAINER,
    DEFAULT_MODE,
    HOST,
    MODES,
    NATIVE,
    SSH,
    ExecutionConfig,
)
from .launch import environment_for, prepare_launch, resolve_mode

__all__ = [
    "BWRAP",
    "CONTAINER",
    "DEFAULT_MODE",
    "HOST",
    "MODES",
    "NATIVE",
    "SSH",
    "ExecutionConfig",
    "ExecutionRefusedError",
    "Launch",
    "PreparedLaunch",
    "agent_state_paths",
    "carry_execution_mode",
    "environment_for",
    "prepare_launch",
    "refusal_event",
    "resolve_mode",
]


def refusal_event(exc: ExecutionRefusedError) -> StreamEvent:
    """The terminal event a runner yields when the environment refused to start."""
    return StreamEvent(raw={}, message_type=MessageType.RESULT, is_complete=True, error=str(exc))


def carry_execution_mode(source: Any, target: Any) -> Any:
    """Copy a thread's execution-mode choice onto a cloned runner and return it."""
    target.execution_mode = getattr(source, "execution_mode", None)
    return target
