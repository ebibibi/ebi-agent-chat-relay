"""Parent mode: a thread that manages child threads without being told (#1489)."""

from __future__ import annotations

import pytest

from claude_discord.database.models import init_db
from claude_discord.database.settings_repo import SettingsRepository
from claude_discord.parent_mode import (
    DEFAULT_MAX_CHILDREN,
    ParentModeSettings,
    build_child_section,
    build_parent_section,
    parse_mode,
)


@pytest.fixture
async def settings(tmp_path, monkeypatch):
    monkeypatch.delenv("CCDB_PARENT_MODE_DEFAULT", raising=False)
    monkeypatch.delenv("CCDB_PARENT_MODE_MAX_CHILDREN", raising=False)
    db = str(tmp_path / "s.db")
    await init_db(db)
    return ParentModeSettings(SettingsRepository(db))


async def test_off_by_default(settings):
    state = await settings.resolve(1, is_child=False)
    assert state.enabled is False
    assert state.source == "default"
    assert await settings.default_enabled() is False


async def test_env_turns_the_default_on(settings, monkeypatch):
    monkeypatch.setenv("CCDB_PARENT_MODE_DEFAULT", "on")
    assert (await settings.resolve(1, is_child=False)).enabled is True


async def test_stored_default_wins_over_env(settings, monkeypatch):
    monkeypatch.setenv("CCDB_PARENT_MODE_DEFAULT", "1")
    await settings.set_default(False)
    assert (await settings.resolve(1, is_child=False)).enabled is False


async def test_thread_override_wins_over_default(settings):
    await settings.set_default(True)
    await settings.set_thread(7, False)
    state = await settings.resolve(7, is_child=False)
    assert state.enabled is False
    assert state.source == "thread"
    await settings.set_thread(8, True)
    await settings.set_default(False)
    assert (await settings.resolve(8, is_child=False)).enabled is True


async def test_clearing_the_override_follows_the_default_again(settings):
    await settings.set_default(True)
    await settings.set_thread(7, False)
    await settings.set_thread(7, None)
    assert await settings.thread_override(7) is None
    assert (await settings.resolve(7, is_child=False)).enabled is True


async def test_children_do_not_inherit_the_default(settings):
    await settings.set_default(True)
    state = await settings.resolve(9, is_child=True)
    assert state.enabled is False
    assert state.source == "child"


async def test_a_child_can_still_be_switched_on_explicitly(settings):
    await settings.set_thread(9, True)
    assert (await settings.resolve(9, is_child=True)).enabled is True


async def test_max_children_default_env_and_stored(settings, monkeypatch):
    assert await settings.max_children() == DEFAULT_MAX_CHILDREN
    monkeypatch.setenv("CCDB_PARENT_MODE_MAX_CHILDREN", "3")
    assert await settings.max_children() == 3
    await settings.set_max_children(8)
    assert await settings.max_children() == 8


@pytest.mark.parametrize("bad", ["0", "-1", "abc", "1000"])
async def test_invalid_env_max_children_falls_back(settings, monkeypatch, bad):
    monkeypatch.setenv("CCDB_PARENT_MODE_MAX_CHILDREN", bad)
    assert await settings.max_children() == DEFAULT_MAX_CHILDREN


@pytest.mark.parametrize("bad", [0, -2, 51])
async def test_set_max_children_rejects_out_of_range(settings, bad):
    with pytest.raises(ValueError):
        await settings.set_max_children(bad)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("on", True), ("off", False), ("default", None), (True, True), (False, False), (None, None)],
)
def test_parse_mode(raw, expected):
    assert parse_mode(raw) is expected


@pytest.mark.parametrize("raw", ["yes please", 3, [], "ON!"])
def test_parse_mode_rejects(raw):
    with pytest.raises(ValueError):
        parse_mode(raw)


def test_parent_section_names_the_thread_and_cap():
    text = build_parent_section(42, 5)
    assert "## Parent mode" in text
    assert "parent_thread_id: 42" in text
    assert '"notify_parent": true' in text
    assert "5" in text
    # Small work stays with the parent; the human talks only to the parent.
    assert "yourself" in text
    assert "one combined report" in text


def test_child_section_forbids_further_children():
    text = build_child_section(42)
    assert "## Child of a managed thread" in text
    assert "42" in text
    assert "Do not create child threads" in text


# ---------------------------------------------------------------------------
# system prompt
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock  # noqa: E402

from claude_code_core.memory_surface import MemorySurface  # noqa: E402
from claude_discord.backend_settings import BackendSettings  # noqa: E402
from claude_discord.cogs._run_helper import _build_system_context  # noqa: E402
from claude_discord.cogs.run_config import RunConfig  # noqa: E402
from claude_discord.database.lineage_repo import ThreadLineageRepository  # noqa: E402


async def _context(settings: ParentModeSettings, thread_id: int, *, api: bool = True) -> str:
    runner = MagicMock()
    runner.working_dir = "/tmp/example"
    runner.api_port = 8080 if api else None
    backend = BackendSettings(
        settings._repo, env_backend="claude", env_model_for_claude="", env_model_for_codex=""
    )
    surface = MemorySurface()
    surface._thread_key = thread_id  # stand in for a relay thread with a known ID
    config = RunConfig(
        surface=surface,
        runner=runner,
        prompt="do it",
        backend_settings=backend,
    )
    return await _build_system_context(config) or ""


async def test_prompt_has_no_parent_section_when_off(settings):
    context = await _context(settings, 10)
    assert "## Parent mode" not in context
    assert "## Child of a managed thread" not in context


async def test_prompt_has_parent_section_when_on(settings):
    await settings.set_thread(10, True)
    await settings.set_max_children(4)
    context = await _context(settings, 10)
    assert "## Parent mode" in context
    assert "parent_thread_id: 10" in context
    assert "At most 4 unfinished children" in context


async def test_prompt_needs_the_api(settings):
    await settings.set_thread(10, True)
    assert "## Parent mode" not in await _context(settings, 10, api=False)


async def test_child_of_a_parent_mode_thread_gets_the_child_section(settings):
    await settings.set_default(True)
    await ThreadLineageRepository(settings._repo.db_path).record(11, 10, "K1")
    context = await _context(settings, 11)
    assert "## Parent mode" not in context
    assert "## Child of a managed thread" in context
    assert "Thread 10 spawned you" in context


async def test_child_of_an_ordinary_thread_gets_nothing(settings):
    await ThreadLineageRepository(settings._repo.db_path).record(11, 10, "K1")
    context = await _context(settings, 11)
    assert "## Child of a managed thread" not in context
