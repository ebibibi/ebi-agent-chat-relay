"""A surface-only RunConfig must survive the whole run helper.

PR4 let a caller pass ``surface=`` instead of ``thread=``. Every caller that
actually did so was still a Discord one until PR6 rewired the scheduler, and
the helper's system-context builder still reached for ``config.thread.id``.
Nothing caught it: the scheduler's own tests mock ``run_claude_with_config``
out entirely, so the one function that broke was never executed.

These tests run it for real against a surface with no Discord object behind it.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from claude_code_core.memory_surface import MemorySurface
from claude_discord.cogs._run_helper import _build_system_context
from claude_discord.cogs.run_config import RunConfig
from claude_discord.concurrency import SessionRegistry


def _runner() -> MagicMock:
    runner = MagicMock()
    runner.working_dir = "/tmp/example"
    runner.model = "test-model"
    return runner


async def test_system_context_is_built_without_a_discord_thread() -> None:
    surface = MemorySurface()
    config = RunConfig(
        surface=surface,
        runner=_runner(),
        prompt="scheduled work",
        registry=SessionRegistry(),
    )

    context = await _build_system_context(config)

    assert str(surface.thread_key) in context


async def test_file_delivery_guidance_forbids_local_path_links() -> None:
    surface = MemorySurface()
    config = RunConfig(
        surface=surface,
        runner=_runner(),
        prompt="send me the report",
    )

    context = await _build_system_context(config)

    assert "Discord cannot open local filesystem paths" in context
    assert "Never describe a local path as a clickable link" in context
    assert f".ccdb-attachments-{surface.thread_key}" in context


async def test_the_session_is_registered_under_the_surface_key() -> None:
    """The registry is how two sessions notice each other — a wrong key is a silent collision."""
    surface = MemorySurface()
    registry = SessionRegistry()
    config = RunConfig(
        surface=surface, runner=_runner(), prompt="scheduled work", registry=registry
    )

    await _build_system_context(config)

    assert surface.thread_key in {s.thread_id for s in registry.list_active()}


async def test_lounge_context_is_built_without_a_discord_thread() -> None:
    lounge_repo = MagicMock()
    lounge_repo.get_recent = AsyncMock(return_value=[])
    config = RunConfig(
        surface=MemorySurface(),
        runner=_runner(),
        prompt="scheduled work",
        lounge_repo=lounge_repo,
    )

    await _build_system_context(config)

    lounge_repo.get_recent.assert_awaited_once()
