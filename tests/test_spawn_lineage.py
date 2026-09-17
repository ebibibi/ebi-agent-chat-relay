"""Tests for spawn lineage — matching family codes on parent and child.

The failure this guards against is subtle: everything still "works" when the
lineage is wrong, it just describes the wrong tree. So the assertions are about
agreement (parent and child carry the same code), stability (the same thread
always yields the same code) and restraint (a fan-out renames the parent once).
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from claude_discord.cogs.claude_chat import ClaudeChatCog
from claude_discord.database.lineage_repo import ThreadLineageRepository
from claude_discord.database.models import init_db
from claude_discord.session_view import build_session_views
from claude_discord.thread_marker import (
    DEFAULT_PARENT_MARKER,
    DEFAULT_SPAWN_MARKER,
    MAX_THREAD_NAME_LENGTH,
    PARENT_MARKER_ENV_VAR,
    SPAWN_MARKER_ENV_VAR,
    family_code,
    mark_parent_thread_name,
    mark_spawned_thread_name,
)

PARENT_ID = 1550121254361768021
OTHER_PARENT_ID = 1550125206285320282


@pytest.fixture(autouse=True)
def default_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Markers come from the environment; pin them so assertions are literal."""
    monkeypatch.delenv(SPAWN_MARKER_ENV_VAR, raising=False)
    monkeypatch.delenv(PARENT_MARKER_ENV_VAR, raising=False)


class TestFamilyCode:
    def test_is_stable(self) -> None:
        assert family_code(PARENT_ID) == family_code(PARENT_ID)

    def test_differs_between_threads(self) -> None:
        assert family_code(PARENT_ID) != family_code(OTHER_PARENT_ID)

    def test_adjacent_snowflakes_do_not_collide(self) -> None:
        """Thread IDs are sequential; without hashing, neighbours would look alike."""
        codes = {family_code(PARENT_ID + offset) for offset in range(12)}
        assert len(codes) >= 10

    def test_avoids_ambiguous_characters(self) -> None:
        codes = "".join(family_code(PARENT_ID + offset) for offset in range(200))
        assert not set(codes) & set("01OIL")


class TestMatchingTitles:
    def test_parent_and_child_share_the_code(self) -> None:
        code = family_code(PARENT_ID)
        child = mark_spawned_thread_name("Audit the DNS", parent_thread_id=PARENT_ID)
        parent = mark_parent_thread_name("Nightly triage", PARENT_ID)
        assert child == f"{DEFAULT_SPAWN_MARKER}{code} Audit the DNS"
        assert parent == f"{DEFAULT_PARENT_MARKER}{code} Nightly triage"

    def test_child_of_one_family_that_spawns_another_carries_both(self) -> None:
        child = mark_spawned_thread_name("Investigate", parent_thread_id=PARENT_ID)
        both = mark_parent_thread_name(child, OTHER_PARENT_ID)
        assert both == (
            f"{DEFAULT_SPAWN_MARKER}{family_code(PARENT_ID)} "
            f"{DEFAULT_PARENT_MARKER}{family_code(OTHER_PARENT_ID)} Investigate"
        )

    def test_parent_tagging_is_idempotent(self) -> None:
        once = mark_parent_thread_name("Nightly triage", PARENT_ID)
        assert mark_parent_thread_name(once, PARENT_ID) == once

    def test_child_tagging_is_idempotent(self) -> None:
        once = mark_spawned_thread_name("Audit", parent_thread_id=PARENT_ID)
        assert mark_spawned_thread_name(once, parent_thread_id=PARENT_ID) == once

    def test_without_a_parent_the_bare_marker_is_used(self) -> None:
        assert mark_spawned_thread_name("Audit") == f"{DEFAULT_SPAWN_MARKER} Audit"

    def test_truncation_keeps_the_code(self) -> None:
        marked = mark_spawned_thread_name("x" * 200, parent_thread_id=PARENT_ID)
        assert len(marked) == MAX_THREAD_NAME_LENGTH
        assert marked.startswith(f"{DEFAULT_SPAWN_MARKER}{family_code(PARENT_ID)}")
        tagged = mark_parent_thread_name("y" * 200, PARENT_ID)
        assert len(tagged) == MAX_THREAD_NAME_LENGTH
        assert tagged.startswith(f"{DEFAULT_PARENT_MARKER}{family_code(PARENT_ID)}")

    def test_disabled_markers_leave_names_alone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(SPAWN_MARKER_ENV_VAR, "")
        monkeypatch.setenv(PARENT_MARKER_ENV_VAR, "")
        assert mark_spawned_thread_name("Audit", parent_thread_id=PARENT_ID) == "Audit"
        assert mark_parent_thread_name("Nightly triage", PARENT_ID) == "Nightly triage"


def _cog_with_parent(
    parent_name: str = "Nightly triage",
) -> tuple[ClaudeChatCog, MagicMock, MagicMock]:
    thread = MagicMock(spec=discord.Thread)
    thread.id = 42
    thread.name = "child"
    thread.mention = "<#42>"
    thread.send = AsyncMock()

    channel = MagicMock()
    channel.create_thread = AsyncMock(return_value=thread)

    parent = MagicMock(spec=discord.Thread)
    parent.id = PARENT_ID
    parent.name = parent_name
    parent.mention = f"<#{PARENT_ID}>"
    parent.edit = AsyncMock()
    parent.send = AsyncMock()

    bot = MagicMock()
    bot.get_channel.return_value = parent
    cog = ClaudeChatCog(bot=bot, repo=MagicMock(), runner=MagicMock())
    return cog, channel, parent


class TestSpawnSessionLineage:
    @pytest.mark.asyncio
    async def test_child_title_carries_the_parent_code(self) -> None:
        cog, channel, _ = _cog_with_parent()
        with patch.object(cog, "_run_claude", new=AsyncMock()):
            await cog.spawn_session(
                channel,
                "Do the thing",
                thread_name="Audit the DNS",
                agent_spawned=True,
                parent_thread_id=PARENT_ID,
            )
        name = channel.create_thread.call_args.kwargs["name"]
        assert name == f"{DEFAULT_SPAWN_MARKER}{family_code(PARENT_ID)} Audit the DNS"

    @pytest.mark.asyncio
    async def test_parent_is_renamed_and_both_sides_are_cross_linked(self) -> None:
        cog, channel, parent = _cog_with_parent()
        with patch.object(cog, "_run_claude", new=AsyncMock()):
            thread = await cog.spawn_session(
                channel, "Do the thing", agent_spawned=True, parent_thread_id=PARENT_ID
            )
        parent.edit.assert_awaited_once()
        assert parent.edit.await_args.kwargs["name"] == (
            f"{DEFAULT_PARENT_MARKER}{family_code(PARENT_ID)} Nightly triage"
        )
        assert thread.mention in parent.send.await_args.args[0]
        assert any(parent.mention in call.args[0] for call in thread.send.await_args_list)

    @pytest.mark.asyncio
    async def test_second_spawn_does_not_rename_the_parent_again(self) -> None:
        """Discord allows two renames per ten minutes; a fan-out must not burn them."""
        tagged = mark_parent_thread_name("Nightly triage", PARENT_ID)
        cog, channel, parent = _cog_with_parent(parent_name=tagged)
        with patch.object(cog, "_run_claude", new=AsyncMock()):
            await cog.spawn_session(
                channel, "Do the thing", agent_spawned=True, parent_thread_id=PARENT_ID
            )
        parent.edit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_unreachable_parent_does_not_fail_the_spawn(self) -> None:
        cog, channel, _ = _cog_with_parent()
        cog.bot.get_channel.return_value = None
        cog.bot.fetch_channel = AsyncMock(side_effect=RuntimeError("gone"))
        with patch.object(cog, "_run_claude", new=AsyncMock()):
            thread = await cog.spawn_session(
                channel, "Do the thing", agent_spawned=True, parent_thread_id=PARENT_ID
            )
        assert thread is channel.create_thread.return_value

    @pytest.mark.asyncio
    async def test_rename_failure_does_not_fail_the_spawn(self) -> None:
        cog, channel, parent = _cog_with_parent()
        parent.edit = AsyncMock(side_effect=discord.HTTPException(MagicMock(), "rate limited"))
        with patch.object(cog, "_run_claude", new=AsyncMock()):
            thread = await cog.spawn_session(
                channel, "Do the thing", agent_spawned=True, parent_thread_id=PARENT_ID
            )
        assert thread is channel.create_thread.return_value
        parent.send.assert_awaited()


class TestLineageRepository:
    @pytest.fixture
    async def repo(self) -> ThreadLineageRepository:
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        await init_db(path)
        yield ThreadLineageRepository(path)
        os.unlink(path)

    @pytest.mark.asyncio
    async def test_records_and_reads_back(self, repo: ThreadLineageRepository) -> None:
        await repo.record(42, PARENT_ID, family_code(PARENT_ID))
        links = await repo.list_all()
        assert [(link.thread_id, link.parent_thread_id) for link in links] == [(42, PARENT_ID)]
        assert links[0].family_code == family_code(PARENT_ID)

    @pytest.mark.asyncio
    async def test_a_thread_has_one_parent(self, repo: ThreadLineageRepository) -> None:
        await repo.record(42, PARENT_ID, family_code(PARENT_ID))
        await repo.record(42, OTHER_PARENT_ID, family_code(OTHER_PARENT_ID))
        links = await repo.list_all()
        assert len(links) == 1
        assert links[0].parent_thread_id == OTHER_PARENT_ID


class TestSessionViewsLineage:
    def test_views_report_parent_family_and_children(self) -> None:
        from claude_discord.database.lineage_repo import Lineage

        record = MagicMock()
        record.thread_id = PARENT_ID
        record.session_id = "s1"
        record.working_dir = "/home/ebi"
        record.backend = "claude"
        record.model = None
        record.origin = "discord"
        record.summary = None
        record.created_at = "2026-09-17 22:00:00"
        record.last_used_at = "2026-09-17 22:00:00"

        child = MagicMock()
        child.thread_id = 42
        child.session_id = "s2"
        child.working_dir = "/home/ebi"
        child.backend = "claude"
        child.model = None
        child.origin = "discord"
        child.summary = None
        child.created_at = "2026-09-17 22:05:00"
        child.last_used_at = "2026-09-17 22:05:00"

        views = build_session_views(
            records=[record, child],
            active=[],
            running_thread_ids=set(),
            lounge_messages=[],
            lineage=[
                Lineage(
                    thread_id=42,
                    parent_thread_id=PARENT_ID,
                    family_code=family_code(PARENT_ID),
                    created_at="2026-09-17 22:05:00",
                )
            ],
        )
        by_thread = {view["thread_id"]: view for view in views}
        assert by_thread[42]["parent_thread_id"] == PARENT_ID
        assert by_thread[42]["family"] == family_code(PARENT_ID)
        assert by_thread[PARENT_ID]["children"] == [42]
        assert by_thread[PARENT_ID]["parent_thread_id"] is None

    def test_no_lineage_is_reported_as_empty_not_missing(self) -> None:
        record = MagicMock()
        record.thread_id = 7
        record.session_id = "s"
        record.working_dir = None
        record.backend = None
        record.model = None
        record.origin = "discord"
        record.summary = None
        record.created_at = "2026-09-17 22:00:00"
        record.last_used_at = "2026-09-17 22:00:00"
        view = build_session_views(
            records=[record], active=[], running_thread_ids=set(), lounge_messages=[]
        )[0]
        assert view["parent_thread_id"] is None
        assert view["family"] is None
        assert view["children"] == []
