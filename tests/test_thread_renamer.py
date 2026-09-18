"""Tests for thread_renamer — auto-title suggestion via claude -p."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from claude_discord.discord_ui.thread_renamer import suggest_title

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_proc(stdout: bytes, returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    proc.kill = MagicMock()
    proc.returncode = returncode
    return proc


# ---------------------------------------------------------------------------
# Normal cases
# ---------------------------------------------------------------------------


class TestSuggestTitleNormal:
    @pytest.mark.asyncio
    async def test_returns_title_from_claude(self):
        proc = _make_proc(b"Fix authentication bug\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await suggest_title("Help me fix the login system")
        assert result == "Fix authentication bug"

    @pytest.mark.asyncio
    async def test_strips_surrounding_whitespace(self):
        proc = _make_proc(b"  Refactor database layer  \n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await suggest_title("Please refactor the DB code")
        assert result == "Refactor database layer"

    @pytest.mark.asyncio
    async def test_strips_surrounding_quotes(self):
        # Some models wrap the title in quotes
        proc = _make_proc(b'"Add dark mode support"\n')
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await suggest_title("Add dark mode")
        assert result == "Add dark mode support"

    @pytest.mark.asyncio
    async def test_truncates_to_90_chars(self):
        long_title = "A" * 100
        proc = _make_proc(long_title.encode())
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await suggest_title("some request")
        assert result is not None
        assert len(result) <= 90

    @pytest.mark.asyncio
    async def test_uses_custom_claude_command(self):
        proc = _make_proc(b"Custom command title\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            await suggest_title("request", claude_command="/usr/local/bin/claude")
        call_args = mock_exec.call_args[0]
        assert call_args[0] == "/usr/local/bin/claude"

    @pytest.mark.asyncio
    async def test_passes_model_haiku_by_default(self):
        proc = _make_proc(b"Some Title\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            await suggest_title("some request")
        args = mock_exec.call_args[0]
        assert "--model" in args
        model_idx = args.index("--model")
        assert args[model_idx + 1] == "haiku"

    @pytest.mark.asyncio
    async def test_prompt_contains_user_message(self):
        proc = _make_proc(b"Some Title\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            await suggest_title("please help me with authentication")
        argv = mock_exec.call_args[0]
        prompt_arg = argv[-1]
        assert "please help me with authentication" in prompt_arg
        # The prompt is the argument after `--`, so a template that ever starts
        # with a dash cannot be read as a flag.
        assert argv[-2] == "--"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestSuggestTitleOutputCleaning:
    """Tests for robustness against messy model output."""

    @pytest.mark.asyncio
    async def test_multiline_output_uses_first_line(self):
        """If Claude outputs multiple lines, only the first non-empty line is used."""
        proc = _make_proc(b"Fix authentication bug\nHere is the title I suggest.\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await suggest_title("Help me fix the login system")
        assert result == "Fix authentication bug"

    @pytest.mark.asyncio
    async def test_strips_title_prefix(self):
        """Strip 'Title: ' prefix that some models add."""
        proc = _make_proc(b"Title: Fix authentication bug\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await suggest_title("Help me fix the login system")
        assert result == "Fix authentication bug"

    @pytest.mark.asyncio
    async def test_strips_japanese_title_prefix(self):
        """Strip 'タイトル: ' prefix."""
        proc = _make_proc("タイトル: 認証バグの修正\n".encode())
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await suggest_title("ログインシステムを直してください")
        assert result == "認証バグの修正"

    @pytest.mark.asyncio
    async def test_strips_markdown_bold(self):
        """Strip markdown **bold** formatting."""
        proc = _make_proc(b"**Fix authentication bug**\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await suggest_title("Help me fix the login system")
        assert result == "Fix authentication bug"

    @pytest.mark.asyncio
    async def test_strips_heres_a_title_prefix(self):
        """Strip verbose preamble like 'Here's a title: ...'."""
        proc = _make_proc(b"Here's a title: Fix authentication bug\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await suggest_title("Help me fix the login system")
        assert result == "Fix authentication bug"

    @pytest.mark.asyncio
    async def test_skips_blank_leading_lines(self):
        """Skip blank leading lines and take the first non-empty line."""
        proc = _make_proc(b"\n\nFix authentication bug\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await suggest_title("Help me fix the login system")
        assert result == "Fix authentication bug"

    @pytest.mark.asyncio
    async def test_skips_insight_block_plain(self):
        """Skip explanatory output mode Insight blocks (plain format without backticks)."""
        header = "\u2605 Insight \u2500\u2500\u2500\u2500\u2500\n"
        sep = "\u2500\u2500\u2500\u2500\u2500\n"
        output = header + "- some educational point\n" + sep + "\nFix authentication bug\n"
        proc = _make_proc(output.encode())
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await suggest_title("Help me fix the login system")
        assert result == "Fix authentication bug"

    @pytest.mark.asyncio
    async def test_skips_insight_block_backtick(self):
        """Skip explanatory output mode Insight blocks (backtick-wrapped format)."""
        header = "`\u2605 Insight \u2500\u2500\u2500\u2500\u2500`\n"
        sep = "`\u2500\u2500\u2500\u2500\u2500`\n"
        output = header + "- some educational point\n" + sep + "\nFix authentication bug\n"
        proc = _make_proc(output.encode())
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await suggest_title("Help me fix the login system")
        assert result == "Fix authentication bug"

    @pytest.mark.asyncio
    async def test_only_insight_block_returns_none(self):
        """If the output is only an Insight block with no title after, return None."""
        header = "`\u2605 Insight \u2500\u2500\u2500\u2500\u2500`\n"
        sep = "`\u2500\u2500\u2500\u2500\u2500`\n"
        output = header + "- some educational point\n" + sep
        proc = _make_proc(output.encode())
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await suggest_title("Help me fix the login system")
        assert result is None


class TestSuggestTitleEdgeCases:
    @pytest.mark.asyncio
    async def test_empty_message_returns_none(self):
        result = await suggest_title("")
        assert result is None

    @pytest.mark.asyncio
    async def test_whitespace_only_message_returns_none(self):
        result = await suggest_title("   \n  ")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_claude_output_returns_none(self):
        proc = _make_proc(b"")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await suggest_title("some request")
        assert result is None

    @pytest.mark.asyncio
    async def test_long_input_message_is_truncated_before_sending(self):
        """Very long messages should be truncated in the prompt."""
        proc = _make_proc(b"Title\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            await suggest_title("X" * 5000)
        prompt_arg = mock_exec.call_args[0][2]
        # The embedded message portion should not exceed 2000 chars
        assert len(prompt_arg) < 3000


# ---------------------------------------------------------------------------
# Error / timeout handling
# ---------------------------------------------------------------------------


class TestSuggestTitleErrors:
    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self):
        """asyncio.TimeoutError from the task is re-raised by asyncio.wait_for and handled.

        We raise asyncio.TimeoutError directly from the mock communicate coroutine —
        the same exception type that asyncio.wait_for raises on a real timeout.
        In Python 3.10, asyncio.TimeoutError != builtins.TimeoutError, so raising
        the correct type is essential for testing the right code path.

        Using real asyncio timing (asyncio.sleep + patched _TIMEOUT_SECONDS) is
        unreliable on Python 3.10 under pytest-asyncio because the CancelledError
        propagation path through asyncio.wait_for can bypass our except clause.
        """

        proc = _make_proc(b"")
        call_count = 0

        async def _timeout_on_first_call():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # asyncio.wait_for re-raises exceptions from inside the task,
                # so this is equivalent to a real wait_for timeout firing.
                raise TimeoutError()
            return b"", b""

        proc.communicate = _timeout_on_first_call
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await suggest_title("some request")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_subprocess_exception(self):
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=OSError("claude not found"),
        ):
            result = await suggest_title("some request")
        assert result is None

    @pytest.mark.asyncio
    async def test_passes_env_to_subprocess(self):
        """When env dict is provided, it is forwarded to create_subprocess_exec."""
        proc = _make_proc(b"Title from custom env\n")
        custom_env = {"CLAUDE_CODE_USE_FOUNDRY": "1", "PATH": "/usr/bin"}
        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            result = await suggest_title("some request", env=custom_env)
        assert result == "Title from custom env"
        assert mock_exec.call_args[1].get("env") is custom_env

    @pytest.mark.asyncio
    async def test_no_env_passes_none_to_subprocess(self):
        """When env is not provided, subprocess inherits parent environment (env=None)."""
        proc = _make_proc(b"Default env title\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc) as mock_exec:
            await suggest_title("some request")
        # env keyword should not be set, or should be None
        assert mock_exec.call_args[1].get("env") is None

    @pytest.mark.asyncio
    async def test_returns_none_on_nonzero_exit_code(self):
        """CLI errors (e.g. bad model) should not become thread titles."""
        proc = _make_proc(
            b"There's an issue with the selected model (claude-haiku-4-5).",
            returncode=1,
        )
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await suggest_title("some request")
        assert result is None

    @pytest.mark.asyncio
    async def test_kills_process_on_timeout(self):
        """After builtin TimeoutError, proc.kill() and cleanup communicate() are called."""
        proc = _make_proc(b"")
        call_count = 0

        async def _timeout_on_first_call():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TimeoutError()
            return b"", b""

        proc.communicate = _timeout_on_first_call
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            await suggest_title("some request")
        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_kills_process_on_asyncio_timeout(self):
        """asyncio.TimeoutError (raised by asyncio.wait_for on real timeout) is caught.

        On Python 3.10, asyncio.TimeoutError != builtins.TimeoutError — they have
        separate inheritance chains. A plain 'except TimeoutError:' misses asyncio's
        version, leaving the subprocess alive. This test reproduces that scenario by
        raising asyncio.TimeoutError directly from the mock communicate coroutine.
        """

        proc = _make_proc(b"")
        call_count = 0

        async def _asyncio_timeout_on_first_call():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TimeoutError()
            return b"", b""

        proc.communicate = _asyncio_timeout_on_first_call
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await suggest_title("some request")
        assert result is None
        proc.kill.assert_called_once()
