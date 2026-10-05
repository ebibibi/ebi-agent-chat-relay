"""Account-pool persistence and the usage_stats per-profile migration."""

from __future__ import annotations

import aiosqlite
import pytest

from claude_code_core.account_pool import DEFAULT_PROFILE, PoolCursor
from claude_code_core.account_pool_repo import AccountPoolRepository, ensure_account_schema
from claude_code_core.session_repo import UsageStatsRepository
from claude_code_core.types import RateLimitInfo

OLD_USAGE_STATS = (
    "CREATE TABLE usage_stats ("
    "rate_limit_type TEXT PRIMARY KEY, "
    "status TEXT NOT NULL, "
    "utilization REAL NOT NULL, "
    "resets_at INTEGER NOT NULL, "
    "is_using_overage INTEGER NOT NULL DEFAULT 0, "
    "recorded_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')))"
)


async def _old_db(path: str) -> None:
    async with aiosqlite.connect(path) as db:
        await db.execute(OLD_USAGE_STATS)
        await db.execute(
            "INSERT INTO usage_stats (rate_limit_type, status, utilization, resets_at, "
            "is_using_overage, recorded_at) VALUES "
            "('five_hour', 'allowed', 0.4, 111, 0, '2026-01-01 00:00:00'), "
            "('seven_day', 'allowed_warning', 0.8, 222, 1, '2026-01-02 00:00:00')"
        )
        await db.commit()


def _info(kind: str, util: float, resets: int = 999) -> RateLimitInfo:
    return RateLimitInfo(rate_limit_type=kind, status="allowed", utilization=util, resets_at=resets)


class TestUsageStatsMigration:
    async def test_existing_rows_move_to_the_default_profile(self, tmp_path) -> None:
        path = str(tmp_path / "s.db")
        await _old_db(path)
        async with aiosqlite.connect(path) as db:
            await ensure_account_schema(db)
            await db.commit()
            cursor = await db.execute(
                "SELECT profile, rate_limit_type, status, utilization, resets_at, "
                "is_using_overage, recorded_at FROM usage_stats ORDER BY rate_limit_type"
            )
            rows = await cursor.fetchall()
        assert rows == [
            (DEFAULT_PROFILE, "five_hour", "allowed", 0.4, 111, 0, "2026-01-01 00:00:00"),
            (DEFAULT_PROFILE, "seven_day", "allowed_warning", 0.8, 222, 1, "2026-01-02 00:00:00"),
        ]

    async def test_migration_is_idempotent(self, tmp_path) -> None:
        path = str(tmp_path / "s.db")
        await _old_db(path)
        for _ in range(3):
            async with aiosqlite.connect(path) as db:
                await ensure_account_schema(db)
                await db.commit()
        repo = UsageStatsRepository(path)
        assert len(await repo.get_latest()) == 2

    async def test_same_type_can_exist_per_profile(self, tmp_path) -> None:
        path = str(tmp_path / "s.db")
        await _old_db(path)
        repo = UsageStatsRepository(path)  # migrates lazily on first use
        await repo.upsert(_info("five_hour", 0.1), profile="work")
        await repo.upsert(_info("five_hour", 0.2), profile="work")
        grouped = await repo.get_by_profile()
        assert {k: [i.utilization for i in v] for k, v in grouped.items()} == {
            DEFAULT_PROFILE: [0.4, 0.8],
            "work": [0.2],
        }
        assert [i.utilization for i in await repo.get_latest("work")] == [0.2]

    async def test_both_init_db_produce_the_new_shape(self, tmp_path) -> None:
        from claude_code_core.models import init_db as core_init
        from claude_discord.database.models import init_db as discord_init

        for name, init in (("core.db", core_init), ("discord.db", discord_init)):
            path = str(tmp_path / name)
            await _old_db(path)
            await init(path)
            async with aiosqlite.connect(path) as db:
                cursor = await db.execute("PRAGMA table_info(usage_stats)")
                columns = [row[1] for row in await cursor.fetchall()]
                cursor = await db.execute("SELECT count(*) FROM usage_stats")
                (count,) = await cursor.fetchone()  # type: ignore[misc]
            assert columns[0] == "profile", name
            assert count == 2, name

    async def test_fresh_database(self, tmp_path) -> None:
        from claude_discord.database.models import init_db

        path = str(tmp_path / "fresh.db")
        await init_db(path)
        repo = UsageStatsRepository(path)
        await repo.upsert(_info("five_hour", 0.5))
        assert [i.rate_limit_type for i in await repo.get_latest()] == ["five_hour"]


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
