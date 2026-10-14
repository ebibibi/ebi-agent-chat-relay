"""Token-free body search over local Claude Code transcripts.

Every Claude Code session writes its full conversation to a JSONL transcript
under ``~/.claude/projects/<escaped-cwd>/<session_id>.jsonl``.  The per-thread
``summary`` search (see ``SessionRepository.search``) only covers the opening
prompt; this module searches the *body* of past conversations — the "I remember
doing something mid-thread, where did it go?" case — without spending a single
AI token.

Strategy: shell out to ``grep`` (fast C scan, no ``shell=True``) to pre-filter
the handful of transcript files that contain the keyword, then parse only those
to pull a readable snippet.  If ``grep`` is unavailable, fall back to a bounded
pure-Python scan of the most-recently-modified transcripts.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterator
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

# Transcript files are named "<uuid>.jsonl"; the uuid is the session_id.
_SESSION_FILE_RE = re.compile(r"^[0-9a-f\-]{36}\.jsonl$")
_SNIPPET_WIDTH = 60
# Upper bound on transcript files parsed for a snippet in one search, so a very
# common keyword can't turn into an unbounded scan.
_DEFAULT_SCAN_CAP = 400


@dataclass(frozen=True)
class TranscriptHit:
    """A transcript file whose conversation body contains the query."""

    session_id: str
    path: str
    snippet: str
    mtime: float


def default_transcripts_root() -> str | None:
    """The standard Claude Code transcript location, or None if absent."""
    root = Path.home() / ".claude" / "projects"
    return str(root) if root.is_dir() else None


def find_transcript(session_id: str, root: str | None) -> str | None:
    """Path to one session's transcript, or None if it isn't on this machine.

    The project directory is derived from the session's working directory, which
    callers generally don't have, so this searches every project directory rather
    than reconstructing the path. The id is validated first because it reaches a
    filesystem glob: anything that isn't exactly a session filename is refused
    rather than allowed to escape ``root``.
    """
    if not root or not _SESSION_FILE_RE.match(f"{session_id}.jsonl"):
        return None
    root_path = Path(root)
    if not root_path.is_dir():
        return None
    for project_dir in sorted(root_path.iterdir()):
        candidate = project_dir / f"{session_id}.jsonl"
        if candidate.is_file():
            return str(candidate)
    return None


def make_snippet(text: str, query: str, width: int = _SNIPPET_WIDTH) -> str:
    """Return a one-line snippet of ``text`` centred on the first ``query`` hit."""
    flat = " ".join(text.split())
    idx = flat.lower().find(query.lower())
    if idx < 0:
        return flat[: width * 2].strip()
    start = max(0, idx - width)
    end = min(len(flat), idx + len(query) + width)
    snippet = flat[start:end].strip()
    if start > 0:
        snippet = "…" + snippet
    if end < len(flat):
        snippet = snippet + "…"
    return snippet


def _iter_text_blocks(obj: object) -> Iterator[str]:
    """Yield human-readable text from one parsed transcript line."""
    if not isinstance(obj, dict):
        return
    message = obj.get("message")
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if isinstance(content, str):
        yield content
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    yield text


def scan_file_for_query(path: str, query: str) -> str | None:
    """Return a snippet for the first message text matching ``query``, else None."""
    needle = query.lower()
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                # Cheap prefilter: transcript text is stored as raw UTF-8, so a
                # match in any message block is present in the raw line too.
                if needle not in line.lower():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                for text in _iter_text_blocks(obj):
                    if needle in text.lower():
                        return make_snippet(text, query)
    except OSError:
        return None
    return None


def _transcript_paths(root: str) -> list[Path]:
    """All session transcript files under ``root``, newest first."""
    base = Path(root)
    files = [
        f
        for f in (*base.glob("*.jsonl"), *base.glob("*/*.jsonl"))
        if _SESSION_FILE_RE.match(f.name)
    ]
    files.sort(key=lambda p: _safe_mtime(p), reverse=True)
    return files


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


async def _grep_candidate_files(
    root: str, query: str, grep_path: str = "grep"
) -> list[Path] | None:
    """Use grep to list transcript files containing ``query``.

    Returns None (not an empty list) when grep is unavailable or errors, so the
    caller knows to fall back to the pure-Python scan.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            grep_path,
            "-rilFI",  # recursive, ignore-case, files-with-match, fixed-string, skip-binary
            "-e",
            query,
            "--",
            root,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (FileNotFoundError, OSError):
        return None

    try:
        stdout, _ = await proc.communicate()
    except asyncio.CancelledError:
        if proc.returncode is None:
            with suppress(ProcessLookupError):
                proc.kill()
        await proc.wait()
        raise
    # grep exit codes: 0 = matches, 1 = no matches, 2 = error.
    if proc.returncode not in (0, 1):
        return None

    paths: list[Path] = []
    for raw in stdout.decode("utf-8", "replace").splitlines():
        candidate = Path(raw)
        if _SESSION_FILE_RE.match(candidate.name):
            paths.append(candidate)
    return paths


async def search_transcripts(
    root: str | None,
    query: str,
    *,
    limit: int = 10,
    scan_cap: int = _DEFAULT_SCAN_CAP,
    use_grep: bool = True,
) -> list[TranscriptHit]:
    """Find transcripts whose conversation body contains ``query``.

    Args:
        root: Directory holding ``<session_id>.jsonl`` transcripts (recursively).
        query: Keyword (case-insensitive substring). Blank returns [].
        limit: Maximum number of hits (newest first).
        scan_cap: Max files parsed on the pure-Python fallback path.
        use_grep: Set False to force the pure-Python scan (tests / no grep).
    """
    query = query.strip()
    if not root or not query or not Path(root).is_dir():
        return []

    candidates: list[Path] | None = None
    if use_grep:
        candidates = await _grep_candidate_files(root, query)

    if candidates is None:
        # Fallback: scan the newest transcripts directly, bounded by scan_cap.
        candidates = _transcript_paths(root)[:scan_cap]
    else:
        candidates = [p for p in candidates if p.exists()]
        candidates.sort(key=_safe_mtime, reverse=True)

    hits: list[TranscriptHit] = []
    for path in candidates:
        snippet = scan_file_for_query(str(path), query)
        if snippet is None:
            continue
        hits.append(
            TranscriptHit(
                session_id=path.stem,
                path=str(path),
                snippet=snippet,
                mtime=_safe_mtime(path),
            )
        )
        if len(hits) >= limit:
            break
    return hits
