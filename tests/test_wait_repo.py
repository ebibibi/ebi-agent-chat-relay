"""Tests for WaitRepository — persistent waits that survive a bot restart."""

from __future__ import annotations

import pytest

from claude_discord.database.models import init_db
from claude_discord.database.wait_repo import WaitLimitError, WaitRepository
from claude_discord.waits import MAX_WAITS_PER_THREAD, ProbeResult, parse_wait_spec


@pytest.fixture
async def repo(tmp_path) -> WaitRepository:
    db = str(tmp_path / "sessions.db")
    await init_db(db)
    return WaitRepository(db)


def _spec(thread_id: int = 100, **overrides: object):
    body: dict = {
        "thread_id": thread_id,
        "argv": ["gh", "pr", "checks", "5"],
        "pending_exit_codes": [8],
        "interval_seconds": 60,
        "timeout_seconds": 600,
    }
    body.update(overrides)
    return parse_wait_spec(body)


async def test_create_round_trips_the_spec(repo: WaitRepository) -> None:
    wait = await repo.create(_spec(note="merge", done_pattern="^ok"), now=1000.0)
    loaded = await repo.get(wait.id)
    assert loaded is not None
    assert loaded.thread_id == 100
    assert loaded.argv == ("gh", "pr", "checks", "5")
    assert loaded.pending_exit_codes == (8,)
    assert loaded.done_pattern == "^ok"
    assert loaded.note == "merge"
    assert loaded.status == "active"
    assert loaded.deadline == 1600.0
    # The first look is one interval out: the push that prompted the wait
    # has rarely even queued a run yet.
    assert loaded.next_check_at == 1060.0
    assert loaded.to_spec() == _spec(note="merge", done_pattern="^ok")


async def test_due_returns_only_active_waits_whose_time_has_come(repo: WaitRepository) -> None:
    early = await repo.create(_spec(), now=1000.0)
    await repo.create(_spec(interval_seconds=600), now=1000.0)
    cancelled = await repo.create(_spec(), now=1000.0)
    await repo.cancel(cancelled.id)
    due = await repo.due(now=1070.0)
    assert [w.id for w in due] == [early.id]


async def test_record_probe_stores_the_last_result(repo: WaitRepository) -> None:
    wait = await repo.create(_spec(), now=1000.0)
    await repo.record_probe(
        wait.id,
        ProbeResult(exit_code=8, output="pending"),
        next_check_at=1200.0,
        consecutive_errors=0,
    )
    loaded = await repo.get(wait.id)
    assert loaded is not None
    assert loaded.last_exit_code == 8
    assert loaded.last_output == "pending"
    assert loaded.next_check_at == 1200.0
    assert loaded.consecutive_errors == 0


async def test_finish_happens_once(repo: WaitRepository) -> None:
    # Two loops racing on the same wait must resume the thread once.
    wait = await repo.create(_spec(), now=1000.0)
    assert await repo.finish(wait.id, "done", now=1100.0) is True
    assert await repo.finish(wait.id, "timeout", now=1101.0) is False
    loaded = await repo.get(wait.id)
    assert loaded is not None
    assert loaded.status == "done"
    assert loaded.finished_at == 1100.0


async def test_cancel_can_be_scoped_to_the_owner_thread(repo: WaitRepository) -> None:
    wait = await repo.create(_spec(thread_id=100), now=1000.0)
    assert await repo.cancel(wait.id, thread_id=999) is False
    assert await repo.cancel(wait.id, thread_id=100) is True
    assert await repo.cancel(wait.id) is False


async def test_list_active_and_thread_ids(repo: WaitRepository) -> None:
    a = await repo.create(_spec(thread_id=1), now=1000.0)
    await repo.create(_spec(thread_id=2), now=1000.0)
    done = await repo.create(_spec(thread_id=3), now=1000.0)
    await repo.finish(done.id, "done", now=1001.0)
    assert {w.thread_id for w in await repo.list_active()} == {1, 2}
    assert [w.id for w in await repo.list_active(thread_id=1)] == [a.id]
    assert await repo.active_thread_ids() == {1, 2}


async def test_per_thread_limit(repo: WaitRepository) -> None:
    for _ in range(MAX_WAITS_PER_THREAD):
        await repo.create(_spec(thread_id=7), now=1000.0)
    with pytest.raises(WaitLimitError):
        await repo.create(_spec(thread_id=7), now=1000.0)
    # Another thread is unaffected.
    await repo.create(_spec(thread_id=8), now=1000.0)


async def test_global_limit(repo: WaitRepository, monkeypatch) -> None:
    import claude_discord.database.wait_repo as wait_repo

    monkeypatch.setattr(wait_repo, "MAX_ACTIVE_WAITS", 2)
    await repo.create(_spec(thread_id=1), now=1000.0)
    await repo.create(_spec(thread_id=2), now=1000.0)
    with pytest.raises(WaitLimitError):
        await repo.create(_spec(thread_id=3), now=1000.0)
