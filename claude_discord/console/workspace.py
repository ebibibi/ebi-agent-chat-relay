"""Optional, explicitly configured Ebi Workspace; Markdown remains authoritative."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

MAX_DOCUMENT_BYTES = 2_000_000
_TASK = re.compile(r"^(\s*[-*+]\s+\[)([ xX])(\]\s+)(.+?)(\r?\n)?$")
# A project's next actions may also be plain bullets ("- item", "1. item"); the plugin's
# goodmorning reads them as open tasks, so the Console does too and checks them off by
# turning "- item" into "- [x] item". single-actions.md keeps checkbox-only tasks.
_PLAIN = re.compile(r"^(\s*(?:[-*+]|\d+[.)])\s+)(?!\[[ xX]\])(\S.*?)(\r?\n)?$")
_NEXT = {
    "次のアクション",
    "🔥 次に取り組むこと",
    "次に取り組むこと",
    "Next actions",
    "Next Actions",
}


class WorkspaceError(ValueError):
    """Invalid or unavailable Workspace data."""


class WorkspaceConflictError(WorkspaceError):
    """The source changed after the browser read it."""


def _read(path: Path) -> str:
    if path.is_symlink():
        raise WorkspaceError("Workspace documents must not be symbolic links")
    if not path.exists():
        return ""
    with path.open("rb") as source:
        data = source.read(MAX_DOCUMENT_BYTES + 1)
    if len(data) > MAX_DOCUMENT_BYTES:
        raise WorkspaceError("Workspace document is too large")
    return data.decode("utf-8")


def _revision(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _tasks(content: str, project: bool) -> list[dict[str, Any]]:
    tasks = []
    active = not project
    fence = None
    for number, line in enumerate(content.splitlines(keepends=True), 1):
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            marker = stripped[:3]
            fence = None if fence == marker else marker if fence is None else fence
            continue
        if fence:
            continue
        if line.startswith("## "):
            active = not project or line[3:].strip() in _NEXT
        if not active:
            continue
        match = _TASK.match(line)
        if match:
            tasks.append({"line": number, "text": match[4], "done": match[2].lower() == "x"})
        elif project and (plain := _PLAIN.match(line)):
            tasks.append({"line": number, "text": plain[2], "done": False, "checkbox": False})
    return tasks


def _edit_line(line: str, *, text: str | None = None, done: bool | None = None) -> str | None:
    """*line* with its task text and/or state changed, or None when it is not a task."""
    match = _TASK.match(line)
    if match:
        mark = ("x" if done else " ") if done is not None else match[2]
        return f"{match[1]}{mark}{match[3]}{text if text is not None else match[4]}{match[5] or ''}"
    plain = _PLAIN.match(line)
    if plain is None:
        return None
    label = text if text is not None else plain[2]
    box = "[x] " if done else "[ ] " if done is False else ""
    return f"{plain[1]}{box}{label}{plain[3] or ''}"


class Workspace:
    def __init__(self, root: Path | None) -> None:
        self.root = root.resolve() if root and (root / "02_Contexts").is_dir() else None
        self._lock = threading.RLock()

    @classmethod
    def discover(cls) -> Workspace:
        """Use shared configuration, never a guessed default vault or plugin cache."""
        value = os.environ.get("EBI_WORKSPACE_NOTES_FOLDER") or os.environ.get(
            "CLAUDE_WORKSPACE_NOTES_FOLDER"
        )
        config = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
        try:
            if not value:
                file = config / "ebi-workspace" / "notes-folder"
                if file.is_file():
                    value = file.read_text(encoding="utf-8").splitlines()[0].strip()
            if not value:
                settings = Path.home() / ".claude" / "settings.local.json"
                if settings.is_file():
                    env = json.loads(settings.read_text(encoding="utf-8")).get("env", {})
                    value = env.get("EBI_WORKSPACE_NOTES_FOLDER") or env.get(
                        "CLAUDE_WORKSPACE_NOTES_FOLDER"
                    )
            path = Path(value).expanduser() if isinstance(value, str) and value else None
            return cls(path if path and path.is_absolute() else None)
        except (OSError, ValueError, IndexError, TypeError):
            return cls(None)

    def _directory(self, path: str) -> Path:
        if self.root is None:
            raise WorkspaceError("Ebi Workspace is not configured")
        if not isinstance(path, str) or not path or "\\" in path or "\0" in path:
            raise WorkspaceError("Invalid Workspace path")
        parts = PurePosixPath(path).parts
        if PurePosixPath(path).is_absolute() or any(p.startswith((".", "_")) for p in parts):
            raise WorkspaceError("Invalid Workspace path")
        directory = self.root / "02_Contexts"
        for part in parts:
            directory = directory / part
            if directory.is_symlink():
                raise WorkspaceError("Workspace paths must not be symbolic links")
        if not directory.is_dir() or not directory.resolve().is_relative_to(
            self.root / "02_Contexts"
        ):
            raise WorkspaceError("Unknown Workspace entry")
        return directory

    def entry(self, path: str) -> dict[str, Any]:
        directory = self._directory(path)
        project = (directory / "status.md").is_file()
        if not project and not (directory / "AGENTS.md").is_file():
            raise WorkspaceError("Unknown Workspace context or project")
        filename = "status.md" if project else "single-actions.md"
        content = _read(directory / filename)
        context = ""
        for parent in directory.parents:
            if self.root is None or parent == self.root / "02_Contexts":
                break
            if (parent / "AGENTS.md").is_file() and not (parent / "status.md").is_file():
                context = parent.relative_to(self.root / "02_Contexts").as_posix()
                break
        return {
            "path": path,
            "name": directory.name,
            "kind": "project" if project else "context",
            "context": context if project else path,
            "file": filename,
            "revision": _revision(content),
            "tasks": _tasks(content, project),
        }

    def snapshot(self) -> dict[str, Any]:
        if self.root is None:
            return {"available": False, "entries": []}
        entries = []
        base = self.root / "02_Contexts"
        for directory, dirs, files in os.walk(base, followlinks=False):
            dirs[:] = sorted(
                d
                for d in dirs
                if not d.startswith((".", "_")) and not (Path(directory) / d).is_symlink()
            )
            if Path(directory) == base or not {"status.md", "AGENTS.md"}.intersection(files):
                continue
            try:
                entries.append(self.entry(Path(directory).relative_to(base).as_posix()))
            except (WorkspaceError, OSError, UnicodeError):
                # One unreadable entry must not expose its target or hide the others.
                continue
        return {"available": True, "name": self.root.name, "entries": entries}

    def document(self, path: str, name: str) -> str:
        self.entry(path)
        if name not in {"overview.md", "status.md", "single-actions.md", "AGENTS.md"}:
            raise WorkspaceError("Unknown Workspace document")
        return _read(self._directory(path) / name)

    def _save(self, file: Path, content: str, revision: str) -> None:
        if _revision(_read(file)) != revision:
            raise WorkspaceConflictError("The file changed. Refresh and try again.")
        fd, temporary = tempfile.mkstemp(prefix=".console-", dir=file.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as target:
                target.write(content)
            if file.exists():
                os.chmod(temporary, file.stat().st_mode & 0o777)
            if _revision(_read(file)) != revision:
                raise WorkspaceConflictError("The file changed. Refresh and try again.")
            os.replace(temporary, file)
        finally:
            Path(temporary).unlink(missing_ok=True)

    @staticmethod
    def _text(text: Any) -> str:
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text) > 4000
            or any(c in text for c in "\r\n\0")
        ):
            raise WorkspaceError("Task text must be one nonempty line of at most 4000 characters")
        return text.strip()

    def _record(self, path: str, action: str) -> None:
        if self.root is None:
            raise WorkspaceError("Ebi Workspace is not configured")
        now = datetime.now().astimezone()
        directory = self._directory(path)
        log = directory / "log.md"
        old = _read(log)
        self._save(
            log,
            old
            + ("" if f"## {now:%Y-%m-%d}\n" in old else f"\n## {now:%Y-%m-%d}\n")
            + f"\n### やったこと\n- Console: {action}\n",
            _revision(old),
        )
        daily = self.root / "01_Daily"
        if daily.is_symlink():
            raise WorkspaceError("Daily notes must not be a symbolic link")
        daily.mkdir(exist_ok=True)
        note = daily / f"{now:%Y-%m-%d}.md"
        old = _read(note)
        self._save(
            note,
            old
            + f"\n## {now:%H:%M} — {path}\n\n"
            + f"[[02_Contexts/{path}/log.md]] — Consoleでタスクを更新。\n",
            _revision(old),
        )

    def _write_tasks(self, path: str, content: str, revision: str, action: str) -> None:
        directory = self._directory(path)
        entry = self.entry(path)
        today = datetime.now().astimezone().date().isoformat()
        content = re.sub(r"(?m)^(最終更新:\s*\**)[0-9-]{10}", rf"\g<1>{today}", content)
        if content.startswith("---\n"):
            end = content.find("\n---", 4)
            if end >= 0:
                front = re.sub(r"(?m)^updated:.*$", f"updated: {today}", content[:end])
                content = front + content[end:]
        # Preflight log paths before changing the task, avoiding partial writes on unsafe paths.
        _read(directory / "log.md")
        if self.root is None:
            raise WorkspaceError("Ebi Workspace is not configured")
        if (self.root / "01_Daily").is_symlink():
            raise WorkspaceError("Daily notes must not be a symbolic link")
        _read(self.root / "01_Daily" / f"{today}.md")
        self._save(directory / entry["file"], content, revision)
        self._record(path, action)

    def update_task(
        self,
        path: str,
        line: int,
        revision: str,
        *,
        text: str | None = None,
        done: bool | None = None,
    ) -> None:
        with self._lock:
            entry = self.entry(path)
            if entry["revision"] != revision:
                raise WorkspaceConflictError("The file changed. Refresh and try again.")
            if (
                isinstance(line, bool)
                or not isinstance(line, int)
                or not any(t["line"] == line for t in entry["tasks"])
            ):
                raise WorkspaceError("Unknown task line")
            if done is not None and not isinstance(done, bool):
                raise WorkspaceError("done must be a boolean")
            content = _read(self._directory(path) / entry["file"])
            if _revision(content) != revision:
                raise WorkspaceConflictError("The file changed. Refresh and try again.")
            lines = content.splitlines(keepends=True)
            task = next(t for t in entry["tasks"] if t["line"] == line)
            label = self._text(text) if text is not None else task["text"]
            edited = _edit_line(lines[line - 1], text=label, done=done)
            if edited is None:
                raise WorkspaceConflictError("The task changed. Refresh and try again.")
            lines[line - 1] = edited
            self._write_tasks(path, "".join(lines), revision, f"タスク更新: {label}")

    def finish(self, path: str, texts: list[str], label: str, *, done: bool) -> dict[str, Any]:
        """Follow a Console item finishing (or reopening) into the original Markdown.

        The task is the one open (or done) task whose text equals one of *texts*,
        tried in order; an ambiguous match picks none rather than guess. Finishing
        with no task still records the conversation in log.md and the daily note;
        a task already in the wanted state is left alone, since the agent wrote it.
        """
        with self._lock:
            entry = self.entry(path)
            task = None
            for text in (t.strip() for t in texts if t and t.strip()):
                same = [t for t in entry["tasks"] if t["text"].strip() == text]
                if len(same) == 1:
                    task = same[0]
                    break
            result: dict[str, Any] = {"path": path, "task": task["text"] if task else None}
            content = _read(self._directory(path) / entry["file"])
            if task is None or task["done"] == done:
                result["changed"] = task is None and done
                if result["changed"]:
                    self._write_tasks(path, content, entry["revision"], f"会話完了: {label}")
                return result
            lines = content.splitlines(keepends=True)
            edited = _edit_line(lines[task["line"] - 1], done=done)
            if edited is None:
                raise WorkspaceConflictError("The task changed. Refresh and try again.")
            lines[task["line"] - 1] = edited
            action = "タスク完了" if done else "タスク再開"
            self._write_tasks(path, "".join(lines), entry["revision"], f"{action}: {task['text']}")
            result["changed"] = True
            return result

    def add_task(self, path: str, text: str, revision: str) -> None:
        with self._lock:
            label = self._text(text)
            entry = self.entry(path)
            content = _read(self._directory(path) / entry["file"])
            if entry["kind"] == "project":
                heading = next(
                    (m for m in re.finditer(r"(?m)^## (.+)$", content) if m[1].strip() in _NEXT),
                    None,
                )
                if heading:
                    at = content.find("\n", heading.end())
                    at = len(content) if at < 0 else at + 1
                    content = content[:at].rstrip("\n") + f"\n- [ ] {label}\n" + content[at:]
                else:
                    content = content.rstrip() + f"\n\n## 次のアクション\n\n- [ ] {label}\n"
            else:
                content = content.rstrip() + f"\n- [ ] {label}\n"
            self._write_tasks(path, content, revision, f"タスク追加: {label}")

    def _repository(self, path: str) -> Path | None:
        """Only an explicitly labelled local repository is an automatic association."""
        try:
            overview = self.document(path, "overview.md")
        except (WorkspaceError, OSError, UnicodeError):
            return None
        match = re.search(
            r"(?im)^\s*(?:-\s*)?(?:リポジトリ|repository|working_dir)\s*[:：]\s*`?([^`\n]+)`?\s*$",
            overview,
        )
        if not match:
            return None
        candidate = Path(match[1].strip().strip('"')).expanduser()
        return candidate.resolve() if candidate.is_absolute() and candidate.is_dir() else None

    def _bind_directory(
        self,
        here: Path,
        entries: list[dict[str, Any]],
        repositories: dict[str, Path | None],
    ) -> str | None:
        """The entry a working directory belongs to: inside its folder, else its repository."""
        if self.root is None:
            return None
        base = self.root / "02_Contexts"
        matches = [e["path"] for e in entries if here.is_relative_to(base / e["path"])]
        if matches:
            return max(matches, key=len)
        repository_matches = [
            path
            for path, repository in repositories.items()
            if repository and here.is_relative_to(repository)
        ]
        return repository_matches[0] if len(repository_matches) == 1 else None

    def bind_items(
        self, items: list[dict[str, Any]], entries: list[dict[str, Any]] | None = None
    ) -> list[dict[str, Any]]:
        """Copies of *items* with ``workspace_path`` filled in where it can be inferred.

        An inferred path is marked ``workspace_by = "auto"`` so a reader can tell a
        guess from a binding a person chose. *entries* reuses a snapshot already taken.
        """
        known: list[dict[str, Any]] = entries if entries is not None else self.snapshot()["entries"]
        paths = {e["path"] for e in known}
        repositories = {e["path"]: self._repository(e["path"]) for e in known}
        result = [dict(i) for i in items]
        by_id = {i["id"]: i for i in result}
        # Hundreds of items share a few working directories; bind each one once.
        by_cwd: dict[str, str | None] = {}
        for item in result:
            explicit = item.get("workspace_path")
            if explicit in paths:
                continue
            cwd = item.get("working_dir")
            if cwd and cwd not in by_cwd:
                by_cwd[cwd] = self._bind_directory(Path(cwd).resolve(), known, repositories)
            item["workspace_path"] = by_cwd[cwd] if cwd else None
            if not item["workspace_path"] and item.get("project"):
                matches = [e["path"] for e in known if item["project"] in (e["path"], e["name"])]
                if len(matches) == 1:
                    item["workspace_path"] = matches[0]
        for item in result:
            seen = {item["id"]}
            parent = by_id.get(item.get("parent_id"))
            while not item["workspace_path"] and parent and parent["id"] not in seen:
                seen.add(parent["id"])
                item["workspace_path"] = parent.get("workspace_path")
                parent = by_id.get(parent.get("parent_id"))
        for item, original in zip(result, items, strict=True):
            if item["workspace_path"] and original.get("workspace_path") not in paths:
                item["workspace_by"] = "auto"
        return result

    def records(self, entries: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        """Per entry: its tasks and when its records last changed (task file or log.md).

        The Focus guide compares this with a thread's last activity to notice work
        that finished in conversation but was never written back.
        """
        out: dict[str, dict[str, Any]] = {}
        for entry in entries:
            try:
                directory = self._directory(entry["path"])
                stamps = [
                    (directory / name).stat().st_mtime
                    for name in (entry["file"], "log.md")
                    if (directory / name).is_file() and not (directory / name).is_symlink()
                ]
            except (WorkspaceError, OSError):
                continue
            updated = (
                datetime.fromtimestamp(max(stamps), UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
                if stamps
                else None
            )
            out[entry["path"]] = {"tasks": entry["tasks"], "updated_at": updated}
        return out

    def prompt(self, path: str, text: str) -> str:
        entry = self.entry(path)
        directory = self._directory(path)
        if self.root is None:
            raise WorkspaceError("Ebi Workspace is not configured")
        return (
            f"{text}\n\n[Ebi Workspace context]\n"
            f"This work belongs to {path}. Read applicable AGENTS.md files from "
            f"{self.root} down to {directory}, then read {directory / entry['file']} "
            f"and {directory / 'overview.md'} if present. Keep tasks in that original "
            f"Markdown file; record completed work in {directory / 'log.md'} and "
            f"a link-only summary in {self.root / '01_Daily'}. Update status dates. "
            "Use the project's repository instructions and working directory when implementing. "
            "Mark a task complete only after its requested work succeeds.\n"
        )
