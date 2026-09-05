"""Behavioral regressions for idle deadlines and child credential boundaries."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_code_core.codex_runner import CodexRunner
from claude_code_core.runner import ClaudeRunner


@pytest.mark.parametrize("runner_type", [ClaudeRunner, CodexRunner])
async def test_silent_process_hits_its_idle_deadline(runner_type) -> None:
    runner = runner_type(timeout_seconds=0.02)
    process = MagicMock(returncode=0)
    process.stdout = asyncio.StreamReader()
    runner._process = process

    async def consume() -> None:
        with pytest.raises(TimeoutError):
            async for _ in runner._read_stream():
                pass

    # A separate outer deadline makes a broken timeout fail, never hang CI.
    await asyncio.wait_for(consume(), 0.5)


@pytest.mark.parametrize("runner_type", [ClaudeRunner, CodexRunner])
async def test_active_stream_can_outlive_the_idle_deadline(runner_type) -> None:
    runner = runner_type(timeout_seconds=0.1)
    process = MagicMock(returncode=0)
    process.stdout = asyncio.StreamReader()
    runner._process = process

    async def feed() -> None:
        for _ in range(6):
            await asyncio.sleep(0.03)
            process.stdout.feed_data(b"{}\n")
        process.stdout.feed_eof()

    async def consume() -> None:
        async for _ in runner._read_stream():
            pass

    await asyncio.gather(feed(), consume())


@pytest.mark.parametrize("runner_type", [ClaudeRunner, CodexRunner])
def test_inbound_credentials_never_reach_children(runner_type, monkeypatch) -> None:
    credentials = dict.fromkeys(
        ("CCDB_INGEST_TOKEN", "DISCORD_WEBHOOK_URL", "CCDB_API_SECRET"), "test-sentinel"
    )
    with patch.dict(os.environ, {**credentials, "OPENAI_API_KEY": "provider-sentinel"}, clear=True):
        env = runner_type()._build_env()
    assert "CCDB_INGEST_TOKEN" not in env
    assert "DISCORD_WEBHOOK_URL" not in env
    assert "CCDB_API_SECRET" not in env
    assert env["OPENAI_API_KEY"] == "provider-sentinel"


def test_overlay_cannot_restore_transport_credentials(tmp_path: Path, monkeypatch) -> None:
    overlay = tmp_path / "cli.env"
    overlay.write_text("CCDB_INGEST_TOKEN=test-sentinel\nDISCORD_BOT_TOKEN=test-sentinel\n")
    with patch.dict(os.environ, {"CCDB_CLI_ENV_FILE": str(overlay)}, clear=True):
        env = ClaudeRunner(api_secret="explicit-child-secret")._build_env()
    assert "CCDB_INGEST_TOKEN" not in env
    assert "DISCORD_BOT_TOKEN" not in env
    assert env["CCDB_API_SECRET"] == "explicit-child-secret"


async def test_failed_attachment_remains_pending_without_resending_success(tmp_path: Path) -> None:
    from claude_discord.cogs.event_processor import _send_attachment_requests

    paths = [tmp_path / "first.txt", tmp_path / "second.txt"]
    for path in paths:
        path.write_text("result")
    marker = tmp_path / ".ccdb-attachments-42"
    marker.write_text("\n".join(str(path) for path in paths) + "\n")
    surface = MagicMock()
    surface.deliver_files = AsyncMock(side_effect=[None, OSError("offline")])
    surface.send_notice = AsyncMock()

    await _send_attachment_requests(surface, str(tmp_path), 42)

    pending = marker.with_name(marker.name + ".pending")
    assert pending.read_text().splitlines() == [str(paths[1])]
    surface.deliver_files = AsyncMock()
    await _send_attachment_requests(surface, str(tmp_path), 42)
    assert [item.path for item in surface.deliver_files.await_args.args[0]] == [str(paths[1])]
    assert not marker.exists()
    assert not pending.exists()


async def test_discord_surface_reports_file_delivery_failure(tmp_path: Path) -> None:
    from claude_code_core.frontend import OutboundFile
    from claude_discord.surface import DiscordSurface

    output = tmp_path / "result.txt"
    output.write_text("result")
    thread = MagicMock()
    thread.send = AsyncMock(side_effect=OSError("offline"))
    surface = DiscordSurface(thread, working_dir=str(tmp_path))
    with pytest.raises(OSError):
        await surface.deliver_files([OutboundFile(path=str(output), display_name="result.txt")])
