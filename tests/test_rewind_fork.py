"""Tests for /rewind and /fork slash commands.

/rewind — Rewind conversation history to a selected earlier turn by
          truncating the session JSONL.  The DB session record is preserved
          so ``--resume`` works from the rewound state.  When no JSONL is
          found (or it is empty), falls back to a full session reset.
/fork   — Create a new thread continuing this conversation from the same point.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from claude_discord.cogs.claude_chat import ClaudeChatCog
from claude_discord.database.repository import SessionRecord


def _make_cog() -> ClaudeChatCog:
    """Return a ClaudeChatCog with minimal mocked dependencies."""
    bot = MagicMock()
    bot.channel_id = 999
    repo = MagicMock()
    repo.get = AsyncMock(return_value=None)
    repo.save = AsyncMock()
    repo.delete = AsyncMock(return_value=True)
    runner = MagicMock()
    runner.clone = MagicMock(return_value=MagicMock())
    return ClaudeChatCog(bot=bot, repo=repo, runner=runner)


def _make_thread_interaction(thread_id: int = 12345) -> MagicMock:
    """Return an Interaction whose channel is a discord.Thread."""
    interaction = MagicMock(spec=discord.Interaction)
    thread = MagicMock(spec=discord.Thread)
    thread.id = thread_id
    interaction.channel = thread
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.defer = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    return interaction


def _make_channel_interaction() -> MagicMock:
    """Return an Interaction whose channel is NOT a thread."""
    interaction = MagicMock(spec=discord.Interaction)
    interaction.channel = MagicMock(spec=discord.TextChannel)
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    return interaction


def _make_session_record(
    thread_id: int = 12345,
    session_id: str = "sess-abc",
    working_dir: str | None = "/tmp/work",
    context_window: int | None = None,
    context_used: int | None = None,
    backend: str | None = None,
) -> SessionRecord:
    return SessionRecord(
        thread_id=thread_id,
        session_id=session_id,
        working_dir=working_dir,
        model=None,
        origin="discord",
        summary=None,
        created_at="2026-01-01 00:00:00",
        last_used_at="2026-01-01 00:00:00",
        context_window=context_window,
        context_used=context_used,
        backend=backend,
    )


# ---------------------------------------------------------------------------
# /rewind — guard-clause tests (no JSONL needed)
# ---------------------------------------------------------------------------


class TestRewindCommand:
    @pytest.mark.asyncio
    async def test_rewind_outside_thread_sends_ephemeral(self) -> None:
        """Using /rewind outside a thread shows an ephemeral error."""
        cog = _make_cog()
        interaction = _make_channel_interaction()

        await cog.rewind_session.callback(cog, interaction)

        interaction.response.send_message.assert_called_once()
        assert interaction.response.send_message.call_args.kwargs.get("ephemeral") is True

    @pytest.mark.asyncio
    async def test_rewind_no_session_sends_ephemeral(self) -> None:
        """Using /rewind when no session exists shows an ephemeral notice."""
        cog = _make_cog()
        cog.repo.get = AsyncMock(return_value=None)
        interaction = _make_thread_interaction()

        await cog.rewind_session.callback(cog, interaction)

        interaction.response.send_message.assert_called_once()
        assert interaction.response.send_message.call_args.kwargs.get("ephemeral") is True

    # ---------------------------------------------------------------------------
    # /rewind — fallback (no JSONL / empty history → behaves like /clear)
    # ---------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_rewind_no_jsonl_falls_back_to_clear(self) -> None:
        """/rewind with no JSONL found falls back to a full session reset."""
        cog = _make_cog()
        thread_id = 12345
        cog.repo.get = AsyncMock(return_value=_make_session_record(thread_id))
        cog.repo.delete = AsyncMock(return_value=True)
        interaction = _make_thread_interaction(thread_id=thread_id)

        with patch("claude_discord.cogs.claude_chat.find_session_jsonl", return_value=None):
            await cog.rewind_session.callback(cog, interaction)

        # DB should be cleared (fallback behaviour)
        cog.repo.delete.assert_called_once_with(thread_id)
        interaction.response.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_rewind_empty_turns_falls_back_to_clear(self) -> None:
        """/rewind with an empty turn list falls back to a full session reset."""
        cog = _make_cog()
        thread_id = 12345
        fake_jsonl = MagicMock(spec=Path)
        cog.repo.get = AsyncMock(return_value=_make_session_record(thread_id))
        cog.repo.delete = AsyncMock(return_value=True)
        interaction = _make_thread_interaction(thread_id=thread_id)

        with (
            patch("claude_discord.cogs.claude_chat.find_session_jsonl", return_value=fake_jsonl),
            patch("claude_discord.cogs.claude_chat.parse_user_turns", return_value=[]),
        ):
            await cog.rewind_session.callback(cog, interaction)

        cog.repo.delete.assert_called_once_with(thread_id)

    @pytest.mark.asyncio
    async def test_rewind_fallback_kills_active_runner(self) -> None:
        """/rewind fallback stops any running Claude process."""
        cog = _make_cog()
        thread_id = 12345
        cog.repo.get = AsyncMock(return_value=_make_session_record(thread_id))
        cog.repo.delete = AsyncMock(return_value=True)
        interaction = _make_thread_interaction(thread_id=thread_id)

        mock_runner = MagicMock()
        mock_runner.kill = AsyncMock()
        cog._active_runners[thread_id] = mock_runner

        with patch("claude_discord.cogs.claude_chat.find_session_jsonl", return_value=None):
            await cog.rewind_session.callback(cog, interaction)

        mock_runner.kill.assert_called_once()

    # ---------------------------------------------------------------------------
    # /rewind — non-Claude backends (no Claude JSONL → must NOT wipe the session)
    # ---------------------------------------------------------------------------

    @pytest.mark.asyncio
    @pytest.mark.parametrize("backend", ["codex", "local", "pi", "agui"])
    async def test_rewind_non_claude_session_keeps_record(self, backend: str) -> None:
        """/rewind on a non-Claude session refuses instead of silently resetting it."""
        cog = _make_cog()
        thread_id = 12345
        cog.repo.get = AsyncMock(return_value=_make_session_record(thread_id, backend=backend))
        cog.repo.delete = AsyncMock(return_value=True)
        interaction = _make_thread_interaction(thread_id=thread_id)

        mock_runner = MagicMock()
        mock_runner.kill = AsyncMock()
        cog._active_runners[thread_id] = mock_runner

        with patch("claude_discord.cogs.claude_chat.find_session_jsonl") as find_jsonl:
            await cog.rewind_session.callback(cog, interaction)

        cog.repo.delete.assert_not_called()
        mock_runner.kill.assert_not_called()
        find_jsonl.assert_not_called()
        interaction.response.send_message.assert_called_once()
        kwargs = interaction.response.send_message.call_args.kwargs
        assert kwargs.get("ephemeral") is True
        message = interaction.response.send_message.call_args.args[0]
        assert backend in message
        assert "/clear" in message

    @pytest.mark.asyncio
    async def test_rewind_claude_session_still_falls_back_when_no_jsonl(self) -> None:
        """An explicit claude backend keeps the existing fallback behaviour."""
        cog = _make_cog()
        thread_id = 12345
        cog.repo.get = AsyncMock(return_value=_make_session_record(thread_id, backend="claude"))
        cog.repo.delete = AsyncMock(return_value=True)
        interaction = _make_thread_interaction(thread_id=thread_id)

        with patch("claude_discord.cogs.claude_chat.find_session_jsonl", return_value=None):
            await cog.rewind_session.callback(cog, interaction)

        cog.repo.delete.assert_called_once_with(thread_id)

    # ---------------------------------------------------------------------------
    # /rewind — happy path (JSONL with turns → show Select menu)
    # ---------------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_rewind_with_turns_shows_select_menu(self) -> None:
        """/rewind with history shows a RewindSelectView, does NOT delete DB."""
        from claude_discord.claude.rewind import TurnEntry

        cog = _make_cog()
        thread_id = 12345
        cog.repo.get = AsyncMock(return_value=_make_session_record(thread_id))
        cog.repo.delete = AsyncMock(return_value=True)
        interaction = _make_thread_interaction(thread_id=thread_id)

        fake_jsonl = MagicMock(spec=Path)
        fake_turns = [
            TurnEntry(line_index=0, uuid="u1", timestamp=None, text="Hello Claude"),
            TurnEntry(line_index=2, uuid="u2", timestamp=None, text="What is 2+2?"),
        ]

        with (
            patch("claude_discord.cogs.claude_chat.find_session_jsonl", return_value=fake_jsonl),
            patch("claude_discord.cogs.claude_chat.parse_user_turns", return_value=fake_turns),
        ):
            await cog.rewind_session.callback(cog, interaction)

        # DB must NOT be deleted when history exists
        cog.repo.delete.assert_not_called()
        # A message with a view should be sent
        interaction.response.send_message.assert_called_once()
        call_kwargs = interaction.response.send_message.call_args.kwargs
        assert "view" in call_kwargs

    @pytest.mark.asyncio
    async def test_rewind_with_turns_does_not_delete_session(self) -> None:
        """/rewind with turns keeps the session record so --resume works."""
        from claude_discord.claude.rewind import TurnEntry

        cog = _make_cog()
        thread_id = 12345
        cog.repo.get = AsyncMock(return_value=_make_session_record(thread_id))
        interaction = _make_thread_interaction(thread_id=thread_id)

        fake_turns = [TurnEntry(line_index=0, uuid="u1", timestamp=None, text="test")]

        with (
            patch(
                "claude_discord.cogs.claude_chat.find_session_jsonl",
                return_value=MagicMock(spec=Path),
            ),
            patch("claude_discord.cogs.claude_chat.parse_user_turns", return_value=fake_turns),
        ):
            await cog.rewind_session.callback(cog, interaction)

        cog.repo.delete.assert_not_called()

    @pytest.mark.asyncio
    async def test_rewind_shows_context_stats_in_prompt(self) -> None:
        """/rewind prompt includes context % when stats are available."""
        from claude_discord.claude.rewind import TurnEntry

        cog = _make_cog()
        thread_id = 12345
        record = _make_session_record(
            thread_id=thread_id, context_window=200000, context_used=150000
        )
        cog.repo.get = AsyncMock(return_value=record)
        interaction = _make_thread_interaction(thread_id=thread_id)

        fake_turns = [TurnEntry(line_index=0, uuid="u1", timestamp=None, text="test")]

        with (
            patch(
                "claude_discord.cogs.claude_chat.find_session_jsonl",
                return_value=MagicMock(spec=Path),
            ),
            patch("claude_discord.cogs.claude_chat.parse_user_turns", return_value=fake_turns),
        ):
            await cog.rewind_session.callback(cog, interaction)

        content: str = interaction.response.send_message.call_args.args[0]
        assert "75" in content  # 150000/200000 = 75%


# ---------------------------------------------------------------------------
# /fork
# ---------------------------------------------------------------------------


class TestForkCommand:
    @pytest.mark.asyncio
    async def test_fork_outside_thread_sends_ephemeral(self) -> None:
        """Using /fork outside a thread shows an ephemeral error."""
        cog = _make_cog()
        interaction = _make_channel_interaction()

        await cog.fork_session.callback(cog, interaction)

        interaction.response.send_message.assert_called_once()
        assert interaction.response.send_message.call_args.kwargs.get("ephemeral") is True

    @pytest.mark.asyncio
    async def test_fork_no_session_sends_ephemeral(self) -> None:
        """Using /fork when no session exists shows an ephemeral error."""
        cog = _make_cog()
        cog.repo.get = AsyncMock(return_value=None)
        interaction = _make_thread_interaction()

        await cog.fork_session.callback(cog, interaction)

        interaction.response.send_message.assert_called_once()
        assert interaction.response.send_message.call_args.kwargs.get("ephemeral") is True

    @pytest.mark.asyncio
    async def test_fork_creates_new_thread(self) -> None:
        """/fork creates a new Discord thread in the parent channel."""
        cog = _make_cog()
        thread_id = 12345
        session_id = "sess-abc"
        record = _make_session_record(thread_id=thread_id, session_id=session_id)
        cog.repo.get = AsyncMock(return_value=record)

        interaction = _make_thread_interaction(thread_id=thread_id)
        parent_channel = MagicMock(spec=discord.TextChannel)
        interaction.channel.parent = parent_channel

        new_thread = MagicMock(spec=discord.Thread)
        new_thread.id = 99999
        new_thread.mention = "<#99999>"

        with patch.object(
            cog, "spawn_session", new=AsyncMock(return_value=new_thread)
        ) as mock_spawn:
            await cog.fork_session.callback(cog, interaction)

        mock_spawn.assert_called_once()

    @pytest.mark.asyncio
    async def test_fork_uses_same_session_id(self) -> None:
        """/fork passes the current session_id to spawn_session for conversation continuity."""
        cog = _make_cog()
        thread_id = 12345
        session_id = "sess-abc"
        record = _make_session_record(thread_id=thread_id, session_id=session_id)
        cog.repo.get = AsyncMock(return_value=record)

        interaction = _make_thread_interaction(thread_id=thread_id)
        parent_channel = MagicMock(spec=discord.TextChannel)
        interaction.channel.parent = parent_channel

        new_thread = MagicMock(spec=discord.Thread)
        new_thread.id = 99999
        new_thread.mention = "<#99999>"

        with patch.object(
            cog, "spawn_session", new=AsyncMock(return_value=new_thread)
        ) as mock_spawn:
            await cog.fork_session.callback(cog, interaction)

        call_kwargs = mock_spawn.call_args.kwargs
        assert call_kwargs.get("session_id") == session_id

    @pytest.mark.asyncio
    async def test_fork_sends_link_to_new_thread(self) -> None:
        """/fork replies with a link to the newly created thread."""
        cog = _make_cog()
        thread_id = 12345
        record = _make_session_record(thread_id=thread_id)
        cog.repo.get = AsyncMock(return_value=record)

        interaction = _make_thread_interaction(thread_id=thread_id)
        parent_channel = MagicMock(spec=discord.TextChannel)
        interaction.channel.parent = parent_channel

        new_thread = MagicMock(spec=discord.Thread)
        new_thread.id = 99999
        new_thread.mention = "<#99999>"

        with patch.object(cog, "spawn_session", new=AsyncMock(return_value=new_thread)):
            await cog.fork_session.callback(cog, interaction)

        # /fork uses defer+followup so the fork link appears via followup.send.
        interaction.followup.send.assert_called_once()
        content: str = interaction.followup.send.call_args.args[0]
        # The reply should reference the new thread.
        assert "<#99999>" in content

    @pytest.mark.asyncio
    async def test_fork_no_parent_channel_sends_ephemeral(self) -> None:
        """/fork in a thread without a parent channel shows an ephemeral error.

        This can happen when the thread's parent channel is unavailable (e.g.
        a DM thread, or a thread the bot can't see).
        """
        cog = _make_cog()
        thread_id = 12345
        record = _make_session_record(thread_id=thread_id)
        cog.repo.get = AsyncMock(return_value=record)

        interaction = _make_thread_interaction(thread_id=thread_id)
        interaction.channel.parent = None  # No parent channel

        await cog.fork_session.callback(cog, interaction)

        interaction.response.send_message.assert_called_once()
        assert interaction.response.send_message.call_args.kwargs.get("ephemeral") is True

    @pytest.mark.asyncio
    async def test_fork_passes_fork_true_to_spawn_session(self) -> None:
        """/fork calls spawn_session with fork=True so --fork-session is used."""
        cog = _make_cog()
        thread_id = 12345
        session_id = "sess-abc"
        record = _make_session_record(thread_id=thread_id, session_id=session_id)
        cog.repo.get = AsyncMock(return_value=record)

        interaction = _make_thread_interaction(thread_id=thread_id)
        parent_channel = MagicMock(spec=discord.TextChannel)
        interaction.channel.parent = parent_channel

        new_thread = MagicMock(spec=discord.Thread)
        new_thread.id = 99999
        new_thread.mention = "<#99999>"

        with patch.object(
            cog, "spawn_session", new=AsyncMock(return_value=new_thread)
        ) as mock_spawn:
            await cog.fork_session.callback(cog, interaction)

        call_kwargs = mock_spawn.call_args.kwargs
        assert call_kwargs.get("fork") is True
