"""Tests for the Thread Inbox feature.

Covers: ThreadInboxRepository, inbox_classifier, ThreadStatusDashboard inbox section.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from claude_discord.database.inbox_repo import ThreadInboxRepository
from claude_discord.discord_ui.thread_dashboard import ThreadStatusDashboard

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path):
    """Temporary SQLite DB with the thread_inbox table."""
    import sqlite3

    from claude_discord.database.models import SCHEMA

    path = str(tmp_path / "test.db")
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def repo(db_path):
    return ThreadInboxRepository(db_path)


def _make_channel():
    channel = MagicMock(spec=discord.TextChannel)
    msg = MagicMock(spec=discord.Message)
    msg.edit = AsyncMock()
    channel.send = AsyncMock(return_value=msg)
    return channel


# ---------------------------------------------------------------------------
# ThreadInboxRepository tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_and_list(repo):
    await repo.upsert(111, "waiting", "high", "https://discord.com/channels/1/2/3")
    entries = await repo.list_all()
    assert len(entries) == 1
    assert entries[0].thread_id == 111
    assert entries[0].status == "waiting"
    assert entries[0].confidence == "high"
    assert entries[0].last_message_url == "https://discord.com/channels/1/2/3"


@pytest.mark.asyncio
async def test_upsert_updates_existing(repo):
    await repo.upsert(111, "waiting", "high", "https://example.com/1")
    await repo.upsert(111, "ambiguous", "low", "https://example.com/2")
    entries = await repo.list_all()
    assert len(entries) == 1
    assert entries[0].status == "ambiguous"
    assert entries[0].last_message_url == "https://example.com/2"


@pytest.mark.asyncio
async def test_remove_existing(repo):
    await repo.upsert(111, "waiting")
    removed = await repo.remove(111)
    assert removed is True
    assert await repo.list_all() == []


@pytest.mark.asyncio
async def test_remove_nonexistent(repo):
    removed = await repo.remove(999)
    assert removed is False


@pytest.mark.asyncio
async def test_list_all_empty(repo):
    assert await repo.list_all() == []


@pytest.mark.asyncio
async def test_multiple_entries(repo):
    await repo.upsert(111, "waiting", "high")
    await repo.upsert(222, "ambiguous", "low")
    entries = await repo.list_all()
    assert len(entries) == 2
    thread_ids = {e.thread_id for e in entries}
    assert thread_ids == {111, 222}


# ---------------------------------------------------------------------------
# inbox_classifier tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_classify_waiting():
    from claude_discord.discord_ui.inbox_classifier import classify

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(b"waiting\n", b""))
        mock_exec.return_value = proc
        result = await classify("何かご不明な点はありますか？")
    assert result == "waiting"


@pytest.mark.asyncio
async def test_classify_done():
    from claude_discord.discord_ui.inbox_classifier import classify

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(b"done\n", b""))
        mock_exec.return_value = proc
        result = await classify("実装が完了しました。")
    assert result == "done"


@pytest.mark.asyncio
async def test_classify_ambiguous_on_unexpected_output():
    from claude_discord.discord_ui.inbox_classifier import classify

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(b"I'm not sure\n", b""))
        mock_exec.return_value = proc
        result = await classify("...some text...")
    assert result == "ambiguous"


@pytest.mark.asyncio
async def test_classify_waiting_on_error():
    from claude_discord.discord_ui.inbox_classifier import classify

    with patch("asyncio.create_subprocess_exec", side_effect=OSError("not found")):
        result = await classify("some text")
    assert result == "waiting"


@pytest.mark.asyncio
async def test_classify_ambiguous_on_empty_text():
    from claude_discord.discord_ui.inbox_classifier import classify

    result = await classify("   ")
    assert result == "ambiguous"


# ---------------------------------------------------------------------------
# ThreadStatusDashboard inbox section tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dashboard_shows_inbox_section(repo):
    await repo.upsert(555, "waiting", "high", "https://discord.com/channels/1/555/999")

    channel = _make_channel()
    dashboard = ThreadStatusDashboard(channel=channel)
    await dashboard.initialize()
    await dashboard.refresh_inbox(repo)

    embed_call = channel.send.return_value.edit.call_args
    embed = channel.send.call_args[1]["embed"] if embed_call is None else embed_call[1]["embed"]

    field_names = [f.name for f in embed.fields]
    assert any("📬" in name for name in field_names), f"Inbox section missing: {field_names}"


@pytest.mark.asyncio
async def test_dashboard_inbox_cleared_after_remove(repo):
    await repo.upsert(555, "waiting")

    channel = _make_channel()
    dashboard = ThreadStatusDashboard(channel=channel)
    await dashboard.initialize()
    await dashboard.refresh_inbox(repo)

    # Now remove it
    await repo.remove(555)
    await dashboard.refresh_inbox(repo)

    embed = channel.send.return_value.edit.call_args[1]["embed"]
    field_names = [f.name for f in embed.fields]
    assert not any("555" in name for name in field_names)


@pytest.mark.asyncio
async def test_refresh_inbox_without_entries_shows_no_inbox_section(repo):
    channel = _make_channel()
    dashboard = ThreadStatusDashboard(channel=channel)
    await dashboard.initialize()
    await dashboard.refresh_inbox(repo)

    embed = channel.send.return_value.edit.call_args[1]["embed"]
    field_names = [f.name for f in embed.fields]
    assert not any("📬" in name for name in field_names)


@pytest.mark.parametrize(
    "request_text",
    [
        "Microsoft Teams のスレッドが、サーバー上に1発言1ファイルで同期されています。\n"
        "返信ドラフトを作成してください。",
        "[Teams Channel Exporter]\nPrepare the customer reply.",
    ],
)
async def test_exporter_draft_stays_waiting_without_model_call(request_text):
    from claude_discord.discord_ui.inbox_classifier import classify

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        result = await classify(
            "Thank you for your question. Here is the answer.", request_text=request_text
        )
    assert result == "waiting"
    mock_exec.assert_not_called()


async def test_classifier_receives_request_and_distinguishes_draft_from_delivery():
    from claude_discord.discord_ui.inbox_classifier import classify

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        proc = AsyncMock()
        proc.communicate = AsyncMock(return_value=(b"waiting\n", b""))
        mock_exec.return_value = proc
        result = await classify(
            "Here is the answer.", request_text="Draft a customer response; I will send it."
        )
    assert result == "waiting"
    prompt = mock_exec.call_args.args[2]
    assert "Draft a customer response; I will send it." in prompt
    assert "Preparing a draft is NOT sending it to the customer" in prompt
    assert "Teams Channel Exporter" in prompt


async def test_inbox_helper_passes_request_context_and_keeps_exporter_visible(repo):
    from claude_discord.cogs.event_processor import _classify_and_update_inbox

    with patch("asyncio.create_subprocess_exec") as mock_exec:
        await _classify_and_update_inbox(
            thread_id=555,
            last_text="Thank you for your question.",
            last_message_url=None,
            inbox_repo=repo,
            dashboard=None,
            claude_command="claude",
            request_text="[Teams Channel Exporter] Prepare a reply.",
        )
    mock_exec.assert_not_called()
    entries = await repo.list_all()
    assert any(entry.thread_id == 555 and entry.status == "waiting" for entry in entries)
