"""Shared pytest fixtures for claude_discord tests.

These fixtures are automatically available to all test files in this directory.
Class-level fixtures with the same name take precedence (pytest scoping rules).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_discord.claude.types import MessageType, StreamEvent


@pytest.fixture(autouse=True)
def _isolate_operator_statusline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests must not execute commands from the operator's real settings file.

    Explicit paths and per-test patches still exercise statusline behavior.
    """
    from claude_discord.discord_ui import statusline

    original = statusline.read_statusline_command
    monkeypatch.setattr(
        statusline,
        "read_statusline_command",
        lambda settings_path=None: original(settings_path) if settings_path else None,
    )


@pytest.fixture
def thread() -> MagicMock:
    """A MagicMock discord.Thread with send and id set."""
    t = MagicMock(spec=discord.Thread)
    t.id = 12345
    msg = MagicMock(spec=discord.Message)
    msg.edit = AsyncMock()
    t.send = AsyncMock(return_value=msg)
    return t


@pytest.fixture
def runner() -> MagicMock:
    """A MagicMock ClaudeRunner with interrupt() wired up.

    clone() returns the same mock so tests that set runner.run = ...
    keep working after _build_system_context triggers runner.clone().
    """
    r = MagicMock()
    r.interrupt = AsyncMock()
    r.clone = MagicMock(return_value=r)
    return r


@pytest.fixture
def repo() -> MagicMock:
    """A MagicMock SessionRepository with async save/get."""
    r = MagicMock()
    r.save = AsyncMock()
    r.get = AsyncMock(return_value=None)
    return r


@pytest.fixture(autouse=True)
def _patch_build_system_context(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Patch _build_system_context to return None by default.

    The always-on File Delivery injection causes runner.clone() on every run,
    which breaks tests using runner.run = async_gen on a plain MagicMock.
    Tests that need real system context should use @pytest.mark.real_system_context.
    """
    if "real_system_context" in {m.name for m in request.node.iter_markers()}:
        return
    monkeypatch.setattr(
        "claude_discord.cogs._run_helper._build_system_context",
        AsyncMock(return_value=None),
    )


def make_async_gen(events: list[StreamEvent]):
    """Return an async generator factory that yields the given events.

    Usage::

        runner.run = make_async_gen([event1, event2])
        async for e in runner.run("prompt"):
            ...
    """

    async def gen(*args, **kwargs):
        for e in events:
            yield e

    return gen


def simple_events(session_id: str = "sess-1") -> list[StreamEvent]:
    """Return a minimal sequence: SYSTEM + RESULT (no tool use)."""
    return [
        StreamEvent(message_type=MessageType.SYSTEM, session_id=session_id),
        StreamEvent(
            message_type=MessageType.RESULT,
            is_complete=True,
            text="Done.",
            session_id=session_id,
            cost_usd=0.01,
            duration_ms=500,
        ),
    ]
