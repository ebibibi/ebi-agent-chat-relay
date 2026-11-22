"""Images and videos attached to Console page items, kept next to the database.

A generator uploads the file once and lists its URL on an item. Only image and video types
are kept, each recognised by its bytes rather than by what the uploader says, so the type the
Console serves is the type the file really is. Every part of a file URL is untrusted: the slug
is the page's, the token the hex minted here, the name a bare filename, and the resolved path
must stay under the page's directory.
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import unicodedata
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, unquote

#: Where the Console serves a page's files. The slug and token follow; see :func:`file_url`.
FILES_PREFIX = "/console/api/pages"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
#: A short video for a social network. Large enough for a minute of phone footage.
MAX_VIDEO_BYTES = 200 * 1024 * 1024
MAX_NAME_CHARS = 120
MAX_NAME_BYTES = 200
_HEAD_BYTES = 16

_TOKEN = re.compile(r"[0-9a-f]{32}")
_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')

#: MIME type -> (kind, extension). The only types a page keeps.
TYPES: dict[str, tuple[str, str]] = {
    "image/png": ("image", "png"),
    "image/jpeg": ("image", "jpg"),
    "image/gif": ("image", "gif"),
    "image/webp": ("image", "webp"),
    "video/mp4": ("video", "mp4"),
    "video/quicktime": ("video", "mov"),
    "video/webm": ("video", "webm"),
}
_EXTENSIONS = {
    "png": "image",
    "jpg": "image",
    "jpeg": "image",
    "gif": "image",
    "webp": "image",
    "mp4": "video",
    "m4v": "video",
    "mov": "video",
    "webm": "video",
}


class PageFileError(ValueError):
    """An upload that is refused; ``status`` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class StoredFile:
    url: str
    name: str
    mime: str
    kind: str
    size: int

    def as_dict(self) -> dict[str, object]:
        return {
            "url": self.url,
            "name": self.name,
            "type": self.mime,
            "kind": self.kind,
            "size": self.size,
        }


def sniff(head: bytes) -> str | None:
    """The MIME type the first bytes of a file prove, or None."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return "image/webp"
    if head[4:8] == b"ftyp":
        return "video/quicktime" if head[8:12] == b"qt  " else "video/mp4"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "video/webm"
    return None


def kind_of_name(name: str) -> str:
    """ "image", "video" or "file", judged by a name's extension (for links we never fetch)."""
    base = name.lower().split("?", 1)[0].split("#", 1)[0].rsplit("/", 1)[-1]
    return _EXTENSIONS.get(base.rsplit(".", 1)[-1], "file") if "." in base else "file"


def declared_type(content_type: str) -> str:
    mime = content_type.split(";", 1)[0].strip().lower()
    if mime not in TYPES:
        allowed = ", ".join(sorted(TYPES))
        raise PageFileError(f"only images and videos can be attached ({allowed})", 415)
    return mime


def size_limit(mime: str) -> int:
    return MAX_IMAGE_BYTES if TYPES[mime][0] == "image" else MAX_VIDEO_BYTES


def safe_name(raw: str | None, ext: str) -> str:
    """A bare, harmless filename ending in *ext*, from the (percent-encoded) name given.

    Bounded in characters for display and in UTF-8 bytes for the disk: most file systems stop
    at 255 bytes, which a Japanese name reaches at 85 characters.
    """
    name = unicodedata.normalize("NFC", unquote(raw or "")).replace("\\", "/").rsplit("/", 1)[-1]
    name = _UNSAFE.sub("_", name).strip().lstrip(".")
    if not name or set(name) <= {"_", " "}:
        name = "attachment"
    stem, dot, _ = name.rpartition(".")
    if dot and stem and kind_of_name(name) != "file":
        name = stem  # the bytes decide the extension, not the uploader
    name = name[: MAX_NAME_CHARS - len(ext) - 1]
    budget = MAX_NAME_BYTES - len(ext) - 1
    while len(name.encode("utf-8")) > budget:
        name = name[:-1]
    return f"{name.rstrip() or 'attachment'}.{ext}"


def file_url(slug: str, token: str, name: str) -> str:
    return f"{FILES_PREFIX}/{slug}/files/{token}/{quote(name, safe='')}"


def parse_url(url: str) -> tuple[str, str, str] | None:
    """``(slug, token, name)`` of a page file URL, or None when *url* is not one."""
    prefix = f"{FILES_PREFIX}/"
    if not url.startswith(prefix):
        return None
    parts = url[len(prefix) :].split("/")
    if len(parts) != 4 or parts[1] != "files":
        return None
    return parts[0], parts[2], unquote(parts[3])


class PageFiles:
    """The files of every page, under ``root/<slug>/<token>/<name>``."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def resolve(self, slug: str, token: str, name: str) -> Path | None:
        """The stored file, or None for anything that is not one."""
        if not _SLUG.fullmatch(slug) or not _TOKEN.fullmatch(token):
            return None
        if not name or name in (".", "..") or "/" in name or "\\" in name or "\x00" in name:
            return None
        page = (self.root / slug).resolve()
        path = (page / token / name).resolve()
        if not path.is_relative_to(page) or not path.is_file():
            return None
        return path

    def describe(self, slug: str, token: str, name: str) -> StoredFile | None:
        """What a stored file is, judged again by its bytes, or None."""
        path = self.resolve(slug, token, name)
        if path is None:
            return None
        with path.open("rb") as handle:
            mime = sniff(handle.read(_HEAD_BYTES))
        if mime is None:
            return None
        return StoredFile(
            file_url(slug, token, path.name), path.name, mime, TYPES[mime][0], path.stat().st_size
        )

    async def save(
        self, slug: str, chunks: AsyncIterator[bytes], content_type: str, filename: str | None
    ) -> StoredFile:
        """Stream an upload to disk, then keep it only if its bytes are the type it claims."""
        claimed = declared_type(content_type)
        limit = size_limit(claimed)
        token = uuid.uuid4().hex
        staging = self.root / slug / f".upload-{token}"
        staging.parent.mkdir(parents=True, exist_ok=True)
        try:
            size = 0
            head = b""
            with staging.open("wb") as handle:
                async for chunk in chunks:
                    size += len(chunk)
                    if size > limit:
                        raise PageFileError(
                            f"the file is larger than {limit // (1024 * 1024)} MB", 413
                        )
                    if len(head) < _HEAD_BYTES:
                        head += chunk[: _HEAD_BYTES - len(head)]
                    await asyncio.to_thread(handle.write, chunk)
            if size == 0:
                raise PageFileError("the file is empty", 400)
            actual = sniff(head)
            if actual is None or TYPES[actual][0] != TYPES[claimed][0]:
                raise PageFileError("the file is not the type it claims to be", 400)
            if TYPES[claimed][0] == "image" and actual != claimed:
                raise PageFileError("the file is not the image type it claims to be", 400)
            name = safe_name(filename, TYPES[actual][1])
            target = self.root / slug / token / name
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staging, target)
            except OSError:
                shutil.rmtree(target.parent, ignore_errors=True)
                raise
        finally:
            staging.unlink(missing_ok=True)
        return StoredFile(file_url(slug, token, name), name, actual, TYPES[actual][0], size)

    def remove_page(self, slug: str) -> None:
        if _SLUG.fullmatch(slug):
            shutil.rmtree(self.root / slug, ignore_errors=True)
