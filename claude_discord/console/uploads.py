"""Images a human pastes into a reply: stored next to delivered files, handed to the agent by path.

An upload is a raw image body. The bytes decide what it is — a PNG header on a
GIF body is refused — and only the image types an agent can read are kept. The
file lands under ``files_dir/0/<token>/`` so the same ``/files`` route that
serves delivered files previews it, and a reply names it by that URL. The URL
is resolved back to a path through ``resolve_file``, so a reply cannot point
the agent at anything outside the files directory.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from pathlib import Path

from .files import resolve_file
from .surface import FILES_ROUTE

#: Key directory for human uploads; conversations use their (non-zero) thread key.
UPLOAD_KEY = "0"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_IMAGES_PER_REPLY = 6

_SIGNATURES: dict[str, tuple[str, tuple[bytes, ...]]] = {
    "image/png": ("png", (b"\x89PNG\r\n\x1a\n",)),
    "image/jpeg": ("jpg", (b"\xff\xd8\xff",)),
    "image/gif": ("gif", (b"GIF87a", b"GIF89a")),
    "image/webp": ("webp", (b"RIFF",)),
}


class UploadError(ValueError):
    """An upload the console refuses; ``status`` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


def accepted_type(content_type: str) -> str:
    """The MIME type without parameters, or UploadError(415) if it is not an image we keep."""
    mime = content_type.split(";", 1)[0].strip().lower()
    if mime not in _SIGNATURES:
        raise UploadError("only PNG, JPEG, GIF and WebP images can be attached", 415)
    return mime


def _matches(mime: str, data: bytes) -> bool:
    _, magics = _SIGNATURES[mime]
    if not any(data.startswith(m) for m in magics):
        return False
    return mime != "image/webp" or data[8:12] == b"WEBP"


async def save_upload(files_dir: Path, data: bytes, mime: str) -> str:
    """Store *data* and return its ``/files`` URL."""
    if not data:
        raise UploadError("the image is empty", 400)
    if not _matches(mime, data):
        raise UploadError("the file is not the image type it claims to be", 400)
    ext, _ = _SIGNATURES[mime]
    token = uuid.uuid4().hex
    name = f"pasted-{datetime.now().strftime('%Y%m%d-%H%M%S')}.{ext}"
    target = files_dir / UPLOAD_KEY / token / name
    await asyncio.to_thread(_write, target, data)
    return f"{FILES_ROUTE}/{UPLOAD_KEY}/{token}/{name}"


def _write(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)


def resolve_upload(files_dir: Path | None, url: str) -> Path | None:
    """The stored file behind an upload URL, or None for anything else."""
    prefix = f"{FILES_ROUTE}/"
    if not isinstance(url, str) or not url.startswith(prefix):
        return None
    parts = url[len(prefix) :].split("/")
    if len(parts) != 3 or parts[0] != UPLOAD_KEY:
        return None
    return resolve_file(files_dir, *parts)


def with_images(text: str, images: list[tuple[str, Path]]) -> str:
    """The reply as the agent reads it: the text, then each image as a preview link and a path."""
    if not images:
        return text
    lines = [f"- [{path.name}]({url}) — {path}" for url, path in images]
    note = "📎 Pasted images (open the local path with your file-reading tool):"
    return "\n\n".join(p for p in (text, note + "\n" + "\n".join(lines)) if p)
