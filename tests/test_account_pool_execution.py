"""Account pools x execution environments.

The execution layer derives the agent's state directory from the child env it
is handed, so a pooled profile's ``CLAUDE_CONFIG_DIR`` / ``CODEX_HOME`` must
already be in that env when ``prepare_launch`` runs, and a sandbox must mount
the profile's directory rather than the relay's own login.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from claude_code_core.account_pool import AccountBinding
from claude_code_core.codex_runner import CodexRunner
from claude_code_core.execution import ExecutionRefusedError, Launch
from claude_code_core.execution.bwrap import build_bwrap_argv
from claude_code_core.execution.config import BwrapSettings
from claude_code_core.execution.guarded_paths import GitLayout, guarded_mounts
from claude_code_core.runner import ClaudeRunner

HOME = "/home/agent"
CLAUDE_PROFILE = "/pool/claude-work"
CODEX_PROFILE = "/pool/codex-work"


def _claude_runner() -> ClaudeRunner:
    runner = ClaudeRunner(command="claude", working_dir="/work/repo")
    runner.account = AccountBinding(
        backend="claude", profile="work", env={"CLAUDE_CONFIG_DIR": CLAUDE_PROFILE}
    )
    runner.execution_mode = "bwrap"
    return runner


def _codex_runner() -> CodexRunner:
    runner = CodexRunner(command="codex", working_dir="/work/repo")
    runner.account = AccountBinding(
        backend="codex", profile="work", env={"CODEX_HOME": CODEX_PROFILE}
    )
    runner.execution_mode = "bwrap"
    return runner


async def _captured_launch_env(runner, module: str) -> dict[str, str]:
    """Run one turn with prepare_launch stubbed; return the env it was given."""
    seen: dict[str, object] = {}

    async def fake_prepare_launch(**kwargs):
        seen.update(kwargs)
        raise ExecutionRefusedError("stop before spawning")

    with (
        patch(f"{module}.prepare_launch", new=fake_prepare_launch),
        patch("asyncio.create_subprocess_exec", new=AsyncMock()) as spawn,
    ):
        _ = [event async for event in runner.run("hi")]
    spawn.assert_not_awaited()
    assert seen["requested_mode"] == "bwrap"
    return dict(seen["env"])  # type: ignore[arg-type]


def _writable_binds(backend: str, argv: tuple[str, ...], env: dict[str, str]) -> list[str]:
    lch = Launch(argv=argv, env={**env, "HOME": HOME}, cwd="/work/repo")
    mounts = guarded_mounts(backend, lch, GitLayout(), realpath=lambda p: p)
    existing = {"/work/repo", CLAUDE_PROFILE, CODEX_PROFILE, f"{HOME}/.claude", f"{HOME}/.codex"}
    out = list(
        build_bwrap_argv(
            BwrapSettings(),
            lch,
            mounts,
            exists=lambda p: p in existing,
            isdir=lambda p: p in existing,
            realpath=lambda p: p,
        )
    )
    return [out[i + 1] for i, a in enumerate(out) if a == "--bind"]


class TestProfileReachesTheSandbox:
    async def test_claude_profile_dir_is_the_mounted_state(self) -> None:
        env = await _captured_launch_env(_claude_runner(), "claude_code_core.runner")
        assert env["CLAUDE_CONFIG_DIR"] == CLAUDE_PROFILE
        binds = _writable_binds("claude", ("claude", "-p"), env)
        assert CLAUDE_PROFILE in binds
        assert f"{HOME}/.claude" not in binds

    async def test_codex_profile_home_is_the_mounted_state(self) -> None:
        env = await _captured_launch_env(_codex_runner(), "claude_code_core.codex_runner")
        assert env["CODEX_HOME"] == CODEX_PROFILE
        binds = _writable_binds("codex", ("codex", "exec", "--json", "-"), env)
        assert CODEX_PROFILE in binds
        assert f"{HOME}/.codex" not in binds


class TestCloneCarriesBoth:
    @pytest.mark.parametrize("factory", [_claude_runner, _codex_runner])
    def test_clone_keeps_account_and_execution_mode(self, factory) -> None:
        runner = factory()
        cloned = runner.clone()
        assert cloned.account == runner.account
        assert cloned.execution_mode == "bwrap"
