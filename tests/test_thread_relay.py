"""Tests for thread-to-thread relay (one session talking to another).

Covers:
- RelayGuard loop/cooldown/rate rules
- build_relay_prompt framing
- ApiServer POST /api/threads/{thread_id}/message
- ClaudeChatCog.deliver_relayed_message queue vs interrupt behaviour
"""

from __future__ import annotations

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.database.models import init_db
from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.ext.api_server import ApiServer
from claude_discord.relay import (
    MAX_HOP,
    MAX_MESSAGES_PER_WINDOW,
    PAIR_COOLDOWN_SECONDS,
    SENDER_WINDOW_SECONDS,
    RelayGuard,
    build_relay_prompt,
)

A, B = 111, 222

# ---------------------------------------------------------------------------
# RelayGuard
# ---------------------------------------------------------------------------


def test_first_message_between_two_threads_is_allowed() -> None:
    assert RelayGuard().check(from_thread=A, to_thread=B, hop=0, now=0.0) is None


def test_a_thread_cannot_talk_to_itself() -> None:
    reason = RelayGuard().check(from_thread=A, to_thread=A, hop=0, now=0.0)
    assert reason is not None and "itself" in reason


def test_hop_limit_stops_an_endless_chain() -> None:
    guard = RelayGuard()
    assert guard.check(from_thread=A, to_thread=B, hop=MAX_HOP, now=0.0) is None
    reason = guard.check(from_thread=A, to_thread=B, hop=MAX_HOP + 1, now=0.0)
    assert reason is not None and "Hop limit" in reason


def test_negative_hop_is_rejected() -> None:
    assert RelayGuard().check(from_thread=A, to_thread=B, hop=-1, now=0.0) is not None


def test_pair_cooldown_blocks_rapid_back_to_back_messages() -> None:
    guard = RelayGuard()
    guard.record(from_thread=A, to_thread=B, now=0.0)

    assert guard.check(from_thread=A, to_thread=B, hop=0, now=1.0) is not None
    assert guard.check(from_thread=A, to_thread=B, hop=0, now=PAIR_COOLDOWN_SECONDS + 1) is None


def test_cooldown_is_per_direction_and_per_pair() -> None:
    guard = RelayGuard()
    guard.record(from_thread=A, to_thread=B, now=0.0)

    # The reply travels the other way and must not be blocked by A's cooldown.
    assert guard.check(from_thread=B, to_thread=A, hop=1, now=1.0) is None
    # A different recipient is a different conversation.
    assert guard.check(from_thread=A, to_thread=999, hop=0, now=1.0) is None


def test_sender_rate_limit_and_window_expiry() -> None:
    guard = RelayGuard()
    for i in range(MAX_MESSAGES_PER_WINDOW):
        guard.record(from_thread=A, to_thread=1000 + i, now=float(i))

    reason = guard.check(from_thread=A, to_thread=2000, hop=0, now=10.0)
    assert reason is not None and "Rate limit" in reason

    # Once the window rolls past, the sender is allowed again.
    assert (
        guard.check(from_thread=A, to_thread=2000, hop=0, now=SENDER_WINDOW_SECONDS + 100) is None
    )


def test_relay_prompt_marks_the_sender_and_the_reply_path() -> None:
    prompt = build_relay_prompt(text="please stand down", from_thread=A, hop=1)

    assert "please stand down" in prompt
    assert str(A) in prompt
    assert "NOT from your human" in prompt
    assert "hop=2" in prompt  # tells the receiver what to send back


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_path() -> str:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(path)
    yield path
    os.unlink(path)


@pytest.fixture
def cog() -> MagicMock:
    c = MagicMock()
    c.deliver_relayed_message = AsyncMock()
    return c


@pytest.fixture
def thread() -> MagicMock:
    t = MagicMock(spec=discord.Thread)
    t.id = B
    return t


@pytest.fixture
def bot(cog: MagicMock, thread: MagicMock) -> MagicMock:
    b = MagicMock()
    b.cogs = {"ClaudeChatCog": cog}
    b.get_channel.return_value = thread
    b.fetch_channel = AsyncMock(side_effect=RuntimeError("Unknown Channel"))
    return b


@pytest.fixture
async def api_client(db_path: str, bot: MagicMock) -> TestClient:
    notif_repo = NotificationRepository(db_path)
    await notif_repo.init_db()
    api = ApiServer(repo=notif_repo, bot=bot, host="127.0.0.1", port=0)
    client = TestClient(TestServer(api.app))
    await client.start_server()
    yield client
    await client.close()


async def test_relay_delivers_wrapped_message_in_queue_mode_by_default(
    api_client: TestClient, cog: MagicMock, thread: MagicMock
) -> None:
    resp = await api_client.post(
        f"/api/threads/{B}/message", json={"text": "I started first", "from_thread": A}
    )

    assert resp.status == 202
    assert (await resp.json())["mode"] == "queue"

    await asyncio.sleep(0)  # let the background delivery task run
    cog.deliver_relayed_message.assert_awaited_once()
    args, kwargs = cog.deliver_relayed_message.call_args
    assert args[0] is thread
    assert "I started first" in args[1]
    assert str(A) in args[1]
    assert kwargs["interrupt"] is False


async def test_interrupt_mode_is_passed_through(api_client: TestClient, cog: MagicMock) -> None:
    resp = await api_client.post(
        f"/api/threads/{B}/message",
        json={"text": "stop now, I have this", "from_thread": A, "mode": "interrupt"},
    )

    assert resp.status == 202
    await asyncio.sleep(0)
    assert cog.deliver_relayed_message.call_args.kwargs["interrupt"] is True


async def test_second_relay_to_same_thread_is_rate_limited(
    api_client: TestClient, cog: MagicMock
) -> None:
    first = await api_client.post(
        f"/api/threads/{B}/message", json={"text": "hello", "from_thread": A}
    )
    second = await api_client.post(
        f"/api/threads/{B}/message", json={"text": "hello again", "from_thread": A}
    )

    assert first.status == 202
    assert second.status == 429
    await asyncio.sleep(0)
    assert cog.deliver_relayed_message.await_count == 1


async def test_relay_beyond_hop_limit_is_refused(api_client: TestClient, cog: MagicMock) -> None:
    resp = await api_client.post(
        f"/api/threads/{B}/message",
        json={"text": "still talking", "from_thread": A, "hop": MAX_HOP + 1},
    )

    assert resp.status == 429
    await asyncio.sleep(0)
    cog.deliver_relayed_message.assert_not_awaited()


async def test_relay_to_self_is_refused(api_client: TestClient) -> None:
    resp = await api_client.post(
        f"/api/threads/{B}/message", json={"text": "hi me", "from_thread": B}
    )
    assert resp.status == 429


@pytest.mark.parametrize(
    "payload",
    [
        {"from_thread": A},
        {"text": "  ", "from_thread": A},
        {"text": "hi"},
        {"text": "hi", "from_thread": "abc"},
        {"text": "hi", "from_thread": A, "mode": "shout"},
        {"text": "hi", "from_thread": A, "hop": "far"},
        {"text": "x" * 4001, "from_thread": A},
    ],
)
async def test_relay_rejects_bad_input(api_client: TestClient, payload: dict) -> None:
    resp = await api_client.post(f"/api/threads/{B}/message", json=payload)
    assert resp.status == 400


async def test_relay_to_unknown_thread_returns_404(api_client: TestClient, bot: MagicMock) -> None:
    bot.get_channel.return_value = None
    resp = await api_client.post(f"/api/threads/{B}/message", json={"text": "hi", "from_thread": A})
    assert resp.status == 404


async def test_relay_to_non_thread_channel_is_rejected(
    api_client: TestClient, bot: MagicMock
) -> None:
    bot.get_channel.return_value = MagicMock(spec=discord.TextChannel)
    resp = await api_client.post(f"/api/threads/{B}/message", json={"text": "hi", "from_thread": A})
    assert resp.status == 400


# ---------------------------------------------------------------------------
# ClaudeChatCog.deliver_relayed_message
# ---------------------------------------------------------------------------


def _make_cog() -> MagicMock:
    from claude_discord.cogs.claude_chat import ClaudeChatCog

    bot = MagicMock()
    repo = MagicMock()
    repo.get = AsyncMock(return_value=None)
    runner = MagicMock()
    runner.clone = MagicMock(return_value=MagicMock())
    return ClaudeChatCog(bot=bot, repo=repo, runner=runner)


def _make_target_thread(thread_id: int = B) -> MagicMock:
    thread = MagicMock(spec=discord.Thread)
    thread.id = thread_id
    thread.parent_id = 999
    thread.send = AsyncMock()
    return thread


async def test_delivery_posts_the_message_so_humans_can_see_it() -> None:
    cog = _make_cog()
    thread = _make_target_thread()
    cog._run_claude = AsyncMock()

    await cog.deliver_relayed_message(thread, "relayed text", interrupt=False)

    assert "relayed text" in thread.send.call_args_list[0].args[0]
    cog._run_claude.assert_awaited_once()


async def test_interrupt_true_delegates_preemption_to_run_claude() -> None:
    """interrupt=True must ask _run_claude to preempt the in-flight turn.

    Eviction/interrupt now lives in _run_claude (the single per-thread run
    slot); deliver_relayed_message only forwards the intent. The actual SIGINT
    is covered by test_claude_chat's _evict_active_run tests.
    """
    cog = _make_cog()
    thread = _make_target_thread()
    running = MagicMock()
    running.interrupt = AsyncMock()
    cog._active_runners[thread.id] = running
    cog._run_claude = AsyncMock()

    await cog.deliver_relayed_message(thread, "stop now", interrupt=True)

    cog._run_claude.assert_awaited_once()
    _, kwargs = cog._run_claude.call_args
    assert kwargs.get("interrupt_existing") is True


async def test_queue_mode_forwards_no_preemption() -> None:
    """The default must not cost the receiver its in-flight work.

    deliver_relayed_message forwards interrupt_existing=False; _run_claude then
    queues behind the current turn (proven by test_claude_chat's queue-mode
    _evict_active_run test and the real-overlap test).
    """
    cog = _make_cog()
    thread = _make_target_thread()
    running = MagicMock()
    running.interrupt = AsyncMock()
    cog._active_runners[thread.id] = running
    cog._run_claude = AsyncMock()

    await cog.deliver_relayed_message(thread, "when you get a moment", interrupt=False)

    cog._run_claude.assert_awaited_once()
    _, kwargs = cog._run_claude.call_args
    assert kwargs.get("interrupt_existing") is False


async def test_delivery_to_a_deleted_thread_is_swallowed_with_a_warning(caplog) -> None:
    """A relay target that no longer exists must not leak an unhandled task exception.

    api_server.py fires this coroutine via ``asyncio.create_task`` with nothing
    awaiting the result, so an uncaught NotFound here surfaces only as
    "Task exception was never retrieved" with no thread context (production
    incident: 2026-09-19). It must be caught and logged instead of propagating.
    """
    cog = _make_cog()
    thread = _make_target_thread()
    thread.send = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "Unknown Channel"))
    cog._run_claude = AsyncMock()

    with caplog.at_level("WARNING"):
        await cog.deliver_relayed_message(thread, "relayed text", interrupt=False)

    cog._run_claude.assert_not_awaited()
    assert any(str(thread.id) in record.message for record in caplog.records)
