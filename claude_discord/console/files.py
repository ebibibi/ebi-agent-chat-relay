"""Find a file an agent delivered in a console conversation, safely.

Every part of the URL is untrusted. The key is digits, the token is the hex
the surface minted, the name is a bare filename, and the resolved path must
still sit under the files directory — anything else is "unknown file".
"""

from __future__ import annotations

import re
from pathlib import Path

_KEY = re.compile(r"^\d{1,20}$")
_TOKEN = re.compile(r"^[0-9a-f]{32}$")


def resolve_file(files_dir: Path | None, key: str, token: str, name: str) -> Path | None:
    """The file behind ``/files/{key}/{token}/{name}``, or None."""
    if files_dir is None or not _KEY.match(key) or not _TOKEN.match(token):
        return None
    if not name or name in (".", "..") or "/" in name or "\\" in name or "\x00" in name:
        return None
    root = files_dir.resolve()
    path = (root / key / token / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        return None
    return path
