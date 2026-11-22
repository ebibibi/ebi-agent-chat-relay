"""Which merged changes need the bot to restart, decided from the changed files.

The bot runs from a git checkout. Python is imported once, so a Python change
only takes effect after a restart. Everything else in the Console's static
directory is read from disk on every request, and docs, tests and CI files are
never loaded by the running bot at all. Restarting for those interrupts the
people using the bot and changes nothing they can see.

The rule is deliberately conservative: a path is restart-free only when it
matches one of the patterns below. Anything unknown restarts.
"""

from __future__ import annotations

from collections.abc import Iterable
from fnmatch import fnmatchcase

__all__ = ["RESTART_FREE_PATTERNS", "needs_restart", "restart_paths"]

#: Paths the running bot never imports, or reads fresh from disk.
RESTART_FREE_PATTERNS: tuple[str, ...] = (
    # Served from disk on every request (``console/server.py`` reads each file per request).
    "claude_discord/console/static/*",
    # Read by people and by CI, never by the running bot.
    "docs/*",
    "tests/*",
    ".github/*",
    "*.md",
    "LICENSE",
    # Used only when the bot (re)starts: the next restart picks them up anyway.
    "scripts/*",
    "deploy/*",
    "Makefile",
    "discord-bot.service",
)

#: Python here is never imported by the running bot; Python anywhere else may be.
_PYTHON_FREE_PREFIXES = ("tests/", "scripts/", "docs/", ".github/")


def _restart_free(path: str) -> bool:
    path = path.strip()
    if not path:
        return True
    if path.endswith(".py") and not path.startswith(_PYTHON_FREE_PREFIXES):
        return False
    return any(fnmatchcase(path, pattern) for pattern in RESTART_FREE_PATTERNS)


def restart_paths(paths: Iterable[str]) -> list[str]:
    """The changed paths that need a restart to take effect, in input order."""
    return [p for p in paths if not _restart_free(p)]


def needs_restart(paths: Iterable[str]) -> bool:
    """True when any changed path needs a restart. An empty change needs none."""
    return bool(restart_paths(paths))
