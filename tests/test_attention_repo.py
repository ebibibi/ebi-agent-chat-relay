"""human_activity storage, the recorder hook, /api/attention and /attention."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_code_core.attention import AttentionConfig, AttentionParams, HumanActivity
from claude_code_core.attention_repo import (
    AttentionRecorder,
    HumanActivityRepository,
    load_report,
)
from claude_discord.cogs.attention_command import (
    AttentionCog,
    format_attention_summary,
    format_minutes,
)
from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.ext.api_server import ApiServer
from claude_discord.ext.attention_api import MAX_RANGE_DAYS, RangeError, parse_range
from claude_discord.setup import BridgeComponents

T0 = datetime(2026, 10, 5, 1, 0, tzinfo=UTC)
TOKYO = AttentionParams(timezone="Asia/Tokyo")


def act(minute: float, *, msg: str, thread: str = "t1", chars: int = 10) -> HumanActivity:
    return HumanActivity(
        frontend="discord",
        conversation_id=thread,
        parent_id="parent",
        thread_title=f"title {thread}",
        author_id="op",
        occurred_at=T0 + timedelta(minutes=minute),
        message_id=msg,
        char_count=chars,
        attachment_count=1,
    )


@pytest.fixture
async def repo(tmp_path: Path) -> HumanActivityRepository:
    r = HumanActivityRepository(str(tmp_path / "sessions.db"))
    await r.init_db()
    return r


class TestRepository:
    async def test_round_trip_preserves_every_field(self, repo: HumanActivityRepository) -> None:
        original = act(0, msg="m1")
        assert await repo.record(original) is True
        [loaded] = await repo.list_between(T0 - timedelta(hours=1), T0 + timedelta(hours=1))
        assert loaded == original

    async def test_the_same_message_is_recorded_once(self, repo: HumanActivityRepository) -> None:
        assert await repo.record(act(0, msg="m1")) is True
        assert await repo.record(act(0, msg="m1")) is False
        assert await repo.record_many([act(0, msg="m1"), act(1, msg="m2")]) == 1
        assert await repo.count() == 2

    async def test_message_ids_are_unique_per_frontend(self, repo: HumanActivityRepository) -> None:
        teams = HumanActivity(
            frontend="teams",
            conversation_id="c",
            author_id="op",
            occurred_at=T0,
            message_id="m1",
        )
        await repo.record(act(0, msg="m1"))
        assert await repo.record(teams) is True

    async def test_list_between_is_half_open_and_filters_author(
        self, repo: HumanActivityRepository
    ) -> None:
        await repo.record_many([act(0, msg="a"), act(10, msg="b"), act(20, msg="c")])
        rows = await repo.list_between(T0, T0 + timedelta(minutes=20))
        assert [r.message_id for r in rows] == ["a", "b"]
        assert await repo.list_between(T0, T0 + timedelta(hours=1), author_id="other") == []

    async def test_init_is_idempotent(self, repo: HumanActivityRepository) -> None:
        await repo.record(act(0, msg="a"))
        await repo.init_db()
        assert await repo.count() == 1

    async def test_no_text_column_exists(self, repo: HumanActivityRepository) -> None:
        import aiosqlite

        async with aiosqlite.connect(repo.db_path) as db:
            cursor = await db.execute("PRAGMA table_info(human_activity)")
            columns = {row[1] for row in await cursor.fetchall()}
        assert not columns & {"content", "text", "message", "body"}

    async def test_load_report_uses_edge_context(self, repo: HumanActivityRepository) -> None:
        # A burst starting the evening before (Tokyo) still charges its span
        # to the message that falls inside the requested day.
        evening = datetime(2026, 10, 4, 14, 58, tzinfo=UTC)  # 23:58 JST
        await repo.record_many(
            [
                HumanActivity("discord", "t", "op", evening, "x", char_count=0),
                HumanActivity("discord", "t", "op", evening + timedelta(minutes=4), "y", 0),
            ]
        )
        report = await load_report(repo, TOKYO, start=date(2026, 10, 5), end=date(2026, 10, 5))
        assert report["total_messages"] == 1
        assert report["total_minutes"] == 3.0


class TestRecorder:
    async def test_records_when_enabled(self, repo: HumanActivityRepository) -> None:
        recorder = AttentionRecorder(repo, AttentionConfig())
        await recorder.record(act(0, msg="a"))
        assert await repo.count() == 1

    async def test_disabled_recorder_writes_nothing(self, repo: HumanActivityRepository) -> None:
        recorder = AttentionRecorder(repo, AttentionConfig(enabled=False))
        assert recorder.enabled is False
        await recorder.record(act(0, msg="a"))
        assert await repo.count() == 0

    async def test_storage_failure_never_raises(self) -> None:
        broken = MagicMock()
        broken.record = AsyncMock(side_effect=RuntimeError("disk full"))
        await AttentionRecorder(broken, AttentionConfig()).record(act(0, msg="a"))


@pytest.fixture
async def api_client(tmp_path: Path, repo: HumanActivityRepository) -> TestClient:
    notifications = NotificationRepository(str(tmp_path / "n.db"))
    await notifications.init_db()
    api = ApiServer(repo=notifications, bot=MagicMock(), api_secret="s3cret")
    BridgeComponents(
        session_repo=MagicMock(), attention_repo=repo, attention_params=TOKYO
    ).apply_to_api_server(api)
    client = TestClient(TestServer(api.app))
    await client.start_server()
    yield client
    await client.close()


AUTH = {"Authorization": "Bearer s3cret"}


class TestApi:
    async def test_requires_the_api_secret(self, api_client: TestClient) -> None:
        resp = await api_client.get("/api/attention")
        assert resp.status == 401

    async def test_group_by_day(
        self, api_client: TestClient, repo: HumanActivityRepository
    ) -> None:
        await repo.record_many([act(0, msg="a"), act(5, msg="b", thread="t2")])
        resp = await api_client.get(
            "/api/attention?from=2026-10-01&to=2026-10-05&group_by=day", headers=AUTH
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["estimate"] is True
        assert data["parameters"]["timezone"] == "Asia/Tokyo"
        assert data["rows"] == [{"day": "2026-10-05", "minutes": 7.0, "messages": 2, "threads": 2}]

    async def test_group_by_thread_includes_titles(
        self, api_client: TestClient, repo: HumanActivityRepository
    ) -> None:
        await repo.record_many([act(0, msg="a", chars=30), act(5, msg="b", thread="t2")])
        resp = await api_client.get(
            "/api/attention?from=2026-10-05&to=2026-10-05&group_by=thread&author=op",
            headers=AUTH,
        )
        data = await resp.json()
        assert [(r["title"], r["minutes"], r["messages"]) for r in data["rows"]] == [
            ("title t1", 5.2, 1),
            ("title t2", 1.8, 1),
        ]

    @pytest.mark.parametrize(
        "query",
        [
            "group_by=week",
            "from=yesterday",
            "from=2026-10-05&to=2026-10-01",
            "from=2020-01-01&to=2026-10-05",
        ],
    )
    async def test_bad_queries_are_400(self, api_client: TestClient, query: str) -> None:
        resp = await api_client.get(f"/api/attention?{query}", headers=AUTH)
        assert resp.status == 400

    async def test_unconfigured_repo_is_503(self, tmp_path: Path) -> None:
        notifications = NotificationRepository(str(tmp_path / "n.db"))
        await notifications.init_db()
        api = ApiServer(repo=notifications, bot=MagicMock())
        client = TestClient(TestServer(api.app))
        await client.start_server()
        try:
            resp = await client.get("/api/attention")
            assert resp.status == 503
        finally:
            await client.close()

    def test_default_range_is_the_last_seven_days(self) -> None:
        assert parse_range(None, None, date(2026, 10, 5)) == (date(2026, 9, 29), date(2026, 10, 5))

    def test_range_limit(self) -> None:
        end = date(2026, 10, 5)
        start = end - timedelta(days=MAX_RANGE_DAYS - 1)
        assert parse_range(start.isoformat(), None, end) == (start, end)
        with pytest.raises(RangeError):
            parse_range((start - timedelta(days=1)).isoformat(), None, end)


class TestSlashCommand:
    def test_format_minutes(self) -> None:
        assert format_minutes(0.2) == "0m"
        assert format_minutes(7) == "7m"
        assert format_minutes(83.4) == "1h 23m"

    def test_summary_is_short_and_states_parameters(self) -> None:
        params = TOKYO.as_dict()
        today = {"total_minutes": 30, "total_messages": 4, "parameters": params, "rows": []}
        week = {"total_minutes": 140, "total_messages": 20, "parameters": params, "rows": []}
        threads = {
            "rows": [
                {"title": f"thread {i}", "conversation_id": str(i), "minutes": 60 - i}
                for i in range(8)
            ]
        }
        text = format_attention_summary(today, week, threads)
        assert "estimate" in text
        assert "Today: **30m**" in text
        assert "**2h 20m** (avg 20m/day)" in text
        assert "thread 4" in text and "thread 5" not in text
        assert "idle gap 10m · lead-in 2m · Asia/Tokyo" in text
        assert len(text) < 2000

    async def test_command_replies_ephemerally_with_the_users_own_data(
        self, repo: HumanActivityRepository
    ) -> None:
        now = datetime.now(UTC)
        await repo.record(
            HumanActivity("discord", "t", "42", now, "m", char_count=5, thread_title="mine")
        )
        await repo.record(
            HumanActivity("discord", "u", "99", now, "n", char_count=5, thread_title="theirs")
        )
        cog = AttentionCog(MagicMock(), repo=repo, params=AttentionParams(timezone="UTC"))
        interaction = MagicMock()
        interaction.user.id = 42
        interaction.response.send_message = AsyncMock()
        await AttentionCog.attention.callback(cog, interaction)  # type: ignore[arg-type]
        text = interaction.response.send_message.await_args.args[0]
        assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
        assert "mine" in text and "theirs" not in text
        assert "Today: **2m**" in text
