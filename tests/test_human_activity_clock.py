"""The restart quiet rule's clock: fed by every frontend, seeded from history."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from claude_code_core.attention import AttentionConfig, HumanActivity
from claude_code_core.attention_repo import AttentionRecorder, HumanActivityRepository
from claude_code_core.human_activity import last_human_activity, note_human_activity
from claude_discord.stores import _seed_human_activity


def _activity(moment: datetime, *, frontend: str = "teams", source: str = "live") -> HumanActivity:
    return HumanActivity(
        frontend=frontend,
        conversation_id="c1",
        parent_id=None,
        thread_title="t",
        author_id="u1",
        occurred_at=moment,
        char_count=3,
        attachment_count=0,
        message_id=f"m{moment.timestamp()}",
        source=source,
    )


def test_an_older_time_never_moves_the_clock_back() -> None:
    note_human_activity("a", at=200.0)
    note_human_activity("b", at=100.0)
    assert last_human_activity() == 200.0


async def test_every_frontend_message_moves_the_clock_even_with_metering_off(
    tmp_path: Path,
) -> None:
    repo = HumanActivityRepository(str(tmp_path / "s.db"))
    await repo.init_db()
    recorder = AttentionRecorder(repo, AttentionConfig(enabled=False))
    moment = datetime(2026, 11, 15, 2, 0, tzinfo=UTC)
    await recorder.record(_activity(moment))
    assert last_human_activity() == moment.timestamp()


async def test_backfilled_history_does_not_count(tmp_path: Path) -> None:
    repo = HumanActivityRepository(str(tmp_path / "s.db"))
    await repo.init_db()
    recorder = AttentionRecorder(repo, AttentionConfig(enabled=True))
    await recorder.record(_activity(datetime(2026, 11, 15, tzinfo=UTC), source="backfill"))
    assert last_human_activity() is None


async def test_startup_seeds_the_clock_from_the_latest_live_message(tmp_path: Path) -> None:
    repo = HumanActivityRepository(str(tmp_path / "s.db"))
    await repo.init_db()
    older = datetime(2026, 11, 15, 1, 0, tzinfo=UTC)
    newer = datetime(2026, 11, 15, 2, 30, tzinfo=UTC)
    await repo.record(_activity(older))
    await repo.record(_activity(newer, frontend="console"))
    await repo.record(_activity(datetime(2026, 11, 15, 3, 0, tzinfo=UTC), source="backfill"))

    await _seed_human_activity(repo)
    assert last_human_activity() == newer.timestamp()


async def test_startup_with_no_history_stays_quiet(tmp_path: Path) -> None:
    repo = HumanActivityRepository(str(tmp_path / "s.db"))
    await repo.init_db()
    await _seed_human_activity(repo)
    assert last_human_activity() is None
