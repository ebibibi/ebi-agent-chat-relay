"""A tool the CLI asks about reaches a person, and only their answer lets it run."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from claude_code_core.approvals import (
    DENY,
    permission_approvers,
    permission_prompt,
    permission_timeout_seconds,
)
from claude_code_core.frontend import Choice, ChoicePrompt
from claude_code_core.parser import parse_line
from claude_code_core.runner import ClaudeRunner
from claude_code_core.types import PermissionRequest
from claude_discord.discord_ui.prompt_views import ChoiceView

CAN_USE_TOOL = {
    "type": "control_request",
    "request_id": "req-1",
    "request": {
        "subtype": "can_use_tool",
        "tool_name": "Bash",
        "input": {"command": "deploy.sh"},
        "decision_reason": "needs approval",
        "decision_reason_type": "hook",
    },
}


def _runner_reading(*lines: dict, timeout: int = 300) -> tuple[ClaudeRunner, MagicMock]:
    runner = ClaudeRunner(timeout_seconds=timeout)
    process = MagicMock()
    process.stdout.readline = AsyncMock(
        side_effect=[(json.dumps(line) + "\n").encode() for line in lines] + [b""]
    )
    process.stdin.write = MagicMock()
    process.stdin.drain = AsyncMock()
    process.returncode = 0
    runner._process = process
    return runner, process


def _written(process: MagicMock) -> list[dict]:
    return [json.loads(call.args[0]) for call in process.stdin.write.call_args_list]


class TestArgs:
    def test_permission_prompts_go_to_the_host(self) -> None:
        args = ClaudeRunner(permission_mode="auto")._build_args("hi", None)
        assert args[args.index("--permission-prompt-tool") + 1] == "stdio"

    def test_flag_does_not_add_tools(self) -> None:
        args = ClaudeRunner(permission_mode="auto")._build_args("hi", None)
        disallowed = args[args.index("--disallowedTools") + 1].split(",")
        assert set(disallowed) == {"AskUserQuestion", "EnterPlanMode", "ExitPlanMode"}

    def test_skipping_permissions_leaves_nothing_to_relay(self) -> None:
        runner = ClaudeRunner(permission_mode="default", dangerously_skip_permissions=True)
        assert "--permission-prompt-tool" not in runner._build_args("hi", None)


class TestParse:
    def test_can_use_tool_becomes_a_permission_request(self) -> None:
        event = parse_line(json.dumps(CAN_USE_TOOL))
        assert event is not None
        assert event.permission_request == PermissionRequest(
            request_id="req-1",
            tool_name="Bash",
            tool_input={"command": "deploy.sh"},
            reason="needs approval",
        )

    def test_other_subtypes_are_marked_unsupported(self) -> None:
        line = {"type": "control_request", "request_id": "r", "request": {"subtype": "x"}}
        event = parse_line(json.dumps(line))
        assert event is not None
        assert event.permission_request is None
        assert event.unsupported_control_request_id == "r"


class TestRunnerProtocol:
    @pytest.mark.asyncio
    async def test_allow_returns_the_original_input(self) -> None:
        runner, process = _runner_reading(CAN_USE_TOOL)
        events = [event async for event in runner._read_stream()]
        assert events[0].permission_request is not None

        await runner.inject_tool_result("req-1", {"approved": True})

        assert _written(process) == [
            {
                "type": "control_response",
                "response": {
                    "subtype": "success",
                    "request_id": "req-1",
                    "response": {"behavior": "allow", "updatedInput": {"command": "deploy.sh"}},
                },
            }
        ]

    @pytest.mark.asyncio
    async def test_deny_is_a_deny(self) -> None:
        runner, process = _runner_reading(CAN_USE_TOOL)
        [event async for event in runner._read_stream()]

        await runner.inject_tool_result("req-1", {"approved": False})

        assert _written(process)[0]["response"]["response"]["behavior"] == "deny"

    @pytest.mark.asyncio
    async def test_unsupported_request_is_refused_not_left_hanging(self) -> None:
        line = {"type": "control_request", "request_id": "r", "request": {"subtype": "x"}}
        runner, process = _runner_reading(line)

        events = [event async for event in runner._read_stream()]

        assert events == []
        response = _written(process)[0]["response"]
        assert (response["subtype"], response["request_id"]) == ("error", "r")

    @pytest.mark.asyncio
    async def test_silence_limit_outlasts_a_pending_prompt(self, monkeypatch) -> None:
        monkeypatch.setenv("CCDB_PERMISSION_TIMEOUT_SECONDS", "600")
        runner, _ = _runner_reading(CAN_USE_TOOL, timeout=300)
        timeouts: list[float] = []
        real_wait_for = asyncio.wait_for

        async def spy(awaitable, timeout=None):
            timeouts.append(timeout)
            return await real_wait_for(awaitable, timeout=timeout)

        monkeypatch.setattr("claude_code_core.runner.asyncio.wait_for", spy)
        [event async for event in runner._read_stream()]

        assert timeouts[:2] == [300, 900]


class TestApprovers:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("", None), ("1, 2", frozenset({"1", "2"})), ("1,bob", frozenset())],
    )
    def test_parse(self, monkeypatch, raw, expected) -> None:
        monkeypatch.setenv("CCDB_APPROVER_USER_IDS", raw)
        assert permission_approvers() == expected

    def test_prompt_shows_reason_and_keeps_failing_closed(self, monkeypatch) -> None:
        monkeypatch.setenv("CCDB_APPROVER_USER_IDS", "1")
        monkeypatch.setenv("CCDB_PERMISSION_TIMEOUT_SECONDS", "600")
        prompt = permission_prompt(PermissionRequest("r", "Bash", {}, reason="why"))
        assert prompt.question.startswith("why")
        assert prompt.allowed_user_ids == frozenset({"1"})
        assert prompt.timeout_seconds == 600
        assert prompt.default_on_timeout == DENY

    @pytest.mark.parametrize("raw", ["soon", "0", "-5"])
    def test_bad_timeout_uses_default(self, monkeypatch, raw) -> None:
        monkeypatch.setenv("CCDB_PERMISSION_TIMEOUT_SECONDS", raw)
        assert permission_timeout_seconds() == 120


class TestChoiceViewAuthorization:
    @staticmethod
    def _view(allowed: frozenset[str] | None) -> ChoiceView:
        prompt = ChoicePrompt(
            question="q",
            choices=(Choice(value="allow", label="Allow"),),
            timeout_seconds=60,
            allowed_user_ids=allowed,
        )
        return ChoiceView(prompt)

    @staticmethod
    def _press(user_id: int) -> SimpleNamespace:
        return SimpleNamespace(
            user=SimpleNamespace(id=user_id),
            response=SimpleNamespace(send_message=AsyncMock()),
        )

    @pytest.mark.asyncio
    async def test_listed_user_may_answer(self) -> None:
        assert await self._view(frozenset({"1"})).interaction_check(self._press(1))

    @pytest.mark.asyncio
    async def test_other_user_is_told_no(self) -> None:
        press = self._press(2)
        assert not await self._view(frozenset({"1"})).interaction_check(press)
        assert press.response.send_message.await_args.kwargs["ephemeral"] is True

    @pytest.mark.asyncio
    async def test_empty_set_refuses_everyone(self) -> None:
        assert not await self._view(frozenset()).interaction_check(self._press(1))

    @pytest.mark.asyncio
    async def test_no_restriction_keeps_old_behaviour(self) -> None:
        assert await self._view(None).interaction_check(self._press(2))

    @pytest.mark.asyncio
    async def test_restricted_prompt_is_not_offered_on_the_console(self) -> None:
        assert self._view(frozenset({"1"})).console_actions(MagicMock()) == []
        assert len(self._view(None).console_actions(MagicMock())) == 1
