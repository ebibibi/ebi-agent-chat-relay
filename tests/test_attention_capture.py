"""Which inbound messages become human-activity rows, on Discord and Teams."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_code_core.attention import AttentionConfig
from claude_code_core.attention_repo import AttentionRecorder
from claude_discord.attention_capture import activity_from_message, is_human_message
from claude_discord.cogs.claude_chat import ClaudeChatCog
from claude_discord.teams_integration import TeamsSessionHost, teams_activity
from claude_discord.thread_policy import initial_thread_name
from claude_teams.activity import parse_activity

CREATED = datetime(2026, 10, 5, 1, 0, tzinfo=UTC)


def recorder(enabled: bool = True) -> tuple[AttentionRecorder, list[Any]]:
    rows: list[Any] = []
    repo = MagicMock()

    async def record(activity: Any) -> bool:
        rows.append(activity)
        return True

    repo.record = record
    return AttentionRecorder(repo, AttentionConfig(enabled=enabled)), rows


def make_cog(rec: AttentionRecorder | None, **kwargs: Any) -> ClaudeChatCog:
    bot = MagicMock()
    bot.channel_id = 111
    bot.user = MagicMock()
    repo = MagicMock()
    repo.get = AsyncMock(return_value=None)
    cog = ClaudeChatCog(bot=bot, repo=repo, runner=MagicMock(), attention_recorder=rec, **kwargs)
    cog._handle_new_conversation = AsyncMock()  # type: ignore[method-assign]
    cog._handle_thread_reply = AsyncMock()  # type: ignore[method-assign]
    cog._handle_mention = AsyncMock()  # type: ignore[method-assign]
    return cog


def message(
    *,
    channel: Any,
    bot: bool = False,
    webhook_id: int | None = None,
    content: str = "please fix the build",
    message_type: discord.MessageType = discord.MessageType.default,
) -> MagicMock:
    msg = MagicMock(spec=discord.Message)
    msg.id = 5001
    msg.author = MagicMock()
    msg.author.bot = bot
    msg.author.system = False
    msg.author.id = 77
    msg.webhook_id = webhook_id
    msg.type = message_type
    msg.content = content
    msg.attachments = [MagicMock(), MagicMock()]
    msg.created_at = CREATED
    msg.channel = channel
    msg.guild = MagicMock()
    msg.mentions = []
    return msg


def text_channel(channel_id: int = 111, name: str = "general") -> MagicMock:
    ch = MagicMock(spec=discord.TextChannel)
    ch.id = channel_id
    ch.name = name
    return ch


def thread(parent_id: int = 111) -> MagicMock:
    th = MagicMock(spec=discord.Thread)
    th.id = 9001
    th.parent_id = parent_id
    th.name = "Fix the build"
    return th


class TestDiscordFilters:
    def test_humans_count_and_bots_webhooks_system_do_not(self) -> None:
        assert is_human_message(message(channel=text_channel()))
        assert not is_human_message(message(channel=text_channel(), bot=True))
        assert not is_human_message(message(channel=text_channel(), webhook_id=1))
        system = message(channel=text_channel())
        system.author.system = True
        assert not is_human_message(system)

    def test_new_thread_is_keyed_by_the_message_id(self) -> None:
        a = activity_from_message(message(channel=text_channel()), opens_thread=True)
        assert (a.conversation_id, a.parent_id, a.message_id) == ("5001", "111", "5001")
        assert a.thread_title == "please fix the build"
        assert (a.char_count, a.attachment_count) == (len("please fix the build"), 2)

    def test_new_thread_title_matches_the_name_the_thread_is_created_with(self) -> None:
        empty = message(channel=text_channel(), content="")
        assert activity_from_message(empty, opens_thread=True).thread_title == "Claude Chat"
        long = message(channel=text_channel(), content="x" * 300)
        assert activity_from_message(long, opens_thread=True).thread_title == "x" * 100
        assert initial_thread_name("x" * 300) == "x" * 100

    def test_thread_reply_is_keyed_by_the_thread(self) -> None:
        a = activity_from_message(message(channel=thread()), opens_thread=False)
        assert (a.conversation_id, a.parent_id, a.thread_title) == ("9001", "111", "Fix the build")

    def test_activity_holds_no_text(self) -> None:
        a = activity_from_message(message(channel=thread()), opens_thread=False)
        assert "please fix" not in repr({k: v for k, v in vars(a).items() if k != "thread_title"})


class TestClaudeChatHook:
    async def test_thread_reply_is_recorded(self) -> None:
        rec, rows = recorder()
        cog = make_cog(rec)
        await cog.on_message(message(channel=thread()))
        assert [r.conversation_id for r in rows] == ["9001"]
        cog._handle_thread_reply.assert_awaited_once()

    async def test_new_conversation_is_recorded_under_the_new_thread(self) -> None:
        rec, rows = recorder()
        cog = make_cog(rec)
        await cog.on_message(message(channel=text_channel()))
        assert [r.conversation_id for r in rows] == ["5001"]

    async def test_inline_reply_channel_is_recorded_under_the_channel(self) -> None:
        rec, rows = recorder()
        cog = make_cog(rec, inline_reply_channel_ids={111})
        await cog.on_message(message(channel=text_channel()))
        assert [r.conversation_id for r in rows] == ["111"]

    async def test_mention_elsewhere_is_recorded(self) -> None:
        rec, rows = recorder()
        cog = make_cog(rec)
        msg = message(channel=text_channel(channel_id=222, name="random"))
        msg.mentions = [cog.bot.user]
        await cog.on_message(msg)
        assert [(r.conversation_id, r.thread_title) for r in rows] == [("222", "random")]

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"bot": True},  # the relay's own posts, /api/spawn, relays, scheduler
            {"webhook_id": 42},  # webhooks and CI triggers
            {"message_type": discord.MessageType.thread_created},
        ],
    )
    async def test_non_human_messages_are_not_recorded(self, kwargs: dict[str, Any]) -> None:
        rec, rows = recorder()
        cog = make_cog(rec)
        await cog.on_message(message(channel=thread(), **kwargs))
        assert rows == []

    async def test_messages_that_reach_no_session_are_not_recorded(self) -> None:
        rec, rows = recorder()
        cog = make_cog(rec)
        await cog.on_message(message(channel=text_channel(channel_id=333)))  # unlisted, no mention
        assert rows == []

    async def test_unauthorized_users_are_not_recorded(self) -> None:
        rec, rows = recorder()
        cog = make_cog(rec, allowed_user_ids={1})
        await cog.on_message(message(channel=thread()))
        assert rows == []

    async def test_disabled_or_absent_recorder_keeps_chat_working(self) -> None:
        rec, rows = recorder(enabled=False)
        for cog in (make_cog(rec), make_cog(None)):
            await cog.on_message(message(channel=thread()))
            cog._handle_thread_reply.assert_awaited_once()
        assert rows == []


def teams_payload(**overrides: Any) -> Any:
    payload: dict[str, Any] = {
        "type": "message",
        "id": "activity-1",
        "timestamp": "2026-10-05T01:00:00.123Z",
        "serviceUrl": "https://smba.trafficmanager.net/jp/",
        "conversation": {"id": "conv-1", "conversationType": "personal", "name": "Ops chat"},
        "from": {"id": "user-1", "name": "User"},
        "recipient": {"id": "bot-id"},
        "text": "deploy now",
        "attachments": [
            {"contentType": "text/html", "content": "<p>deploy now</p>"},
            {"contentType": "application/vnd.microsoft.teams.file.download.info"},
        ],
    }
    payload.update(overrides)
    return parse_activity(payload)


def teams_host(rec: AttentionRecorder) -> tuple[TeamsSessionHost, list[Any]]:
    calls: list[Any] = []

    async def run_session(config: Any) -> str:
        calls.append(config)
        return "s"

    settings = SimpleNamespace(
        current_backend=AsyncMock(return_value="claude"),
        current_model=AsyncMock(return_value=None),
        current_effort=AsyncMock(return_value=None),
    )
    host = TeamsSessionHost(
        app_id="bot-id",
        frontend=SimpleNamespace(
            remember=MagicMock(), resolve_surface=AsyncMock(return_value=SimpleNamespace())
        ),
        ledger=SimpleNamespace(register=AsyncMock(return_value=42)),
        session_repo=SimpleNamespace(get=AsyncMock(return_value=None)),
        backend_factory=SimpleNamespace(
            build=MagicMock(return_value=SimpleNamespace(command="claude")),
            codex_command="codex",
        ),
        backend_settings=settings,
        run_session=run_session,
        attention_recorder=rec,
    )
    return host, calls


class TestTeamsHook:
    def test_teams_activity_counts_files_not_the_html_echo(self) -> None:
        a = teams_activity(teams_payload(), parent_id=None, text="deploy now")
        assert (a.frontend, a.conversation_id, a.author_id) == ("teams", "conv-1", "user-1")
        assert (a.char_count, a.attachment_count, a.thread_title) == (10, 1, "Ops chat")
        assert a.occurred_at == datetime(2026, 10, 5, 1, 0, 0, 123000, tzinfo=UTC)

    async def test_a_human_message_reaching_a_session_is_recorded(self) -> None:
        rec, rows = recorder()
        host, calls = teams_host(rec)
        await host.handle(teams_payload())
        assert len(calls) == 1
        assert [r.message_id for r in rows] == ["activity-1"]

    async def test_the_bot_hearing_itself_is_not_recorded(self) -> None:
        rec, rows = recorder()
        host, calls = teams_host(rec)
        await host.handle(teams_payload(**{"from": {"id": "bot-id"}}))
        assert rows == [] and calls == []

    async def test_an_empty_message_is_not_recorded(self) -> None:
        rec, rows = recorder()
        host, _ = teams_host(rec)
        await host.handle(teams_payload(text="   "))
        assert rows == []
