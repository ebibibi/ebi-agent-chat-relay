"""The vault notes a thread's session wrote, with diffs since the thread started."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from claude_discord.console import thread_notes as module
from claude_discord.console.thread_notes import changed_notes, note_writes, transcript_note_writes
from claude_discord.console.vault import Vault
from claude_discord.console.workspace import Workspace
from tests.test_console_conversations import AUTH
from tests.test_console_conversations import env as conversation_env

env = conversation_env


def tool(name: str, tool_input: dict) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": name, "input": tool_input}]},
        },
        ensure_ascii=False,
    )


def git(root: Path, *args: str, when: str = "2026-01-01T00:00:00Z") -> None:
    env_vars = {
        **os.environ,
        "GIT_AUTHOR_DATE": when,
        "GIT_COMMITTER_DATE": when,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@x",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@x",
    }
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, env=env_vars)


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "02_Contexts" / "Work").mkdir(parents=True)
    (root / "02_Contexts" / "Work" / "AGENTS.md").write_text("# Work\n", encoding="utf-8")
    (root / "04_Knowledge").mkdir()
    (root / "04_Knowledge" / "Idea.md").write_text("one\ntwo\n", encoding="utf-8")
    (root / "secret").mkdir()
    git(root, "init", "-q")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "base")
    return root


@pytest.mark.parametrize(
    ("name", "text", "expected"),
    [
        ("Edit", "/home/u/notes/04_Knowledge/Idea.md", ["04_Knowledge/Idea.md"]),
        ("Bash", "printf x >> 01_Daily/2026-10-17.md", ["01_Daily/2026-10-17.md"]),
        ("Bash", "cat 04_Knowledge/Idea.md", []),
        ("Read", "/v/04_Knowledge/Idea.md", []),
        ("Write", "/repo/docs/readme.md", []),
    ],
)
def test_note_writes(name, text, expected):
    assert note_writes(name, text) == expected


def test_transcript_lists_each_written_note_once():
    lines = [
        tool("Edit", {"file_path": "/v/04_Knowledge/Idea.md"}),
        tool("Write", {"file_path": "/v/02_Contexts/Work/New.md", "content": "x"}),
        tool("Edit", {"file_path": "/v/04_Knowledge/Idea.md"}),
        "noise",
    ]
    assert transcript_note_writes(lines) == ["04_Knowledge/Idea.md", "02_Contexts/Work/New.md"]


def _transcript(home: Path, session: str, lines: list[str]) -> None:
    path = home / "projects" / "-p" / f"{session}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_changed_notes_diff_since_the_thread_started(vault, tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(module, "ambient_home", lambda backend: home)
    (vault / "04_Knowledge" / "Idea.md").write_text("one\nTWO\n", encoding="utf-8")
    (vault / "02_Contexts" / "Work" / "New.md").write_text("fresh\n", encoding="utf-8")
    (vault / "secret" / "Diary.md").write_text("private\n", encoding="utf-8")
    _transcript(
        home,
        "abc-1",
        [
            tool("Edit", {"file_path": f"{vault}/04_Knowledge/Idea.md"}),
            tool("Write", {"file_path": f"{vault}/02_Contexts/Work/New.md"}),
            tool("Write", {"file_path": f"{vault}/99_Gone/Missing.md"}),
            tool(
                "Bash",
                {
                    "command": f"echo x >> {vault}/02_Contexts/Work/AGENTS.md"
                    f" && git checkout -- {vault}/02_Contexts/Work/AGENTS.md"
                },
            ),
        ],
    )
    v = Vault(vault, tmp_path / "exclude.txt")
    notes = changed_notes(v, "abc-1", "claude", "2026-06-01T00:00:00Z")
    by_path = {n["path"]: n for n in notes}
    assert set(by_path) == {"04_Knowledge/Idea.md", "02_Contexts/Work/New.md"}
    assert "-two" in by_path["04_Knowledge/Idea.md"]["diff"]
    assert "+TWO" in by_path["04_Knowledge/Idea.md"]["diff"]
    assert by_path["02_Contexts/Work/New.md"] == {
        "path": "02_Contexts/Work/New.md",
        "status": "added",
        "diff": "+fresh\n",
        "truncated": False,
    }
    # A thread that started before the vault had any commit shows whole notes as new.
    assert changed_notes(v, "abc-1", "claude", "2025-01-01T00:00:00Z")[0]["status"] == "added"
    v.save_excludes("04_Knowledge/\n")
    assert [n["path"] for n in changed_notes(v, "abc-1", "claude", "2026-06-01")] == [
        "02_Contexts/Work/New.md"
    ]
    assert changed_notes(v, "unknown-9", "claude", None) == []


async def test_route_reads_the_threads_session(env, vault, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    home = tmp_path / "home"
    monkeypatch.setattr(module, "ambient_home", lambda backend: home)
    (vault / "04_Knowledge" / "Idea.md").write_text("changed\n", encoding="utf-8")
    _transcript(home, "abc-1", [tool("Edit", {"file_path": f"{vault}/04_Knowledge/Idea.md"})])
    env.console.workspace = Workspace(vault)

    class Sessions:
        async def get(self, thread_id: int):
            if thread_id != 7:
                return None
            return SimpleNamespace(
                session_id="abc-1", backend="claude", created_at="2026-06-01T00:00:00Z"
            )

    env.console.api.session_repo = Sessions()
    assert (await env.client.get("/console/api/items/t7/notes")).status == 401
    body = await (await env.client.get("/console/api/items/t7/notes", headers=AUTH)).json()
    assert [n["path"] for n in body["notes"]] == ["04_Knowledge/Idea.md"]
    other = await (await env.client.get("/console/api/items/t8/notes", headers=AUTH)).json()
    assert other == {"notes": []}
    capture = await (await env.client.get("/console/api/items/c123/notes", headers=AUTH)).json()
    assert capture == {"notes": []}
