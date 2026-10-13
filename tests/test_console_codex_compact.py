"""``/compact`` for Codex: the JSON-RPC exchange with ``codex app-server``.

A tiny script stands in for ``codex``: it answers ``thread/resume`` and
``thread/compact/start`` the way codex-cli 0.162.1 does, or fails on purpose.
"""

from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest

from claude_discord.console.codex_compact import CompactError, compact_codex_session

FAKE = textwrap.dedent(
    """
    import json, sys
    mode = sys.argv[1]
    assert sys.argv[2] == "app-server"
    def out(m):
        print(json.dumps(m), flush=True)
    for line in sys.stdin:
        m = json.loads(line)
        if m.get("method") == "thread/resume":
            if mode == "unknown":
                out({"id": m["id"], "error": {"message": "no rollout found"}})
                continue
            out({"method": "thread/status/changed", "params": {"status": {"type": "idle"}}})
            thread = {"id": m["params"]["threadId"]}
            if mode in ("large", "oversized"):
                size = 256 * 1024 if mode == "large" else 11 * 1024 * 1024
                thread["turns"] = [{"text": "x" * size}]
            out({"id": m["id"], "result": {"thread": thread}})
        elif m.get("method") == "thread/compact/start":
            out({"id": m["id"], "result": {}})
            if mode in ("ok", "large"):
                item = {"type": "contextCompaction", "id": "c1"}
                out({"method": "item/started", "params": {"item": item}})
                out({"method": "item/completed", "params": {"item": item}})
            if mode == "hang":
                continue
            out({"method": "turn/completed", "params": {}})
    """
)


@pytest.fixture
def fake_codex(tmp_path: Path):
    script = tmp_path / "fake_codex.py"
    script.write_text(FAKE)
    return lambda mode: f"{sys.executable} {script} {mode}"


async def test_compaction_completes(fake_codex) -> None:
    await compact_codex_session("01a1-2b", codex_command=fake_codex("ok"))


async def test_large_resume_history_can_be_compacted(fake_codex) -> None:
    await compact_codex_session("01a1-2b", codex_command=fake_codex("large"))


async def test_oversized_response_is_reported(fake_codex) -> None:
    with pytest.raises(CompactError, match="response exceeded"):
        await compact_codex_session("01a1-2b", codex_command=fake_codex("oversized"))


async def test_unknown_session_is_reported(fake_codex) -> None:
    with pytest.raises(CompactError, match="no rollout found"):
        await compact_codex_session("01a1-2b", codex_command=fake_codex("unknown"))


async def test_a_turn_without_compaction_is_an_error(fake_codex) -> None:
    with pytest.raises(CompactError, match="without compacting"):
        await compact_codex_session("01a1-2b", codex_command=fake_codex("noop"))


async def test_timeout_is_reported(fake_codex) -> None:
    with pytest.raises(CompactError, match="in time"):
        await compact_codex_session("01a1-2b", codex_command=fake_codex("hang"), timeout=1)


@pytest.mark.parametrize("bad", ["", "../etc", "abc; rm -rf /", "ABC"])
async def test_session_id_is_validated(bad) -> None:
    with pytest.raises(CompactError):
        await compact_codex_session(bad, codex_command="/nonexistent")
