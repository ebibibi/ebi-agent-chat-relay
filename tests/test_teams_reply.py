"""Tests for the decision behind /api/teams/reply/status."""

from __future__ import annotations

from datetime import datetime, timedelta

from claude_discord.ext import teams_reply
from claude_discord.ext.teams_reply import Draft, evaluate
from claude_discord.ext.teams_sync import IncomingMessage, StoredMessage

NOW = datetime(2026, 11, 4, 12, 0, 0)
BASE = "1793000000000"
AFTER = "1793000000999"
BODY = "ご連絡ありがとうございます。\n\n■ 確認結果\n\n見直したところ、原因はプロキシの設定でした。"
FILE_DRAFT = Draft(text=BODY, source=teams_reply.SOURCE_FILE, updated_at="2026-11-04 11:50:00")


def run(**over) -> dict:
    row = {
        "status": "done",
        "result": "作業メモ: 確認しました",
        "error": None,
        "teams_base_mid": BASE,
        "created_at": (NOW - timedelta(minutes=5)).isoformat(sep=" "),
    }
    row.update(over)
    return row


def msg(mid: str, text: str = "本文", digest: str = "h", author: str = "") -> IncomingMessage:
    return IncomingMessage(mid=mid, hash=digest, text=text, author=author)


def judge(**over) -> teams_reply.ReplyStatus:
    args = {
        "run": run(),
        "running": False,
        "file_draft": FILE_DRAFT,
        "visible": [msg(BASE)],
        "stored": {BASE: StoredMessage(mid=BASE, hash="h")},
        "chain": {},
        "now": NOW,
    }
    args.update(over)
    return evaluate(**args)


def test_no_linked_run_is_none() -> None:
    assert judge(run=None).state == teams_reply.STATE_NONE


def test_a_turn_in_flight_outranks_a_finished_draft() -> None:
    assert judge(running=True).state == teams_reply.STATE_GENERATING


def test_a_saved_body_with_an_unchanged_thread_is_ready() -> None:
    status = judge()
    assert status.state == teams_reply.STATE_READY
    assert status.draft is not None
    assert status.draft.text == BODY


def test_the_final_message_is_never_called_ready() -> None:
    status = judge(file_draft=None)
    assert status.state == teams_reply.STATE_UNVERIFIED
    assert status.draft is not None
    assert status.draft.source == teams_reply.SOURCE_FINAL_MESSAGE


def test_an_anchor_line_is_removed_from_a_final_message_fallback() -> None:
    final = "基準メッセージ: mid 1 / 送信者 A / 末尾「…」\n\n本文です"
    status = judge(file_draft=None, run=run(result=final))
    assert status.draft is not None
    assert status.draft.text == "本文です"


def test_a_new_message_on_screen_makes_the_reply_stale() -> None:
    status = judge(visible=[msg(BASE), msg(AFTER, "追加で質問です", author="相手")])
    assert status.state == teams_reply.STATE_STALE
    assert [(c.mid, c.kind, c.author, c.excerpt) for c in status.changes] == [
        (AFTER, "new", "相手", "追加で質問です")
    ]
    # The old body stays available: the person may still choose to use it.
    assert status.draft is not None


def test_a_message_only_in_the_vault_still_counts() -> None:
    stored = {
        BASE: StoredMessage(mid=BASE, hash="h"),
        AFTER: StoredMessage(mid=AFTER, hash="x"),
    }
    status = judge(visible=[], stored=stored, chain={AFTER: {"author": "相手"}})
    assert status.state == teams_reply.STATE_STALE
    assert status.changes[0].author == "相手"


def test_an_attachment_only_message_is_not_ignored() -> None:
    status = judge(visible=[msg(AFTER, text="")])
    assert status.state == teams_reply.STATE_STALE


def test_an_edited_message_makes_the_reply_stale() -> None:
    status = judge(visible=[msg(BASE, "書き直しました", digest="other")])
    assert status.state == teams_reply.STATE_STALE
    assert status.changes[0].kind == "edited"


def test_a_body_not_rendered_yet_is_not_an_edit() -> None:
    assert judge(visible=[msg(BASE, text="", digest="empty")]).state == teams_reply.STATE_READY


def test_a_message_the_vault_never_saw_at_or_before_the_base_is_not_an_edit() -> None:
    # The vault stops at the early-stop point, so older unknown mids are normal.
    older = "1792000000000"
    assert judge(visible=[msg(older, digest="q"), msg(BASE)]).state == teams_reply.STATE_READY


def test_the_posted_reply_is_not_mistaken_for_a_change() -> None:
    posted = BODY.replace("\n\n", "\n").replace("原因は", "原因は、")
    status = judge(visible=[msg(BASE), msg(AFTER, posted, author="自分")])
    assert status.state == teams_reply.STATE_POSTED


def test_a_customer_message_after_the_posted_reply_is_stale() -> None:
    later = "1793000001000"
    status = judge(visible=[msg(AFTER, BODY), msg(later, "ありがとうございます。別件です")])
    assert status.state == teams_reply.STATE_STALE
    assert [c.mid for c in status.changes] == [later]


def test_an_unrelated_short_message_is_not_the_posted_reply() -> None:
    assert not teams_reply.looks_like_draft("了解です", BODY)
    assert not teams_reply.looks_like_draft("", BODY)


def test_a_failed_run_without_a_body_is_failed_with_the_reason() -> None:
    status = judge(run=run(status="error", result=None, error="API 429"), file_draft=None)
    assert status.state == teams_reply.STATE_FAILED
    assert status.reason == "API 429"


def test_a_body_saved_before_a_failure_is_still_usable() -> None:
    status = judge(run=run(status="error", result=None, error="boom"))
    assert status.state == teams_reply.STATE_READY


def test_a_recent_run_with_no_session_yet_is_generating() -> None:
    # A session may still be queued for a slot.
    status = judge(run=run(status="running", result=None), file_draft=None)
    assert status.state == teams_reply.STATE_GENERATING


def test_an_old_run_with_no_session_is_reported_as_interrupted() -> None:
    created = (NOW - timedelta(hours=2)).isoformat(sep=" ")
    status = judge(run=run(status="running", result=None, created_at=created), file_draft=None)
    assert status.state == teams_reply.STATE_FAILED
    assert status.reason


def test_a_run_with_no_body_anywhere_is_failed_not_empty_ready() -> None:
    assert judge(run=run(result=""), file_draft=None).state == teams_reply.STATE_FAILED


def test_the_contract_names_the_file_and_later_turns() -> None:
    text = teams_reply.render_draft_contract("/vault/t/reply-drafts/a.txt")
    assert "/vault/t/reply-drafts/a.txt" in text
    assert "後のターン" in text
