"""Tests for suggest_retitle — asking the model whether a title still fits."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_discord.discord_ui.thread_renamer import suggest_retitle


def _make_proc(stdout: bytes, returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    proc.kill = MagicMock()
    proc.returncode = returncode
    return proc


class TestSuggestRetitle:
    @pytest.mark.asyncio
    async def test_returns_new_title_when_the_subject_moved(self):
        proc = _make_proc(b"Migrate the billing database\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await suggest_retitle("Fix the login bug", ("let's move billing to postgres",))
        assert result == "Migrate the billing database"

    @pytest.mark.asyncio
    async def test_keep_verdict_returns_none(self):
        proc = _make_proc(b"KEEP\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await suggest_retitle("Fix the login bug", ("still on the login bug",))
        assert result is None

    @pytest.mark.asyncio
    async def test_keep_verdict_is_case_insensitive(self):
        proc = _make_proc(b"keep\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await suggest_retitle("Fix the login bug", ("more login work",))
        assert result is None

    @pytest.mark.asyncio
    async def test_title_equal_to_the_current_one_returns_none(self):
        proc = _make_proc(b"  Fix the login bug \n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await suggest_retitle("Fix the login bug", ("more login work",))
        assert result is None

    @pytest.mark.asyncio
    async def test_no_messages_means_no_call(self):
        with patch("asyncio.create_subprocess_exec") as spawn:
            result = await suggest_retitle("Fix the login bug", ())
        assert result is None
        spawn.assert_not_called()

    @pytest.mark.asyncio
    async def test_failure_returns_none(self):
        proc = _make_proc(b"", returncode=1)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await suggest_retitle("Fix the login bug", ("something else entirely",))
        assert result is None

    @pytest.mark.asyncio
    async def test_prompt_carries_the_current_title_and_the_messages(self):
        proc = _make_proc(b"KEEP\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc) as spawn:
            await suggest_retitle("Fix the login bug", ("deploy the scheduler", "check CI"))
        prompt = spawn.call_args.args[-1]
        assert "Fix the login bug" in prompt
        assert "deploy the scheduler" in prompt
        assert "check CI" in prompt
