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
import re
import tempfile
import threading
import time
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


# -- links -----------------------------------------------------------------------------
INDEX_SECONDS = 30.0
MAX_BACKLINK_FILES = 50_000
_WIKILINK = re.compile(r"(!?)\[\[([^\[\]\n]+?)\]\]")
_FENCE = re.compile(r"(?ms)^(```|~~~).*?^\1")


def link_target(raw: str) -> str:
    """``Note#Heading|Alias`` → ``Note``: the part of a wikilink that names a file."""
    return raw.split("|", 1)[0].split("#", 1)[0].split("^", 1)[0].strip()


def wikilinks(text: str) -> list[str]:
    """Every wikilink target in *text* outside code fences, in order, without duplicates."""
    body = _FENCE.sub("", text)
    seen: dict[str, None] = {}
    for match in _WIKILINK.finditer(body):
        target = link_target(match[2])
        if target:
            seen.setdefault(target, None)
    return list(seen)


class NoteIndex:
    """File names in the vault for Obsidian-style link resolution, plus backlinks.

    Built from a walk that skips hidden folders, symbolic links and excluded paths, so an
    excluded note can never be resolved, listed or reported as linking somewhere.
    """

    def __init__(self, vault: Vault) -> None:
        self.vault = vault
        self._lock = threading.Lock()
        self._built = 0.0
        self._patterns: list[str] | None = None
        self._paths: list[str] = []
        self._by_name: dict[str, list[str]] = {}
        self._links: dict[str, tuple[float, list[str]]] = {}

    def _walk(self) -> list[str]:
        root = self.vault.root
        if root is None:
            return []
        patterns = self.vault.patterns()
        out = []
        for directory, dirs, files in os.walk(root, followlinks=False):
            base = Path(directory)
            rel_dir = base.relative_to(root).as_posix()
            dirs[:] = sorted(
                d
                for d in dirs
                if not (base / d).is_symlink()
                and not excluded(f"{rel_dir}/{d}/x" if rel_dir != "." else f"{d}/x", patterns)
            )
            for name in files:
                rel = name if rel_dir == "." else f"{rel_dir}/{name}"
                suffix = PurePosixPath(name).suffix.lower()
                if suffix not in NOTE_SUFFIXES and suffix not in FILE_TYPES:
                    continue
                if (base / name).is_symlink() or excluded(rel, patterns):
                    continue
                out.append(rel)
        return sorted(out)

    def refresh(self, *, force: bool = False) -> None:
        """Rebuild when stale, and at once when the exclusion list changed (never fail open)."""
        with self._lock:
            patterns = self.vault.patterns()
            fresh = time.monotonic() - self._built < INDEX_SECONDS
            if not force and self._paths and fresh and patterns == self._patterns:
                return
            self._patterns = patterns
            paths = self._walk()
            by_name: dict[str, list[str]] = {}
            for rel in paths:
                name = PurePosixPath(rel).name.lower()
                by_name.setdefault(name, []).append(rel)
                if name.endswith(".md"):
                    by_name.setdefault(name[:-3], []).append(rel)
            self._paths, self._by_name, self._built = paths, by_name, time.monotonic()

    def _visible(self, paths: list[str]) -> list[str]:
        """Drop anything the current exclusion list hides, whatever the index holds."""
        patterns = self.vault.patterns()
        return [p for p in paths if not excluded(p, patterns)]

    def resolve(self, target: str, source: str = "") -> dict[str, Any]:
        found = self._resolve(target, source)
        if found["path"] is not None and not self._visible([found["path"]]):
            return {"path": None, "candidates": []}
        return {"path": found["path"], "candidates": self._visible(found["candidates"])}

    def _resolve(self, target: str, source: str = "") -> dict[str, Any]:
        """Where ``[[target]]`` in note *source* points: ``{"path", "candidates"}``.

        Obsidian's order: a path from the vault root, then relative to the source note,
        then a unique file name; with several files of that name, the one closest to the
        source wins (same folder, then the shortest path).
        """
        self.refresh()
        name = link_target(target)
        if not name:
            return {"path": None, "candidates": []}
        known = set(self._paths)
        wanted = [name, f"{name}.md"]
        for candidate in wanted:
            if candidate.lstrip("/") in known:
                return {"path": candidate.lstrip("/"), "candidates": []}
        source_dir = PurePosixPath(source).parent
        for candidate in wanted:
            joined = os.path.normpath((source_dir / candidate).as_posix())
            if joined in known:
                return {"path": joined, "candidates": []}
        if "/" in name:
            tails = [p for p in self._paths if any(p.endswith("/" + w) for w in wanted)]
        else:
            tails = list(dict.fromkeys(self._by_name.get(name.lower(), [])))
        if not tails:
            return {"path": None, "candidates": []}
        same = [p for p in tails if PurePosixPath(p).parent == source_dir]
        best = (same or sorted(tails, key=lambda p: (p.count("/"), p)))[0]
        return {"path": best, "candidates": tails if len(tails) > 1 else []}

    def search_names(self, query: str, limit: int = 50) -> list[str]:
        """Notes whose path contains every space-separated term, shortest first."""
        self.refresh()
        terms = [t for t in query.lower().split() if t]
        notes = [p for p in self._paths if p.endswith(".md")]
        hits = [p for p in notes if all(t in p.lower() for t in terms)] if terms else notes
        return self._visible(sorted(hits, key=lambda p: (len(PurePosixPath(p).name), p)))[:limit]

    def backlinks(self, rel: str) -> list[str]:
        """Notes whose wikilinks resolve to *rel*; each file is re-read only when it changed."""
        self.refresh()
        root = self.vault.root
        if root is None:
            return []
        stem = PurePosixPath(rel).stem.lower()
        out = []
        for path in [p for p in self._paths if p.endswith(".md")][:MAX_BACKLINK_FILES]:
            if path == rel:
                continue
            try:
                stamp = (root / path).stat().st_mtime
            except OSError:
                continue
            cached = self._links.get(path)
            if cached is None or cached[0] != stamp:
                try:
                    with (root / path).open("rb") as source:
                        text = source.read(MAX_NOTE_BYTES).decode("utf-8", errors="replace")
                except OSError:
                    continue
                cached = (stamp, wikilinks(text))
                self._links[path] = cached
            targets = [
                t
                for t in cached[1]
                if PurePosixPath(t).stem.lower() == stem or PurePosixPath(t).name.lower() == stem
            ]
            if any(self.resolve(t, path)["path"] == rel for t in targets):
                out.append(path)
        return self._visible(out)
