"""A thread is bound to the Workspace project whose records its session wrote."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from claude_discord.console import workspace_records as records_module
from claude_discord.console.workspace import Workspace
from claude_discord.console.workspace_records import (
    RecordScanner,
    RecordWatcher,
    entry_for,
    record_writes,
)
from tests.test_console_conversations import env as conversation_env
from tests.test_console_workspace import notes as workspace_notes

env = conversation_env
notes = workspace_notes

VAULT = "/home/u/notes/02_Contexts"


def claude_line(name: str, tool_input: dict) -> str:
    return json.dumps(
        {
            "type": "assistant",
            "message": {"content": [{"type": "tool_use", "name": name, "input": tool_input}]},
        },
        ensure_ascii=False,
    )


def codex_line(name: str, arguments: object) -> str:
    args = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return json.dumps(
        {
            "type": "response_item",
            "payload": {"type": "function_call", "name": name, "arguments": args},
        }
    )


@pytest.mark.parametrize(
    ("name", "text", "expected"),
    [
        ("Edit", f"{VAULT}/Work/Site/status.md", [("Work/Site", "status.md")]),
        ("Write", "notes/02_Contexts/仕事/案件 A/log.md", [("仕事/案件 A", "log.md")]),
        ("Bash", f"printf 'x' >> '{VAULT}/Work/Site/log.md'", [("Work/Site", "log.md")]),
        (
            "Bash",
            f'D="{VAULT}/Work/Site"; printf "- done" >> "$D/log.md"',
            [("Work/Site", "log.md")],
        ),
        ("Bash", f"cat {VAULT}/Work/Site/log.md | tail", []),
        ("Read", f"{VAULT}/Work/Site/status.md", []),
        (
            "Bash",
            f"sed -i 's/a/b/' {VAULT}/Work/single-actions.md",
            [("Work", "single-actions.md")],
        ),
        ("Edit", f"{VAULT}/Work/Site/notes.md", []),
        ("Edit", "/elsewhere/log.md", []),
    ],
)
def test_record_writes_need_a_write_and_a_record_file(name, text, expected):
    got = [(w.folder, w.file) for w in record_writes(name, text)]
    assert got == expected


def test_scanner_reads_claude_and_codex_transcripts():
    lines = [
        claude_line("Read", {"file_path": f"{VAULT}/A/status.md"}),
        claude_line("Edit", {"file_path": f"{VAULT}/A/status.md", "old_string": "x"}),
        codex_line("apply_patch", f"*** Begin Patch\n*** Update File: {VAULT}/B/log.md\n"),
        codex_line("shell", {"command": ["bash", "-lc", f"echo hi >> {VAULT}/C/log.md"]}),
        "not json",
        json.dumps({"type": "user", "message": {"content": "02_Contexts/A/log.md >>"}}),
    ]
    got = [(w.folder, w.file) for w in RecordScanner().writes(lines)]
    assert got == [("A", "status.md"), ("B", "log.md"), ("C", "log.md")]


def test_entry_is_the_deepest_known_folder():
    entries = [{"path": "Work"}, {"path": "Work/Site"}]
    assert entry_for("Work/Site/docs", entries) == "Work/Site"
    assert entry_for("Work/Other", entries) == "Work"
    assert entry_for("Home", entries) is None
    assert entry_for("Work/../Home", entries) is None


def test_watcher_reads_only_new_lines(tmp_path, monkeypatch):
    transcript = tmp_path / "projects" / "-x" / "abc-123.jsonl"
    transcript.parent.mkdir(parents=True)
    monkeypatch.setattr(records_module, "ambient_home", lambda backend: tmp_path)
    entries = [{"path": "Work/Site"}, {"path": "Home"}]
    watcher = RecordWatcher()
    assert watcher.latest("abc-123", "claude", entries) is None
    transcript.write_text(
        claude_line("Edit", {"file_path": f"{VAULT}/Work/Site/log.md"}) + "\n", encoding="utf-8"
    )
    assert watcher.latest("abc-123", "claude", entries) == ("Work/Site", "log.md")
    assert watcher.latest("abc-123", "claude", entries) is None
    with transcript.open("a", encoding="utf-8") as out:
        out.write(claude_line("Write", {"file_path": f"{VAULT}/Home/single-actions.md"}) + "\n")
        out.write(claude_line("Edit", {"file_path": f"{VAULT}/Home/single"}))  # partial line
    assert watcher.latest("abc-123", "claude", entries) == ("Home", "single-actions.md")
    assert watcher.latest("../etc", "claude", entries) is None


async def test_server_binds_recorded_threads_without_overriding_a_person(
    env, notes, tmp_path, monkeypatch
):
    env.console.workspace = Workspace(notes)
    vault = notes / "02_Contexts"
    home = tmp_path / "claude-home"
    for sid, path in (("aaa-1", "Work/Client/Website"), ("bbb-2", "Work/Client")):
        transcript = home / "projects" / "-p" / f"{sid}.jsonl"
        transcript.parent.mkdir(parents=True, exist_ok=True)
        transcript.write_text(
            claude_line("Edit", {"file_path": str(vault / path / "log.md")}) + "\n",
            encoding="utf-8",
        )
    monkeypatch.setattr(records_module, "ambient_home", lambda backend: home)
    today = "2099-01-01 10:00:00"
    sessions = [
        SimpleNamespace(thread_id=11, session_id="aaa-1", backend="claude", last_used_at=today),
        SimpleNamespace(thread_id=22, session_id="bbb-2", backend=None, last_used_at=today),
    ]

    class Sessions:
        async def list_all(self, limit: int = 500) -> list[SimpleNamespace]:
            return sessions

    env.console.api.session_repo = Sessions()
    await env.console.work_repo.bind_workspace("t22", "Work/Client/Website", "pinned")
    assert await env.console.bind_recorded() == 1
    first = await env.console.work_repo.get("t11")
    assert (first.workspace_path, first.workspace_source, first.workspace_reason) == (
        "Work/Client/Website",
        "recorded",
        "record:log.md",
    )
    assert (await env.console.work_repo.get("t22")).workspace_path == "Work/Client/Website"
    # Nothing new since the last pass: no transcript is read again.
    assert await env.console.bind_recorded() == 0


def test_watcher_follows_a_session_into_its_pool_profile(tmp_path, monkeypatch):
    """A pooled session's transcript is in its profile's home; a move is followed."""
    ambient, profile = tmp_path / "ambient", tmp_path / "work"
    monkeypatch.setattr(records_module, "ambient_home", lambda backend: ambient)
    entries = [{"path": "Work/Site"}, {"path": "Home"}]
    line = claude_line("Edit", {"file_path": f"{VAULT}/Work/Site/log.md"}) + "\n"
    for home in (ambient, profile):
        path = home / "projects" / "-x" / "abc-123.jsonl"
        path.parent.mkdir(parents=True)
        path.write_text(line, encoding="utf-8")
    watcher = RecordWatcher()
    assert watcher.latest("abc-123", "claude", entries) == ("Work/Site", "log.md")
    # The session moved to a profile: its copy there is read, not the cached ambient file.
    moved = profile / "projects" / "-x" / "abc-123.jsonl"
    with moved.open("a", encoding="utf-8") as out:
        out.write(claude_line("Write", {"file_path": f"{VAULT}/Home/single-actions.md"}) + "\n")
    assert watcher.latest("abc-123", "claude", entries, profile) == ("Home", "single-actions.md")
