"""Read-only access to threads deleted from Discord but kept as JSONL exports.

Thread clean-up exports every message of a thread to ``<thread_id>.jsonl``
before deleting it: a ``{"_meta": {"id", "name"}}`` line, then raw Discord
message objects. Search should still find those conversations, and the
person should be able to read them, even though nothing can be resumed.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_code_core.transcript_search import make_snippet

from .messages import KIND_ACTIVITY, MAX_CONTENT_CHARS, classify

_ARCHIVE_FILE = re.compile(r"^(\d{1,20})\.jsonl$")
# Discord message types: 0 default, 19 reply, 21 thread starter.
_TYPES = {0: "default", 19: "reply", 21: "thread_starter"}
MAX_MESSAGES = 500


@dataclass(frozen=True)
class ArchiveHit:
    thread_id: int
    title: str
    snippet: str
    last_activity_at: str | None


def _conversation(raw: dict[str, Any]) -> dict[str, object] | None:
    """One archived Discord message as the console renders it, or None for bookkeeping."""
    if raw.get("type") == 21:
        # The thread starter is a stub; the person's opening words are the referenced message.
        raw = raw.get("referenced_message") or {}
    content = str(raw.get("content") or "")
    author = raw.get("author") or {}
    is_bot = bool(author.get("bot"))
    kind = classify(
        is_bot=is_bot,
        content=content,
        message_type=_TYPES.get(raw.get("type", 0), "other"),
        attachments=raw.get("attachments") or None,
    )
    if kind == KIND_ACTIVITY or not content.strip():
        return None
    return {
        "id": raw.get("id"),
        "author": str(author.get("global_name") or author.get("username") or "unknown"),
        "is_bot": is_bot,
        "kind": kind,
        "content": content[:MAX_CONTENT_CHARS],
        "truncated": len(content) > MAX_CONTENT_CHARS,
        "created_at": raw.get("timestamp"),
    }


class ThreadArchive:
    def __init__(self, root: str) -> None:
        self.root = Path(root)

    @classmethod
    def from_env(cls) -> ThreadArchive | None:
        root = (os.getenv("CCDB_CONSOLE_THREAD_ARCHIVE") or "").strip()
        return cls(root) if root and Path(root).is_dir() else None

    def _path(self, thread_id: int) -> Path:
        return self.root / f"{int(thread_id)}.jsonl"

    def _read(self, thread_id: int) -> tuple[str, list[dict[str, object]]] | None:
        try:
            lines = self._path(thread_id).read_text(encoding="utf-8").splitlines()
        except (OSError, ValueError):
            return None
        title, messages = "", []
        for line in lines:
            try:
                raw = json.loads(line)
            except ValueError:
                continue
            if not isinstance(raw, dict):
                continue
            if "_meta" in raw:
                title = str((raw["_meta"] or {}).get("name") or "")
                continue
            message = _conversation(raw)
            if message is not None:
                messages.append(message)
        messages.sort(key=lambda m: str(m["created_at"] or ""))
        return title, messages

    async def read(self, thread_id: int) -> tuple[str, list[dict[str, object]]] | None:
        """Title and conversation messages (oldest first), or None when not archived."""
        found = await asyncio.to_thread(self._read, thread_id)
        if found is None:
            return None
        title, messages = found
        return title, messages[-MAX_MESSAGES:]

    async def search(self, query: str, limit: int = 50) -> list[ArchiveHit]:
        """Archived threads whose title or messages contain *query*, newest first."""
        paths = await _grep(self.root, query)
        hits: list[ArchiveHit] = []
        for path in paths:
            match = _ARCHIVE_FILE.match(path.name)
            if match is None:
                continue
            found = await asyncio.to_thread(self._read, int(match.group(1)))
            if found is None:
                continue
            title, messages = found
            q = query.lower()
            body = next((m for m in messages if q in str(m["content"]).lower()), None)
            if body is None and q not in title.lower():
                continue  # matched only inside tool output or JSON keys
            hits.append(
                ArchiveHit(
                    thread_id=int(match.group(1)),
                    title=title,
                    snippet=make_snippet(str(body["content"]) if body else title, query),
                    last_activity_at=str(messages[-1]["created_at"]) if messages else None,
                )
            )
        hits.sort(key=lambda h: h.last_activity_at or "", reverse=True)
        return hits[:limit]


async def _grep(root: Path, query: str) -> list[Path]:
    proc = await asyncio.create_subprocess_exec(
        "grep",
        "-rilF",
        "--include=*.jsonl",
        "-e",
        query,
        "--",
        str(root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        stdout, _ = await proc.communicate()
    except asyncio.CancelledError:
        if proc.returncode is None:
            with suppress(ProcessLookupError):
                proc.kill()
        await proc.wait()
        raise
    if proc.returncode not in (0, 1):
        return []
    return [Path(line) for line in stdout.decode("utf-8", "replace").splitlines()]
