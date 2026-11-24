"""The goals routes: read the note with the board's threads, write edits back safely."""

from __future__ import annotations

from claude_discord.console import goals as gl
from claude_discord.console.workspace import Workspace
from tests.test_console_conversations import AUTH, WRITE
from tests.test_console_conversations import env as conversation_env
from tests.test_console_workspace import notes as workspace_notes

env = conversation_env
notes = workspace_notes

FOCUS = """# Focus

## Goals

### 1️⃣ Launch the website
- **Done when**: the client signs off
- **Due**: 2026-12-20
- see [[02_Contexts/Work/Client/Website/status|website]]

### 2️⃣ Hire someone
"""


def setup(env, notes, tmp_path, monkeypatch, *, configured: bool = True) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.delenv(gl.CONFIG_ENV, raising=False)
    monkeypatch.delenv(gl.LIMIT_ENV, raising=False)
    (notes / "02_Contexts" / "Work" / "focus.md").write_text(FOCUS, encoding="utf-8")
    status = notes / "02_Contexts/Work/Client/Website/status.md"
    status.write_text(
        status.read_text(encoding="utf-8")
        + "## マイルストーン\n| マイルストーン | 目標日 | 状態 |\n|---|---|---|\n"
        "| Design | 2026-11-01 | ✅ |\n| Launch | 2026-12-20 | ⬜ |\n",
        encoding="utf-8",
    )
    env.console.workspace = Workspace(notes)
    if configured:
        gl.save_config("02_Contexts/Work/focus.md", 5)


async def test_goals_need_a_workspace_and_a_configured_note(env, notes, tmp_path, monkeypatch):
    env.console.workspace = Workspace(None)
    assert (await env.client.get("/console/api/goals")).status == 401
    data = await (await env.client.get("/console/api/goals", headers=AUTH)).json()
    assert data == {"available": False, "reason": "workspace"}
    setup(env, notes, tmp_path, monkeypatch, configured=False)
    data = await (await env.client.get("/console/api/goals", headers=AUTH)).json()
    assert data == {"available": False, "reason": "config"}
    bad = await env.client.put(
        "/console/api/goals/config", headers=WRITE, json={"note": "02_Contexts/none.md"}
    )
    assert bad.status == 400
    ok = await env.client.put(
        "/console/api/goals/config",
        headers=WRITE,
        json={"note": "02_Contexts/Work/focus.md", "limit": 5},
    )
    assert ok.status == 200
    data = await (await env.client.get("/console/api/goals", headers=AUTH)).json()
    assert data["available"] is True and data["limit"] == 5


async def test_goal_shows_projects_milestones_and_threads(env, notes, tmp_path, monkeypatch):
    setup(env, notes, tmp_path, monkeypatch)
    created = await env.client.post(
        "/console/api/items",
        headers=WRITE,
        json={"title": "Build the page", "workspace_path": "Work/Client/Website"},
    )
    item = (await created.json())["item"]
    data = await (await env.client.get("/console/api/goals", headers=AUTH)).json()
    launch, hire = data["goals"]
    assert launch["title"] == "Launch the website" and launch["due"] == "2026-12-20"
    assert launch["done_when"] == "the client signs off"
    assert launch["projects"] == [
        {
            "path": "Work/Client/Website",
            "name": "Website",
            "milestones": {"done": 1, "total": 2, "next": {"name": "Launch", "date": "2026-12-20"}},
        }
    ]
    assert launch["items"] == [item["id"]]
    assert hire["items"] == [] and data["active"] == 2

    # A person moves the thread to another goal with a tag.
    patched = await env.client.patch(
        f"/console/api/items/{item['id']}",
        headers=WRITE,
        json={"tags": [f"goal:{hire['id']}"]},
    )
    assert patched.status == 200
    data = await (await env.client.get("/console/api/goals", headers=AUTH)).json()
    assert data["goals"][1]["items"] == [item["id"]]
    assert data["goals"][1]["why_placed"] == {item["id"]: "chosen"}


async def test_edits_write_back_with_revision_checks(env, notes, tmp_path, monkeypatch):
    setup(env, notes, tmp_path, monkeypatch)
    data = await (await env.client.get("/console/api/goals", headers=AUTH)).json()
    revision = data["note"]["revision"]
    hire = data["goals"][1]
    body = {"op": "add", "title": "Write a book", "revision": revision}
    assert (await env.client.post("/console/api/goals", headers=AUTH, json=body)).status == 403
    saved = await env.client.post("/console/api/goals", headers=WRITE, json=body)
    assert saved.status == 200
    new_revision = (await saved.json())["note"]["revision"]
    text = (notes / "02_Contexts/Work/focus.md").read_text(encoding="utf-8")
    assert "### 3️⃣ Write a book" in text
    # The stale revision is refused with the note as it is now.
    stale = await env.client.post("/console/api/goals", headers=WRITE, json=body)
    assert stale.status == 409
    assert "Write a book" in (await stale.json())["current"]["text"]
    bad = await env.client.post(
        "/console/api/goals", headers=WRITE, json={"op": "zap", "revision": new_revision}
    )
    assert bad.status == 400

    # A thread put under a goal follows the goal when it is renamed.
    created = await env.client.post("/console/api/items", headers=WRITE, json={"title": "Draft"})
    item = (await created.json())["item"]
    await env.client.patch(
        f"/console/api/items/{item['id']}", headers=WRITE, json={"tags": [f"goal:{hire['id']}"]}
    )
    renamed = await env.client.post(
        "/console/api/goals",
        headers=WRITE,
        json={"op": "update", "id": hire["id"], "title": "Hire two", "revision": new_revision},
    )
    assert renamed.status == 200
    data = await (await env.client.get("/console/api/goals", headers=AUTH)).json()
    hire2 = next(g for g in data["goals"] if g["title"] == "Hire two")
    assert hire2["items"] == [item["id"]]

    linked = await env.client.post(
        "/console/api/goals",
        headers=WRITE,
        json={
            "op": "link",
            "id": hire2["id"],
            "project": "Work/Client/Website",
            "revision": data["note"]["revision"],
        },
    )
    assert linked.status == 200
    data = await (await env.client.get("/console/api/goals", headers=AUTH)).json()
    hire3 = next(g for g in data["goals"] if g["title"] == "Hire two")
    assert [p["path"] for p in hire3["projects"]] == ["Work/Client/Website"]
