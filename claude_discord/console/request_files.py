"""The files a thread delivered, for the decision cards that ask the person to look at them.

A console conversation keeps a delivered file as a message ``📎 Files:`` followed by
Markdown links into :data:`~.surface.FILES_ROUTE`. A card that says "review the draft" is only
answerable when the draft is on it, so the secretary home and canvas mode show those files on
the card (#1374). Only links into the thread's own file area are taken: a message cannot point a
card at another thread's files or at an outside address.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from .surface import FILES_ROUTE

FILES_MARKER = "📎 Files:"
#: How far back a card looks for delivered files, and how many it shows.
SCAN_MESSAGES = 40
MAX_FILES = 6
_LINK = re.compile(r"\[([^\]\n]{1,200})\]\((/console/api/files/(\d+)/[0-9a-f]{32}/[^)\s]{1,300})\)")


def delivered_files(thread_key: int, contents: Iterable[str]) -> list[dict[str, str]]:
    """``{"name", "url"}`` of the files in *contents* (newest first), this thread's only."""
    found: list[dict[str, str]] = []
    seen: set[str] = set()
    for content in contents:
        if not content.startswith(FILES_MARKER):
            continue
        for name, url, owner in _LINK.findall(content):
            if owner != str(thread_key) or not url.startswith(FILES_ROUTE) or url in seen:
                continue
            seen.add(url)
            found.append({"name": name, "url": url})
            if len(found) >= MAX_FILES:
                return found
    return found


async def files_for(conversations: Any, thread_key: int) -> list[dict[str, str]]:
    """The files a console conversation delivered recently; [] for anything else."""
    if conversations is None or await conversations.get(thread_key) is None:
        return []
    history = await conversations.history(thread_key, SCAN_MESSAGES)
    newest_first = [m.content for m in reversed(history)]
    return delivered_files(thread_key, newest_first)
