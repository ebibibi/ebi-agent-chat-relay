"""What a thread family produced, for the "📦 Deliverables" section of the situation panel.

A parent that runs children ends up with its prototypes, reports and screenshots spread over
every child's messages; the person had to ask where to find them (#1537). The panel lists
them in one place, from two sources:

- **Collected, needing no agent:** files the relay's own bot attached in each family thread
  (what ``.ccdb-attachments-*`` delivers), and files a Console conversation delivered
  (``📎 Files:`` links into its own file area). Only these two, so a message cannot put an
  outside address or another thread's file on the list.
- **Declared by an agent:** ``deliverables`` in a family member's situation record: things
  that are not files (a PR, a release, a note), or a title and a "superseded" flag for a
  file it attached, matched by file name.

Discord threads are read through the API, so each thread's scan is cached until the thread
gets a new message or :data:`CACHE_SECONDS` pass, whichever is first. The attachment URLs
Discord hands out are signed and expire after about a day; the cache is far shorter.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from typing import Any

from .request_files import FILES_MARKER, _attachment_url
from .surface import FILES_ROUTE

logger = logging.getLogger(__name__)

#: Raw messages read per chat thread: a long child still shows its early prototypes.
DISCORD_SCAN = 300
#: Console conversation messages read per thread.
CONSOLE_SCAN = 400
#: Threads of one family read at most; a larger family shows its most recent members.
MAX_FAMILY = 30
MAX_FILES_PER_THREAD = 60
CACHE_SECONDS = 600
#: Chat threads read at once, so a family of twenty does not fire twenty API reads together.
PARALLEL_READS = 4

_LINK = re.compile(r"\[([^\]\n]{1,200})\]\((/console/api/files/(\d+)/[0-9a-f]{32}/[^)\s]{1,300})\)")


def _iso(value: Any) -> str:
    """ISO 8601 of a datetime, or the string as given (console rows store text)."""
    iso = getattr(value, "isoformat", None)
    return str(iso()) if callable(iso) else str(value or "")


def bot_files(messages: Iterable[Any], *, thread_id: int, bot_id: int | None) -> list[dict]:
    """Files the relay's own bot attached in a chat thread, newest first.

    Webhook posts and other bots are skipped: the list is the relay's word that this
    thread delivered the file. Only Discord's attachment hosts are accepted.
    """
    found: list[dict[str, Any]] = []
    if bot_id is None:
        return found
    for message in messages:
        author = getattr(message, "author", None)
        if getattr(author, "id", None) != bot_id or getattr(message, "webhook_id", None):
            continue
        for attachment in getattr(message, "attachments", None) or []:
            url = _attachment_url(getattr(attachment, "url", ""))
            if url is None:
                continue
            size = getattr(attachment, "size", None)
            found.append(
                {
                    "source": "file",
                    "thread_id": str(thread_id),
                    "name": str(getattr(attachment, "filename", "") or "file"),
                    "url": url,
                    "content_type": getattr(attachment, "content_type", None),
                    "size": size if isinstance(size, int) else None,
                    "at": _iso(getattr(message, "created_at", "")),
                }
            )
            if len(found) >= MAX_FILES_PER_THREAD:
                return found
    return found


def console_files(thread_key: int, messages: Iterable[Any]) -> list[dict]:
    """Files a Console conversation delivered (``📎 Files:`` messages), newest first."""
    found: list[dict[str, Any]] = []
    seen: set[str] = set()
    for message in messages:
        content = str(getattr(message, "content", "") or "")
        if not content.startswith(FILES_MARKER):
            continue
        for name, url, owner in _LINK.findall(content):
            if owner != str(thread_key) or not url.startswith(FILES_ROUTE) or url in seen:
                continue
            seen.add(url)
            found.append(
                {
                    "source": "file",
                    "thread_id": str(thread_key),
                    "name": name,
                    "url": url,
                    "content_type": None,
                    "size": None,
                    "at": _iso(getattr(message, "created_at", "")),
                }
            )
            if len(found) >= MAX_FILES_PER_THREAD:
                return found
    return found


def merge(files: list[dict], records: list[Any]) -> list[dict]:
    """One list, newest first: the collected files with what agents declared folded in.

    A declaration naming a ``file`` gives the newest file of that name (in its own thread
    first, then anywhere in the family) its title, note and superseded flag; one that matches
    nothing is still listed, marked ``missing``, so a typo is visible instead of lost.
    """
    files = sorted((dict(f) for f in files), key=lambda f: f["at"], reverse=True)
    declared: list[dict[str, Any]] = []
    for record in records:
        thread = str(record.thread_id)
        for entry in record.deliverables:
            if entry.get("file"):
                target = _match(files, entry["file"], thread)
                if target is not None:
                    _annotate(target, entry)
                    if not entry.get("url"):
                        continue
            row = {"source": "declared", "thread_id": thread, **entry}
            row["at"] = entry.get("at") or record.updated_at
            if entry.get("file") and not entry.get("url"):
                row["missing"] = True
            declared.append(row)
    # Stable: a record's own entries keep the order the agent wrote them in.
    return sorted([*files, *declared], key=lambda e: e["at"], reverse=True)


def _match(files: list[dict], name: str, thread: str) -> dict | None:
    own = [f for f in files if f["name"] == name and f["thread_id"] == thread]
    anywhere = [f for f in files if f["name"] == name]
    return (own or anywhere or [None])[0]


def _annotate(target: dict, entry: dict) -> None:
    for key in ("title", "note", "kind"):
        if entry.get(key):
            target[key] = entry[key]
    if entry.get("superseded"):
        target["superseded"] = True


def family_of(thread_id: int, parents: dict[int, int]) -> list[int]:
    """The root of *thread_id*'s family and every descendant, root first.

    *parents* maps a child thread to the thread that spawned it. A loop in it (which
    lineage never records, but a hand-edited database could) cannot hang the walk.
    """
    root, seen = int(thread_id), {int(thread_id)}
    while root in parents and parents[root] not in seen:
        root = parents[root]
        seen.add(root)
    children: dict[int, list[int]] = {}
    for child, parent in parents.items():
        children.setdefault(parent, []).append(child)
    order, queue, visited = [], [root], {root}
    while queue and len(order) < MAX_FAMILY:
        current = queue.pop(0)
        order.append(current)
        for child in sorted(children.get(current, []), reverse=True):
            if child not in visited:
                visited.add(child)
                queue.append(child)
    return order


@dataclass
class _Cached:
    expires: float
    last_message_id: int | None
    files: list[dict]


ThreadReader = Callable[[int], Awaitable[Any]]


class DeliverableCollector:
    """Reads a family's files (cached per thread) and merges what its agents declared."""

    def __init__(
        self,
        *,
        lineage: Callable[[], Awaitable[dict[int, int]]],
        conversations: Callable[[], Any],
        chat_thread: ThreadReader,
        bot_id: Callable[[], int | None],
        records: Callable[[list[int]], Awaitable[list[Any]]],
    ) -> None:
        self._lineage = lineage
        self._conversations = conversations
        self._chat_thread = chat_thread
        self._bot_id = bot_id
        self._records = records
        self._cache: dict[int, _Cached] = {}
        self._gate = asyncio.Semaphore(PARALLEL_READS)

    async def for_thread(self, thread_id: int) -> dict[str, Any]:
        family = family_of(thread_id, await self._lineage())
        results = await asyncio.gather(*(self._files(t) for t in family))
        files = [f for found, _ in results for f in found]
        unread = [str(t) for t, (_, ok) in zip(family, results, strict=True) if not ok]
        try:
            records = await self._records(family)
        except Exception:
            logger.exception("console: deliverables of %s: records unreadable", thread_id)
            records = []
        return {
            "deliverables": merge(files, records),
            "threads": [str(t) for t in family],
            "unread": unread,
        }

    async def _files(self, thread_id: int) -> tuple[list[dict], bool]:
        """The thread's files and whether it could be read at all."""
        try:
            conversations = self._conversations()
            if conversations is not None and await conversations.get(thread_id) is not None:
                history = await conversations.history(thread_id, CONSOLE_SCAN)
                return console_files(thread_id, reversed(history)), True
            return await self._chat_files(thread_id)
        except Exception:
            logger.exception("console: deliverables of thread %s unreadable", thread_id)
            return [], False

    async def _chat_files(self, thread_id: int) -> tuple[list[dict], bool]:
        thread = await self._chat_thread(thread_id)
        if thread is None:
            return [], False
        last = getattr(thread, "last_message_id", None)
        cached = self._cache.get(thread_id)
        if cached and cached.expires > time.monotonic() and cached.last_message_id == last:
            return cached.files, True
        async with self._gate:
            history = [m async for m in thread.history(limit=DISCORD_SCAN)]
        files = bot_files(history, thread_id=thread_id, bot_id=self._bot_id())
        self._cache[thread_id] = _Cached(time.monotonic() + CACHE_SECONDS, last, files)
        return files, True
