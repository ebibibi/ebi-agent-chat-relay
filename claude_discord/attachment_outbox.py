"""Durable pending attachment requests, acknowledged one file at a time.

A process crash after the remote send but before its checkpoint can still
duplicate the last file: Discord supplies no durable idempotency key for files.
Completed checkpoints, however, are never replayed after a later send fails.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import tempfile
from pathlib import Path
from weakref import WeakValueDictionary

from claude_code_core.frontend import ConversationSurface, Notice, NoticeLevel, OutboundFile

logger = logging.getLogger(__name__)
_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


def _read_paths(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _checkpoint(path: Path, paths: list[str]) -> None:
    fd, temporary = tempfile.mkstemp(prefix=path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write("".join(f"{item}\n" for item in paths))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)


async def deliver_pending(surface: ConversationSurface, marker: Path) -> None:
    """Keep undelivered requests across turns, including newly written markers."""
    key = str(marker.absolute())
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        pending = marker.with_name(marker.name + ".pending")
        try:
            paths = list(dict.fromkeys([*_read_paths(pending), *_read_paths(marker)]))
            if not paths:
                marker.unlink(missing_ok=True)
                pending.unlink(missing_ok=True)
                return
            # Persist before consuming the new request. A crash between these
            # operations is harmless because the merge above deduplicates it.
            _checkpoint(pending, paths)
            marker.unlink(missing_ok=True)
            while paths:
                path = Path(paths[0])
                if not path.is_absolute():
                    path = marker.parent / path
                await surface.deliver_files([OutboundFile(path=str(path), display_name=path.name)])
                paths = paths[1:]
                _checkpoint(pending, paths)
            pending.unlink(missing_ok=True)
        except Exception:
            logger.warning("Attachment delivery is pending; retry on next session completion")
            with contextlib.suppress(Exception):
                await surface.send_notice(
                    Notice(
                        level=NoticeLevel.WARNING,
                        title="Attachments are still pending",
                        body="Pending files will retry on the next completed turn.",
                    )
                )
