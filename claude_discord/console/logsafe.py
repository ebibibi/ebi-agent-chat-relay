"""Client-supplied values made safe for a log line."""

from __future__ import annotations

import re

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def q(value: object) -> str:
    """*value* with no line breaks or control characters, at most 200 characters.

    A line break in a logged value could forge a log entry. ``str.replace``
    comes first because it is the form static analysers recognise.
    """
    text = str(value).replace("\r", " ").replace("\n", " ")
    return _CONTROL.sub(" ", text)[:200]
