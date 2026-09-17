"""Tests for the agent-spawn thread marker.

The point of the marker is that a thread nobody opened by hand is recognisable
in Discord's channel list, so the cases that matter are the ones where it could
silently disappear: a title that was already long, a caller that already wrote
the marker, and an operator who turned it off.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from claude_discord.cogs.claude_chat import ClaudeChatCog
from claude_discord.thread_marker import (
    DEFAULT_SPAWN_MARKER,
    MAX_THREAD_NAME_LENGTH,
    SPAWN_MARKER_ENV_VAR,
    mark_spawned_thread_name,
    spawn_marker,
)


class TestMarkSpawnedThreadName:
    def test_prefixes_the_agent_chosen_name(self) -> None:
        assert mark_spawned_thread_name("Fix the build") == f"{DEFAULT_SPAWN_MARKER} Fix the build"

    def test_does_not_mark_twice(self) -> None:
        once = mark_spawned_thread_name("Fix the build")
        assert mark_spawned_thread_name(once) == once

    def test_truncation_keeps_the_marker(self) -> None:
        marked = mark_spawned_thread_name("x" * 200)
        assert len(marked) == MAX_THREAD_NAME_LENGTH
        assert marked.startswith(DEFAULT_SPAWN_MARKER)

    def test_long_unmarked_name_is_still_truncated(self) -> None:
        assert len(mark_spawned_thread_name("y" * 200, marker="")) == MAX_THREAD_NAME_LENGTH

    def test_empty_marker_leaves_the_name_alone(self) -> None:
        assert mark_spawned_thread_name("Fix the build", marker="") == "Fix the build"

    def test_custom_marker(self) -> None:
        assert mark_spawned_thread_name("Fix", marker="[bot]") == "[bot] Fix"


class TestSpawnMarkerConfig:
    def test_unset_env_uses_the_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(SPAWN_MARKER_ENV_VAR, raising=False)
        assert spawn_marker() == DEFAULT_SPAWN_MARKER

    def test_env_overrides_the_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(SPAWN_MARKER_ENV_VAR, "[bot]")
        assert spawn_marker() == "[bot]"

    def test_empty_env_disables_rather_than_falling_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An explicit empty value must turn the marker off, not restore the default."""
        monkeypatch.setenv(SPAWN_MARKER_ENV_VAR, "")
        assert spawn_marker() == ""
        assert mark_spawned_thread_name("Fix the build") == "Fix the build"


def _spawn_fixtures() -> tuple[MagicMock, MagicMock]:
    thread = MagicMock(spec=discord.Thread)
    thread.id = 42
    thread.name = "spawned"
    thread.send = AsyncMock()
    channel = MagicMock()
    channel.create_thread = AsyncMock(return_value=thread)
    return channel, thread


class TestSpawnSessionMarking:
    @pytest.mark.asyncio
    async def test_agent_spawned_thread_is_marked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(SPAWN_MARKER_ENV_VAR, raising=False)
        channel, _ = _spawn_fixtures()
        cog = ClaudeChatCog(bot=MagicMock(), repo=MagicMock(), runner=MagicMock())

        with patch.object(cog, "_run_claude", new=AsyncMock()):
            await cog.spawn_session(
                channel, "Do the thing", thread_name="Audit the DNS", agent_spawned=True
            )

        name = channel.create_thread.call_args.kwargs["name"]
        assert name == f"{DEFAULT_SPAWN_MARKER} Audit the DNS"

    @pytest.mark.asyncio
    async def test_prompt_derived_name_is_marked_too(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A caller that supplies no title still gets a recognisable thread."""
        monkeypatch.delenv(SPAWN_MARKER_ENV_VAR, raising=False)
        channel, _ = _spawn_fixtures()
        cog = ClaudeChatCog(bot=MagicMock(), repo=MagicMock(), runner=MagicMock())

        with patch.object(cog, "_run_claude", new=AsyncMock()):
            await cog.spawn_session(channel, "Do the thing", agent_spawned=True)

        assert channel.create_thread.call_args.kwargs["name"] == (
            f"{DEFAULT_SPAWN_MARKER} Do the thing"
        )

    @pytest.mark.asyncio
    async def test_human_initiated_spawn_is_not_marked(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """/fork and session resume carry their own prefix and must stay untouched."""
        monkeypatch.delenv(SPAWN_MARKER_ENV_VAR, raising=False)
        channel, _ = _spawn_fixtures()
        cog = ClaudeChatCog(bot=MagicMock(), repo=MagicMock(), runner=MagicMock())

        with patch.object(cog, "_run_claude", new=AsyncMock()):
            await cog.spawn_session(channel, "Continue", thread_name="🔀 Fork of something")

        assert channel.create_thread.call_args.kwargs["name"] == "🔀 Fork of something"
