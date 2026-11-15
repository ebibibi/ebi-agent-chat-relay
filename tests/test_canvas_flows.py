"""Canvas flows (#1374): a way back to every source thread; review cards carry what to review."""

from __future__ import annotations

import os
import tempfile

import pytest

from claude_discord.canvas_widget import WidgetError, parse_widget
from claude_discord.console.canvas_repo import CanvasRepository
from claude_discord.console.request_files import delivered_files
from claude_discord.decision_request import (
    MATERIAL_FILES,
    MATERIAL_INLINE,
    MATERIAL_LINK,
    MATERIAL_NONE,
    DecisionRequestError,
    material_state,
    parse_request,
)

DRAFT = "お世話になっております。" * 20


@pytest.mark.parametrize(
    ("materials", "files", "expected"),
    [
        ("添付の claude-screenshare.txt（Obsidian 09_Outputs/reports/x.md）", [], MATERIAL_NONE),
        ("添付の claude-screenshare.txt", [{"name": "a.txt", "url": "/x"}], MATERIAL_FILES),
        ("https://youtu.be/8HmFwe6mnjw", [], MATERIAL_LINK),
        (DRAFT, [], MATERIAL_INLINE),
        ("```\nshort code\n```", [], MATERIAL_INLINE),
        ("- old\n+ new", [], MATERIAL_INLINE),
        ("", [], MATERIAL_NONE),
        (None, [], MATERIAL_NONE),
    ],
)
def test_material_state_is_decided_by_rules(materials, files, expected) -> None:
    assert material_state(materials, files) == expected


def _review(materials: str) -> dict:
    return {
        "title": "回答案を確認してください",
        "context": "Claudeの画面共有が消えた件。送る前の確認で、送るのは人。",
        "options": [{"label": "OK", "effect": "このまま送れる形にする", "recommended": True}],
        "deadline": "今日中。過ぎても害はない",
        "detail": {
            "about": "a",
            "now": "b",
            "ask": "c",
            "choices": "d",
            "recommendation": "e",
            "materials": materials,
            "deadline": "g",
        },
    }


def test_a_review_that_only_points_at_an_attachment_is_refused() -> None:
    with pytest.raises(DecisionRequestError) as caught:
        parse_request(_review("添付の claude-screenshare.txt"), kind="review")
    assert any("thing to review" in p for p in caught.value.problems)
    assert parse_request(_review(DRAFT), kind="review").detail["materials"] == DRAFT
    # A question is not a review: a short material is fine there.
    question = _review("月約5ドル")
    question["options"].append({"label": "やめる", "effect": "何も変えない", "recommended": False})
    assert parse_request(question, kind="waiting")


def test_delivered_files_are_this_threads_own_links_only() -> None:
    mine = "/console/api/files/42/" + "a" * 32 + "/draft.md"
    other = "/console/api/files/7/" + "b" * 32 + "/x.md"
    contents = [
        "plain text [not a file](/console/api/files/42/" + "c" * 32 + "/y.md)",
        f"📎 Files:\n- [draft.md]({mine})\n- [x.md]({other})\n- [evil](https://example.com/a)",
        f"📎 Files:\n- [draft.md]({mine})",
    ]
    assert delivered_files(42, contents) == [{"name": "draft.md", "url": mine}]


def test_widgets_name_their_source_thread() -> None:
    spec = parse_widget(
        {"kind": "markdown", "title": "回答案", "data": {"text": DRAFT}, "thread_id": "123"}
    )
    assert spec.thread_id == "123"
    table = parse_widget(
        {
            "kind": "table",
            "title": "待ち",
            "data": {"columns": ["件"], "rows": [["A"], ["B"]], "row_threads": ["5", None]},
        }
    )
    assert table.data["row_threads"] == ["5", None]
    with pytest.raises(WidgetError):
        parse_widget({"kind": "markdown", "title": "t", "data": {"text": "x"}, "thread_id": "x1"})
    with pytest.raises(WidgetError):
        parse_widget(
            {
                "kind": "table",
                "title": "t",
                "data": {"columns": ["a"], "rows": [["1"]], "row_threads": ["1", "2"]},
            }
        )


async def test_the_source_thread_is_stored_with_the_widget() -> None:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        repo = CanvasRepository(path)
        await repo.init_db()
        spec = parse_widget(
            {"kind": "markdown", "title": "t", "data": {"text": "x"}, "thread_id": "99"}
        )
        widget, _ = await repo.put_widget("k", spec)
        shown = widget.as_dict()
        assert shown["thread_id"] == "99" and "_thread_id" not in shown["data"]
    finally:
        os.unlink(path)


def test_a_chat_threads_attachments_are_this_bots_own_cdn_files_only() -> None:
    from types import SimpleNamespace as Ns

    from claude_discord.console.request_files import attachment_files

    me, other_bot, person = Ns(id=1, bot=True), Ns(id=2, bot=True), Ns(id=3, bot=False)
    good = "https://cdn.discordapp.com/attachments/1/2/draft.md?ex=1"

    def msg(author, url, name="f", webhook=None):
        return Ns(author=author, webhook_id=webhook, attachments=[Ns(filename=name, url=url)])

    messages = [
        msg(person, "https://cdn.discordapp.com/attachments/9/9/mine.png"),
        msg(other_bot, "https://cdn.discordapp.com/attachments/9/9/spoof.md"),
        msg(me, "https://cdn.discordapp.com/attachments/9/9/hook.md", webhook=5),
        msg(me, "https://evil.example/cdn.discordapp.com/x.md"),
        msg(me, "https://cdn.discordapp.com.evil.example/x.md"),
        msg(me, "javascript:alert(1)"),
        msg(me, good, "draft.md"),
    ]
    assert attachment_files(messages, bot_id=1) == [{"name": "draft.md", "url": good}]
    assert attachment_files(messages, bot_id=None) == []
