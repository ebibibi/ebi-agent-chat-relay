"""Tests for recognising a Teams Channel Exporter reply-draft request."""

from __future__ import annotations

import pytest

from claude_discord.teams_exporter import (
    TEAMS_EXPORTER_TAG,
    is_reply_draft_request,
    source_tags,
)


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


def test_a_thread_started_by_the_exporter_is_tagged_from_its_first_prompt() -> None:
    # The session keeps only the start of the first prompt; the marker opens it.
    summary = "Microsoft Teams のスレッドが、サーバー上に1発言1ファイルで同期されています。\n\n同期"
    assert source_tags(summary) == (TEAMS_EXPORTER_TAG,)
    assert source_tags("Fix the login bug") == ()
    assert source_tags(None) == ()
