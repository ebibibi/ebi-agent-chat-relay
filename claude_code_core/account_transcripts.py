"""Move a CLI session transcript from one profile directory to another.

Measured on Claude Code 2.1.289 and codex-cli 0.160.0 (see
``docs/account-pools.md``): a session id is only resumable from the
configuration directory that holds its transcript. ``--resume <id>`` in another
``CLAUDE_CONFIG_DIR`` fails with "No conversation found"; ``codex exec resume
<id>`` in another ``CODEX_HOME`` fails with "no rollout found". Copying the
transcript file to the same relative path under the target directory is enough
for both CLIs to resume the same session id there, with the full history.

- Claude: ``<CLAUDE_CONFIG_DIR>/projects/<escaped cwd>/<session_id>.jsonl``
  (plus an optional ``<session_id>/`` directory of sub-agent transcripts and
  tool results).
- Codex: ``<CODEX_HOME>/sessions/YYYY/MM/DD/rollout-<ts>-<session_id>.jsonl``.

The copy overwrites an older copy at the target, so a thread that moves
A → B → A resumes on A with the turns it took on B.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
from collections.abc import Mapping
from pathlib import Path

logger = logging.getLogger(__name__)

_SESSION_ID = re.compile(r"^[a-f0-9-]+$")


def _overlay_value(key: str, source: Mapping[str, str]) -> str | None:
    """Read *key* from the ``CCDB_CLI_ENV_FILE`` overlay, as ClaudeRunner does."""
    path = source.get("CCDB_CLI_ENV_FILE")
    if not path:
        return None
    try:
        lines = Path(path).read_text().splitlines()
    except OSError:
        return None
    value: str | None = None
    for line in lines:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            name, raw = line.split("=", 1)
            if name == key:
                value = raw
    return value


def ambient_home(backend: str, env: Mapping[str, str] | None = None) -> Path:
    """The directory a CLI uses when the relay does not choose one.

    For Claude this honours a ``CLAUDE_CONFIG_DIR`` set by the
    ``CCDB_CLI_ENV_FILE`` overlay, because ClaudeRunner applies that overlay to
    every spawn. (CodexRunner does not read the overlay.)
    """
    source = os.environ if env is None else env
    if backend == "claude":
        configured = _overlay_value("CLAUDE_CONFIG_DIR", source) or source.get("CLAUDE_CONFIG_DIR")
        return Path(configured).expanduser() if configured else Path.home() / ".claude"
    if backend == "codex":
        configured = source.get("CODEX_HOME")
        return Path(configured).expanduser() if configured else Path.home() / ".codex"
    raise ValueError(f"Unsupported backend for account pools: {backend!r}")


def find_transcript(backend: str, home: Path, session_id: str) -> Path | None:
    """Return the transcript file for *session_id* under *home*, if any."""
    if not _SESSION_ID.fullmatch(session_id):
        return None
    if backend == "claude":
        projects = home / "projects"
        if not projects.is_dir():
            return None
        return next(projects.glob(f"*/{session_id}.jsonl"), None)
    sessions = home / "sessions"
    if not sessions.is_dir():
        return None
    return next(sessions.rglob(f"rollout-*-{session_id}.jsonl"), None)


def copy_transcript(backend: str, session_id: str, source: Path, target: Path) -> bool:
    """Copy a session's transcript from *source* home to *target* home.

    Returns True when the target now holds the transcript. Never raises: a
    failed copy is reported and the caller falls back to a text handoff.
    """
    if source.resolve() == target.resolve():
        return True
    found = find_transcript(backend, source, session_id)
    if found is None:
        logger.info("No %s transcript for session %s under %s", backend, session_id, source)
        return False
    relative = found.relative_to(source)
    destination = target / relative
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(found, destination)
        if backend == "claude":
            # Sub-agent transcripts and spilled tool results sit beside the file.
            sidecar = found.with_suffix("")
            if sidecar.is_dir():
                shutil.copytree(sidecar, destination.with_suffix(""), dirs_exist_ok=True)
    except OSError:
        logger.warning(
            "Could not copy %s transcript %s to %s", backend, session_id, target, exc_info=True
        )
        return False
    logger.info("Copied %s transcript %s: %s -> %s", backend, session_id, source, target)
    return True
