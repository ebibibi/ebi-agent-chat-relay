"""The vault notes a thread changed, and what changed in them.

The session transcript says which notes the agent wrote (the same tool-call reading as
``workspace_records``, widened from the four record files to any note under a numbered
top-level vault folder). The vault is a git repository, so the change is shown as a diff
between the last commit before the thread started and the file on disk now — which also
includes anything else that touched the note meanwhile; the UI says "since this thread
started" for that reason. Excluded and hidden notes never appear.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from claude_code_core.account_transcripts import ambient_home, find_transcript

from .vault import MAX_NOTE_BYTES, Vault, VaultError
from .workspace_records import _WRITE_MARKERS, WRITE_TOOLS, _tool_calls

MAX_READ_BYTES = 8_000_000
MAX_NOTES = 30
MAX_DIFF_CHARS = 60_000
GIT_TIMEOUT = 10.0
# A vault-relative note path: a numbered top-level folder ("02_Contexts", "04_Knowledge",
# "99_Attachments" …), then anything up to ".md". Absolute and ~ paths match from there.
_NOTE = re.compile(r"(?:^|[\s\"'`=(/])((?:0\d|99)_[^\n\"'`$<>|]+?\.md)(?![\w.-])")


def note_writes(name: str, text: str) -> list[str]:
    """Vault-relative notes one tool call wrote (write tool, or a shell write marker)."""
    if ".md" not in text or (name not in WRITE_TOOLS and not _WRITE_MARKERS.search(text)):
        return []
    return [m[1] for m in _NOTE.finditer(text)]


def transcript_note_writes(lines: Iterable[str]) -> list[str]:
    """Every note the transcript's tool calls wrote, first write first, without repeats."""
    seen: dict[str, None] = {}
    for line in lines:
        if ".md" not in line:
            continue
        for name, text in _tool_calls(line):
            for path in note_writes(name, text):
                seen.setdefault(path, None)
    return list(seen)


def session_note_writes(session_id: str, backend: str | None) -> list[str]:
    kind = backend or "claude"
    try:
        path = find_transcript(kind, ambient_home(kind), session_id)
    except (OSError, ValueError):
        return []
    if path is None:
        return []
    try:
        size = path.stat().st_size
        with path.open("rb") as source:
            source.seek(max(0, size - MAX_READ_BYTES))
            data = source.read()
    except OSError:
        return []
    return transcript_note_writes(data.decode("utf-8", errors="replace").splitlines())


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, no shell
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=GIT_TIMEOUT,
        check=False,
    )


def _base_commit(root: Path, since: str | None) -> str | None:
    """The last commit before *since* (an ISO time), or None when there is none/no git."""
    if not since:
        return None
    done = _git(root, "rev-list", "-1", f"--before={since}", "HEAD")
    commit = done.stdout.strip()
    return commit if done.returncode == 0 and re.fullmatch(r"[0-9a-f]{7,64}", commit) else None


def _diff(root: Path, rel: str, base: str | None) -> tuple[str, str]:
    """(status, unified diff) of *rel* from *base* to the working tree."""
    if base is not None and _git(root, "cat-file", "-e", f"{base}:{rel}").returncode == 0:
        done = _git(root, "diff", "--no-color", "--unified=2", base, "--", rel)
        text = done.stdout if done.returncode == 0 else ""
        return ("modified" if text.strip() else "unchanged"), text
    # New since the thread started (or no history): the whole note is the change.
    try:
        with (root / rel).open("rb") as source:
            body = source.read(MAX_NOTE_BYTES).decode("utf-8", errors="replace")
    except OSError:
        return "missing", ""
    return "added", "".join(f"+{line}\n" for line in body.splitlines())


def changed_notes(
    vault: Vault, session_id: str, backend: str | None, since: str | None
) -> list[dict[str, Any]]:
    """The visible notes *session_id* wrote, each with its diff since *since*."""
    root = vault.root
    if root is None:
        return []
    out: list[dict[str, Any]] = []
    base = _base_commit(root, since)
    for rel in session_note_writes(session_id, backend):
        try:
            clean, _ = vault.resolve(rel, {".md"})
        except VaultError:
            continue  # excluded, hidden, outside the vault or gone
        status, diff = _diff(root, clean, base)
        if status in ("unchanged", "missing"):
            continue
        out.append(
            {
                "path": clean,
                "status": status,
                "diff": diff[:MAX_DIFF_CHARS],
                "truncated": len(diff) > MAX_DIFF_CHARS,
            }
        )
        if len(out) >= MAX_NOTES:
            break
    return out
