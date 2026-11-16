"""The Console asks the browser to resize the layout for the soft keyboard (#1426).

With Chrome's default (``resizes-visual``) the layout viewport keeps the height it
had with the address bar showing, and on Android the composer ended up behind the
keyboard by about that much. ``resizes-content`` makes the page itself shrink.
"""

from __future__ import annotations

import re
from pathlib import Path

INDEX = Path(__file__).parent.parent / "claude_discord" / "console" / "static" / "index.html"


def _viewport_content() -> str:
    match = re.search(r'<meta name="viewport" content="([^"]+)"', INDEX.read_text(encoding="utf-8"))
    assert match, "index.html has no viewport meta"
    return match.group(1)


def test_keyboard_resizes_the_layout() -> None:
    assert "interactive-widget=resizes-content" in _viewport_content()


def test_existing_viewport_settings_are_kept() -> None:
    content = _viewport_content()
    for part in ("width=device-width", "initial-scale=1", "viewport-fit=cover"):
        assert part in content
