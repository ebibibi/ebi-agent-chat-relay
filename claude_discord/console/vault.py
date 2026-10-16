"""Read any note in the Ebi Workspace vault, minus what its owner excludes.

The Workspace routes only know four fixed files per entry. Agents mention every kind of note,
so the Console can open any Markdown note or attachment under the vault root — except paths
the owner excluded. The exclusion list is a plain text file (one pattern per line, ``#``
comments) that can be edited by hand or from the Console:

    ${XDG_CONFIG_HOME:-~/.config}/ebi-workspace/console-exclude.txt

Patterns follow a small, predictable subset of .gitignore:

- ``secret/``           a directory, wherever it is (and everything below it)
- ``02_Contexts/x/``    a directory at that path from the vault root
- ``*.private.md``      a file name pattern, wherever it is
- ``01_Daily/2025-*``   a path pattern from the vault root

Hidden files and folders (``.obsidian``, ``.git``, ``.trash`` …) are always excluded. An
excluded path is reported exactly like a missing one, so its existence does not leak.
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import tempfile
import threading
from pathlib import Path, PurePosixPath
from typing import Any

MAX_NOTE_BYTES = 2_000_000
MAX_FILE_BYTES = 20_000_000
MAX_EXCLUDE_BYTES = 20_000
NOTE_SUFFIXES = {".md"}
#: Attachments a note may embed. No SVG: an SVG is a document that can carry script.
FILE_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
}
DEFAULT_EXCLUDES = (
    "# Ebi AI Console: notes the Console must never show, list, search or link.",
    "# One pattern per line. 'dir/' = a folder anywhere, 'a/b/' = a folder from the vault",
    "# root, '*.x.md' = a file name anywhere, 'a/b*' = a path from the vault root.",
    "# Hidden folders (.obsidian, .git, .trash …) are always excluded.",
    "",
)


class VaultError(ValueError):
    """Unknown, excluded, unsafe or unreadable vault path (all look the same to a client)."""


def exclude_file() -> Path:
    config = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    return config / "ebi-workspace" / "console-exclude.txt"


def parse_patterns(text: str) -> list[str]:
    patterns = []
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            patterns.append(line.lstrip("/"))
    return patterns


def excluded(rel: str, patterns: list[str]) -> bool:
    """Whether vault-relative POSIX path *rel* is hidden from the Console."""
    parts = PurePosixPath(rel).parts
    if any(p.startswith(".") for p in parts):
        return True
    for pattern in patterns:
        directory = pattern.endswith("/")
        body = pattern.rstrip("/")
        if not body:
            continue
        if "/" in body:
            # Anchored at the vault root: the path itself, or a folder above it.
            for end in range(len(parts), 0, -1):
                prefix = "/".join(parts[:end])
                if fnmatch.fnmatchcase(prefix, body) and (not directory or end < len(parts)):
                    return True
        else:
            names = parts[:-1] if directory else parts
            if any(fnmatch.fnmatchcase(name, body) for name in names):
                return True
    return False


def _revision(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Vault:
    """The vault root plus the owner's exclusion list."""

    def __init__(self, root: Path | None, excludes: Path | None = None) -> None:
        self.root = root.resolve() if root else None
        self.excludes_path = excludes or exclude_file()
        self._lock = threading.Lock()
        self._cache: tuple[float, list[str]] | None = None

    # -- exclusion list ----------------------------------------------------------
    def exclude_text(self) -> str:
        try:
            return self.excludes_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return "\n".join(DEFAULT_EXCLUDES)

    def patterns(self) -> list[str]:
        try:
            stamp = self.excludes_path.stat().st_mtime
        except OSError:
            stamp = -1.0
        with self._lock:
            if self._cache is None or self._cache[0] != stamp:
                self._cache = (stamp, parse_patterns(self.exclude_text()))
            return self._cache[1]

    def save_excludes(self, text: Any) -> list[str]:
        if not isinstance(text, str) or len(text.encode("utf-8")) > MAX_EXCLUDE_BYTES:
            raise VaultError("The exclusion list must be text of at most 20 KB")
        if "\0" in text:
            raise VaultError("The exclusion list must be plain text")
        target = self.excludes_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink():
            raise VaultError("The exclusion list must not be a symbolic link")
        fd, temporary = tempfile.mkstemp(prefix=".console-", dir=target.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as out:
                out.write(text if text.endswith("\n") or not text else text + "\n")
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)
        with self._lock:
            self._cache = None
        return self.patterns()

    # -- paths -------------------------------------------------------------------
    def resolve(self, rel: Any, suffixes: set[str]) -> tuple[str, Path]:
        """(normalised relative path, absolute file) for a readable, non-excluded file."""
        if self.root is None:
            raise VaultError("Ebi Workspace is not configured")
        if not isinstance(rel, str) or not rel or len(rel) > 1000:
            raise VaultError("Unknown note")
        if "\\" in rel or "\0" in rel:
            raise VaultError("Unknown note")
        pure = PurePosixPath(rel)
        if pure.is_absolute() or any(p in ("", ".", "..") for p in pure.parts):
            raise VaultError("Unknown note")
        clean = pure.as_posix()
        if pure.suffix.lower() not in suffixes or excluded(clean, self.patterns()):
            raise VaultError("Unknown note")
        current = self.root
        for part in pure.parts:
            current = current / part
            if current.is_symlink():
                raise VaultError("Unknown note")
        if not current.is_file() or not current.resolve().is_relative_to(self.root):
            raise VaultError("Unknown note")
        return clean, current

    def note(self, rel: Any) -> dict[str, Any]:
        clean, path = self.resolve(rel, NOTE_SUFFIXES)
        with path.open("rb") as source:
            data = source.read(MAX_NOTE_BYTES + 1)
        if len(data) > MAX_NOTE_BYTES:
            raise VaultError("The note is too large to show")
        return {
            "path": clean,
            "text": data.decode("utf-8", errors="replace"),
            "revision": _revision(data),
            "modified": path.stat().st_mtime,
        }

    def file(self, rel: Any) -> tuple[Path, str]:
        """An attachment a note embeds: its path and content type."""
        clean, path = self.resolve(rel, set(FILE_TYPES))
        if path.stat().st_size > MAX_FILE_BYTES:
            raise VaultError("The file is too large to show")
        return path, FILE_TYPES[PurePosixPath(clean).suffix.lower()]
