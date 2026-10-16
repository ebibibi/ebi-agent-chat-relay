"""Bind a thread to the Ebi Workspace project whose records its session actually wrote.

The strongest evidence of where work belongs, short of a person saying so, is where the
agent wrote it down. Agents follow the Workspace rules and record finished work in the
project's ``log.md`` / ``status.md`` (or a context's ``single-actions.md``), so this reads
the session transcript the CLI keeps on disk and looks for tool calls that wrote one of
those files inside the configured vault.

- Claude Code: ``<home>/projects/<escaped cwd>/<session>.jsonl``; ``tool_use`` blocks.
- Codex: ``<home>/sessions/YYYY/MM/DD/rollout-*-<session>.jsonl``; function calls.

A write tool (Edit, Write, apply_patch, …) naming the file counts, and so does a shell
command that names the file together with a write marker (``>>``, ``tee``, ``sed -i``,
``write_text`` …). Merely reading a record does not. Transcripts are read incrementally:
only the bytes appended since the last pass.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_code_core.account_transcripts import ambient_home, find_transcript

logger = logging.getLogger(__name__)

RECORD_FILES = ("log.md", "status.md", "overview.md", "single-actions.md")
WRITE_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit", "apply_patch"}
_WRITE_MARKERS = re.compile(
    r">>|(?<![<>=-])>\s*\S|\btee\b|\bsed\s+-i|write_text|\.write\(|open\([^)]*['\"][wa]"
    r"|\*\*\* (?:Update|Add) File|cat\s*<<|\bcp\b|\bmv\b"
)
MAX_READ_BYTES = 8_000_000


@dataclass(frozen=True)
class RecordWrite:
    """One record file the session wrote: the folder under 02_Contexts and the file name."""

    folder: str
    file: str


def _strings(value: Any) -> Iterator[str]:
    """Every string leaf of a tool input, so paths are read unescaped."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _tool_calls(line: str) -> Iterator[tuple[str, str]]:
    """(tool name, input text) for every tool call on one transcript line."""
    try:
        event = json.loads(line)
    except ValueError:
        return
    if not isinstance(event, dict):
        return
    # Claude Code: {"type": "assistant", "message": {"content": [{"type": "tool_use", ...}]}}
    message = event.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), list):
        for block in message["content"]:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                yield str(block.get("name", "")), "\n".join(_strings(block.get("input")))
    # Codex: {"type": "response_item", "payload": {"type": "function_call", "name": ..., ...}}
    payload = event.get("payload")
    if isinstance(payload, dict) and payload.get("type") in (
        "function_call",
        "custom_tool_call",
        "local_shell_call",
    ):
        body: Any = payload.get("arguments") or payload.get("input") or payload.get("action")
        if isinstance(body, str):
            with contextlib.suppress(ValueError):
                body = json.loads(body)
        name = str(payload.get("name") or payload.get("type") or "")
        yield name, "\n".join(_strings(body))


_FILES = "|".join(re.escape(f) for f in RECORD_FILES)
# ".../02_Contexts/<folder>/log.md", whatever comes before (absolute, ~, or relative).
_DIRECT = re.compile(rf"02_Contexts/([^\n\"'`$]+?)/({_FILES})(?![\w.-])")
# D=".../02_Contexts/<folder>" ... >> "$D/log.md": a folder in a variable, then the file.
_FOLDER = re.compile(r"02_Contexts/([^\n\"'`$]+?)/?[\"'`\n]")
_VIA_VARIABLE = re.compile(rf"\$\{{?\w+\}}?/({_FILES})(?![\w.-])")


def record_writes(name: str, text: str) -> list[RecordWrite]:
    """Record files one tool call wrote, judged from its name and input."""
    if "02_Contexts/" not in text:
        return []
    if name not in WRITE_TOOLS and not _WRITE_MARKERS.search(text):
        return []
    found = [RecordWrite(folder=m[1], file=m[2]) for m in _DIRECT.finditer(text)]
    if not found:
        files = [m[1] for m in _VIA_VARIABLE.finditer(text)]
        folders = [m[1] for m in _FOLDER.finditer(text + "\n")]
        if files and len(set(folders)) == 1:
            found = [RecordWrite(folder=folders[0], file=f) for f in files]
    return found


class RecordScanner:
    """Finds record writes in transcript lines."""

    def writes(self, lines: Iterable[str]) -> list[RecordWrite]:
        found: list[RecordWrite] = []
        for line in lines:
            if "02_Contexts" not in line:
                continue
            for name, text in _tool_calls(line):
                found += record_writes(name, text)
        return found


def entry_for(folder: str, entries: list[dict[str, Any]]) -> str | None:
    """The deepest Workspace entry that *folder* (relative to 02_Contexts) is or sits in."""
    paths = {e["path"] for e in entries}
    parts = Path(folder).parts
    if any(p in ("..", ".") for p in parts):
        return None
    for end in range(len(parts), 0, -1):
        candidate = "/".join(parts[:end])
        if candidate in paths:
            return candidate
    return None


class RecordWatcher:
    """Remembers how far each transcript was read and what it recorded last."""

    def __init__(self) -> None:
        self.scanner = RecordScanner()
        self._offsets: dict[str, tuple[Path, int]] = {}

    def latest(
        self, session_id: str, backend: str | None, entries: list[dict[str, Any]]
    ) -> tuple[str, str] | None:
        """The entry and file most recently written by *session_id* since the last call."""
        key = f"{backend or 'claude'}:{session_id}"
        known = self._offsets.get(key)
        path = known[0] if known else None
        if path is None or not path.is_file():
            try:
                path = find_transcript(
                    backend or "claude", ambient_home(backend or "claude"), session_id
                )
            except (OSError, ValueError):
                path = None
            if path is None:
                return None
        offset = known[1] if known and known[0] == path else 0
        try:
            size = path.stat().st_size
            if size < offset:
                offset = 0
            with path.open("rb") as source:
                source.seek(max(offset, size - MAX_READ_BYTES))
                data = source.read()
        except OSError:
            return None
        # Only complete lines; a line still being written is read next time.
        end = data.rfind(b"\n") + 1
        self._offsets[key] = (path, max(offset, size - MAX_READ_BYTES) + end)
        lines = data[:end].decode("utf-8", errors="replace").splitlines()
        for write in reversed(self.scanner.writes(lines)):
            entry = entry_for(write.folder, entries)
            if entry is not None:
                return entry, write.file
        return None
