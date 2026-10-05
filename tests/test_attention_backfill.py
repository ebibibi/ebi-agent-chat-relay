"""Backfilling human activity from Discord history, with HTTP mocked."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from claude_code_core.attention_repo import HumanActivityRepository
from claude_discord.attention_backfill import (
    DISCORD_EPOCH_MS,
    DiscordRest,
    activity_from_payload,
    backfill_guild,
    snowflake_for,
)
from claude_discord.cli import main as cli_main

SINCE = datetime(2026, 9, 21, tzinfo=UTC)


def flake(moment: datetime, n: int = 0) -> str:
    return str(snowflake_for(moment) + n)


def msg(
    moment: datetime, n: int = 0, *, author: str = "op", content: str = "hello", **extra: Any
) -> dict[str, Any]:
    return {
        "id": flake(moment, n),
        "type": 0,
        "timestamp": moment.isoformat(),
        "author": {"id": author},
        "content": content,
        "attachments": [],
        **extra,
    }


class FakeDiscord:
    """Serves canned pages and records every request."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.requests: list[tuple[str, dict[str, str]]] = []

    async def __call__(
        self, path: str, params: Mapping[str, str]
    ) -> tuple[int, Mapping[str, str], Any]:
        self.requests.append((path, dict(params)))
        handler = self.routes.get(path)
        if handler is None:
            return 404, {}, {"message": "Unknown"}
        if callable(handler):
            return handler(dict(params))
        return 200, {}, handler


async def no_sleep(_: float) -> None:
    return None


@pytest.fixture
async def repo(tmp_path: Path) -> HumanActivityRepository:
    r = HumanActivityRepository(str(tmp_path / "sessions.db"))
    await r.init_db()
    return r


def paged_messages(messages: list[dict[str, Any]]):
    """Discord's ``after`` pagination: up to 100 newer than *after*, newest first."""

    def handler(params: dict[str, str]) -> tuple[int, dict[str, str], Any]:
        after = int(params["after"])
        limit = int(params["limit"])
        newer = sorted((m for m in messages if int(m["id"]) > after), key=lambda m: int(m["id"]))
        return 200, {}, list(reversed(newer[:limit]))

    return handler


class TestPayloadFilter:
    def test_human_message(self) -> None:
        a = activity_from_payload(
            msg(SINCE, attachments=[{}], content="abc"),
            conversation_id="c",
            parent_id=None,
            title="t",
        )
        assert a is not None and (a.char_count, a.attachment_count) == (3, 1)

    @pytest.mark.parametrize(
        "extra",
        [
            {"author": {"id": "b", "bot": True}},
            {"author": {"id": "s", "system": True}},
            {"webhook_id": "123"},
            {"type": 7},  # member join
            {"type": 21},  # thread starter echo
        ],
    )
    def test_non_human_payloads_are_dropped(self, extra: dict[str, Any]) -> None:
        payload = {**msg(SINCE), **extra}
        assert (
            activity_from_payload(payload, conversation_id="c", parent_id=None, title=None) is None
        )

    def test_snowflake_epoch(self) -> None:
        epoch = datetime.fromtimestamp(DISCORD_EPOCH_MS / 1000, tz=UTC)
        assert snowflake_for(epoch) == 0
        assert snowflake_for(epoch + timedelta(milliseconds=1)) == 1 << 22


class TestRateLimits:
    async def test_429_waits_for_retry_after_then_succeeds(self) -> None:
        responses = [(429, {}, {"retry_after": 1.5}), (200, {}, ["ok"])]
        waits: list[float] = []

        async def raw(path: str, params: Mapping[str, str]) -> Any:
            return responses.pop(0)

        async def sleep(seconds: float) -> None:
            waits.append(seconds)

        assert await DiscordRest(raw, sleep=sleep).get("/x") == (200, ["ok"])
        assert waits == [1.5]

    async def test_exhausted_bucket_waits_before_the_next_call(self) -> None:
        waits: list[float] = []

        async def raw(path: str, params: Mapping[str, str]) -> Any:
            return 200, {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset-After": "0.7"}, []

        async def sleep(seconds: float) -> None:
            waits.append(seconds)

        await DiscordRest(raw, sleep=sleep).get("/x")
        assert waits == [0.7]

    async def test_persistent_failure_raises(self) -> None:
        async def raw(path: str, params: Mapping[str, str]) -> Any:
            return 503, {}, None

        with pytest.raises(RuntimeError):
            await DiscordRest(raw, sleep=no_sleep).get("/x")


class TestBackfill:
    def guild(self, *, thread_messages: list[dict[str, Any]]) -> FakeDiscord:
        day = SINCE + timedelta(days=2)
        old = SINCE - timedelta(days=30)
        started = msg(day, 1, content="start a thread")
        return FakeDiscord(
            {
                "/guilds/g/channels": [
                    {"id": "100", "type": 0, "name": "general"},
                    {"id": "200", "type": 2, "name": "voice"},
                    {"id": "300", "type": 15, "name": "forum"},
                ],
                "/guilds/g/threads/active": {
                    "threads": [
                        {
                            "id": started["id"],
                            "parent_id": "100",
                            "name": "Active thread",
                            "last_message_id": flake(day, 999),
                        },
                    ]
                },
                "/channels/100/messages": paged_messages(
                    [
                        started,
                        msg(day, 2, content="plain channel chat"),
                        {**msg(day, 3), "author": {"id": "b", "bot": True}},
                    ]
                ),
                "/channels/100/threads/archived/public": {
                    "threads": [
                        {
                            "id": "400",
                            "parent_id": "100",
                            "name": "Archived thread",
                            "last_message_id": flake(day, 999),
                            "thread_metadata": {"archive_timestamp": day.isoformat()},
                        },
                        {
                            "id": "401",
                            "parent_id": "100",
                            "name": "Long dead",
                            "last_message_id": flake(old),
                            "thread_metadata": {"archive_timestamp": old.isoformat()},
                        },
                    ],
                    "has_more": True,
                },
                "/channels/100/threads/archived/private": lambda p: (403, {}, {"code": 50013}),
                "/channels/300/threads/archived/public": {"threads": [], "has_more": False},
                f"/channels/{started['id']}/messages": paged_messages([]),
                "/channels/400/messages": paged_messages(thread_messages),
            }
        )

    async def test_walks_channels_active_and_archived_threads(
        self, repo: HumanActivityRepository
    ) -> None:
        day = SINCE + timedelta(days=2)
        thread_messages = [msg(day + timedelta(seconds=i), i, content="x" * i) for i in range(250)]
        fake = self.guild(thread_messages=thread_messages)

        stats = await backfill_guild(
            DiscordRest(fake, sleep=no_sleep), repo, guild_id="g", since=SINCE
        )

        assert (stats.channels, stats.threads) == (1, 2)
        assert stats.human_messages == 252 and stats.inserted == 252
        rows = await repo.list_between(SINCE, SINCE + timedelta(days=10))
        by_conv: dict[str, int] = {}
        for r in rows:
            by_conv[r.conversation_id] = by_conv.get(r.conversation_id, 0) + 1
        started_id = flake(day, 1)
        assert by_conv == {"400": 250, started_id: 1, "100": 1}
        starter = next(r for r in rows if r.conversation_id == started_id)
        assert (starter.parent_id, starter.thread_title) == ("100", "Active thread")
        # 250 messages took three pages of 100.
        pages = [p for path, p in fake.requests if path == "/channels/400/messages"]
        assert len(pages) == 3
        # The dead thread was never read; the archive stopped paging once it
        # reached threads archived before the start date.
        assert all(path != "/channels/401/messages" for path, _ in fake.requests)
        archive_calls = [p for p, _ in fake.requests if p.endswith("/archived/public")]
        assert archive_calls.count("/channels/100/threads/archived/public") == 1
        assert stats.skipped_containers == []

    async def test_rerunning_is_idempotent(self, repo: HumanActivityRepository) -> None:
        fake = self.guild(thread_messages=[msg(SINCE + timedelta(days=2), 5)])
        rest = DiscordRest(fake, sleep=no_sleep)
        first = await backfill_guild(rest, repo, guild_id="g", since=SINCE)
        second = await backfill_guild(rest, repo, guild_id="g", since=SINCE)
        assert first.inserted == 3
        assert (second.human_messages, second.inserted) == (3, 0)
        assert await repo.count() == 3

    async def test_author_restriction_and_until(self, repo: HumanActivityRepository) -> None:
        day = SINCE + timedelta(days=2)
        fake = self.guild(
            thread_messages=[
                msg(day, 10, author="op"),
                msg(day, 11, author="friend"),
                msg(day + timedelta(days=3), 12, author="op"),
            ]
        )
        stats = await backfill_guild(
            DiscordRest(fake, sleep=no_sleep),
            repo,
            guild_id="g",
            since=SINCE,
            until=day + timedelta(days=1),
            author_ids=["op"],
        )
        assert stats.inserted == 3  # starter + channel chat + one thread message

    async def test_unreadable_guild_raises(self, repo: HumanActivityRepository) -> None:
        with pytest.raises(RuntimeError):
            await backfill_guild(
                DiscordRest(FakeDiscord({}), sleep=no_sleep), repo, guild_id="g", since=SINCE
            )


class TestCli:
    def test_missing_token_exits_with_a_message(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
        monkeypatch.setattr(
            "sys.argv",
            [
                "ccdb",
                "attention-backfill",
                "--guild",
                "1",
                "--since",
                "2026-09-21",
                "--env",
                str(tmp_path / "missing.env"),
                "--db",
                str(tmp_path / "x.db"),
            ],
        )
        with pytest.raises(SystemExit) as exc:
            cli_main()
        assert exc.value.code == 1
        assert "DISCORD_BOT_TOKEN" in capsys.readouterr().err

    def test_bad_date_exits(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture
    ) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "dummy")
        monkeypatch.setattr(
            "sys.argv",
            [
                "ccdb",
                "attention-backfill",
                "--guild",
                "1",
                "--since",
                "last week",
                "--env",
                str(tmp_path / "missing.env"),
                "--db",
                str(tmp_path / "x.db"),
            ],
        )
        with pytest.raises(SystemExit):
            cli_main()
        assert "YYYY-MM-DD" in capsys.readouterr().err
