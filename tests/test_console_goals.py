"""Goals read from and written back to one Markdown note (console/goals.py)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from claude_discord.console import goals as gl

NOTE = """---
title: focus
---

# What I work on

## いま集中するもの

### 1️⃣ Ship the website（🟢 2026-09-18 着手）

- **なぜ**: nobody can find us
- **判定**: search impressions reach double digits

### ⏸️ Ask for referrals

- **判定**: a reply within 4 weeks

### 2️⃣ Make the relay a product

- **判定（2026-12末）**: two customer instances from config alone
- **プロジェクト**: [[02_Contexts/dev/relay/status|relay]]
- see [[02_Contexts/biz/garage/status|garage]] and [[02_Contexts/biz/notes/idea]]

---

## 待機列

| a | b |
"""


def ids(text: str) -> dict[str, dict]:
    return {g["title"]: g for g in gl.parse_goals(text)}


def test_parse_reads_headings_fields_and_state() -> None:
    goals = gl.parse_goals(NOTE)
    assert [g["title"] for g in goals] == [
        "Ship the website",
        "Ask for referrals",
        "Make the relay a product",
    ]
    first, paused, relay = goals
    assert first["number"] == "1️⃣" and first["state"] == "active"
    assert first["done_when"] == "search impressions reach double digits"
    assert first["why"] == "nobody can find us"
    assert paused["state"] == "paused"
    # A month in the label means its last day.
    assert relay["due"] == "2026-12-31"
    assert relay["project_links"] == ["02_Contexts/dev/relay/status"]
    assert relay["id"] == gl.goal_id("Make the relay a product")


def test_without_a_known_section_every_level_three_heading_counts() -> None:
    goals = gl.parse_goals("# Plan\n### A\n- Due: 2027-01\n### B\n")
    assert [(g["title"], g["due"]) for g in goals] == [("A", "2027-01-31"), ("B", None)]


def test_resolve_projects_counts_status_links_and_the_projects_line() -> None:
    relay = ids(NOTE)["Make the relay a product"]

    def resolve(target: str, source: str) -> str | None:
        return target + ".md"

    found = gl.resolve_projects(
        relay, resolve, "x.md", {"dev/relay", "biz/garage", "biz/notes", "biz"}
    )
    # The prose link to a plain note is not a project link.
    assert found == ["dev/relay", "biz/garage"]


def test_milestones_count_done_rows_and_find_the_next_dated_one() -> None:
    status = """## マイルストーン

| マイルストーン | 目標日 | 状態 |
|---|---|---|
| Design | 2026-11-01 | ✅ |
| Build | 2026-12-20 | 🟡 |
| Launch | 2026-12-06 | ⬜ |

## 次のアクション
| not | a milestone |
"""
    assert gl.milestones(status) == {
        "done": 1,
        "total": 3,
        "next": {"name": "Launch", "date": "2026-12-06"},
    }
    assert gl.milestones("## マイルストーン\n| （未設定） | — | ⬜ |\n")["total"] == 0


NOW = datetime(2026, 11, 24, tzinfo=UTC)


def item(item_id: str, **kw) -> dict:
    base = {
        "id": item_id,
        "thread_id": item_id[1:],
        "bucket": "ai",
        "status": "running",
        "running": True,
        "last_activity_at": "2026-11-24T00:00:00Z",
        "tags": [],
    }
    return {**base, **kw}


def screen(items: list[dict]) -> dict:
    goals = gl.parse_goals(NOTE)
    for g in goals:
        g["projects"] = ["dev/relay"] if g["title"].startswith("Make") else []
    projects = {
        "dev/relay": {"kind": "project", "name": "relay"},
        "biz/sns": {"kind": "project", "name": "sns", "milestones": {"done": 0}},
        "biz": {"kind": "context", "name": "biz"},
    }
    return gl.build(goals, items, projects, now=NOW)


def test_threads_land_under_goal_project_parent_or_nowhere() -> None:
    relay_id = gl.goal_id("Make the relay a product")
    site_id = gl.goal_id("Ship the website")
    items = [
        item("t1", workspace_path="dev/relay/sub"),
        item("t2", parent_id="t1"),
        item("t3", workspace_path="biz/sns", status="waiting", bucket="me", running=False),
        item("t4", workspace_path="biz"),
        item("t5", tags=[f"goal:{site_id}"], workspace_path="biz/sns"),
        item("t6", tags=["goal:none"], workspace_path="dev/relay"),
        item(
            "t7", workspace_path="dev/relay", bucket="done", last_activity_at="2026-10-01T00:00:00Z"
        ),
        item(
            "t8", workspace_path="dev/relay", bucket="done", last_activity_at="2026-11-22T00:00:00Z"
        ),
    ]
    out = screen(items)
    by = {g["id"]: g for g in out["goals"]}
    assert by[relay_id]["items"] == ["t1", "t2", "t8"]
    assert by[relay_id]["why_placed"] == {"t1": "project", "t2": "parent", "t8": "project"}
    assert by[relay_id]["counts"] == {"running": 2, "yours": 0, "done": 1, "open": 2}
    assert by[site_id]["items"] == ["t5"]
    assert by[site_id]["why_placed"] == {"t5": "chosen"}
    assert [(p["path"], p["items"]) for p in out["outside"]] == [("biz/sns", ["t3"])]
    assert out["outside"][0]["counts"]["yours"] == 1
    # A context is not a project, and "no goal" is a person's choice too.
    assert out["unlinked"] == ["t4", "t6"]


def test_goal_tag_helpers() -> None:
    assert gl.chosen_goal({"tags": ["x", "goal:g1"]}) == "g1"
    assert gl.with_goal_tag(["x", "goal:g1"], "g2") == ["goal:g2", "x"]
    assert gl.with_goal_tag(["goal:g1"], None) == []


# -- editing ---------------------------------------------------------------------------------
def gid(title: str) -> str:
    return gl.goal_id(title)


def test_add_goes_after_the_last_active_goal_with_the_next_number() -> None:
    text = gl.edit(
        NOTE, {"op": "add", "title": "Hire", "done_when": "one person", "due": "2027-03"}
    )
    goals = gl.parse_goals(text)
    assert [g["title"] for g in goals][-1] == "Hire"
    hire = goals[-1]
    assert hire["number"] == "3️⃣" and hire["done_when"] == "one person"
    assert hire["due"] == "2027-03-31"
    # The rest of the note is untouched.
    assert text.startswith(NOTE.split("### 2️⃣")[0])
    assert "## 待機列" in text and "---\n\n## 待機列" in text
    with pytest.raises(gl.GoalError):
        gl.edit(text, {"op": "add", "title": "Hire"})
    with pytest.raises(gl.GoalError):
        gl.edit(NOTE, {"op": "add", "title": "  "})


def test_update_rewrites_only_the_lines_it_is_about() -> None:
    text = gl.edit(
        NOTE,
        {
            "op": "update",
            "id": gid("Ship the website"),
            "title": "Ship the site",
            "due": "2027-01-15",
        },
    )
    site = ids(text)["Ship the site"]
    assert site["number"] == "1️⃣" and site["due"] == "2027-01-15"
    # The status suffix after the title stays.
    assert "### 1️⃣ Ship the site（🟢 2026-09-18 着手）" in text
    assert "- **なぜ**: nobody can find us" in text
    text = gl.edit(text, {"op": "update", "id": gid("Ship the site"), "done_when": "page one"})
    assert "- **判定**: page one" in text
    text = gl.edit(text, {"op": "update", "id": gid("Ship the site"), "due": ""})
    assert "期限" not in text
    with pytest.raises(gl.GoalError):
        gl.edit(NOTE, {"op": "update", "id": "gnope", "title": "x"})
    with pytest.raises(gl.GoalError):
        gl.edit(NOTE, {"op": "update", "id": gid("Ship the website"), "due": "soon"})


def test_state_marks_the_heading_and_renumbers() -> None:
    text = gl.edit(NOTE, {"op": "state", "id": gid("Ship the website"), "state": "done"})
    goals = ids(text)
    assert goals["Ship the website"]["state"] == "done"
    assert "### ✅ Ship the website" in text
    assert goals["Make the relay a product"]["number"] == "1️⃣"
    text = gl.edit(text, {"op": "state", "id": gid("Ask for referrals"), "state": "active"})
    goals = ids(text)
    assert goals["Ask for referrals"]["number"] == "1️⃣"
    assert goals["Make the relay a product"]["number"] == "2️⃣"


def test_move_relays_whole_blocks_and_keeps_the_tail() -> None:
    text = gl.edit(NOTE, {"op": "move", "id": gid("Make the relay a product"), "index": 0})
    goals = gl.parse_goals(text)
    assert [g["title"] for g in goals] == [
        "Make the relay a product",
        "Ship the website",
        "Ask for referrals",
    ]
    assert [g["number"] for g in goals] == ["1️⃣", "2️⃣", ""]
    assert goals[0]["project_links"] == ["02_Contexts/dev/relay/status"]
    assert text.count("---") == NOTE.count("---")
    assert "\n---\n\n## 待機列" in text
    assert text.startswith(NOTE.split("### 1️⃣")[0])


def test_link_and_unlink_use_the_projects_line() -> None:
    site = gid("Ship the website")
    text = gl.edit(NOTE, {"op": "link", "id": site, "link": "02_Contexts/biz/site/status"})
    assert ids(text)["Ship the website"]["project_links"] == ["02_Contexts/biz/site/status"]
    again = gl.edit(text, {"op": "link", "id": site, "link": "02_Contexts/biz/site/status"})
    assert again == text
    text = gl.edit(text, {"op": "link", "id": site, "link": "02_Contexts/biz/ads/status"})
    assert len(ids(text)["Ship the website"]["project_links"]) == 2
    text = gl.edit(text, {"op": "unlink", "id": site, "link": "02_Contexts/biz/site/status"})
    assert ids(text)["Ship the website"]["project_links"] == ["02_Contexts/biz/ads/status"]
    text = gl.edit(text, {"op": "unlink", "id": site, "link": "02_Contexts/biz/ads/status"})
    assert "プロジェクト" not in text.split("### ⏸️")[0]
    with pytest.raises(gl.GoalError):
        gl.edit(NOTE, {"op": "link", "id": site, "link": "a]]b"})


def test_config_file_and_environment(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(gl.CONFIG_ENV, raising=False)
    monkeypatch.delenv(gl.LIMIT_ENV, raising=False)
    cfg = tmp_path / "goals.txt"
    assert gl.load_config(cfg) == {"note": None, "limit": None}
    gl.save_config("02_Contexts/biz/focus.md", 5, cfg)
    assert gl.load_config(cfg) == {"note": "02_Contexts/biz/focus.md", "limit": 5}
    monkeypatch.setenv(gl.CONFIG_ENV, "other.md")
    assert gl.load_config(cfg)["note"] == "other.md"
    for bad in ("../x.md", "/abs.md", "a.txt", "", "a\\b.md"):
        with pytest.raises(gl.GoalError):
            gl.clean_note_path(bad)


def test_goal_brief_names_the_goal_its_note_and_projects() -> None:
    from claude_discord.console import goal_brief

    relay = ids(NOTE)["Make the relay a product"]
    section = goal_brief.section_text(NOTE, relay)
    assert section.startswith("- **判定（2026-12末）**")
    text = goal_brief.brief(
        relay,
        note_file="/vault/focus.md",
        section=section,
        projects=[
            {
                "path": "dev/relay",
                "directory": "/vault/02_Contexts/dev/relay",
                "milestones": {"done": 1, "total": 2, "next": {"name": "GA", "date": "2026-12-01"}},
            }
        ],
        parent="Stage 2 waiting for review",
    )
    assert text.startswith("[GOAL CONTEXT]\n")
    assert '"Make the relay a product" (active, due 2026-12-31)' in text
    assert "Done when: two customer instances from config alone" in text
    assert "/vault/focus.md" in text and "/vault/02_Contexts/dev/relay/overview.md" in text
    assert "milestones 1/2, next: GA 2026-12-01" in text
    assert "The parent conversation is currently: Stage 2 waiting for review" in text
    long = goal_brief.brief(relay, note_file="x", section="a" * 5000, projects=[])
    assert len(long) < 2000
