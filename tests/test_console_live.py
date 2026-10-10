"""The console's live view, Stop, and delivered files."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from claude_code_core.frontend import ActivitySpec, OutboundFile, StatusKind
from claude_discord.console.conversations import ConversationRepository
from claude_discord.console.files import resolve_file
from claude_discord.console.surface import ConsoleFrontend
from claude_discord.database.frontend_thread_repo import FrontendThreadRepository
from claude_discord.database.models import init_db


@pytest.fixture
async def frontend(tmp_path) -> ConsoleFrontend:
    db = str(tmp_path / "sessions.db")
    await init_db(db)
    repo = ConversationRepository(db)
    await repo.init_db()
    return ConsoleFrontend(repo, FrontendThreadRepository(db), files_dir=tmp_path / "console_files")


async def test_activity_status_and_draft_are_live_not_stored(frontend) -> None:
    surface = await frontend.open(external_id="c1", title="t")
    await surface.set_status(StatusKind.TOOL_COMMAND)
    activity = await surface.open_activity(ActivitySpec(kind="tool", title="Bash", detail="ls"))
    await activity.complete("ok")
    stream = surface.open_stream()
    await stream.append("half an ans")

    live = frontend.live.get(surface.thread_key).as_dict()
    assert live["status"] == "tool_command"
    assert live["activities"] == [{"title": "Bash", "detail": "ls", "done": True, "ok": True}]
    assert live["draft"] == "half an ans"
    assert await frontend.repo.history(surface.thread_key, 10) == []

    await stream.append("wer")
    await stream.finalize()
    assert frontend.live.get(surface.thread_key).draft == ""
    [message] = await frontend.repo.history(surface.thread_key, 10)
    assert message.content == "half an answer"


async def test_stop_calls_the_runner_once(frontend) -> None:
    surface = await frontend.open(external_id="c1", title="t")
    stopped: list[bool] = []

    async def on_stop() -> None:
        stopped.append(True)

    handle = await surface.offer_interrupt(on_stop)
    assert frontend.live.get(surface.thread_key).as_dict()["can_stop"] is True
    assert await frontend.live.stop(surface.thread_key) is True
    assert await frontend.live.stop(surface.thread_key) is False
    assert stopped == [True]
    await handle.disable()


async def test_a_disabled_interrupt_cannot_be_pressed(frontend) -> None:
    surface = await frontend.open(external_id="c1", title="t")

    async def on_stop() -> None:
        raise AssertionError("must not run")

    handle = await surface.offer_interrupt(on_stop)
    await handle.disable()
    assert await frontend.live.stop(surface.thread_key) is False


async def test_a_delivered_file_is_kept_and_linked(frontend, tmp_path) -> None:
    surface = await frontend.open(external_id="c1", title="t")
    source = tmp_path / "report.md"
    source.write_text("# hi")

    await surface.deliver_files([OutboundFile(display_name="report.md", path=str(source))])

    [message] = await frontend.repo.history(surface.thread_key, 10)
    link = message.content.split("](")[1].rstrip(")")
    _, key, token, name = link.removeprefix("/console/api/").split("/")
    path = resolve_file(frontend.files_dir, key, token, name)
    assert path is not None and path.read_text() == "# hi"


@pytest.mark.parametrize(
    ("key", "token", "name"),
    [
        ("1", "0" * 32, ".."),
        ("1", "../../etc", "passwd"),
        ("x", "0" * 32, "a"),
        ("1", "0" * 32, "missing.txt"),
    ],
)
def test_file_paths_cannot_escape(tmp_path: Path, key: str, token: str, name: str) -> None:
    (tmp_path / "1" / ("0" * 32)).mkdir(parents=True)
    assert resolve_file(tmp_path, key, token, name) is None


def test_no_files_dir_means_no_files() -> None:
    assert resolve_file(None, "1", "0" * 32, "a") is None


async def test_the_host_clears_live_state_when_the_turn_ends(tmp_path) -> None:
    from unittest.mock import AsyncMock, MagicMock

    from claude_discord.console.host import ConsoleSessionHost

    db = str(tmp_path / "sessions.db")
    await init_db(db)
    repo = ConversationRepository(db)
    await repo.init_db()
    frontend = ConsoleFrontend(repo, FrontendThreadRepository(db))
    seen: list[dict] = []

    async def run_session(config) -> None:
        await config.surface.set_status(StatusKind.THINKING)
        seen.append(frontend.live.get(config.surface.thread_key).as_dict())

    settings = MagicMock()
    settings.current_backend = AsyncMock(return_value="claude")
    settings.current_model = AsyncMock(return_value=None)
    settings.current_effort = AsyncMock(return_value=None)
    settings.repo = None
    sessions = MagicMock()
    sessions.get = AsyncMock(return_value=None)
    host = ConsoleSessionHost(
        frontend=frontend,
        session_repo=sessions,
        backend_factory=MagicMock(codex_command="codex"),
        backend_settings=settings,
        run_session=run_session,
    )
    key = await host.start(external_id="c1", title="t", prompt="p", author="me")
    while host._tasks:
        await asyncio.gather(*list(host._tasks))
    assert seen[0]["status"] == "thinking"
    assert frontend.live.get(key) is None
