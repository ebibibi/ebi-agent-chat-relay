"""Recognise a Teams Channel Exporter reply-draft request.

The browser extension asks for a reply to a Teams thread through
``/api/ingest``. Its request envelope is fixed, so the text alone tells a reply
draft apart from every other ingest (the monthly report sweep, for one, says
something else). Two policies hang off this: such a thread is never classified
done by a model, and it lands on the console board at P0, because someone in
Teams is waiting for the answer now.
"""

from __future__ import annotations

#: Phrases only a reply-draft request contains, compared case-insensitively.
REPLY_DRAFT_MARKERS = (
    "microsoft teams のスレッドが、サーバー上に1発言1ファイルで同期されています",
    "[teams channel exporter]",
    "source: teams channel exporter",
)

#: The console priority a reply draft starts at: P0, "must happen today".
REPLY_DRAFT_PRIORITY = 0


def is_reply_draft_request(text: str) -> bool:
    """Whether *text* is the exporter's request for a reply draft."""
    folded = text.casefold()
    return any(marker in folded for marker in REPLY_DRAFT_MARKERS)
