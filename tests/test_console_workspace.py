"""The optional Workspace view reads and updates the original Markdown tasks."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_discord.console.workspace import Workspace, WorkspaceConflictError, WorkspaceError


@pytest.fixture
def notes(tmp_path: Path) -> Path:
    root = tmp_path / "notes"
    ctx = root / "02_Contexts" / "Work" / "Client"
    ctx.mkdir(parents=True)
    (ctx / "AGENTS.md").write_text("# Client\n", encoding="utf-8")
    (ctx / "single-actions.md").write_text("# Tasks\n- [ ] Call back\n", encoding="utf-8")
    project = ctx / "Website"
    project.mkdir()
    (project / "status.md").write_text(
        "---\nupdated: 2026-01-01\n---\n# Website\n最終更新: 2026-01-01\n"
        "## 現在地\nReady\n## 次のアクション\n- [ ] 🤖 Ship it #auto\n"
        "- [x] Old task\n```md\n- [ ] Example only\n```\n"
        "## 連絡待ち\n- Client / Feedback\n",
        encoding="utf-8",
    )
    (project / "overview.md").write_text("# Website overview\n", encoding="utf-8")
    return root


def test_no_explicit_configuration_does_not_guess_a_vault(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
    (tmp_path / "claude-notes" / "02_Contexts").mkdir(parents=True)
    assert Workspace.discover().snapshot() == {"available": False, "entries": []}


def test_shared_configuration_and_environment_override(notes, tmp_path, monkeypatch):
    config = tmp_path / ".config" / "ebi-workspace"
    config.mkdir(parents=True)
    (config / "notes-folder").write_text(str(notes), encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config.parent))
    assert Workspace.discover().root == notes
    monkeypatch.setenv("EBI_WORKSPACE_NOTES_FOLDER", str(tmp_path / "missing"))
    assert not Workspace.discover().snapshot()["available"]


def test_nested_context_and_project_tasks_exclude_examples_archives_and_symlinks(notes):
    archived = notes / "02_Contexts" / "_archive" / "Old"
    archived.mkdir(parents=True)
    (archived / "status.md").write_text("## 次のアクション\n- [ ] Old\n")
    (notes / "02_Contexts" / "Alias").symlink_to(
        notes / "02_Contexts" / "Work", target_is_directory=True
    )
    data = Workspace(notes).snapshot()
    entries = {e["path"]: e for e in data["entries"]}
    assert set(entries) == {"Work/Client", "Work/Client/Website"}
    project = entries["Work/Client/Website"]
    assert project["context"] == "Work/Client"
    assert project["kind"] == "project"
    assert [t["text"] for t in project["tasks"]] == ["🤖 Ship it #auto", "Old task"]
    assert project["tasks"][1]["done"] is True
    assert entries["Work/Client"]["tasks"][0]["text"] == "Call back"


def test_task_completion_edits_only_original_task_and_records_change(notes):
    ws = Workspace(notes)
    entry = next(e for e in ws.snapshot()["entries"] if e["kind"] == "project")
    task = entry["tasks"][0]
    ws.update_task(entry["path"], task["line"], entry["revision"], done=True)
    content = (notes / "02_Contexts" / entry["path"] / "status.md").read_text()
    assert "- [x] 🤖 Ship it #auto" in content
    assert "## 連絡待ち\n- Client / Feedback" in content
    assert "- [ ] Example only" in content
    assert (notes / "02_Contexts" / entry["path"] / "log.md").exists()
    assert list((notes / "01_Daily").glob("*.md"))
    with pytest.raises(WorkspaceConflictError):
        ws.update_task(entry["path"], task["line"], entry["revision"], text="Stale edit")
    assert "Stale edit" not in (notes / "02_Contexts" / entry["path"] / "status.md").read_text()


def test_add_and_rename_task_in_context(notes):
    ws = Workspace(notes)
    entry = ws.entry("Work/Client")
    ws.add_task(entry["path"], "Read feedback", entry["revision"])
    entry = ws.entry("Work/Client")
    task = entry["tasks"][-1]
    ws.update_task(entry["path"], task["line"], entry["revision"], text="Review feedback")
    assert ws.entry("Work/Client")["tasks"][-1]["text"] == "Review feedback"


@pytest.mark.parametrize(
    "path", ["../outside", "/tmp", "Work/../Client", "Work\\Client", "Work/Client/overview.md"]
)
def test_paths_cannot_escape_or_target_arbitrary_files(notes, path):
    with pytest.raises(WorkspaceError):
        Workspace(notes).entry(path)


def test_symlinked_task_file_is_not_read_or_written(notes, tmp_path):
    source = notes / "02_Contexts" / "Work/Client/single-actions.md"
    source.unlink()
    secret = tmp_path / "outside.md"
    secret.write_text("- [ ] Outside")
    source.symlink_to(secret)
    with pytest.raises(WorkspaceError):
        Workspace(notes).entry("Work/Client")
    assert secret.read_text() == "- [ ] Outside"


def test_stale_and_non_task_line_updates_are_rejected(notes):
    ws = Workspace(notes)
    entry = ws.entry("Work/Client")
    with pytest.raises(WorkspaceError):
        ws.update_task(entry["path"], 1, entry["revision"], done=True)
    with pytest.raises(WorkspaceError):
        ws.update_task(entry["path"], 2, entry["revision"], text="Text\n- [ ] Injected")
    with pytest.raises(WorkspaceError):
        ws.update_task(entry["path"], 2, entry["revision"], done="yes")


def test_binding_follows_working_directory_or_parent_and_disambiguates_names(notes):
    ws = Workspace(notes)
    items = [
        {"id": "a", "working_dir": str(notes / "02_Contexts/Work/Client/Website")},
        {"id": "b", "parent_id": "a"},
        {"id": "c", "project": "Website"},
        {"id": "d", "title": "Unrelated"},
    ]
    bound = ws.bind_items(items)
    assert [i.get("workspace_path") for i in bound] == [
        "Work/Client/Website",
        "Work/Client/Website",
        "Work/Client/Website",
        None,
    ]
    assert "workspace_path" not in items[0]
    prompt = ws.prompt("Work/Client/Website", "Continue the task")
    assert "Continue the task" in prompt
    assert str(notes / "02_Contexts/Work/Client/Website/status.md") in prompt
    assert "AGENTS.md" in prompt and "log.md" in prompt


def test_daily_symlink_is_rejected_before_task_write(notes, tmp_path):
    from datetime import datetime

    ws = Workspace(notes)
    entry = ws.entry("Work/Client")
    daily = notes / "01_Daily"
    daily.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("Do not touch")
    (daily / f"{datetime.now().astimezone():%Y-%m-%d}.md").symlink_to(outside)
    with pytest.raises(WorkspaceError):
        ws.update_task(entry["path"], entry["tasks"][0]["line"], entry["revision"], done=True)
    assert ws.entry(entry["path"])["revision"] == entry["revision"]
    assert outside.read_text() == "Do not touch"


def test_binding_uses_explicit_repository_location_in_project_overview(notes, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    overview = notes / "02_Contexts/Work/Client/Website/overview.md"
    overview.write_text(f"# Website\n- リポジトリ: `{repo}`\n")
    items = [
        {"id": "code", "working_dir": str(repo / "src")},
        {"id": "other", "working_dir": str(tmp_path)},
    ]
    bound = Workspace(notes).bind_items(items)
    assert bound[0]["workspace_path"] == "Work/Client/Website"
    assert bound[1]["workspace_path"] is None


def test_update_rechecks_revision_after_reading_task_content(notes, monkeypatch):
    import claude_discord.console.workspace as module

    ws = Workspace(notes)
    entry = ws.entry("Work/Client")
    read = module._read
    reads = 0

    def changed_after_entry(file):
        nonlocal reads
        if file.name == "single-actions.md":
            reads += 1
            if reads == 2:
                file.write_text("# Replaced externally\n")
        return read(file)

    monkeypatch.setattr(module, "_read", changed_after_entry)
    with pytest.raises(WorkspaceConflictError):
        ws.update_task(entry["path"], entry["tasks"][0]["line"], entry["revision"], done=True)


def test_binding_resolves_each_working_directory_once(notes, monkeypatch):
    """The board binds every item on each poll; resolving per item per entry made it slow."""
    (notes / "02_Contexts/Work/Client/Website/src").mkdir()
    cwd = str(notes / "02_Contexts/Work/Client/Website/src")
    items = [{"id": str(n), "working_dir": cwd} for n in range(20)]
    real_resolve = Path.resolve
    calls = []

    def counting_resolve(self, *args, **kwargs):
        if str(self) == cwd:
            calls.append(self)
        return real_resolve(self, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", counting_resolve)
    bound = Workspace(notes).bind_items(items)
    assert {i["workspace_path"] for i in bound} == {"Work/Client/Website"}
    assert len(calls) == 1


def test_a_guessed_binding_is_marked_and_an_explicit_one_is_not(notes):
    items = [
        {"id": "a", "working_dir": str(notes / "02_Contexts/Work/Client/Website")},
        {"id": "b", "workspace_path": "Work/Client"},
        {"id": "c", "title": "Unrelated"},
    ]
    bound = Workspace(notes).bind_items(items)
    assert [i.get("workspace_by") for i in bound] == ["auto", None, None]


def test_records_give_tasks_and_when_the_records_last_changed(notes):
    import os

    ws = Workspace(notes)
    entries = ws.snapshot()["entries"]
    project = notes / "02_Contexts/Work/Client/Website"
    os.utime(project / "status.md", (1_700_000_000, 1_700_000_000))
    (project / "log.md").write_text("# log\n", encoding="utf-8")
    os.utime(project / "log.md", (1_800_000_000, 1_800_000_000))
    records = ws.records(entries)
    website = records["Work/Client/Website"]
    assert [t["text"] for t in website["tasks"]] == ["🤖 Ship it #auto", "Old task"]
    # The newer of the task file and the log: either one means the work was recorded.
    assert website["updated_at"] == "2027-01-15T08:00:00Z"
    assert records["Work/Client"]["updated_at"] is not None
    assert ws.bind_items([{"id": "x"}], entries)[0]["workspace_path"] is None


def test_finish_checks_one_exact_open_task_and_records_it(notes):
    workspace = Workspace(notes)
    result = workspace.finish("Work/Client", ["Call back"], "Call back", done=True)
    assert result == {"path": "Work/Client", "task": "Call back", "changed": True}
    assert workspace.entry("Work/Client")["tasks"][0]["done"] is True
    assert "タスク完了: Call back" in (notes / "02_Contexts/Work/Client/log.md").read_text("utf-8")
    again = workspace.finish("Work/Client", ["Call back"], "Call back", done=True)
    assert again == {"path": "Work/Client", "task": "Call back", "changed": False}


def test_finish_ignores_ambiguous_tasks(notes):
    actions = notes / "02_Contexts/Work/Client/single-actions.md"
    actions.write_text("# Tasks\n- [ ] Call back\n- [ ] Call back\n", encoding="utf-8")
    workspace = Workspace(notes)
    result = workspace.finish("Work/Client", ["Call back"], "Call back", done=True)
    assert result["task"] is None
    assert [t["done"] for t in workspace.entry("Work/Client")["tasks"]] == [False, False]
