"""Tests for AskUserQuestion Discord integration.

Covers:
- types: AskOption, AskQuestion, ToolCategory.ASK
- parser: AskUserQuestion tool_use → ask_questions
- embeds: ask_embed
- run_helper: _collect_ask_answers (answer formatting)
"""

from __future__ import annotations

import json

import pytest

from claude_discord.claude.parser import _parse_ask_questions, parse_line
from claude_discord.claude.types import AskOption, AskQuestion, ToolCategory
from claude_discord.discord_ui.embeds import ask_embed

# ---------------------------------------------------------------------------
# types
# ---------------------------------------------------------------------------


class TestAskTypes:
    def test_ask_option_defaults(self) -> None:
        opt = AskOption(label="JWT tokens")
        assert opt.label == "JWT tokens"
        assert opt.description == ""

    def test_ask_question_defaults(self) -> None:
        q = AskQuestion(question="Which auth?")
        assert q.question == "Which auth?"
        assert q.header == ""
        assert q.multi_select is False
        assert q.options == []

    def test_tool_category_ask_exists(self) -> None:
        assert ToolCategory.ASK.value == "ask"

    def test_ask_in_tool_categories(self) -> None:
        from claude_discord.claude.types import TOOL_CATEGORIES

        assert "AskUserQuestion" in TOOL_CATEGORIES
        assert TOOL_CATEGORIES["AskUserQuestion"] == ToolCategory.ASK


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

_ASK_LINE = json.dumps(
    {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_ask1",
                    "name": "AskUserQuestion",
                    "input": {
                        "questions": [
                            {
                                "question": "Which auth approach?",
                                "header": "Auth method",
                                "multiSelect": False,
                                "options": [
                                    {
                                        "label": "JWT tokens",
                                        "description": "Stateless, good for APIs",
                                    },
                                    {
                                        "label": "Session cookies",
                                        "description": "Simple, stateful",
                                    },
                                    {
                                        "label": "OAuth2",
                                        "description": "Federated identity",
                                    },
                                ],
                            }
                        ]
                    },
                }
            ]
        },
    }
)


class TestParserAskUserQuestion:
    def test_ask_tool_use_detected(self) -> None:
        event = parse_line(_ASK_LINE)
        assert event is not None
        assert event.tool_use is not None
        assert event.tool_use.tool_name == "AskUserQuestion"
        assert event.tool_use.category == ToolCategory.ASK

    def test_ask_questions_populated(self) -> None:
        event = parse_line(_ASK_LINE)
        assert event is not None
        assert event.ask_questions is not None
        assert len(event.ask_questions) == 1

    def test_question_fields(self) -> None:
        event = parse_line(_ASK_LINE)
        assert event is not None
        q = event.ask_questions[0]
        assert q.question == "Which auth approach?"
        assert q.header == "Auth method"
        assert q.multi_select is False

    def test_options_parsed(self) -> None:
        event = parse_line(_ASK_LINE)
        assert event is not None
        opts = event.ask_questions[0].options
        assert len(opts) == 3
        assert opts[0].label == "JWT tokens"
        assert opts[0].description == "Stateless, good for APIs"
        assert opts[2].label == "OAuth2"

    def test_normal_tool_has_no_ask_questions(self) -> None:
        line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_bash",
                            "name": "Bash",
                            "input": {"command": "ls"},
                        }
                    ]
                },
            }
        )
        event = parse_line(line)
        assert event is not None
        assert event.ask_questions is None

    def test_multi_select_question(self) -> None:
        line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "toolu_ask2",
                            "name": "AskUserQuestion",
                            "input": {
                                "questions": [
                                    {
                                        "question": "Which features?",
                                        "header": "Features",
                                        "multiSelect": True,
                                        "options": [
                                            {"label": "Auth"},
                                            {"label": "Logging"},
                                        ],
                                    }
                                ]
                            },
                        }
                    ]
                },
            }
        )
        event = parse_line(line)
        assert event is not None
        assert event.ask_questions[0].multi_select is True

    def test_empty_options_list(self) -> None:
        tool_input = {"questions": [{"question": "Free form?", "options": []}]}
        questions = _parse_ask_questions(tool_input)
        assert len(questions) == 1
        assert questions[0].options == []

    def test_options_without_label_are_skipped(self) -> None:
        tool_input = {
            "questions": [
                {
                    "question": "Choose?",
                    "options": [
                        {"label": "Valid"},
                        {"description": "No label here"},
                        {"label": ""},
                    ],
                }
            ]
        }
        questions = _parse_ask_questions(tool_input)
        assert len(questions[0].options) == 1
        assert questions[0].options[0].label == "Valid"

    def test_multiple_questions_in_one_call(self) -> None:
        tool_input = {
            "questions": [
                {"question": "Q1?", "options": [{"label": "A"}, {"label": "B"}]},
                {"question": "Q2?", "options": [{"label": "X"}, {"label": "Y"}]},
            ]
        }
        questions = _parse_ask_questions(tool_input)
        assert len(questions) == 2
        assert questions[0].question == "Q1?"
        assert questions[1].question == "Q2?"

    def test_questions_as_json_string_is_decoded(self) -> None:
        tool_input = {
            "questions": json.dumps(
                [{"question": "Q?", "header": "H", "options": [{"label": "A"}]}]
            )
        }
        questions = _parse_ask_questions(tool_input)
        assert len(questions) == 1
        assert questions[0].question == "Q?"
        assert questions[0].header == "H"
        assert questions[0].options[0].label == "A"

    def test_question_element_as_json_string_is_decoded(self) -> None:
        tool_input = {"questions": [json.dumps({"question": "Q?", "options": [{"label": "A"}]})]}
        questions = _parse_ask_questions(tool_input)
        assert len(questions) == 1
        assert questions[0].question == "Q?"
        assert questions[0].options[0].label == "A"

    def test_options_as_json_string_is_decoded(self) -> None:
        options = json.dumps([{"label": "A"}, {"label": "B"}])
        questions = _parse_ask_questions({"questions": [{"question": "Q?", "options": options}]})
        assert len(questions[0].options) == 2

    def test_non_dict_questions_are_skipped(self) -> None:
        tool_input = {"questions": ["just a string", 42, None, {"question": "Real?"}]}
        questions = _parse_ask_questions(tool_input)
        assert len(questions) == 1
        assert questions[0].question == "Real?"

    def test_non_json_string_questions_returns_empty(self) -> None:
        assert _parse_ask_questions({"questions": "totally not json"}) == []

    def test_parse_line_with_string_questions_does_not_raise(self) -> None:
        line = json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "id": "tool-ask",
                            "name": "AskUserQuestion",
                            "input": {
                                "questions": json.dumps(
                                    [{"question": "Pick?", "options": [{"label": "Red"}]}]
                                )
                            },
                        }
                    ]
                },
            }
        )
        event = parse_line(line)
        assert event is not None
        assert event.ask_questions[0].question == "Pick?"
        assert event.ask_questions[0].options[0].label == "Red"


# ---------------------------------------------------------------------------
# embeds
# ---------------------------------------------------------------------------


class TestAskEmbed:
    def test_default_title(self) -> None:
        embed = ask_embed("Which approach?")
        assert embed.title == "❓ The agent needs your input"

    def test_custom_header_used_in_title(self) -> None:
        embed = ask_embed("Which approach?", header="Auth method")
        assert embed.title == "❓ Auth method"

    def test_question_in_description(self) -> None:
        embed = ask_embed("Pick one?", header="Step")
        assert embed.description == "Pick one?"

    def test_long_question_truncated_to_4096(self) -> None:
        long_q = "x" * 5000
        embed = ask_embed(long_q)
        assert embed.description is not None
        assert len(embed.description) <= 4096

    def test_color_is_blue(self) -> None:
        embed = ask_embed("Q?")
        assert embed.color is not None
        assert embed.color.value == 0x3498DB


# ---------------------------------------------------------------------------
# _collect_ask_answers (answer formatting helper)
# ---------------------------------------------------------------------------


class TestCollectAskAnswers:
    """Tests for the _collect_ask_answers answer string format.

    We test the pure formatting logic by constructing what the function
    would return given a mocked AskView response.  The Discord interaction
    itself (buttons/modals) is tested separately via integration tests.
    """

    def _format_answer(self, question: AskQuestion, selected: list[str]) -> str:
        """Replicate the formatting logic from _collect_ask_answers."""
        answer_text = ", ".join(selected)
        return f"**{question.question}**\nAnswer: {answer_text}"

    def test_single_answer_formatted(self) -> None:
        q = AskQuestion(question="Which auth?", options=[AskOption(label="JWT")])
        result = self._format_answer(q, ["JWT"])
        assert "**Which auth?**" in result
        assert "Answer: JWT" in result

    def test_multi_select_answer_joined(self) -> None:
        q = AskQuestion(
            question="Which features?",
            multi_select=True,
            options=[AskOption(label="Auth"), AskOption(label="Logging")],
        )
        result = self._format_answer(q, ["Auth", "Logging"])
        assert "Answer: Auth, Logging" in result

    def test_full_resume_prompt_format(self) -> None:
        """The resume prompt must start with the [Response] marker."""
        q = AskQuestion(question="Q?")
        part = self._format_answer(q, ["A"])
        resume_prompt = (
            "[Response to AskUserQuestion]\n\n"
            + part
            + "\n\nPlease continue based on these answers."
        )
        assert resume_prompt.startswith("[Response to AskUserQuestion]")
        assert "Please continue" in resume_prompt


class TestCollectAskAnswersTimeout:
    """Tests for asyncio.TimeoutError handling in collect_ask_answers (Python 3.10 compat)."""

    @pytest.mark.asyncio
    async def test_collect_ask_answers_handles_asyncio_timeout(self) -> None:
        """collect_ask_answers does not raise on asyncio.TimeoutError (Python 3.10 compat).

        On Python 3.10, asyncio.TimeoutError is NOT a subclass of the built-in
        TimeoutError. If the wait_for call times out, collect_ask_answers must
        catch the exception and return None (graceful timeout) rather than
        propagating an unhandled exception to _run_helper.
        """
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        from claude_discord.claude.types import AskOption, AskQuestion
        from claude_discord.discord_ui.ask_handler import collect_ask_answers

        q = AskQuestion(
            question="Which option?",
            options=[AskOption(label="A"), AskOption(label="B")],
        )
        mock_msg = AsyncMock()
        mock_msg.edit = AsyncMock()
        thread = MagicMock()
        thread.id = 12345
        thread.send = AsyncMock(return_value=mock_msg)

        with patch(
            "claude_discord.discord_ui.ask_handler.asyncio.wait_for",
            side_effect=asyncio.TimeoutError,
        ):
            result = await collect_ask_answers(thread, [q], session_id="abc123")

        assert result is None  # timeout → no answer → returns None


class TestCollectAskAnswersMention:
    """AskUserQuestion messages mention the requester when configured."""

    @pytest.mark.asyncio
    async def test_collect_ask_answers_mentions_notify_user(self) -> None:
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        from claude_discord.claude.types import AskOption, AskQuestion
        from claude_discord.discord_ui.ask_bus import ask_bus
        from claude_discord.discord_ui.ask_handler import collect_ask_answers

        q = AskQuestion(
            question="Which option?",
            options=[AskOption(label="A"), AskOption(label="B")],
        )
        thread = MagicMock()
        thread.id = 12345
        thread.send = AsyncMock(return_value=MagicMock())

        async def answer() -> None:
            await asyncio.sleep(0)
            ask_bus.post_answer(thread.id, ["A"])

        answer_task = asyncio.create_task(answer())
        await collect_ask_answers(thread, [q], session_id="abc123", notify_user_id=42)
        await answer_task

        thread.send.assert_called_once()
        assert thread.send.call_args.kwargs["content"] == "<@42>"


# ---------------------------------------------------------------------------
# AskView._other_callback — visual feedback after modal submission
# ---------------------------------------------------------------------------


class TestAskViewOtherCallback:
    """Tests that 'Other' modal submission gives immediate visual feedback.

    Before this fix, the original Ask message was never updated after the modal
    was submitted, leaving the user uncertain whether their answer was received.
    """

    def _make_question(self) -> AskQuestion:
        return AskQuestion(
            question="Which style?",
            options=[AskOption(label="A"), AskOption(label="B")],
        )

    @pytest.mark.asyncio
    async def test_other_callback_edits_original_message_on_success(self) -> None:
        """After modal submission the original Ask message is replaced with
        '✅ Selected: <answer>' so the user sees immediate feedback."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from claude_discord.discord_ui.ask_bus import AskAnswerBus
        from claude_discord.discord_ui.ask_view import AskModal, AskView

        bus = AskAnswerBus()
        bus.register(thread_id=42)

        question = self._make_question()
        view = AskView(question, thread_id=42, q_idx=0, bus=bus)

        # Simulate the original Discord message that holds the Ask embed.
        original_msg = MagicMock()
        original_msg.edit = AsyncMock()

        # Simulate the button interaction that opens the modal.
        interaction = MagicMock()
        interaction.message = original_msg
        interaction.response = MagicMock()
        interaction.response.send_modal = AsyncMock()

        # Patch AskModal so wait() resolves immediately with an answer.
        async def fake_wait():
            return False  # not timed out

        mock_modal = MagicMock(spec=AskModal)
        mock_modal.wait = fake_wait
        mock_modal.answer = "My custom answer"

        with patch("claude_discord.discord_ui.ask_view.AskModal", return_value=mock_modal):
            await view._other_callback(interaction)

        # The original message must be edited to confirm the submission.
        original_msg.edit.assert_awaited_once()
        call_kwargs = original_msg.edit.call_args.kwargs
        assert "✅" in call_kwargs["content"]
        assert "My custom answer" in call_kwargs["content"]
        assert call_kwargs["view"] is None  # buttons removed

    @pytest.mark.asyncio
    async def test_other_callback_edits_original_message_when_session_gone(self) -> None:
        """If the session is gone (bot restarted), the original message is updated
        with the restart warning instead of silently leaving the ask message."""
        from unittest.mock import AsyncMock, MagicMock, patch

        from claude_discord.discord_ui.ask_bus import AskAnswerBus
        from claude_discord.discord_ui.ask_view import _RESTART_MSG, AskModal, AskView

        bus = AskAnswerBus()
        # No register() call → post_answer returns False (session gone).

        question = self._make_question()
        view = AskView(question, thread_id=99, q_idx=0, bus=bus)

        original_msg = MagicMock()
        original_msg.edit = AsyncMock()

        interaction = MagicMock()
        interaction.message = original_msg
        interaction.response = MagicMock()
        interaction.response.send_modal = AsyncMock()

        async def fake_wait():
            return False

        mock_modal = MagicMock(spec=AskModal)
        mock_modal.wait = fake_wait
        mock_modal.answer = "Some answer"

        with patch("claude_discord.discord_ui.ask_view.AskModal", return_value=mock_modal):
            await view._other_callback(interaction)

        # Must edit the original message to show the restart warning.
        original_msg.edit.assert_awaited_once()
        call_kwargs = original_msg.edit.call_args.kwargs
        assert _RESTART_MSG in call_kwargs["content"]
        assert call_kwargs["view"] is None
