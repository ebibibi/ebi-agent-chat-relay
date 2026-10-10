"""work_items: captures, thread overlays, validation, and the tree's integrity."""

from __future__ import annotations

import pytest

from claude_discord.console.work_repo import (
    DEFAULT_PRIORITY,
    STATE_DONE,
    WorkItemError,
    WorkItemRepository,
    thread_item_id,
    validate_changes,
)


@pytest.fixture
async def repo(tmp_path) -> WorkItemRepository:
    r = WorkItemRepository(str(tmp_path / "sessions.db"))
    await r.init_db()
    return r


async def test_capture_needs_a_title(repo) -> None:
    with pytest.raises(WorkItemError, match="title"):
        await repo.create_capture({"note": "no title"})


async def test_capture_round_trips(repo) -> None:
    item = await repo.create_capture(
        {"title": "Write the ADR", "priority": 1, "due_at": "2026-10-11"}
    )
    stored = await repo.get(item.id)
    assert stored == item
    assert stored.thread_id is None
    assert stored.due_at == "2026-10-11T00:00:00Z"


async def test_first_touch_of_a_thread_creates_its_overlay(repo) -> None:
    item = await repo.update(thread_item_id(42), {"priority": 0})
    assert item.thread_id == 42
    assert item.priority == 0
    again = await repo.update(thread_item_id(42), {"project": "relay"})
    assert again.priority == 0, "a later change must not reset an earlier one"
    assert again.project == "relay"


async def test_update_of_an_unknown_capture_is_refused(repo) -> None:
    with pytest.raises(WorkItemError, match="unknown"):
        await repo.update("cdeadbeef", {"priority": 1})


@pytest.mark.parametrize(
    "changes, message",
    [
        ({"bogus": 1}, "unknown fields"),
        ({"priority": 4}, "priority"),
        ({"priority": True}, "priority"),
        ({"priority": "1"}, "priority"),
        ({"state": "maybe"}, "state"),
        ({"due_at": "tomorrow"}, "ISO 8601"),
        ({"title": "x" * 201}, "at most"),
        ({"title": 5}, "string"),
    ],
)
def test_validation_refuses_bad_changes(changes, message) -> None:
    with pytest.raises(WorkItemError, match=message):
        validate_changes(changes)


def test_timestamps_are_normalised_to_utc() -> None:
    clean = validate_changes({"snoozed_until": "2026-10-11T09:00:00+09:00"})
    assert clean["snoozed_until"] == "2026-10-11T00:00:00Z"


def test_empty_text_clears_the_field() -> None:
    assert validate_changes({"project": "  "}) == {"project": None}


async def test_parent_cycles_are_refused(repo) -> None:
    a = await repo.create_capture({"title": "a"})
    b = await repo.create_capture({"title": "b", "parent_id": a.id})
    with pytest.raises(WorkItemError, match="cycle"):
        await repo.update(a.id, {"parent_id": b.id})
    with pytest.raises(WorkItemError, match="own parent"):
        await repo.update(a.id, {"parent_id": a.id})


async def test_attach_thread_keeps_fields_and_children(repo) -> None:
    parent = await repo.create_capture({"title": "Ship console", "priority": 0, "project": "relay"})
    child = await repo.create_capture({"title": "Write tests", "parent_id": parent.id})
    await repo.update(parent.id, {"state": STATE_DONE})

    overlay = await repo.attach_thread(parent.id, 99)

    assert overlay.id == thread_item_id(99)
    assert overlay.priority == 0 and overlay.project == "relay"
    assert overlay.state == "open", "a started item is open work again"
    assert await repo.get(parent.id) is None
    assert (await repo.get(child.id)).parent_id == overlay.id


async def test_new_items_default_to_normal_priority(repo) -> None:
    item = await repo.create_capture({"title": "x"})
    assert item.priority == DEFAULT_PRIORITY


async def test_a_cycle_through_lineage_is_refused(repo) -> None:
    # t2 was spawned by t1; putting t1 under t2 would close a loop.
    with pytest.raises(WorkItemError, match="cycle"):
        await repo.update(thread_item_id(1), {"parent_id": "t2"}, implicit_parents={"t2": "t1"})
