"""File attachment sender for session-complete events.

Sends files as Discord attachments when Claude has been asked to deliver them.
Files are specified by writing their paths to ``.ccdb-attachments`` in the
working directory; the bot reads this marker and sends the listed files.

Discord limits: 10 files per message, 8 MB per file (non-boosted server).
Files exceeding the size limit are skipped.
"""

from __future__ import annotations

import contextlib
import io
import logging
from pathlib import Path

import discord

logger = logging.getLogger(__name__)

# Per-file size limit matching Discord's default upload cap for non-boosted servers.
_MAX_FILE_BYTES = 8 * 1024 * 1024  # 8 MB

# Discord API hard limit: 10 files per message.
_MAX_FILES_PER_MESSAGE = 10


def _relative_path(file_path: str, working_dir: str | None) -> str:
    """Return a display name for the file.

    Prefers a path relative to *working_dir* so the user sees ``src/foo.py``
    rather than the full absolute path.  Falls back to the bare filename when
    the file lives outside *working_dir* or no *working_dir* is given.
    """
    if working_dir:
        with contextlib.suppress(ValueError):
            return Path(file_path).relative_to(working_dir).as_posix()
    return Path(file_path).name


def collect_discord_files(
    file_paths: list[str],
    working_dir: str | None,
    max_bytes: int = _MAX_FILE_BYTES,
) -> list[discord.File]:
    """Read qualifying files from disk and return ``discord.File`` objects.

    Each file is read into an in-memory ``BytesIO`` buffer so that callers
    can safely delete the source file (e.g. worktree cleanup) after this
    function returns without invalidating the attachment.

    Skips files that:
    * Do not exist on disk
    * Exceed *max_bytes* (default: 8 MB, matching Discord's non-boosted limit)

    Binary files (images, ZIPs, PDFs, etc.) are accepted — Discord supports them.

    Args:
        file_paths: Absolute or working-dir-relative paths to attach.
        working_dir: Base directory used to compute relative display names.
        max_bytes: Per-file size limit.

    Returns:
        List of ``discord.File`` objects, ready to pass to ``thread.send()``.
    """
    result: list[discord.File] = []

    for path_str in file_paths:
        path = Path(path_str)

        if not path.exists() or not path.is_file():
            logger.debug("Skipping missing or non-file path: %s", path)
            continue

        try:
            size = path.stat().st_size
        except OSError:
            logger.debug("Cannot stat file, skipping: %s", path)
            continue

        if size > max_bytes:
            logger.info(
                "Skipping file exceeding size limit (%d > %d bytes): %s",
                size,
                max_bytes,
                path,
            )
            continue

        try:
            data = path.read_bytes()
        except OSError:
            logger.debug("Cannot read file, skipping: %s", path, exc_info=True)
            continue

        display_name = _relative_path(path_str, working_dir)
        result.append(discord.File(io.BytesIO(data), filename=display_name))

    return result


def collect_discord_files_from_blobs(
    blobs: list[tuple[str, bytes]],
    max_bytes: int = _MAX_FILE_BYTES,
) -> list[discord.File]:
    """Build ``discord.File`` objects from in-memory ``(filename, bytes)`` pairs.

    The in-memory twin of :func:`collect_discord_files`: used when the file
    bytes are already in hand (e.g. downloaded from a remote API and forwarded
    over ``/api/spawn``) rather than sitting on disk.

    Skips blobs that exceed *max_bytes* (default: 8 MB, Discord's non-boosted
    upload cap). The display filename is reduced to its basename so a
    caller-supplied path can never embed directory components.

    Args:
        blobs: ``(filename, data)`` pairs to attach.
        max_bytes: Per-file size limit.

    Returns:
        List of ``discord.File`` objects, ready to pass to ``thread.send()``.
    """
    result: list[discord.File] = []

    for filename, data in blobs:
        if len(data) > max_bytes:
            logger.info(
                "Skipping blob exceeding size limit (%d > %d bytes): %s",
                len(data),
                max_bytes,
                filename,
            )
            continue
        display_name = Path(filename).name or "attachment"
        result.append(discord.File(io.BytesIO(data), filename=display_name))

    return result


async def send_file_blobs(
    thread: discord.Thread,
    blobs: list[tuple[str, bytes]],
    content: str | None = "-# 📎 Files attached",
) -> None:
    """Send in-memory ``(filename, bytes)`` attachments to *thread* in batches.

    The in-memory twin of :func:`send_files`. Sends in batches of up to 10
    (Discord API limit); any Discord error is suppressed so a network hiccup
    never propagates to the caller. Does nothing when *blobs* is empty or every
    blob fails qualification (oversized).

    Args:
        thread: Discord thread to post attachments to.
        blobs: ``(filename, data)`` pairs to send.
        content: Message text for the first batch (subsequent batches send no
            text). Pass ``None`` to attach files with no accompanying message.
    """
    if not blobs:
        return

    files = collect_discord_files_from_blobs(blobs)
    if not files:
        return

    for i in range(0, len(files), _MAX_FILES_PER_MESSAGE):
        batch = files[i : i + _MAX_FILES_PER_MESSAGE]
        try:
            await thread.send(
                content=content if i == 0 else None,
                files=batch,
            )
        except Exception:
            logger.warning(
                "Failed to send blob attachment batch %d/%d to Discord",
                i // _MAX_FILES_PER_MESSAGE + 1,
                -(-len(files) // _MAX_FILES_PER_MESSAGE),
                exc_info=True,
            )


async def send_files(
    thread: discord.Thread,
    file_paths: list[str],
    working_dir: str | None,
    *,
    require_delivery: bool = False,
) -> None:
    """Send files as Discord attachments.

    Called from ``EventProcessor._on_complete()`` when the ``.ccdb-attachments``
    marker file is present.  Sends in batches of up to 10 (Discord API limit).
    Any Discord error is suppressed so that a network hiccup never kills the
    session-complete flow.

    Does nothing when *file_paths* is empty or all files fail qualification
    (binary / missing / oversized).

    Args:
        thread: Discord thread to post attachments to.
        file_paths: Paths of files to send.
        working_dir: Runner working directory for relative display names.
        require_delivery: Raise on a missing/oversized file or failed send so
            durable callers can retain the request. False keeps legacy behavior.
    """
    if not file_paths:
        return

    files = collect_discord_files(file_paths, working_dir)
    if require_delivery and len(files) != len(file_paths):
        for file in files:
            file.close()
        raise OSError("One or more attachments could not be read or exceed the upload limit")
    if not files:
        return

    for i in range(0, len(files), _MAX_FILES_PER_MESSAGE):
        batch = files[i : i + _MAX_FILES_PER_MESSAGE]
        try:
            await thread.send(
                content="-# 📎 Files attached" if i == 0 else None,
                files=batch,
            )
        except Exception:
            logger.warning(
                "Failed to send file attachment batch %d/%d to Discord",
                i // _MAX_FILES_PER_MESSAGE + 1,
                -(-len(files) // _MAX_FILES_PER_MESSAGE),
                exc_info=True,
            )
            if require_delivery:
                raise
        finally:
            for file in batch:
                file.close()
