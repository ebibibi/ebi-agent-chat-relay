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
    assert "Work/Client/Website" in env.runs[-1].prompt
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
