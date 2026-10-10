"""Board assembly: whose move it is, which list, which parent, which order."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from claude_discord.console.board import (
    BUCKET_AI,
    BUCKET_DONE,
    BUCKET_IDLE,
    BUCKET_ME,
    BUCKET_SNOOZED,
    BUCKET_TODO,
    ThreadSnapshot,
    build_board,
    clean_title,
)
from claude_discord.console.work_repo import WorkItem

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)


def overlay(item_id: str, **fields) -> WorkItem:
    base = {
        "id": item_id,
        "thread_id": int(item_id[1:]) if item_id.startswith("t") else None,
        "title": None,
        "note": None,
        "parent_id": None,
        "priority": 2,
        "due_at": None,
        "snoozed_until": None,
        "project": None,
        "state": "open",
        "created_at": "2026-10-10T00:00:00Z",
        "updated_at": "2026-10-10T00:00:00Z",
    }
    base.update(fields)
    return WorkItem(**base)


def board(threads=(), overlays=(), lineage=None, running=frozenset()):
    return {
        i["id"]: i
        for i in build_board(
            threads=threads,
            overlays=overlays,
            lineage=lineage or {},
            running=set(running),
            now=NOW,
        )
    }


@pytest.mark.parametrize(
    "name, status, bucket",
    [
        ("❓ Which tenant?", "waiting", BUCKET_ME),
        ("👀 Draft ready", "review", BUCKET_ME),
        ("📋 Approve in portal", "action", BUCKET_ME),
        ("⚠️ Build broke", "error", BUCKET_ME),
        ("✅ Shipped", "done", BUCKET_DONE),
        ("Still talking", "idle", BUCKET_IDLE),
    ],
)
def test_status_comes_from_the_thread_marker(name, status, bucket) -> None:
    item = board(threads=[ThreadSnapshot(1, name)])["t1"]
    assert (item["status"], item["bucket"]) == (status, bucket)


def test_a_running_turn_beats_any_marker() -> None:
    item = board(threads=[ThreadSnapshot(1, "❓ asked earlier")], running={1})["t1"]
    assert (item["status"], item["bucket"]) == ("running", BUCKET_AI)


def test_the_humans_done_beats_the_marker() -> None:
    item = board(threads=[ThreadSnapshot(1, "❓ q")], overlays=[overlay("t1", state="done")])["t1"]
    assert item["bucket"] == BUCKET_DONE


def test_snooze_hides_until_it_ends() -> None:
    later = overlay("t1", snoozed_until="2026-10-11T00:00:00Z")
    past = overlay("t2", snoozed_until="2026-10-09T00:00:00Z")
    items = board(
        threads=[ThreadSnapshot(1, "❓ a"), ThreadSnapshot(2, "❓ b")], overlays=[later, past]
    )
    assert items["t1"]["bucket"] == BUCKET_SNOOZED
    assert items["t2"]["bucket"] == BUCKET_ME
    assert items["t2"]["snoozed_until"] is None, "an expired snooze is not reported"


def test_captures_are_todo() -> None:
    item = board(overlays=[overlay("cabc", title="Call the bank")])["cabc"]
    assert (item["status"], item["bucket"], item["thread_id"]) == ("todo", BUCKET_TODO, None)


def test_titles_lose_markers_and_lineage_tags() -> None:
    assert clean_title("✅ 🤖K2 MB GA v2 lane 14") == "MB GA v2 lane 14"
    item = board(
        threads=[ThreadSnapshot(1, "❓ 🌳K2 Parent")], overlays=[overlay("t1", title="Mine")]
    )
    assert item["t1"]["title"] == "Mine", "the human's title wins"


def test_lineage_makes_the_tree_and_an_overlay_can_move_it() -> None:
    threads = [ThreadSnapshot(1, "parent"), ThreadSnapshot(2, "child"), ThreadSnapshot(3, "other")]
    items = board(threads=threads, lineage={2: 1, 3: 1}, overlays=[overlay("t3", parent_id="t2")])
    assert items["t2"]["parent_id"] == "t1"
    assert items["t3"]["parent_id"] == "t2"


def test_a_parent_not_on_the_board_is_dropped() -> None:
    items = board(threads=[ThreadSnapshot(2, "orphan")], lineage={2: 1})
    assert items["t2"]["parent_id"] is None


def test_overdue_is_flagged_only_for_open_work() -> None:
    items = board(
        threads=[ThreadSnapshot(1, "❓ late"), ThreadSnapshot(2, "✅ late but done")],
        overlays=[
            overlay("t1", due_at="2026-10-09T00:00:00Z"),
            overlay("t2", due_at="2026-10-09T00:00:00Z"),
        ],
    )
    assert items["t1"]["overdue"] is True
    assert items["t2"]["overdue"] is False


def test_triage_order_priority_then_overdue_then_longest_wait() -> None:
    threads = [
        ThreadSnapshot(1, "❓ p2 recent", last_activity_at="2026-10-10T11:00:00Z"),
        ThreadSnapshot(2, "❓ p2 old", last_activity_at="2026-10-09T11:00:00Z"),
        ThreadSnapshot(3, "❓ p0", last_activity_at="2026-10-10T11:59:00Z"),
        ThreadSnapshot(4, "❓ p2 overdue", last_activity_at="2026-10-10T11:30:00Z"),
    ]
    overlays = [overlay("t3", priority=0), overlay("t4", due_at="2026-10-01T00:00:00Z")]
    order = [
        i["id"]
        for i in build_board(threads=threads, overlays=overlays, lineage={}, running=set(), now=NOW)
    ]
    assert order == ["t3", "t4", "t2", "t1"]


def test_a_done_verdict_older_than_new_activity_yields_to_the_thread() -> None:
    stale = overlay("t1", state="done", updated_at="2026-10-10T08:00:00Z")
    fresh = overlay("t2", state="done", updated_at="2026-10-10T11:00:00Z")
    items = board(
        threads=[
            ThreadSnapshot(
                1, "❓ asked after you closed it", last_activity_at="2026-10-10T09:00:00Z"
            ),
            ThreadSnapshot(
                2, "❓ asked before you closed it", last_activity_at="2026-10-10T10:00:00Z"
            ),
        ],
        overlays=[stale, fresh],
    )
    assert items["t1"]["bucket"] == BUCKET_ME
    assert items["t2"]["bucket"] == BUCKET_DONE


def test_a_loop_through_lineage_is_cut_so_nothing_disappears() -> None:
    # t2 is t1's child by lineage; an overlay then puts t1 under t2.
    items = board(
        threads=[ThreadSnapshot(1, "a"), ThreadSnapshot(2, "b")],
        lineage={2: 1},
        overlays=[overlay("t1", parent_id="t2")],
    )
    roots = [i for i in items.values() if i["parent_id"] is None]
    assert len(roots) == 1, "exactly one link of the loop is cut"
