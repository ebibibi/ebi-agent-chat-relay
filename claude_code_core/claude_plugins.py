"""Discovery of skills shipped by installed Claude Code plugins.

Claude Code keeps plugin skills under ``~/.claude/plugins/cache/<marketplace>/
<plugin>/<version>/skills`` and records the current ``installPath`` of each
plugin in ``plugins/installed_plugins.json``. Nothing outside Claude Code looks
there, so any other consumer — the ``/skill`` autocomplete, or a non-Claude
backend such as pi — has to read that index itself. The version segment changes
on every plugin update, which is why the path is resolved from the index on
each call rather than configured once.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Scope Claude Code records for a plugin installed for the user as a whole, as
# opposed to "project"/"local" installs that belong to one repository.
USER_SCOPE = "user"


def claude_config_dir() -> Path:
    """Return Claude Code's config directory, honouring ``CLAUDE_CONFIG_DIR``."""
    configured = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(configured) if configured else Path.home() / ".claude"


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        logger.warning("Failed to read %s", path)
        return None
    return data if isinstance(data, dict) else None


def _disabled_plugins(claude_dir: Path) -> set[str]:
    """Plugin keys explicitly switched off in the user's ``settings.json``.

    A plugin absent from ``enabledPlugins`` counts as enabled, matching how an
    install without a settings entry behaves in Claude Code.
    """
    settings = _read_json(claude_dir / "settings.json") or {}
    enabled = settings.get("enabledPlugins")
    if not isinstance(enabled, dict):
        return set()
    return {key for key, value in enabled.items() if value is False}


def plugin_skill_dirs(
    claude_dir: Path | None = None,
    *,
    user_scope_only: bool = False,
    enabled_only: bool = False,
) -> list[Path]:
    """Return the ``skills/`` directory of each installed plugin that has one.

    ``user_scope_only`` drops project/local installs, which a repository can
    introduce on its own. ``enabled_only`` drops plugins the user disabled.
    Returns an empty list when the index is missing or malformed.
    """
    claude_dir = claude_dir if claude_dir is not None else claude_config_dir()
    index = _read_json(claude_dir / "plugins" / "installed_plugins.json")
    plugins = index.get("plugins") if index else None
    if not isinstance(plugins, dict):
        return []

    disabled = _disabled_plugins(claude_dir) if enabled_only else set()
    dirs: list[Path] = []
    for key, entries in plugins.items():
        if key in disabled or not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if user_scope_only and entry.get("scope") != USER_SCOPE:
                continue
            install_path = entry.get("installPath")
            if not isinstance(install_path, str) or not install_path:
                continue
            skills_dir = Path(install_path) / "skills"
            if skills_dir.is_dir() and skills_dir not in dirs:
                dirs.append(skills_dir)
    return dirs
