"""The Focus guide: the next move and why, batched decisions, calm, and record drift."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from claude_discord.console.guide import build_guide

NOW = datetime(2026, 10, 15, 3, 0, tzinfo=UTC)


def _ts(delta: timedelta) -> str:
    return (NOW + delta).strftime("%Y-%m-%dT%H:%M:%SZ")


def _item(item_id: str, **over):
    base = {
        "id": item_id,
        "title": f"Item {item_id}",
        "thread_id": "1",
        "status": "idle",
        "bucket": "idle",
        "priority": 2,
        "due_at": None,
        "overdue": False,
        "parent_id": None,
        "last_activity_at": _ts(-timedelta(hours=1)),
        "focus": 100,
        "actionable": True,
        "workspace_path": None,
    }
    base.update(over)
    return base


def test_the_next_move_comes_first_with_structured_reasons() -> None:
    items = [
        _item("low", focus=100),
        _item(
            "top",
            focus=260,
            priority=0,
            status="waiting",
            bucket="me",
            overdue=True,
            due_at=_ts(-timedelta(hours=2)),
            ai={"next_step": "Answer the question", "reason": "Customer waits"},
        ),
    ]
    guide = build_guide(items, now=NOW)
    first = guide["next"][0]
    assert first["id"] == "top"
    assert first["reasons"][:3] == ["overdue", "priority", "asked"]
    assert first["unblocks"] is True
    assert first["next_step"] == "Answer the question"
    assert first["why"] == "Customer waits"
    assert [n["id"] for n in guide["next"]] == ["top", "low"]
    assert guide["calm"] is False


def test_a_capture_without_a_thread_unblocks_no_agent() -> None:
    guide = build_guide([_item("cap", thread_id=None, status="todo", bucket="todo")], now=NOW)
    assert guide["next"][0]["unblocks"] is False
    assert guide["next"][0]["reasons"] == []


def test_due_soon_and_review_reasons() -> None:
    item = _item("r", status="review", bucket="me", due_at=_ts(timedelta(hours=5)))
    assert build_guide([item], now=NOW)["next"][0]["reasons"] == ["due_soon", "review"]


def test_waiting_and_agent_work_leave_the_human_calm() -> None:
    items = [
        _item("run", status="running", bucket="ai", focus=None, actionable=None),
        _item("later", focus=150, actionable=False),
        _item("cand", status="candidate", bucket="candidate", focus=None, actionable=None),
    ]
    guide = build_guide(items, now=NOW)
    assert guide["next"] == []
    assert guide["calm"] is True
    assert guide["counts"] == {"me": 0, "running": 1, "waiting": 1, "candidate": 1}


def test_decisions_in_one_family_are_batched_under_the_root() -> None:
    items = [
        _item("root", title="Release", status="running", bucket="ai", focus=None),
        _item("c1", parent_id="root", status="waiting", bucket="me", focus=120),
        _item("c2", parent_id="c1", status="review", bucket="me", focus=200),
        _item("solo", status="waiting", bucket="me", focus=300),
        _item("x", parent_id="other", focus=100),
        _item("y", parent_id="other", focus=None),
    ]
    batches = build_guide(items, now=NOW)["batches"]
    assert batches == [{"root_id": "root", "title": "Release", "items": ["c2", "c1"]}]


def test_drift_is_absent_without_a_workspace() -> None:
    assert build_guide([_item("a")], now=NOW)["drift"] is None


def test_a_finished_thread_with_its_task_still_open_is_drift() -> None:
    records = {
        "Work/Site": {
            "tasks": [{"line": 7, "text": "Ship it", "done": False}],
            "updated_at": _ts(timedelta(hours=1)),
        }
    }
    items = [
        _item(
            "a",
            title="Ship it",
            status="candidate",
            bucket="candidate",
            focus=None,
            workspace_path="Work/Site",
        ),
        # Still in progress: an open task is expected.
        _item("b", title="Ship it", workspace_path="Work/Site"),
    ]
    drift = build_guide(items, records=records, now=NOW)["drift"]
    assert drift == [
        {
            "id": "a",
            "title": "Ship it",
            "kind": "task_open",
            "workspace_path": "Work/Site",
            "task": "Ship it",
        }
    ]


def test_records_older_than_the_finished_conversation_are_drift_only_when_bound_explicitly():
    records = {"Work/Site": {"tasks": [], "updated_at": _ts(-timedelta(days=1))}}
    done = {"status": "done", "bucket": "done", "focus": None, "workspace_path": "Work/Site"}
    items = [
        _item("explicit", **done),
        _item("guessed", workspace_by="auto", **done),
        _item("old", last_activity_at=_ts(-timedelta(days=10)), **done),
    ]
    drift = build_guide(items, records=records, now=NOW)["drift"]
    assert [(d["id"], d["kind"]) for d in drift] == [("explicit", "record_stale")]


def test_records_updated_after_the_conversation_are_not_drift() -> None:
    records = {"Work/Site": {"tasks": [], "updated_at": _ts(timedelta(minutes=5))}}
    item = _item(
        "a", status="candidate", bucket="candidate", focus=None, workspace_path="Work/Site"
    )
    assert build_guide([item], records=records, now=NOW)["drift"] == []
