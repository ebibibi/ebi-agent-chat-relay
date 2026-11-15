"""Which merged changes need the bot to restart."""

from __future__ import annotations

import pytest

from claude_discord.deploy_policy import needs_restart, restart_paths


@pytest.mark.parametrize(
    "path",
    [
        "claude_discord/console/static/app.js",
        "claude_discord/console/static/app.css",
        "claude_discord/console/static/index.html",
        "docs/console.md",
        "docs/adr/0026-x.md",
        "docs/ja/guide/reference.md",
        "README.md",
        "AGENTS.md",
        "CHANGELOG.md",
        "tests/test_x.py",
        "scripts/pre-start.sh",
        "scripts/tool.py",
        ".github/workflows/post-merge.yml",
        "examples/ebibot/README.md",
        "Makefile",
    ],
)
def test_restart_free(path: str) -> None:
    assert not needs_restart([path])


@pytest.mark.parametrize(
    "path",
    [
        "claude_discord/restart_reservation.py",
        "claude_discord/console/server.py",
        "claude_code_core/runner.py",
        "claude_teams/endpoint.py",
        "examples/ebibot/cogs/auto_upgrade.py",
        "pyproject.toml",
        "uv.lock",
        "examples/context_links.json",
        "something/new.bin",
    ],
)
def test_needs_restart(path: str) -> None:
    assert needs_restart([path])


def test_one_python_file_among_static_files_restarts() -> None:
    paths = ["claude_discord/console/static/app.js", "docs/x.md", "claude_discord/bot.py"]
    assert needs_restart(paths)
    assert restart_paths(paths) == ["claude_discord/bot.py"]


def test_nothing_changed_needs_nothing() -> None:
    assert not needs_restart([])
    assert not needs_restart(["", "  "])
