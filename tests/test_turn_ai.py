"""The per-turn AI: one turn's backend/model/effort/connection, never stored."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_discord.turn_ai import TurnAI, TurnAIError, parse_turn_ai


def test_a_screen_ai_is_checked_against_what_is_offered() -> None:
    assert parse_turn_ai(None) is None
    ai = parse_turn_ai({"backend": "claude", "model": "opus", "effort": "high"})
    assert ai == TurnAI(backend="claude", model="opus", effort="high")
    assert parse_turn_ai({"backend": "codex", "model": "", "effort": None}).model is None
    for bad in (
        "claude",
        {"backend": "nope"},
        {"backend": "claude", "model": "-x"},
        {"backend": "claude", "model": "a b"},
        {"backend": "claude", "effort": "ludicrous"},
        {"backend": "claude", "connection": "not-a-route"},
        {"backend": "claude", "shell": "rm"},
        {"backend": "claude", "model": 3},
    ):
        with pytest.raises(TurnAIError):
            parse_turn_ai(bad)


async def test_a_chat_thread_turn_uses_the_screen_ai_and_stores_nothing() -> None:
    from claude_discord.cogs.claude_chat import ClaudeChatCog

    cog = ClaudeChatCog.__new__(ClaudeChatCog)
    settings = MagicMock()
    settings.current_backend = AsyncMock(return_value="claude")
    settings.explicit_model = AsyncMock(return_value="sonnet")
    settings.current_model = AsyncMock(return_value="sonnet")
    settings.current_effort = AsyncMock(return_value="medium")
    settings.chosen_connection = AsyncMock(return_value=None)
    settings.repo = None
    factory = MagicMock()
    factory.build.return_value = MagicMock(spec=["allowed_tools", "working_dir", "effort"])
    cog._factory = factory
    cog._backend_settings = settings
    args = dict(
        thread_id=7,
        model_override=None,
        tools_override=None,
        fork_session=False,
        working_dir_override=None,
        effort_override=None,
    )
    runner = await cog._build_runner_for_thread(
        **args, ai=TurnAI(backend="codex", model="gpt-6-astra", effort="high")
    )
    assert factory.build.call_args.kwargs == {
        "backend": "codex",
        "model": "gpt-6-astra",
        "thread_id": 7,
    }
    assert runner.effort == "high"
    for name in ("set_backend", "set_model", "set_effort", "set_connection"):
        assert not getattr(settings, name).called, name
    runner = await cog._build_runner_for_thread(**args)
    assert factory.build.call_args.kwargs["backend"] == "claude"
    assert runner.effort == "medium"
