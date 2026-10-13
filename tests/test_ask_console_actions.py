"""AskUserQuestion options can be answered from the Ebi AI Console."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import discord

from claude_discord.claude.types import AskOption, AskQuestion
from claude_discord.discord_ui.ask_handler import collect_ask_answers
from claude_discord.message_actions import message_actions


def _thread(msg: MagicMock) -> MagicMock:
    thread = MagicMock(spec=discord.Thread)
    thread.id = 8080
    thread.send = AsyncMock(return_value=msg)
    return thread


async def test_single_choice_question_is_answered_from_the_console() -> None:
    msg = MagicMock(id=9090)
    msg.edit = AsyncMock()
    thread = _thread(msg)
    q = AskQuestion(question="Which?", options=[AskOption(label="A"), AskOption(label="B")])

    task = asyncio.create_task(collect_ask_answers(thread, [q], "sid"))
    for _ in range(5):
        await asyncio.sleep(0)
    offered = message_actions.for_message(8080, 9090)
    assert [a["label"] for a in offered] == ["A", "B"]
    assert await message_actions.invoke(8080, 9090, "1", "ebi")

    result = await asyncio.wait_for(task, 5)
    assert result is not None and "Answer: B" in result
    msg.edit.assert_awaited()
    assert message_actions.for_message(8080, 9090) == []


async def test_multi_select_question_is_not_offered() -> None:
    msg = MagicMock(id=9091)
    msg.edit = AsyncMock()
    thread = _thread(msg)
    q = AskQuestion(question="Which?", multi_select=True, options=[AskOption(label="A")])

    task = asyncio.create_task(collect_ask_answers(thread, [q], "sid"))
    for _ in range(5):
        await asyncio.sleep(0)
    assert message_actions.for_message(8080, 9091) == []
    task.cancel()
