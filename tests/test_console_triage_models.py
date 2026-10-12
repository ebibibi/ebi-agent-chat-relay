"""Triage follows the backend: Haiku on Claude, Codex's cheapest model on Codex."""

from __future__ import annotations

import json
import stat
import sys

import pytest

from claude_discord.console import triage_models as tm

CATALOG = {
    "models": [
        {"slug": "gpt-6.1-sol", "visibility": "list", "priority": 1, "description": "Workhorse."},
        {
            "slug": "gpt-6-luna",
            "visibility": "list",
            "priority": 4,
            "description": "Fast and affordable model.",
        },
        {
            "slug": "gpt-reserve",
            "visibility": "hide",
            "priority": 3,
            "description": "Fast and affordable.",
        },
        {
            "slug": "gpt-5.6-luna",
            "visibility": "list",
            "priority": 9,
            "description": "Older fast and efficient.",
        },
    ]
}


def test_the_newest_listed_cheap_model_is_picked() -> None:
    assert tm.pick_cheap_codex_model(CATALOG) == "gpt-6-luna"
    assert (
        tm.pick_cheap_codex_model({"models": [{"slug": "big", "description": "Frontier."}]}) is None
    )
    assert tm.pick_cheap_codex_model("nonsense") is None


def test_the_catalog_is_read_from_codex_home(tmp_path) -> None:
    (tmp_path / "models_cache.json").write_text(json.dumps(CATALOG))
    assert tm.cheapest_codex_model({"CODEX_HOME": str(tmp_path)}) == "gpt-6-luna"
    assert tm.cheapest_codex_model({"CODEX_HOME": str(tmp_path / "none")}) is None


@pytest.fixture
def calls(monkeypatch):
    seen: list[tuple[str, str | None]] = []

    async def fake_claude(prompt, *, command, model, **_):
        seen.append(("claude", model))
        return "{}"

    async def fake_codex(prompt, *, command, model, **_):
        seen.append(("codex", model))
        return "{}"

    monkeypatch.setattr(tm, "ask_model", fake_claude)
    monkeypatch.setattr(tm, "ask_codex", fake_codex)
    monkeypatch.setattr(tm, "cheapest_codex_model", lambda env=None: "gpt-6-luna")
    return seen


async def test_each_call_follows_the_current_backend(calls) -> None:
    backend = {"now": "claude"}

    async def current() -> str:
        return backend["now"]

    ask = tm.BackendAsk(current, claude_command="claude", codex_command="codex")
    await ask("p")
    assert (calls[-1], ask.model) == (("claude", "haiku"), "haiku")
    backend["now"] = "codex"
    await ask("p")
    assert (calls[-1], ask.model) == (("codex", "gpt-6-luna"), "gpt-6-luna")
    backend["now"] = "pi"  # neither: the installed Claude CLI
    await ask("p")
    assert calls[-1] == ("claude", "haiku")


async def test_a_missing_cli_falls_back_to_the_other(calls) -> None:
    async def codex() -> str:
        return "codex"

    async def claude() -> str:
        return "claude"

    await tm.BackendAsk(codex, claude_command="claude", codex_command=None)("p")
    await tm.BackendAsk(claude, claude_command=None, codex_command="codex", codex_model="pinned")(
        "p"
    )
    assert calls == [("claude", "haiku"), ("codex", "pinned")]
    assert await tm.BackendAsk(claude, claude_command=None, codex_command=None)("p") is None


@pytest.mark.skipif(sys.platform == "win32", reason="uses a POSIX shell script")
async def test_codex_runs_ephemeral_read_only_with_the_prompt_on_stdin(tmp_path) -> None:
    fake = tmp_path / "codex"
    fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\necho "--stdin--"\ncat\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    out = await tm.ask_codex("-rf prompt", command=str(fake), model="gpt-6-luna")
    assert out is not None
    args, stdin = out.split("--stdin--\n")
    lines = args.split()
    assert lines[0] == "exec"
    for flag in ("--ephemeral", "--ignore-user-config", "--ignore-rules", "read-only"):
        assert flag in lines
    assert lines[lines.index("-m") + 1] == "gpt-6-luna"
    assert lines[-1] == "-"
    assert stdin == "-rf prompt"
