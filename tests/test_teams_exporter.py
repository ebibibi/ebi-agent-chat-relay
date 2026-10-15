"""Tests for recognising a Teams Channel Exporter reply-draft request."""

from __future__ import annotations

import pytest

from claude_discord.teams_exporter import is_reply_draft_request


@pytest.mark.parametrize(
    "text",
    [
        "Microsoft Teams のスレッドが、サーバー上に1発言1ファイルで同期されています。",
        "[Teams Channel Exporter]\nPlease draft a reply.",
        "Source: Teams Channel Exporter",
    ],
)
def test_reply_draft_requests_are_recognised(text: str) -> None:
    assert is_reply_draft_request(text)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "Microsoft Teams の全チャット・全チャネルを、1発言1ファイルでサーバーに同期しました。",
        "Teams の返信を考えて",
    ],
)
def test_other_text_is_not_a_reply_draft(text: str) -> None:
    assert not is_reply_draft_request(text)
