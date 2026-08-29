"""Tests for the AI Lounge feature.

Covers:
- LoungeRepository CRUD and pruning
- lounge.build_lounge_prompt() formatting
- ApiServer GET/POST /api/lounge endpoints
- run_claude_in_thread lounge context injection
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.database.lounge_repo import LoungeMessage, LoungeRepository
from claude_discord.database.models import init_db
from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.ext.api_server import ApiServer
from claude_discord.lounge import (
    _NO_MESSAGES,
    MAX_RECOMMENDED_MESSAGE_CHARS,
    build_lounge_prompt,
    length_hint,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def db_path() -> str:
    """Temporary SQLite DB with schema initialized."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    await init_db(path)
    yield path
    os.unlink(path)


@pytest.fixture
async def lounge_repo(db_path: str) -> LoungeRepository:
    return LoungeRepository(db_path)


@pytest.fixture
def bot() -> MagicMock:
    b = MagicMock()
    channel = MagicMock()
    channel.send = AsyncMock()
    b.get_channel.return_value = channel
    return b


@pytest.fixture
async def notif_repo(db_path: str) -> NotificationRepository:
    """NotificationRepository sharing the same DB (simulates real wiring)."""
    # NotificationRepository uses its own table; init_db already ran
    repo = NotificationRepository(db_path)
    await repo.init_db()
    return repo


@pytest.fixture
async def api_client_with_lounge(
    notif_repo: NotificationRepository,
    lounge_repo: LoungeRepository,
    bot: MagicMock,
) -> TestClient:
    api = ApiServer(
        repo=notif_repo,
        bot=bot,
        default_channel_id=12345,
        host="127.0.0.1",
        port=0,
        lounge_repo=lounge_repo,
        lounge_channel_id=99999,
    )
    server = TestServer(api.app)
    client = TestClient(server)
    await client.start_server()
    yield client
    await client.close()


@pytest.fixture
async def api_client_no_lounge(
    notif_repo: NotificationRepository,
    bot: MagicMock,
) -> TestClient:
    """ApiServer without lounge_repo wired up."""
    api = ApiServer(
        repo=notif_repo,
        bot=bot,
        default_channel_id=12345,
        host="127.0.0.1",
        port=0,
    )
    server = TestServer(api.app)
    client = TestClient(server)
    await client.start_server()
    yield client
    await client.close()


# ---------------------------------------------------------------------------
# LoungeRepository tests
# ---------------------------------------------------------------------------


class TestLoungeRepository:
    async def test_post_and_get_recent(self, lounge_repo: LoungeRepository) -> None:
        msg = await lounge_repo.post("Hello from AI!", label="TestBot")
        assert msg.id > 0
        assert msg.label == "TestBot"
        assert msg.message == "Hello from AI!"
        assert msg.posted_at  # non-empty datetime string
        assert msg.thread_id is None  # no thread_id by default

        recent = await lounge_repo.get_recent(limit=10)
        assert len(recent) == 1
        assert recent[0].id == msg.id

    async def test_post_with_thread_id(self, lounge_repo: LoungeRepository) -> None:
        """thread_id is stored and returned correctly."""
        msg = await lounge_repo.post("Working on it", label="Bot", thread_id=123456789)
        assert msg.thread_id == 123456789

        recent = await lounge_repo.get_recent(limit=10)
        assert recent[0].thread_id == 123456789

    async def test_post_without_thread_id(self, lounge_repo: LoungeRepository) -> None:
        """thread_id defaults to None when not provided."""
        msg = await lounge_repo.post("No thread", label="Bot")
        assert msg.thread_id is None

    async def test_get_recent_oldest_first(self, lounge_repo: LoungeRepository) -> None:
        """Messages returned in chronological (oldest-first) order."""
        a = await lounge_repo.post("First", label="A")
        b = await lounge_repo.post("Second", label="B")
        c = await lounge_repo.post("Third", label="C")

        recent = await lounge_repo.get_recent(limit=10)
        ids = [m.id for m in recent]
        assert ids == [a.id, b.id, c.id]

    async def test_get_recent_limit(self, lounge_repo: LoungeRepository) -> None:
        for i in range(5):
            await lounge_repo.post(f"msg {i}", label="Bot")

        recent = await lounge_repo.get_recent(limit=3)
        assert len(recent) == 3
        # Should be the 3 newest, in oldest-first order
        assert recent[-1].message == "msg 4"

    async def test_count(self, lounge_repo: LoungeRepository) -> None:
        assert await lounge_repo.count() == 0
        await lounge_repo.post("one")
        await lounge_repo.post("two")
        assert await lounge_repo.count() == 2

    async def test_label_safety_cap(self, lounge_repo: LoungeRepository) -> None:
        """Labels longer than 50 chars are truncated."""
        long_label = "X" * 100
        msg = await lounge_repo.post("hi", label=long_label)
        assert len(msg.label) <= 50

    async def test_message_safety_cap(self, lounge_repo: LoungeRepository) -> None:
        """Messages longer than 1000 chars are truncated."""
        long_msg = "A" * 2000
        msg = await lounge_repo.post(long_msg, label="Bot")
        assert len(msg.message) <= 1000

    async def test_default_label(self, lounge_repo: LoungeRepository) -> None:
        msg = await lounge_repo.post("no label given")
        assert msg.label == "AI"

    async def test_pruning_keeps_max_messages(self, lounge_repo: LoungeRepository) -> None:
        """After exceeding _MAX_STORED_MESSAGES, old messages are pruned."""
        from claude_discord.database.lounge_repo import _MAX_STORED_MESSAGES

        # Insert max + 5 messages
        for i in range(_MAX_STORED_MESSAGES + 5):
            await lounge_repo.post(f"msg {i}", label="Bot")

        count = await lounge_repo.count()
        assert count == _MAX_STORED_MESSAGES


# ---------------------------------------------------------------------------
# build_lounge_prompt tests
# ---------------------------------------------------------------------------


class TestBuildLoungePrompt:
    def test_empty_returns_no_messages_placeholder(self) -> None:
        result = build_lounge_prompt([])
        assert _NO_MESSAGES.strip() in result

    def test_messages_included(self) -> None:
        messages = [
            LoungeMessage(
                id=1, label="BotA", message="Starting work", posted_at="2026-02-21 10:00:00"
            ),
            LoungeMessage(
                id=2, label="BotB", message="Good luck!", posted_at="2026-02-21 10:01:00"
            ),
        ]
        result = build_lounge_prompt(messages)
        assert "BotA" in result
        assert "Starting work" in result
        assert "BotB" in result
        assert "Good luck!" in result

    def test_timestamp_trimmed_to_hhmm(self) -> None:
        messages = [
            LoungeMessage(id=1, label="Bot", message="hi", posted_at="2026-02-21 14:30:00"),
        ]
        result = build_lounge_prompt(messages)
        assert "14:30" in result
        # Full datetime should not appear (seconds stripped)
        assert "14:30:00" not in result

    def test_curl_instructions_included(self) -> None:
        """The prompt always explains how to post a message."""
        result = build_lounge_prompt([])
        assert "curl" in result
        assert "CCDB_API_URL" in result
        assert "/api/lounge" in result

    def test_curl_includes_thread_id(self) -> None:
        """The curl example includes thread_id in the JSON body."""
        result = build_lounge_prompt([])
        assert "thread_id" in result
        assert "DISCORD_THREAD_ID" in result

    def test_prompt_draws_the_line_between_lounge_and_apis(self) -> None:
        """The prompt tells sessions to use the APIs for discovery/locking, not the lounge."""
        result = build_lounge_prompt([])
        assert "/api/sessions" in result
        assert "/api/claims" in result
        # And it names the lounge's own remaining job (broadcast / intent).
        assert "BROADCAST" in result

    def test_prompt_states_the_length_limit(self) -> None:
        """The prompt gives a number, not just 'keep it short'.

        Sessions demonstrably talked past the soft wording and posted
        multi-paragraph retrospectives, so the limit is now explicit and
        covers the closing note as well as the opening one.
        """
        result = build_lounge_prompt([])
        assert str(MAX_RECOMMENDED_MESSAGE_CHARS) in result
        assert "closing note" in result

    def test_this_thread_marker_for_matching_thread(self) -> None:
        """Messages from current thread are annotated with [this thread]."""
        messages = [
            LoungeMessage(
                id=1,
                label="BotA",
                message="My earlier work",
                posted_at="2026-03-14 09:00:00",
                thread_id=42,
            ),
            LoungeMessage(
                id=2,
                label="BotB",
                message="Other work",
                posted_at="2026-03-14 09:01:00",
                thread_id=99,
            ),
        ]
        result = build_lounge_prompt(messages, current_thread_id=42)
        # Message from thread 42 should have [this thread]
        assert "[this thread]" in result
        # Only the first message line should have the marker
        lines = result.split("\n")
        thread_marker_lines = [line for line in lines if "[this thread]" in line]
        assert len(thread_marker_lines) == 1
        assert "BotA" in thread_marker_lines[0]
        assert "My earlier work" in thread_marker_lines[0]

    def test_no_marker_when_no_current_thread_id(self) -> None:
        """Without current_thread_id, no [this thread] markers appear."""
        messages = [
            LoungeMessage(
                id=1,
                label="BotA",
                message="work",
                posted_at="2026-03-14 09:00:00",
                thread_id=42,
            ),
        ]
        result = build_lounge_prompt(messages)  # no current_thread_id
        assert "[this thread]" not in result

    def test_no_marker_when_thread_id_is_none(self) -> None:
        """Messages without thread_id are never annotated."""
        messages = [
            LoungeMessage(
                id=1,
                label="BotA",
                message="old message",
                posted_at="2026-03-14 09:00:00",
                thread_id=None,
            ),
        ]
        result = build_lounge_prompt(messages, current_thread_id=42)
        assert "[this thread]" not in result

    def test_all_matching_messages_get_marker(self) -> None:
        """All messages from the current thread get [this thread]."""
        messages = [
            LoungeMessage(
                id=1, label="A", message="m1", posted_at="2026-03-14 09:00:00", thread_id=42
            ),
            LoungeMessage(
                id=2, label="B", message="m2", posted_at="2026-03-14 09:01:00", thread_id=99
            ),
            LoungeMessage(
                id=3, label="C", message="m3", posted_at="2026-03-14 09:02:00", thread_id=42
            ),
        ]
        result = build_lounge_prompt(messages, current_thread_id=42)
        lines = result.split("\n")
        thread_marker_lines = [line for line in lines if "[this thread]" in line]
        assert len(thread_marker_lines) == 2


# ---------------------------------------------------------------------------
# API endpoint tests
# ---------------------------------------------------------------------------


class TestLoungeApiEndpoints:
    async def test_get_lounge_empty(self, api_client_with_lounge: TestClient) -> None:
        resp = await api_client_with_lounge.get("/api/lounge")
        assert resp.status == 200
        data = await resp.json()
        assert data["messages"] == []

    async def test_post_and_get_lounge(self, api_client_with_lounge: TestClient) -> None:
        post_resp = await api_client_with_lounge.post(
            "/api/lounge",
            json={"message": "Testing the lounge!", "label": "TestAI"},
        )
        assert post_resp.status == 201
        body = await post_resp.json()
        assert body["status"] == "posted"
        assert body["label"] == "TestAI"
        assert body["message"] == "Testing the lounge!"

        get_resp = await api_client_with_lounge.get("/api/lounge")
        assert get_resp.status == 200
        data = await get_resp.json()
        assert len(data["messages"]) == 1
        assert data["messages"][0]["label"] == "TestAI"

    async def test_post_lounge_with_thread_id(self, api_client_with_lounge: TestClient) -> None:
        """POST with thread_id stores and returns it."""
        resp = await api_client_with_lounge.post(
            "/api/lounge",
            json={"message": "From thread", "label": "Bot", "thread_id": 987654321},
        )
        assert resp.status == 201
        body = await resp.json()
        assert body["thread_id"] == 987654321

        # GET also returns thread_id
        get_resp = await api_client_with_lounge.get("/api/lounge")
        data = await get_resp.json()
        assert data["messages"][0]["thread_id"] == 987654321

    async def test_post_lounge_without_thread_id(self, api_client_with_lounge: TestClient) -> None:
        """POST without thread_id returns null."""
        resp = await api_client_with_lounge.post(
            "/api/lounge",
            json={"message": "No thread", "label": "Bot"},
        )
        assert resp.status == 201
        body = await resp.json()
        assert body["thread_id"] is None

    async def test_post_lounge_invalid_thread_id_ignored(
        self, api_client_with_lounge: TestClient
    ) -> None:
        """Invalid thread_id is silently ignored (stored as null)."""
        resp = await api_client_with_lounge.post(
            "/api/lounge",
            json={"message": "Bad thread", "label": "Bot", "thread_id": "not-a-number"},
        )
        assert resp.status == 201
        body = await resp.json()
        assert body["thread_id"] is None

    async def test_post_lounge_missing_message(self, api_client_with_lounge: TestClient) -> None:
        resp = await api_client_with_lounge.post("/api/lounge", json={"label": "Bot"})
        assert resp.status == 400
        data = await resp.json()
        assert "message" in data["error"].lower()

    async def test_post_lounge_invalid_json(self, api_client_with_lounge: TestClient) -> None:
        resp = await api_client_with_lounge.post(
            "/api/lounge",
            data="not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status == 400

    async def test_get_lounge_limit_param(self, api_client_with_lounge: TestClient) -> None:
        for i in range(5):
            await api_client_with_lounge.post(
                "/api/lounge", json={"message": f"msg {i}", "label": "Bot"}
            )
        resp = await api_client_with_lounge.get("/api/lounge?limit=3")
        data = await resp.json()
        assert len(data["messages"]) == 3

    async def test_get_lounge_limit_capped_at_50(self, api_client_with_lounge: TestClient) -> None:
        """limit > 50 is silently capped."""
        resp = await api_client_with_lounge.get("/api/lounge?limit=999")
        assert resp.status == 200  # no error, just capped

    async def test_get_lounge_invalid_limit(self, api_client_with_lounge: TestClient) -> None:
        resp = await api_client_with_lounge.get("/api/lounge?limit=abc")
        assert resp.status == 400

    async def test_post_lounge_forwards_to_discord(
        self, api_client_with_lounge: TestClient, bot: MagicMock
    ) -> None:
        """Posting a message sends it to the configured lounge Discord channel."""
        resp = await api_client_with_lounge.post(
            "/api/lounge",
            json={"message": "Hello Discord!", "label": "Claude"},
        )
        assert resp.status == 201
        # bot.get_channel should have been called with lounge_channel_id
        bot.get_channel.assert_called_with(99999)
        channel = bot.get_channel.return_value
        channel.send.assert_called_once()
        call_args = channel.send.call_args[0][0]
        assert "Claude" in call_args
        assert "Hello Discord!" in call_args

    async def test_post_lounge_stores_but_does_not_mirror_when_channel_unset(
        self,
        notif_repo: NotificationRepository,
        lounge_repo: LoungeRepository,
        bot: MagicMock,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Mirror OFF: the message is stored (AI layer intact) with no Discord send."""
        # lounge_channel_id=None falls back to COORDINATION_CHANNEL_ID; clear it so
        # "off" really means off even when the test process has the env var set.
        monkeypatch.delenv("COORDINATION_CHANNEL_ID", raising=False)
        api = ApiServer(
            repo=notif_repo,
            bot=bot,
            default_channel_id=12345,
            host="127.0.0.1",
            port=0,
            lounge_repo=lounge_repo,
            lounge_channel_id=None,  # mirror off
        )
        server = TestServer(api.app)
        client = TestClient(server)
        await client.start_server()
        try:
            resp = await client.post("/api/lounge", json={"message": "db only", "label": "x"})
            assert resp.status == 201
            assert (await lounge_repo.get_recent(limit=1))[0].message == "db only"
            bot.get_channel.return_value.send.assert_not_called()
        finally:
            await client.close()

    async def test_deleted_mirror_channel_disables_itself(
        self, api_client_with_lounge: TestClient, bot: MagicMock
    ) -> None:
        """A deleted channel disables the mirror instead of warning on every post."""
        import discord

        bot.get_channel.return_value = None
        bot.fetch_channel = AsyncMock(
            side_effect=discord.NotFound(MagicMock(status=404), "Unknown Channel")
        )

        first = await api_client_with_lounge.post("/api/lounge", json={"message": "one"})
        second = await api_client_with_lounge.post("/api/lounge", json={"message": "two"})

        assert first.status == 201 and second.status == 201  # posts still succeed (DB saved)
        # The mirror gave up after the first NotFound rather than retrying.
        assert bot.fetch_channel.await_count == 1

    async def test_lounge_503_when_not_configured(self, api_client_no_lounge: TestClient) -> None:
        """GET and POST return 503 when lounge_repo is not wired."""
        get_resp = await api_client_no_lounge.get("/api/lounge")
        assert get_resp.status == 503

        post_resp = await api_client_no_lounge.post("/api/lounge", json={"message": "hi"})
        assert post_resp.status == 503


# ---------------------------------------------------------------------------
# run_claude_in_thread lounge injection tests
# ---------------------------------------------------------------------------


class TestRunHelperLoungeInjection:
    """Verify that lounge context is injected as --append-system-prompt when lounge_repo is set."""

    @pytest.mark.real_system_context
    async def test_lounge_context_injected_as_system_prompt(self) -> None:
        """When lounge_repo has messages, they appear in --append-system-prompt (not user prompt).

        Lounge context is ephemeral metadata that must NOT accumulate in session history.
        Injecting it via --append-system-prompt (system prompt) prevents the "Prompt is too
        long" error that would otherwise occur in long-running sessions.
        """
        from claude_discord.cogs._run_helper import run_claude_in_thread

        lounge_repo_mock = AsyncMock(spec=LoungeRepository)
        lounge_repo_mock.get_recent.return_value = [
            LoungeMessage(
                id=1, label="BotX", message="Busy here!", posted_at="2026-02-21 09:00:00"
            ),
        ]

        captured_prompt: list[str] = []

        async def fake_run(prompt: str, session_id: str | None):
            captured_prompt.append(prompt)
            from claude_discord.claude.types import MessageType, StreamEvent

            result = StreamEvent(message_type=MessageType.RESULT)
            result.is_complete = True
            result.session_id = "sess-123"
            yield result

        thread = MagicMock()
        thread.send = AsyncMock(return_value=MagicMock(embeds=[MagicMock(title="T")]))
        thread.id = 42

        runner = MagicMock()
        runner.working_dir = None
        # clone() returns the same mock so fake_run is accessible from the clone.
        runner.clone.return_value = runner
        runner.run = fake_run

        await run_claude_in_thread(
            thread=thread,
            runner=runner,
            repo=None,
            prompt="Do something cool",
            session_id=None,
            lounge_repo=lounge_repo_mock,
        )

        assert captured_prompt, "runner.run was not called"
        # User prompt is unchanged — lounge context is NOT in the user message.
        assert captured_prompt[0] == "Do something cool"

        # Lounge context is in --append-system-prompt via clone().
        runner.clone.assert_called_once()
        _, kwargs = runner.clone.call_args
        system_prompt = kwargs.get("append_system_prompt", "")
        assert "BotX" in system_prompt
        assert "Busy here!" in system_prompt

    @pytest.mark.real_system_context
    async def test_no_lounge_context_when_repo_is_none(self) -> None:
        """When lounge_repo is None, no lounge messages appear in --append-system-prompt."""
        from claude_discord.cogs._run_helper import run_claude_in_thread

        captured_prompt: list[str] = []

        async def fake_run(prompt: str, session_id: str | None):
            captured_prompt.append(prompt)
            from claude_discord.claude.types import MessageType, StreamEvent

            result = StreamEvent(message_type=MessageType.RESULT)
            result.is_complete = True
            result.session_id = "sess-456"
            yield result

        thread = MagicMock()
        thread.send = AsyncMock(return_value=MagicMock(embeds=[MagicMock(title="T")]))
        thread.id = 99

        runner = MagicMock()
        runner.working_dir = None
        runner.run = fake_run
        runner.clone = MagicMock(return_value=runner)

        user_prompt = "Plain prompt without lounge"
        await run_claude_in_thread(
            thread=thread,
            runner=runner,
            repo=None,
            prompt=user_prompt,
            session_id=None,
            lounge_repo=None,
        )

        assert captured_prompt
        assert captured_prompt[0] == user_prompt
        # clone is called (File Delivery always injected), but no lounge context
        if runner.clone.called:
            system_prompt = runner.clone.call_args[1].get("append_system_prompt", "")
            assert "AI Lounge" not in system_prompt


# ---------------------------------------------------------------------------
# length_hint tests
# ---------------------------------------------------------------------------


class TestLengthHint:
    def test_short_message_gets_no_hint(self) -> None:
        assert length_hint("Fixing a flaky test in ccdb.") is None

    def test_message_at_the_limit_gets_no_hint(self) -> None:
        assert length_hint("x" * MAX_RECOMMENDED_MESSAGE_CHARS) is None

    def test_long_message_gets_a_hint_naming_both_numbers(self) -> None:
        message = "x" * (MAX_RECOMMENDED_MESSAGE_CHARS + 50)
        hint = length_hint(message)
        assert hint is not None
        assert str(MAX_RECOMMENDED_MESSAGE_CHARS) in hint
        assert str(len(message)) in hint
