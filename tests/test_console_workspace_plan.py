"""Workspace next actions ranked by the installed plugin's scanner, mapped to Markdown lines."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from claude_discord.console import workspace_plan as plan_module
from claude_discord.console.workspace import Workspace
from claude_discord.console.workspace_plan import WorkspacePlan, discover_plugin, today_plan
from tests.test_console_conversations import AUTH
from tests.test_console_conversations import env as conversation_env
from tests.test_console_workspace import notes as workspace_notes

env = conversation_env
notes = workspace_notes

# A stand-in with the same public shape as the plugin's scripts/workspace_scanner.py.
FAKE_SCANNER = """
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from recurrence import MARK  # the real scanner imports a sibling module too

@dataclass
class WaitingItem:
    text: str
    who: str = ""
    what: str = ""
    since: date | None = None
    follow_up: date | None = None

@dataclass
class ProjectScore:
    name: str
    context: str
    path: Path
    rel_path: str = ""
    score: int = 0
    priority: str = ""
    nearest_deadline: date | None = None
    days_until_deadline: int | None = None
    waiting: list = field(default_factory=list)
    waiting_noise: int = 0
    due_follow_ups: list = field(default_factory=list)
    due_today: list = field(default_factory=list)
    lapsed_milestones: list = field(default_factory=list)
    staleness_days: int = 0
    current_status: str = ""
    next_actions: list = field(default_factory=list)
    category: str = ""

@dataclass
class SingleAction:
    context: str
    text: str
    priority: str = ""
    deadline: date | None = None
    score: int = 0
    lapsed: bool = False

@dataclass
class ScanResult:
    projects: list
    single_actions: list
    scan_date: date

LAPSED_DAYS = 14

def follow_up_score(priority, days):
    return 70

def scan_workspace(folder):
    assert (Path(folder) / "02_Contexts").is_dir()
    today = date(2026, 10, 16)
    nudge = WaitingItem("Client / Feedback", "Client", "Feedback", date(2026, 10, 1), today)
    later = WaitingItem("Vendor / Quote", "Vendor", "Quote", None, date(2026, 10, 30))
    return ScanResult(
        projects=[
            ProjectScore("Website", "Work/Client", Path(folder), "Work/Client/Website", 80, "P1",
                date(2026, 10, 18), 2, [nudge, later], 0, [nudge], [], [], 0,
                "Ready\\nmore", [MARK + " Ship it #auto", "Write docs"], "active"),
            ProjectScore("Idle", "Work", Path(folder), "Work/Idle", 0, "", None, None, [], 0, [],
                [], [], 0, "", ["Nothing urgent"], "active"),
        ],
        single_actions=[
            SingleAction("Work/Client", "Call back", "P2", date(2026, 10, 17), 40),
            SingleAction("Work/Client", "Ancient", "P3", date(2026, 8, 1), 10, True),
        ],
        scan_date=today,
    )
"""


@pytest.fixture
def plugin(tmp_path: Path) -> Path:
    root = tmp_path / "plugin"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "workspace_scanner.py").write_text(FAKE_SCANNER, encoding="utf-8")
    (scripts / "recurrence.py").write_text('MARK = "🤖"\n', encoding="utf-8")
    return root


def test_plugin_comes_from_the_setting_or_claude_codes_registry(tmp_path, plugin, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv(plan_module.PLUGIN_ENV, raising=False)
    assert discover_plugin() is None
    registry = tmp_path / ".claude" / "plugins" / "installed_plugins.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps(
            {
                "plugins": {
                    "other@x": [{"installPath": "/nope"}],
                    "ebi-workspace@ebi-tools": [{"installPath": str(plugin)}],
                }
            }
        ),
        encoding="utf-8",
    )
    assert discover_plugin() == plugin
    monkeypatch.setenv(plan_module.PLUGIN_ENV, str(tmp_path / "missing"))
    assert discover_plugin() is None
    monkeypatch.setenv(plan_module.PLUGIN_ENV, str(plugin))
    assert discover_plugin() == plugin


def test_a_broken_registry_means_no_plugin(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv(plan_module.PLUGIN_ENV, raising=False)
    registry = tmp_path / ".claude" / "plugins" / "installed_plugins.json"
    registry.parent.mkdir(parents=True)
    registry.write_text("{not json", encoding="utf-8")
    assert discover_plugin() is None


def test_unavailable_without_workspace_or_plugin(notes, plugin):
    assert WorkspacePlan(Workspace(None), plugin).read() == {
        "available": False,
        "reason": "workspace",
    }
    assert WorkspacePlan(Workspace(notes), None).read() == {
        "available": False,
        "reason": "plugin",
    }


def test_plan_ranks_by_the_plugin_and_points_at_markdown_lines(notes, plugin):
    workspace = Workspace(notes)
    plan = WorkspacePlan(workspace, plugin).read()
    assert plan["available"] is True and plan["date"] == "2026-10-16"
    kinds = [(i["kind"], i["text"]) for i in plan["items"]]
    # Scores 80, 70 (due follow-up), 40; zero scores and lapsed deadlines stay out.
    assert kinds == [
        ("project", "🤖 Ship it #auto"),
        ("follow_up", "Client / Feedback"),
        ("single", "Call back"),
    ]
    project, follow_up, single = plan["items"]
    entry = workspace.entry("Work/Client/Website")
    assert project["line"] == entry["tasks"][0]["line"]
    assert project["revision"] == entry["revision"]
    assert project["more"] == 1 and project["status"] == "Ready"
    assert project["priority"] == "P1" and project["milestone_days"] == 2
    # The project's deadline belongs to its milestone; the next action has no date of its own.
    assert project["days"] is None and project["due_source"] == "task"
    assert follow_up["line"] is None and follow_up["revision"] is None
    # A follow-up is re-dated on its 連絡待ち line.
    assert follow_up["due_source"] == "follow_up" and follow_up["due_line"] == 15
    assert follow_up["follow_up_revision"] == entry["revision"]
    assert single["path"] == "Work/Client" and single["line"] == 2 and single["days"] == 1
    assert [w["what"] for w in plan["waiting"]] == ["Feedback", "Quote"]
    assert plan["lapsed"] == 1 and plan["total"] == 3


def test_priority_and_due_markers_still_find_the_line(notes, plugin):
    actions = notes / "02_Contexts" / "Work" / "Client" / "single-actions.md"
    actions.write_text("# Tasks\n- [ ] [P2] Call back 📅2026-10-17 🔁毎週\n", encoding="utf-8")
    item = WorkspacePlan(Workspace(notes), plugin).read()["items"][2]
    assert item["line"] == 2 and item["raw"].startswith("[P2] Call back")


def test_ambiguous_tasks_are_listed_without_a_line(notes, plugin):
    actions = notes / "02_Contexts" / "Work" / "Client" / "single-actions.md"
    actions.write_text("- [ ] Call back\n- [ ] Call back\n", encoding="utf-8")
    item = WorkspacePlan(Workspace(notes), plugin).read()["items"][2]
    assert item["text"] == "Call back" and item["line"] is None


def test_plan_is_cached_until_invalidated(notes, plugin, monkeypatch):
    plan = WorkspacePlan(Workspace(notes), plugin)
    calls = []
    real = plan._scan
    monkeypatch.setattr(plan, "_scan", lambda: calls.append(1) or real())
    plan.read()
    plan.read()
    assert len(calls) == 1
    plan.invalidate()
    plan.read()
    assert len(calls) == 2


def test_a_failing_scanner_makes_the_plan_unavailable(notes, plugin):
    (plugin / "scripts" / "workspace_scanner.py").write_text("raise SystemExit(3)\n")
    assert WorkspacePlan(Workspace(notes), plugin).read() == {
        "available": False,
        "reason": "scanner",
    }


def test_today_plan_is_the_goodmorning_section_only(tmp_path):
    daily = tmp_path / "01_Daily"
    daily.mkdir()
    day = date(2026, 10, 16)
    assert today_plan(tmp_path, day) is None
    (daily / "2026-10-16.md").write_text(
        "# 2026-10-16\n## 今日のプラン\n\n### オススメ着手順\n1. Ship\n\n## 10:00 — Work\nlog\n",
        encoding="utf-8",
    )
    assert today_plan(tmp_path, day) == "### オススメ着手順\n1. Ship"


def test_today_plan_ignores_a_symlinked_daily_note(tmp_path):
    daily = tmp_path / "01_Daily"
    daily.mkdir()
    outside = tmp_path / "outside.md"
    outside.write_text("## 今日のプラン\nsecret\n", encoding="utf-8")
    (daily / "2026-10-16.md").symlink_to(outside)
    assert today_plan(tmp_path, date(2026, 10, 16)) is None


async def test_plan_route_is_authenticated(env, notes, plugin):
    env.console.workspace = Workspace(notes)
    env.console.workspace_plan = WorkspacePlan(env.console.workspace, plugin)
    assert (await env.client.get("/console/api/workspace/plan")).status == 401
    response = await env.client.get("/console/api/workspace/plan", headers=AUTH)
    body = await response.json()
    assert body["available"] is True and body["items"][0]["kind"] == "project"


def test_equal_scores_keep_goodmornings_order(notes, plugin):
    plan = WorkspacePlan(Workspace(notes), plugin)
    scan = plan._scan()
    scan["single_actions"][0]["score"] = 80
    shaped = plan.shape(scan)
    assert [i["kind"] for i in shaped["items"]] == ["project", "single", "follow_up"]


def _project(rel_path: str, score: int, category: str, *, days: int = 0, actions=("Do it",)):
    return {
        "rel_path": rel_path,
        "score": score,
        "category": category,
        "priority": "P2",
        "next_actions": list(actions),
        "current_status": "",
        "staleness_days": days,
        "nearest_deadline": None,
        "due_follow_ups": [],
        "follow_up_scores": [],
        "waiting": [],
        "lapsed_milestones": [],
    }


def test_stale_projects_stay_ranked_and_one_always_gets_a_slot(notes, plugin):
    plan = WorkspacePlan(Workspace(notes), plugin)
    scan = plan._scan()
    busy = [_project(f"Busy/{n}", 20, "active") for n in range(plan_module.MAX_ITEMS + 2)]
    scan["projects"] = [
        *busy,
        _project("Old/Mild", 10, "stale", days=9),
        _project("Old/Long", 10, "stale", days=30),
        _project("Old/Plain", 20, "no_next_action", actions=()),
        _project("Old/Empty", 10, "stale", days=12, actions=()),
    ]
    scan["single_actions"] = []
    shaped = plan.shape(scan)
    assert len(shaped["items"]) == plan_module.MAX_ITEMS
    last = shaped["items"][-1]
    # Equal scores: the longest-stalled one takes the reserved slot.
    assert last["path"] == "Old/Long" and last["stale_days"] == 30
    assert all(i["stale_days"] is None for i in shaped["items"][:-1])
    assert shaped["total"] == len(busy) + 2
    stalled = [(s["path"], s["kind"]) for s in shaped["stalled"]]
    assert stalled == [
        ("Old/Long", "project"),
        ("Old/Mild", "project"),
        ("Old/Empty", "no_next"),
        ("Old/Plain", "no_next"),
    ]
    assert shaped["stalled_total"] == 4
    assert shaped["stalled"][3]["stale_days"] is None


def test_a_stale_project_already_shown_is_not_duplicated(notes, plugin):
    plan = WorkspacePlan(Workspace(notes), plugin)
    scan = plan._scan()
    scan["projects"] = [_project("Old/Urgent", 50, "stale", days=11)] + [
        _project(f"Busy/{n}", 20, "active") for n in range(plan_module.MAX_ITEMS + 2)
    ]
    scan["single_actions"] = []
    items = plan.shape(scan)["items"]
    assert items[0]["path"] == "Old/Urgent" and items[0]["stale_days"] == 11
    assert [i["path"] for i in items].count("Old/Urgent") == 1
    assert sum(1 for i in items if i["stale_days"] is not None) == 1


def test_a_project_row_names_the_milestone_that_set_its_deadline(notes, plugin):
    plan = WorkspacePlan(Workspace(notes), plugin)
    scan = plan._scan()
    dated = {**_project("Work/Dated", 90, "active"), "nearest_deadline": "2026-10-17"}
    dated["nearest_milestone"] = "Launch"
    older = _project("Work/Older", 20, "active")  # a plugin without nearest_milestone
    scan["projects"] = [dated, older]
    scan["single_actions"] = []
    rows = {r["path"]: r for r in plan.shape(scan)["items"]}
    assert rows["Work/Dated"]["milestone"] == "Launch"
    assert rows["Work/Older"]["milestone"] is None


def _dated(plan: WorkspacePlan, notes: Path, status: str, **project) -> dict:
    (notes / "02_Contexts" / "Work" / "Client" / "Website" / "status.md").write_text(
        status, encoding="utf-8"
    )
    scan = plan._scan()
    scan["projects"] = [
        {
            **_project("Work/Client/Website", 90, "active", actions=("🧑 Review the video",)),
            **project,
        }
    ]
    scan["single_actions"] = []
    return plan.shape(scan)["items"][0]


_STATUS = (
    "# Website\n## 次のアクション\n- [ ] 🧑 Review the video 📅2026-11-30\n"
    "## マイルストーン\n| マイルストーン | 目標日 | 状態 |\n|---|---|---|\n"
    "| Receive the check | 2026-10-09 | 🟡 |\n| Launch | 2026-10-20 | ⬜ |\n"
)


def test_a_row_shows_its_own_date_and_the_milestone_shows_its_date(notes, plugin):
    # The reported bug: the row's tag showed the milestone's date, so moving the date the
    # button wrote never changed it, and moving the milestone only surfaced the next one.
    plan = WorkspacePlan(Workspace(notes), plugin)
    row = _dated(
        plan, notes, _STATUS, nearest_deadline="2026-10-09", nearest_milestone="Receive the check"
    )
    assert row["due"] == "2026-11-30" and row["days"] == 45 and row["due_source"] == "task"
    assert row["line"] == 3
    assert row["milestone"] == "Receive the check"
    assert row["milestone_due"] == "2026-10-09" and row["milestone_days"] == -7
    assert row["milestone_line"] == 7 and row["milestone_revision"] == row["revision"]


def test_a_next_action_without_a_date_has_none(notes, plugin):
    plan = WorkspacePlan(Workspace(notes), plugin)
    row = _dated(plan, notes, _STATUS.replace(" 📅2026-11-30", ""))
    assert row["due"] is None and row["due_source"] == "task" and row["milestone"] is None


def test_an_unlocatable_milestone_has_no_line_to_edit(notes, plugin):
    plan = WorkspacePlan(Workspace(notes), plugin)
    row = _dated(plan, notes, _STATUS, nearest_deadline="2026-10-12", nearest_milestone="Gone")
    assert row["milestone_due"] == "2026-10-12" and row["milestone_line"] is None
    assert row["milestone_revision"] is None


def test_a_project_without_next_action_still_offers_its_milestone(notes, plugin):
    plan = WorkspacePlan(Workspace(notes), plugin)
    (notes / "02_Contexts" / "Work" / "Client" / "Website" / "status.md").write_text(
        _STATUS, encoding="utf-8"
    )
    scan = plan._scan()
    scan["projects"] = [
        {
            **_project("Work/Client/Website", 10, "no_next_action", actions=()),
            "nearest_deadline": "2026-10-09",
            "nearest_milestone": "Receive the check",
        }
    ]
    scan["single_actions"] = []
    row = plan.shape(scan)["stalled"][0]
    assert row["kind"] == "no_next" and row["due"] is None
    assert row["milestone_line"] == 7 and row["milestone_revision"]


def test_an_ambiguous_waiting_line_cannot_be_redated(notes, plugin):
    status = notes / "02_Contexts" / "Work" / "Client" / "Website" / "status.md"
    status.write_text(
        status.read_text(encoding="utf-8") + "- Client / Feedback 📅2026-10-01\n", encoding="utf-8"
    )
    follow_up = WorkspacePlan(Workspace(notes), plugin).read()["items"][1]
    assert follow_up["kind"] == "follow_up" and follow_up["due_line"] is None


def _real_plan(notes: Path, status: str, actions: str | None = None):
    real = discover_plugin()
    if real is None:
        pytest.skip("the Ebi Workspace plugin is not installed here")
    project = notes / "02_Contexts" / "Work" / "Client" / "Website"
    (project / "status.md").write_text(status, encoding="utf-8")
    if actions is not None:
        (project.parent / "single-actions.md").write_text(actions, encoding="utf-8")
    workspace = Workspace(notes)
    return workspace, WorkspacePlan(workspace, real)


def _iso(days: int) -> str:
    return date.fromordinal(date.today().toordinal() + days).isoformat()


def _real_status(milestones: str, next_action: str = "🧑 Review the video", waiting: str = "なし"):
    return (
        "# Website\n優先度: P1\n最終更新: " + date.today().isoformat() + "\n## 現在地\nok\n"
        f"## 次のアクション\n- [ ] {next_action}\n## マイルストーン\n"
        "| マイルストーン | 目標日 | 状態 |\n|---|---|---|\n"
        f"{milestones}## 連絡待ち\n{waiting}\n"
    )


def _find(plan: WorkspacePlan, kind: str, path: str = "Work/Client/Website") -> dict:
    plan.invalidate()
    data = plan.read()
    return next(
        i for i in [*data["items"], *data["stalled"]] if i["kind"] == kind and i["path"] == path
    )


# Every date a Focus row shows, re-dated where it is written, read back by the plugin's real
# scanner: what the browser shows after the save is the date that was chosen.


def test_real_scanner_task_date_on_a_project_next_action(notes, plugin):
    status = _real_status(f"| Check | {_iso(-3)} | 🟡 |\n")
    workspace, plan = _real_plan(notes, status)
    row = _find(plan, "project")
    assert row["due"] is None and row["milestone_due"] == _iso(-3)
    workspace.set_due(row["path"], row["revision"], _iso(9), line=row["line"])
    row = _find(plan, "project")
    assert row["due"] == _iso(9) and row["milestone_due"] == _iso(-3)


def test_real_scanner_milestone_date_and_the_next_one_surfacing(notes, plugin):
    status = _real_status(f"| Check | {_iso(-7)} | 🟡 |\n| 1on1 | {_iso(-6)} | ⬜ |\n")
    workspace, plan = _real_plan(notes, status)
    row = _find(plan, "project")
    assert row["milestone"] == "Check" and row["milestone_due"] == _iso(-7)
    workspace.set_due(
        row["path"], row["milestone_revision"], _iso(7), milestone_line=row["milestone_line"]
    )
    row = _find(plan, "project")
    # The moved milestone is no longer the nearest; the row says which one now is.
    assert row["milestone"] == "1on1" and row["milestone_due"] == _iso(-6)
    workspace.set_due(
        row["path"], row["milestone_revision"], _iso(8), milestone_line=row["milestone_line"]
    )
    row = _find(plan, "project")
    assert row["milestone"] == "Check" and row["milestone_due"] == _iso(7)


def test_real_scanner_follow_up_date(notes, plugin):
    status = _real_status(
        f"| Check | {_iso(30)} | ⬜ |\n",
        waiting=f"- Client / Feedback / {_iso(-10)}〜 📅{_iso(-1)}",
    )
    workspace, plan = _real_plan(notes, status)
    row = _find(plan, "follow_up")
    assert row["due"] == _iso(-1) and row["due_line"] is not None
    workspace.set_due(
        row["path"], row["follow_up_revision"], _iso(0), follow_up_line=row["due_line"]
    )
    row = _find(plan, "follow_up")
    assert row["due"] == _iso(0)
    text = workspace.document("Work/Client/Website", "status.md")
    assert text.count("📅") == 1


@pytest.mark.parametrize(
    "task",
    [
        "[P1] Call back 📅{d}",
        "[P1] Call back 期限: {d}",
        "[P1] Call back（期限: **{md}**）",
        "[P1] Call back 📅{d} 🔁毎週月曜",
    ],
)
def test_real_scanner_single_action_in_every_notation(notes, plugin, task):
    past = date.fromordinal(date.today().toordinal() - 2)
    line = task.format(d=past.isoformat(), md=f"{past.month}/{past.day}")
    workspace, plan = _real_plan(
        notes, _real_status(f"| Check | {_iso(30)} | ⬜ |\n"), f"# Tasks\n- [ ] {line}\n"
    )
    row = _find(plan, "single", "Work/Client")
    assert row["due"] == past.isoformat()
    workspace.set_due(row["path"], row["revision"], _iso(3), line=row["line"])
    row = _find(plan, "single", "Work/Client")
    assert row["due"] == _iso(3)
    text = workspace.document("Work/Client", "single-actions.md")
    assert text.count("📅") == 1 and "期限" not in text
