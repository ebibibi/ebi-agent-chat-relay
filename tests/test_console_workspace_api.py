"""Authenticated Workspace tasks and context propagation into actual Console turns."""

from __future__ import annotations

from claude_discord.console.workspace import Workspace
from tests.test_console_conversations import AUTH, WRITE, _settle
from tests.test_console_conversations import env as conversation_env
from tests.test_console_workspace import notes as workspace_notes

env = conversation_env
notes = workspace_notes


async def test_workspace_is_optional_and_authenticated(env):
    env.console.workspace = Workspace(None)
    assert (await env.client.get("/console/api/workspace")).status == 401
    response = await env.client.get("/console/api/workspace", headers=AUTH)
    assert await response.json() == {"available": False, "entries": []}


async def test_task_edit_conflict_and_csrf(env, notes):
    env.console.workspace = Workspace(notes)
    response = await env.client.get("/console/api/workspace", headers=AUTH)
    entry = next(e for e in (await response.json())["entries"] if e["kind"] == "project")
    payload = {
        "path": entry["path"],
        "revision": entry["revision"],
        "line": entry["tasks"][0]["line"],
        "done": True,
    }
    assert (
        await env.client.patch("/console/api/workspace/tasks", json=payload, headers=AUTH)
    ).status == 403
    assert (
        await env.client.patch("/console/api/workspace/tasks", json=payload, headers=WRITE)
    ).status == 200
    assert (
        await env.client.patch("/console/api/workspace/tasks", json=payload, headers=WRITE)
    ).status == 409


async def test_start_reply_and_child_keep_workspace_association(env, notes):
    env.console.workspace = Workspace(notes)
    created = await env.client.post(
        "/console/api/items",
        headers=WRITE,
        json={"title": "Ship it", "workspace_path": "Work/Client/Website", "start": True},
    )
    assert created.status == 201
    item = (await created.json())["item"]
    await _settle(env.host)
    assert item["workspace_path"] == "Work/Client/Website"
    assert str(notes / "02_Contexts/Work/Client/Website/status.md") in env.runs[-1].prompt
    reply = await env.client.post(
        f"/console/api/items/{item['id']}/reply", headers=WRITE, json={"text": "Continue"}
    )
    assert reply.status == 202
    await _settle(env.host)
    assert env.runs[-1].prompt.startswith("Continue\n\n[EBI WORKSPACE CONTEXT]\n")
    assert "Work/Client/Website" in env.runs[-1].prompt
    response = await env.client.get(f"/console/api/items/{item['id']}/messages", headers=AUTH)
    messages = (await response.json())["messages"]
    human = [m["content"] for m in messages if m["kind"] == "human"]
    assert human == ["Ship it", "Continue"]
    briefs = [m for m in messages if m["author"] == "Ebi Workspace"]
    assert len(briefs) == 2
    assert all(
        m["kind"] == "activity" and "[EBI WORKSPACE CONTEXT]" in m["content"] for m in briefs
    )
    child = await env.client.post(
        "/console/api/items", headers=WRITE, json={"title": "Child task", "parent_id": item["id"]}
    )
    assert (await child.json())["item"]["workspace_path"] == "Work/Client/Website"
    board = await env.client.get("/console/api/board", headers=AUTH)
    assert any(
        i.get("workspace_path") == "Work/Client/Website" for i in (await board.json())["items"]
    )


async def test_invalid_binding_and_document_paths_are_rejected(env, notes):
    env.console.workspace = Workspace(notes)
    response = await env.client.post(
        "/console/api/items", headers=WRITE, json={"title": "Bad", "workspace_path": "../outside"}
    )
    assert response.status == 400
    response = await env.client.get(
        "/console/api/workspace/document",
        headers=AUTH,
        params={"path": "Work/Client", "name": "../../secret"},
    )
    assert response.status == 400


async def test_run_original_task_checks_revision_and_keeps_it_open_until_work_succeeds(env, notes):
    env.console.workspace = Workspace(notes)
    entry = env.console.workspace.entry("Work/Client/Website")
    payload = {
        "path": entry["path"],
        "revision": entry["revision"],
        "line": entry["tasks"][0]["line"],
    }
    response = await env.client.post("/console/api/workspace/run", json=payload, headers=WRITE)
    assert response.status == 201
    await _settle(env.host)
    assert "Ship it" in env.runs[-1].prompt
    assert env.console.workspace.entry(entry["path"])["tasks"][0]["done"] is False
    payload["revision"] = "stale"
    assert (
        await env.client.post("/console/api/workspace/run", json=payload, headers=WRITE)
    ).status == 409


async def test_board_guide_checks_records_only_with_a_workspace(env, notes):
    env.console.workspace = Workspace(None)
    board = await (await env.client.get("/console/api/board", headers=AUTH)).json()
    assert board["guide"]["drift"] is None
    env.console.workspace = Workspace(notes)
    board = await (await env.client.get("/console/api/board", headers=AUTH)).json()
    assert board["guide"]["drift"] == []


async def test_done_checks_the_original_task_and_reopen_unchecks_it(env, notes):
    env.console.workspace = Workspace(notes)
    entry = env.console.workspace.entry("Work/Client/Website")
    payload = {
        "path": entry["path"],
        "revision": entry["revision"],
        "line": entry["tasks"][0]["line"],
    }
    started = await env.client.post("/console/api/workspace/run", json=payload, headers=WRITE)
    item_id = (await started.json())["item"]["id"]
    await _settle(env.host)
    done = await env.client.post(f"/console/api/items/{item_id}/done", json={}, headers=WRITE)
    assert done.status == 200
    assert (await done.json())["workspace"] == {
        "path": "Work/Client/Website",
        "task": "🤖 Ship it #auto",
        "changed": True,
    }
    assert env.console.workspace.entry(entry["path"])["tasks"][0]["done"] is True
    log = (notes / "02_Contexts/Work/Client/Website/log.md").read_text(encoding="utf-8")
    assert "タスク完了: 🤖 Ship it #auto" in log
    reopened = await env.client.post(f"/console/api/items/{item_id}/reopen", json={}, headers=WRITE)
    assert (await reopened.json())["workspace"]["changed"] is True
    assert env.console.workspace.entry(entry["path"])["tasks"][0]["done"] is False


async def test_done_without_a_matching_task_records_the_conversation(env, notes):
    env.console.workspace = Workspace(notes)
    created = await env.client.post(
        "/console/api/items",
        headers=WRITE,
        json={"title": "Fix the footer", "workspace_path": "Work/Client/Website"},
    )
    item_id = (await created.json())["item"]["id"]
    done = await env.client.post(f"/console/api/items/{item_id}/done", json={}, headers=WRITE)
    assert (await done.json())["workspace"] == {
        "path": "Work/Client/Website",
        "task": None,
        "changed": True,
    }
    log = (notes / "02_Contexts/Work/Client/Website/log.md").read_text(encoding="utf-8")
    assert "会話完了: Fix the footer" in log
    assert env.console.workspace.entry("Work/Client/Website")["tasks"][0]["done"] is False
    # Reopening a conversation that never touched a task leaves the notes alone.
    reopened = await env.client.post(f"/console/api/items/{item_id}/reopen", json={}, headers=WRITE)
    assert (await reopened.json())["workspace"]["changed"] is False


async def test_done_leaves_a_task_the_agent_already_checked(env, notes):
    env.console.workspace = Workspace(notes)
    created = await env.client.post(
        "/console/api/items",
        headers=WRITE,
        json={"title": "Old task", "workspace_path": "Work/Client/Website"},
    )
    item_id = (await created.json())["item"]["id"]
    done = await env.client.post(f"/console/api/items/{item_id}/done", json={}, headers=WRITE)
    assert (await done.json())["workspace"]["changed"] is False
    assert not (notes / "02_Contexts/Work/Client/Website/log.md").exists()


async def test_done_without_a_workspace_reports_nothing(env):
    env.console.workspace = Workspace(None)
    created = await env.client.post("/console/api/items", headers=WRITE, json={"title": "Plain"})
    item_id = (await created.json())["item"]["id"]
    done = await env.client.post(f"/console/api/items/{item_id}/done", json={}, headers=WRITE)
    assert done.status == 200
    assert (await done.json())["workspace"] is None


async def test_a_picked_binding_is_pinned_and_none_or_auto_can_be_chosen(env, notes):
    env.console.workspace = Workspace(notes)
    created = await env.client.post("/console/api/items", headers=WRITE, json={"title": "Loose"})
    item = (await created.json())["item"]
    assert item["workspace_source"] is None

    async def patch(body):
        response = await env.client.patch(
            f"/console/api/items/{item['id']}", headers=WRITE, json=body
        )
        assert response.status == 200
        return (await response.json())["item"]

    picked = await patch({"workspace_path": "Work/Client", "workspace_source": "recorded"})
    assert (picked["workspace_path"], picked["workspace_source"]) == ("Work/Client", "pinned")
    none = await patch({"workspace_path": None, "workspace_source": "pinned"})
    assert (none["workspace_path"], none["workspace_source"]) == (None, "pinned")
    auto = await patch({"workspace_path": None})
    assert (auto["workspace_path"], auto["workspace_source"]) == (None, None)


async def test_running_a_task_pins_the_conversation_to_its_entry(env, notes):
    env.console.workspace = Workspace(notes)
    entry = env.console.workspace.entry("Work/Client/Website")
    payload = {
        "path": entry["path"],
        "revision": entry["revision"],
        "line": entry["tasks"][0]["line"],
    }
    response = await env.client.post("/console/api/workspace/run", json=payload, headers=WRITE)
    item = (await response.json())["item"]
    assert (item["workspace_source"], item["workspace_reason"]) == ("pinned", "task")


async def test_the_board_reports_how_each_binding_was_decided(env, notes):
    env.console.workspace = Workspace(notes)
    created = await env.client.post(
        "/console/api/items",
        headers=WRITE,
        json={"title": "Pinned", "workspace_path": "Work/Client"},
    )
    item = (await created.json())["item"]
    board = await (await env.client.get("/console/api/board", headers=AUTH)).json()
    row = next(i for i in board["items"] if i["id"] == item["id"])
    assert (row["workspace_path"], row["workspace_source"]) == ("Work/Client", "pinned")
    assert "workspace_by" not in row


async def test_context_route_is_authenticated_and_checks_the_path(env, notes):
    env.console.workspace = Workspace(notes)
    url = "/console/api/workspace/context"
    assert (await env.client.get(url, params={"path": "Work/Client"})).status == 401
    ok = await env.client.get(url, headers=AUTH, params={"path": "Work/Client/Website"})
    assert (await ok.json())["current"] == "Ready"
    bad = await env.client.get(url, headers=AUTH, params={"path": "../x"})
    assert bad.status == 400
