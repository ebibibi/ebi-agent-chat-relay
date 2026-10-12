"""Starting a CLI safely on every platform: npm's Windows .cmd wrappers included."""

from __future__ import annotations

import pytest

from claude_code_core import runner as runner_mod
from claude_code_core.runner import resolve_cli_argv


def test_elsewhere_the_command_is_used_as_found(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(runner_mod.sys, "platform", "linux")
    monkeypatch.setattr(runner_mod.shutil, "which", lambda c: f"/usr/bin/{c}")
    assert resolve_cli_argv("claude") == ["/usr/bin/claude"]


@pytest.mark.parametrize("token", ["%~dp0", "%dp0%"])
def test_a_windows_npm_wrapper_becomes_node_and_its_script(monkeypatch, tmp_path, token) -> None:
    script = tmp_path / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    script.parent.mkdir(parents=True)
    script.write_text("")
    wrapper = tmp_path / "codex.CMD"
    wrapper.write_text(
        f'@ECHO off\r\n"%_prog%" "{token}\\node_modules\\@openai\\codex\\bin\\codex.js" %*\r\n'
    )
    monkeypatch.setattr(runner_mod.sys, "platform", "win32")
    monkeypatch.setattr(
        runner_mod.shutil, "which", lambda c: str(wrapper) if c == "codex" else "C:/node.exe"
    )
    assert resolve_cli_argv("codex") == ["C:/node.exe", str(script)]


def test_a_wrapper_that_cannot_be_resolved_is_not_run_through_cmd(monkeypatch, tmp_path) -> None:
    wrapper = tmp_path / "tool.cmd"
    wrapper.write_text("@echo off\r\nsomething.exe %*\r\n")
    monkeypatch.setattr(runner_mod.sys, "platform", "win32")
    monkeypatch.setattr(runner_mod.shutil, "which", lambda c: str(wrapper))
    assert resolve_cli_argv("tool") is None


def test_a_windows_exe_is_used_as_is(monkeypatch) -> None:
    monkeypatch.setattr(runner_mod.sys, "platform", "win32")
    monkeypatch.setattr(runner_mod.shutil, "which", lambda c: "C:/bin/claude.exe")
    assert resolve_cli_argv("claude") == ["C:/bin/claude.exe"]
