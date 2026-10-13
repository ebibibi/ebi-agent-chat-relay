"""AskUserQuestion interaction handler.

Handles the full lifecycle of AskUserQuestion tool calls from Claude Code:
- Saving question state to DB for restart recovery
- Showing Discord buttons via AskView
- Waiting for user answers (up to 24 hours)
- Returning the formatted answer prompt for Claude to resume
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import discord

from ..claude.types import AskQuestion
from ..database.ask_repo import PendingAskRepository
from ..message_actions import STYLE_PRIMARY, MessageAction, message_actions
from .ask_bus import ask_bus as _ask_bus
from .ask_view import AskView
from .embeds import ask_embed
from .mentions import user_mention_kwargs

logger = logging.getLogger(__name__)

# How long to wait for the user to answer (seconds).  24 hours lets users
# step away for the day and come back without "Interaction Failed" errors.
ASK_ANSWER_TIMEOUT = 86_400  # 24 h


async def collect_ask_answers(
    thread: discord.Thread | discord.TextChannel,
    questions: list[AskQuestion],
    session_id: str,
    ask_repo: PendingAskRepository | None = None,
    notify_user_id: int | None = None,
) -> str | None:
    """Show Discord UI for each question and return the formatted answer string.

    Processes questions sequentially (one at a time).  For each question:
    1. Saves it to the DB (for bot-restart recovery).
    2. Registers a Queue with ask_bus and shows the AskView.
    3. Awaits the answer for up to 24 hours via asyncio.wait_for.
    4. Cleans up the DB entry once answered or timed out.

    Returns a human-readable string to inject as the next human turn, or None
    if no question received an answer.
    """
    # Serialise questions once for DB storage.
    questions_dicts = [
        {
            "question": q.question,
            "header": q.header,
            "multi_select": q.multi_select,
            "options": [{"label": o.label, "description": o.description} for o in q.options],
        }
        for q in questions
    ]

    parts: list[str] = []
    for q_idx, q in enumerate(questions):
        # Persist so on_ready can re-register the view after a bot restart.
        if ask_repo is not None:
            await ask_repo.save(
                thread_id=thread.id,
                session_id=session_id,
                questions=questions_dicts,
                question_idx=q_idx,
            )

        # Register a waiter in the bus before showing the view so there is no
        # race between the user clicking and the queue being registered.
        answer_queue = _ask_bus.register(thread.id)

        view = AskView(q, thread_id=thread.id, q_idx=q_idx, ask_repo=ask_repo)
        msg = await thread.send(
            embed=ask_embed(q.question, q.header),
            view=view,
            **user_mention_kwargs(notify_user_id),
        )

        message_actions.register(thread.id, msg.id, _console_actions(q, thread.id, msg))

        try:
            selected = await asyncio.wait_for(answer_queue.get(), timeout=ASK_ANSWER_TIMEOUT)
        except TimeoutError:
            _ask_bus.unregister(thread.id)
            if ask_repo is not None:
                await ask_repo.delete(thread.id)
            # Remove buttons from the timed-out message so they stay inert.
            with contextlib.suppress(discord.HTTPException):
                await msg.edit(
                    content="-# ⏰ Question timed out — please send a new message to continue.",
                    embed=None,
                    view=None,
                )
            logger.info(
                "AskUserQuestion timed out after %ds for thread %d: %r",
                ASK_ANSWER_TIMEOUT,
                thread.id,
                q.question,
            )
            continue
        finally:
            _ask_bus.unregister(thread.id)
            message_actions.clear(msg.id)

        if ask_repo is not None:
            await ask_repo.delete(thread.id)

        if not selected:
            continue

        answer_text = ", ".join(selected)
        parts.append(f"**{q.question}**\nAnswer: {answer_text}")

    if not parts:
        return None

    return (
        "[Response to AskUserQuestion]\n\n"
        + "\n\n".join(parts)
        + "\n\nPlease continue based on these answers."
    )


def _console_actions(q: AskQuestion, thread_id: int, msg: discord.Message) -> list[MessageAction]:
    """The options as actions the Ebi AI Console can press (single choice only).

    A multi-select or a free-text answer is still given by replying in text.
    """
    if q.multi_select:
        return []

    def answer(label: str):  # noqa: ANN202
        async def press(who: str) -> None:
            if _ask_bus.post_answer(thread_id, [label]):
                with contextlib.suppress(discord.HTTPException):
                    await msg.edit(content=f"-# ✅ **Selected:** {label}", embed=None, view=None)

        return press

    return [MessageAction(o.label, answer(o.label), STYLE_PRIMARY) for o in q.options[:25]]
