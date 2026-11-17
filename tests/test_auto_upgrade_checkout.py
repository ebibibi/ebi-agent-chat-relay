"""Auto-upgrade of a git checkout: apply restart-free changes now, reserve the rest."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord.ext import commands

from claude_discord.cogs.auto_upgrade import AutoUpgradeCog, UpgradeConfig
from claude_discord.restart_reservation import get_restart_reservation, set_restart_reservation
from claude_discord.session_slots import (
    SessionSlotScheduler,
    get_session_slots,
    set_session_slots,
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, path: str, text: str) -> None:
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    _git(repo, "add", path)
    _git(repo, "commit", "-q", "-m", f"change {path}")


@pytest.fixture
def repos(tmp_path: Path) -> tuple[Path, Path]:
    """An upstream repo and the bot's checkout cloned from it."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _git(upstream, "init", "-q", "-b", "main")
    _git(upstream, "config", "user.email", "t@example.com")
    _git(upstream, "config", "user.name", "t")
    _commit(upstream, "claude_discord/bot.py", "v1\n")
    checkout = tmp_path / "checkout"
    _git(tmp_path, "clone", "-q", str(upstream), str(checkout))
    return upstream, checkout


@pytest.fixture
def slots():
    original_slots, original_res = get_session_slots(), get_restart_reservation()
    scheduler = SessionSlotScheduler(2)
    set_session_slots(scheduler)
    set_restart_reservation(None)
    yield scheduler
    reservation = get_restart_reservation()
    if reservation is not None and reservation.reserved:
        reservation.cancel()
    set_session_slots(original_slots)
    set_restart_reservation(original_res)


def _cog(checkout: Path) -> AutoUpgradeCog:
    bot = MagicMock(spec=commands.Bot)
    bot.resume_repo = None
    config = UpgradeConfig(
        package_name="pkg",
        trigger_prefix="🔄 ebibot-upgrade",
        working_dir=str(checkout),
        restart_command=["true"],
        git_checkout=True,
    )
    return AutoUpgradeCog(bot=bot, config=config)


def _thread() -> MagicMock:
    thread = MagicMock(spec=discord.Thread)
    thread.id = 1
    thread.send = AsyncMock()
    return thread


def _posted(thread: MagicMock) -> str:
    return "\n".join(str(c.args[0]) for c in thread.send.call_args_list if c.args)


async def test_static_only_change_is_applied_without_a_restart(repos, slots) -> None:
    upstream, checkout = repos
    _commit(upstream, "claude_discord/console/static/app.js", "new ui\n")
    _commit(upstream, "docs/console.md", "doc\n")
    thread = _thread()

    await _cog(checkout)._run_pipeline(thread, status_target=None)

    assert (checkout / "claude_discord/console/static/app.js").read_text() == "new ui\n"
    assert "without a restart" in _posted(thread)
    assert get_restart_reservation() is None


async def test_python_change_reserves_a_quiet_restart_and_does_not_pull(repos, slots) -> None:
    upstream, checkout = repos
    _commit(upstream, "claude_discord/bot.py", "v2\n")
    thread = _thread()

    await _cog(checkout)._run_pipeline(thread, status_target=None)

    # The running process must not see half-new Python; pre-start.sh pulls on restart.
    assert (checkout / "claude_discord/bot.py").read_text() == "v1\n"
    reservation = get_restart_reservation()
    assert reservation is not None and reservation.reserved
    assert reservation.status()["requested_by"] == "auto-upgrade"
    assert "Restart reserved" in _posted(thread)


async def test_up_to_date_does_nothing(repos, slots) -> None:
    _, checkout = repos
    thread = _thread()
    await _cog(checkout)._run_pipeline(thread, status_target=None)
    assert "Already up to date" in _posted(thread)
    assert get_restart_reservation() is None


async def test_a_checkout_with_local_work_is_left_alone(repos, slots) -> None:
    upstream, checkout = repos
    _commit(upstream, "docs/x.md", "doc\n")
    (checkout / "claude_discord/bot.py").write_text("local edit\n")
    thread = _thread()

    await _cog(checkout)._run_pipeline(thread, status_target=None)

    assert not (checkout / "docs/x.md").exists()
    assert "local work" in _posted(thread)
