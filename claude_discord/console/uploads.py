"""Files a human attaches to a reply: stored next to delivered files, handed to the agent by path.

An upload is a raw body. Images (PNG, JPEG, GIF, WebP) are checked by their
bytes — a PNG header on a GIF body is refused — so the thumbnail and the agent
see what the type says. Any other file is kept as it came, under the name the
browser gave it (reduced to a safe bare filename). Each file lands under
``files_dir/0/<token>/`` so the same ``/files`` route that serves delivered
files serves it back, always as a download. A reply names uploads by that URL,
resolved back to a path through ``resolve_file``, so a reply cannot point the
agent at anything outside the files directory.
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
import uuid
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote

from .files import resolve_file
from .surface import FILES_ROUTE

#: Key directory for human uploads; conversations use their (non-zero) thread key.
UPLOAD_KEY = "0"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_UPLOAD_BYTES = 50 * 1024 * 1024
MAX_ATTACHMENTS_PER_REPLY = 10
MAX_NAME_CHARS = 120

_SIGNATURES: dict[str, tuple[str, tuple[bytes, ...]]] = {
    "image/png": ("png", (b"\x89PNG\r\n\x1a\n",)),
    "image/jpeg": ("jpg", (b"\xff\xd8\xff",)),
    "image/gif": ("gif", (b"GIF87a", b"GIF89a")),
    "image/webp": ("webp", (b"RIFF",)),
}
#: Characters no filename keeps: separators, Windows-reserved, and control characters.
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')


class UploadError(ValueError):
    """An upload the console refuses; ``status`` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


def image_type(content_type: str) -> str | None:
    """The image MIME type an upload claims, or None when it is not an image we preview."""
    mime = content_type.split(";", 1)[0].strip().lower()
    return mime if mime in _SIGNATURES else None


def size_limit(content_type: str) -> int:
    return MAX_IMAGE_BYTES if image_type(content_type) else MAX_UPLOAD_BYTES


def _matches(mime: str, data: bytes) -> bool:
    _, magics = _SIGNATURES[mime]
    if not any(data.startswith(m) for m in magics):
        return False
    return mime != "image/webp" or data[8:12] == b"WEBP"


def safe_name(raw: str | None) -> str | None:
    """A bare, harmless filename from what the browser sent (percent-encoded), or None."""
    if not raw:
        return None
    name = unicodedata.normalize("NFC", unquote(raw)).replace("\\", "/").rsplit("/", 1)[-1]
    name = _UNSAFE.sub("_", name).strip().lstrip(".")
    if not name or set(name) <= {"_", " "}:
        return None
    if len(name) > MAX_NAME_CHARS:
        stem, dot, ext = name.rpartition(".")
        keep = ext[:16] if dot and stem else ""
        name = name[: MAX_NAME_CHARS - len(keep) - 1].rstrip() + ("." + keep if keep else "")
    return name


async def save_upload(
    files_dir: Path, data: bytes, content_type: str, filename: str | None = None
) -> str:
    """Store *data* and return its ``/files`` URL."""
    if not data:
        raise UploadError("the file is empty", 400)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    mime = image_type(content_type)
    if mime is not None:
        if not _matches(mime, data):
            raise UploadError("the file is not the image type it claims to be", 400)
        ext, _ = _SIGNATURES[mime]
        name = safe_name(filename) or f"pasted-{stamp}.{ext}"
        if not name.lower().endswith(f".{ext}") and not (
            ext == "jpg" and name.lower().endswith(".jpeg")
        ):
            name = f"{name}.{ext}"
    else:
        name = safe_name(filename) or f"attachment-{stamp}.bin"
    token = uuid.uuid4().hex
    target = files_dir / UPLOAD_KEY / token / name
    await asyncio.to_thread(_write, target, data)
    # Percent-encoded: a name may hold spaces, "#", "%" or non-ASCII characters.
    return f"{FILES_ROUTE}/{UPLOAD_KEY}/{token}/{quote(name, safe='')}"


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
    return resolve_file(files_dir, *[unquote(p) for p in parts])


def with_attachments(text: str, files: list[tuple[str, Path]]) -> str:
    """The reply as the agent reads it: the text, then each file as a link and a local path."""
    if not files:
        return text
    lines = [f"- [{path.name}]({url}) — {path}" for url, path in files]
    note = "📎 Attached files (open the local path with your file-reading tool):"
    return "\n\n".join(p for p in (text, note + "\n" + "\n".join(lines)) if p)
