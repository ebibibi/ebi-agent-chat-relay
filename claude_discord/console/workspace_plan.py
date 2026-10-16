"""What the Ebi Workspace says to do now, ranked by the plugin's own goodmorning scoring.

The scoring lives in the Ebi Workspace plugin (``scripts/workspace_scanner.py``), which is
licensed separately and is the single source of truth for priorities, due dates, recurrence,
stale projects and follow-ups. The Console never copies it: it runs the installed plugin's
scanner in a separate, isolated Python process and reads JSON back. Without the plugin the
plan is simply unavailable and the rest of the Workspace integration keeps working.

Every ranked task is mapped back to its Markdown line through ``Workspace.entry`` so the
Focus view can complete, re-date or hand it to an agent with the same revision checks as the
Workspace panel.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import threading
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .workspace import Workspace, WorkspaceError

logger = logging.getLogger(__name__)

PLUGIN_ENV = "EBI_WORKSPACE_PLUGIN_DIR"
SCANNER = Path("scripts") / "workspace_scanner.py"
CACHE_SECONDS = 60.0
SCAN_TIMEOUT = 20.0
MAX_ITEMS = 8
MAX_PLAN_CHARS = 8000

# Runs inside the isolated child: ``python -I -c`` ignores PYTHONPATH, the user site and the
# current directory, so only the plugin's scripts directory is importable besides stdlib.
_RUNNER = r"""
import json, sys
from dataclasses import asdict, is_dataclass
from datetime import date
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import workspace_scanner as ws
result = ws.scan_workspace(Path(sys.argv[2]))
def plain(value):
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value
out = {
    "scan_date": result.scan_date.isoformat(),
    "lapsed_days": getattr(ws, "LAPSED_DAYS", 14),
    "projects": [plain(asdict(p)) for p in result.projects],
    "single_actions": [plain(asdict(s)) for s in result.single_actions],
}
for p, raw in zip(out["projects"], result.projects):
    p["follow_up_scores"] = [
        ws.follow_up_score(raw.priority, (w.follow_up - result.scan_date).days)
        for w in raw.due_follow_ups
    ]
json.dump(out, sys.stdout, ensure_ascii=False)
"""


def discover_plugin() -> Path | None:
    """The installed Ebi Workspace plugin: an explicit setting, else Claude Code's registry."""
    value = os.environ.get(PLUGIN_ENV)
    candidates: list[Path] = [Path(value).expanduser()] if value else []
    if not value:
        registry = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
        try:
            plugins = json.loads(registry.read_text(encoding="utf-8")).get("plugins", {})
        except (OSError, ValueError, AttributeError):
            plugins = {}
        for name, installs in plugins.items() if isinstance(plugins, dict) else []:
            if name.split("@", 1)[0] != "ebi-workspace" or not isinstance(installs, list):
                continue
            candidates += [
                Path(i["installPath"])
                for i in installs
                if isinstance(i, dict) and isinstance(i.get("installPath"), str)
            ]
    for candidate in candidates:
        if candidate.is_absolute() and (candidate / SCANNER).is_file():
            return candidate
    return None


def _strip(text: str) -> str:
    """Task text as the plugin displays it: no priority tag, nothing from 📅 on."""
    text = re.sub(r"^\[P[123]\]\s*", "", text.strip())
    cut = re.search(r"📅\d{4}-\d{2}-\d{2}", text)
    return (text[: cut.start()] if cut else text).strip()


def _first_line(text: str | None, limit: int = 200) -> str | None:
    line = next((x.strip() for x in (text or "").splitlines() if x.strip()), "")
    return line[:limit] or None


def _locate(entry: dict[str, Any], text: str) -> dict[str, Any] | None:
    """The one open task in *entry* whose text the scanner reported, or None if ambiguous."""
    wanted = _strip(text)
    same = [t for t in entry["tasks"] if not t["done"] and _strip(t["text"]) == wanted]
    return same[0] if len(same) == 1 else None


def today_plan(root: Path, today: date) -> str | None:
    """The ``## 今日のプラン`` section of today's daily note, written by goodmorning."""
    daily = root / "01_Daily"
    note = daily / f"{today.isoformat()}.md"
    if daily.is_symlink() or note.is_symlink() or not note.is_file():
        return None
    try:
        content = note.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    match = re.search(r"(?m)^## 今日のプラン\s*$", content)
    if not match:
        return None
    rest = content[match.end() :]
    end = re.search(r"(?m)^## ", rest)
    section = (rest[: end.start()] if end else rest).strip()
    return section[:MAX_PLAN_CHARS] or None


class WorkspacePlan:
    """Runs the plugin scanner at most once a minute and shapes it for the Focus view."""

    def __init__(self, workspace: Workspace, plugin: Path | None = None) -> None:
        self.workspace = workspace
        self.plugin = plugin
        self._lock = threading.Lock()
        self._cached: tuple[float, str, dict[str, Any]] | None = None

    @classmethod
    def discover(cls, workspace: Workspace) -> WorkspacePlan:
        return cls(workspace, discover_plugin() if workspace.root else None)

    def invalidate(self) -> None:
        with self._lock:
            self._cached = None

    def _scan(self) -> dict[str, Any]:
        assert self.plugin is not None and self.workspace.root is not None
        done = subprocess.run(  # noqa: S603 - fixed interpreter and argv, no shell
            [
                sys.executable,
                "-I",
                "-c",
                _RUNNER,
                str(self.plugin / SCANNER.parent),
                str(self.workspace.root),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=SCAN_TIMEOUT,
            check=False,
            cwd=str(self.plugin),
        )
        if done.returncode != 0:
            raise WorkspaceError(f"Workspace scanner failed: {done.stderr.strip()[-500:]}")
        return json.loads(done.stdout)

    def read(self) -> dict[str, Any]:
        if self.workspace.root is None:
            return {"available": False, "reason": "workspace"}
        if self.plugin is None:
            return {"available": False, "reason": "plugin"}
        day = datetime.now().astimezone().date().isoformat()
        with self._lock:
            cached = self._cached
            if cached and cached[1] == day and time.monotonic() - cached[0] < CACHE_SECONDS:
                return cached[2]
            try:
                plan = self.shape(self._scan())
            except (OSError, ValueError, subprocess.SubprocessError, WorkspaceError) as exc:
                logger.warning("Ebi Workspace plan unavailable: %s", exc)
                return {"available": False, "reason": "scanner"}
            self._cached = (time.monotonic(), day, plan)
            return plan

    def shape(self, scan: dict[str, Any]) -> dict[str, Any]:
        """Turn scanner output into Focus rows that point at real Markdown lines."""
        assert self.workspace.root is not None
        today = date.fromisoformat(scan["scan_date"])
        entries: dict[str, dict[str, Any] | None] = {}

        def entry(path: str) -> dict[str, Any] | None:
            if path not in entries:
                try:
                    entries[path] = self.workspace.entry(path)
                except (WorkspaceError, OSError, UnicodeError):
                    entries[path] = None
            return entries[path]

        def row(kind: str, path: str, text: str, score: int, **extra: Any) -> dict[str, Any]:
            found = entry(path)
            task = _locate(found, text) if found and kind != "follow_up" else None
            return {
                "kind": kind,
                "path": path,
                "text": _strip(text),
                "score": score,
                "line": task["line"] if task else None,
                "raw": task["text"] if task else None,
                "revision": found["revision"] if found and task else None,
                **extra,
            }

        def due(value: str | None) -> dict[str, Any]:
            if not value:
                return {"due": None, "days": None}
            return {"due": value, "days": (date.fromisoformat(value) - today).days}

        lapsed = int(scan.get("lapsed_days", 14))
        rows: list[dict[str, Any]] = []
        follow_ups: list[dict[str, Any]] = []
        waiting: list[dict[str, Any]] = []
        for p in scan["projects"]:
            path = p["rel_path"]
            if p["category"] == "active" and p["score"] > 0 and p["next_actions"]:
                rows.append(
                    row(
                        "project",
                        path,
                        p["next_actions"][0],
                        p["score"],
                        priority=p["priority"] or None,
                        more=len(p["next_actions"]) - 1,
                        status=_first_line(p["current_status"]),
                        **due(p["nearest_deadline"]),
                    )
                )
            scores = p.get("follow_up_scores") or []
            for w, score in zip(p["due_follow_ups"], scores, strict=False):
                follow_ups.append(
                    row(
                        "follow_up",
                        path,
                        f"{w['who']} / {w['what']}" if w["who"] else w["text"],
                        score,
                        priority=p["priority"] or None,
                        **due(w["follow_up"]),
                    )
                )
            for w in p["waiting"]:
                waiting.append(
                    {
                        "path": path,
                        "who": w["who"] or None,
                        "what": w["what"] or w["text"],
                        "since": w["since"],
                        **due(w["follow_up"]),
                    }
                )
        # goodmorning's order: projects, then follow-ups, then single actions, stably by score.
        rows += follow_ups
        for s in scan["single_actions"]:
            if s["score"] > 0 and not s["lapsed"]:
                rows.append(
                    row(
                        "single",
                        s["context"],
                        s["text"],
                        s["score"],
                        priority=s["priority"] or None,
                        **due(s["deadline"]),
                    )
                )
        rows.sort(key=lambda r: -r["score"])
        waiting.sort(key=lambda w: w["due"] or "9999")
        lapsed_count = sum(1 for s in scan["single_actions"] if s["lapsed"]) + sum(
            len(p["lapsed_milestones"]) for p in scan["projects"]
        )
        return {
            "available": True,
            "date": today.isoformat(),
            "items": rows[:MAX_ITEMS],
            "total": len(rows),
            "waiting": waiting,
            "lapsed": lapsed_count,
            "lapsed_days": lapsed,
            "today_plan": today_plan(self.workspace.root, today),
        }
