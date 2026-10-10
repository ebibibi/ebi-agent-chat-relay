"""Which thread messages the console treats as the relay's own activity."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from claude_discord.console.messages import (
    KIND_ACTIVITY,
    KIND_AGENT,
    KIND_HUMAN,
    classify,
    serialize_message,
    with_kind,
)
from claude_discord.discord_ui.embeds import COLOR_ASK, COLOR_TOOL


@pytest.mark.parametrize(
    "content",
    [
        "-# ⏺ Session running",
        "-# 📎 Files attached",
        "[WAIT FINISHED — automatic continuation]\nWait #12 ended",
        "[PR COMPLETION GATE — automatic continuation]",
        "[MESSAGE FROM ANOTHER CLAUDE SESSION — thread 123]\nhello",
        "🟡 <@418192003549888523> The agent has finished — your reply is needed here.",
        "```\n🔗 API: Anthropic API (direct)\n📁 ~ | 💪 opus\n```",
        "```\n🧠 █░░ 13% | ⏱ 5h\n```",
    ],
)
def test_relay_bookkeeping_is_activity(content: str) -> None:
    assert classify(is_bot=True, content=content) == KIND_ACTIVITY


def test_an_embed_only_tool_call_is_activity() -> None:
    embeds = [{"title": "🔧 Bash", "description": "", "color": COLOR_TOOL}]
    assert classify(is_bot=True, content="", embeds=embeds) == KIND_ACTIVITY


def test_a_question_embed_is_for_the_human() -> None:
    embeds = [{"title": "Question", "description": "Which one?", "color": COLOR_ASK}]
    assert classify(is_bot=True, content="", embeds=embeds) == KIND_AGENT


def test_a_rename_notice_is_activity() -> None:
    assert classify(is_bot=True, content="✅ title", message_type="thread_name_change") == (
        KIND_ACTIVITY
    )


def test_an_answer_and_a_human_message_are_shown() -> None:
    assert classify(is_bot=True, content="**Done.** PR #12 merged.") == KIND_AGENT
    assert classify(is_bot=False, content="-# anything a person writes") == KIND_HUMAN
    # A code block that is the answer itself is not the usage footer.
    assert classify(is_bot=True, content="```py\nprint(1)\n```") == KIND_AGENT


def test_attachments_are_shown_even_with_a_status_caption() -> None:
    files = [{"filename": "a.md", "url": "https://cdn/a.md", "size": 3}]
    assert classify(is_bot=True, content="-# 📎 Files attached", attachments=files) == KIND_AGENT


def _message(content: str, *, bot: bool = True, **extra: object) -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        content=content,
        author=SimpleNamespace(display_name="Relay", bot=bot),
        type=SimpleNamespace(name=extra.pop("type", "default")),
        embeds=extra.pop("embeds", []),
        attachments=extra.pop("attachments", []),
        created_at=datetime(2026, 10, 10, tzinfo=UTC),
        jump_url="https://discord.com/channels/1/2/3",
    )


def test_a_console_reply_is_shown_as_the_person_who_wrote_it() -> None:
    out = serialize_message(_message("please retry\n\n-# 🖥️ via Relay Console (me@example.com)"))
    assert out["kind"] == KIND_HUMAN
    assert out["author"] == "me@example.com"
    assert out["content"] == "please retry"


def test_embeds_are_serialized_with_their_colour() -> None:
    embed = SimpleNamespace(title="🔧 Bash", description="ls", colour=SimpleNamespace(value=1))
    empty = SimpleNamespace(title=None, description=None, colour=None)
    out = serialize_message(_message("", embeds=[embed, empty]))
    assert out["kind"] == KIND_ACTIVITY
    assert out["embeds"] == [{"title": "🔧 Bash", "description": "ls", "color": 1}]
    assert out["created_at"] == "2026-10-10T00:00:00+00:00"


def test_a_stored_console_message_gets_a_kind() -> None:
    stored = {"id": 1, "author": "agent", "is_bot": True, "content": "-# ⏺ Session running"}
    out = with_kind(stored)
    assert out["kind"] == KIND_ACTIVITY
    assert out["embeds"] == [] and out["attachments"] == []
    assert with_kind({**stored, "is_bot": False})["kind"] == KIND_HUMAN
