"""Account-pool persistence. usage_stats must stay usable by the previous release."""

from __future__ import annotations

import aiosqlite
import pytest

from claude_code_core.account_pool import DEFAULT_PROFILE, PoolCursor
from claude_code_core.account_pool_repo import AccountPoolRepository
from claude_code_core.session_repo import UsageStatsRepository
from claude_code_core.types import RateLimitInfo

# Verbatim from the release before account pools (claude_code_core/session_repo.py).
# It runs on every rate_limit_event, so if a revert or `make dev-off` puts the old
# code back on a database this release touched, it must still succeed.
OLD_UPSERT = """INSERT INTO usage_stats
                     (rate_limit_type, status, utilization, resets_at, is_using_overage)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(rate_limit_type) DO UPDATE SET
                     status = excluded.status,
                     utilization = excluded.utilization,
                     resets_at = excluded.resets_at,
                     is_using_overage = excluded.is_using_overage,
                     recorded_at = datetime('now', 'localtime')"""
OLD_SELECT = "SELECT * FROM usage_stats ORDER BY rate_limit_type"


def _info(kind: str, util: float, resets: int = 999) -> RateLimitInfo:
    return RateLimitInfo(rate_limit_type=kind, status="allowed", utilization=util, resets_at=resets)


async def _init_both(tmp_path):
    from claude_code_core.models import init_db as core_init
    from claude_discord.database.models import init_db as discord_init

    paths = []
    for name, init in (("core.db", core_init), ("discord.db", discord_init)):
        path = str(tmp_path / name)
        await init(path)
        paths.append(path)
    return paths


class TestRollbackSafety:
    async def test_old_upsert_still_works_after_this_release_used_the_db(self, tmp_path) -> None:
        for path in await _init_both(tmp_path):
            repo = UsageStatsRepository(path)
            await repo.upsert(_info("five_hour", 0.2))
            await repo.upsert(_info("five_hour", 0.7), profile="work")
            async with aiosqlite.connect(path) as db:
                for util in (0.3, 0.4):  # insert, then the ON CONFLICT path
                    await db.execute(OLD_UPSERT, ("five_hour", "allowed", util, 5, 0))
                await db.execute(OLD_UPSERT, ("seven_day", "allowed", 0.1, 6, 0))
                await db.commit()
                cursor = await db.execute(OLD_SELECT)
                rows = await cursor.fetchall()
            assert [(r[0], r[2]) for r in rows] == [("five_hour", 0.4), ("seven_day", 0.1)]
            # And this release reads what the old code wrote as the default profile.
            latest = await repo.get_latest()
            assert {i.rate_limit_type: i.utilization for i in latest} == {
                "five_hour": 0.4,
                "seven_day": 0.1,
            }
            assert [i.utilization for i in await repo.get_latest("work")] == [0.7]

    async def test_usage_stats_schema_is_unchanged(self, tmp_path) -> None:
        for path in await _init_both(tmp_path):
            async with aiosqlite.connect(path) as db:
                cursor = await db.execute("PRAGMA table_info(usage_stats)")
                columns = {row[1]: row[5] for row in await cursor.fetchall()}  # name -> pk
            assert "profile" not in columns
            assert columns["rate_limit_type"] == 1

    async def test_pre_existing_rows_belong_to_the_default_profile(self, tmp_path) -> None:
        path = str(tmp_path / "old.db")
        async with aiosqlite.connect(path) as db:
            await db.execute(
                "CREATE TABLE usage_stats (rate_limit_type TEXT PRIMARY KEY, status TEXT NOT NULL, "
                "utilization REAL NOT NULL, resets_at INTEGER NOT NULL, "
                "is_using_overage INTEGER NOT NULL DEFAULT 0, "
                "recorded_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')))"
            )
            await db.execute(OLD_UPSERT, ("five_hour", "allowed", 0.4, 111, 0))
            await db.commit()
        repo = UsageStatsRepository(path)  # creates the new tables lazily
        await repo.upsert(_info("five_hour", 0.1), profile="work")
        grouped = await repo.get_by_profile()
        assert {k: [i.utilization for i in v] for k, v in grouped.items()} == {
            DEFAULT_PROFILE: [0.4],
            "work": [0.1],
        }

    async def test_same_type_upserts_per_named_profile(self, tmp_path) -> None:
        repo = UsageStatsRepository(str(tmp_path / "s.db"))
        await repo.upsert(_info("five_hour", 0.1), profile="work")
        await repo.upsert(_info("five_hour", 0.2), profile="work")
        await repo.upsert(_info("five_hour", 0.3), profile="home")
        grouped = await repo.get_by_profile()
        assert {k: [i.utilization for i in v] for k, v in grouped.items()} == {
            "home": [0.3],
            "work": [0.2],
        }


@pytest.fixture()
def repo(tmp_path) -> AccountPoolRepository:
    return AccountPoolRepository(str(tmp_path / "pool.db"))


class TestAccountPoolRepository:
    async def test_pins(self, repo: AccountPoolRepository) -> None:
        assert await repo.get_pin(1) is None
        await repo.set_pin(1, "claude", "a")
        await repo.set_pin(1, "claude", "b")
        await repo.set_pin(2, "codex", "x")
        assert await repo.get_pin(1) == ("claude", "b")
        assert await repo.get_pin(2) == ("codex", "x")

    async def test_cursor_round_trip(self, repo: AccountPoolRepository) -> None:
        assert await repo.get_cursor("claude") == PoolCursor()
        await repo.set_cursor("claude", PoolCursor(rr_next=2, sticky="b"))
        await repo.set_cursor("codex", PoolCursor(rr_next=1))
        assert await repo.get_cursor("claude") == PoolCursor(rr_next=2, sticky="b")
        assert await repo.get_cursor("codex") == PoolCursor(rr_next=1)

    async def test_blocks(self, repo: AccountPoolRepository) -> None:
        assert await repo.get_blocks() == {}
        await repo.set_block("a", 100, "limit")
        await repo.set_block("a", 200)
        await repo.set_block("b", 50)
        assert await repo.get_blocks() == {"a": 200, "b": 50}
