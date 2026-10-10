"""Tests for /api/tasks endpoints in ApiServer."""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.database.task_repo import TaskRepository
from claude_discord.ext.api_server import ApiServer


@pytest.fixture
async def notif_repo() -> NotificationRepository:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    r = NotificationRepository(path)
    await r.init_db()
    yield r
    os.unlink(path)


@pytest.fixture
async def task_repo() -> TaskRepository:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    r = TaskRepository(path)
    await r.init_db()
    yield r
    os.unlink(path)


@pytest.fixture
def bot() -> MagicMock:
    b = MagicMock()
    b.get_channel = MagicMock(return_value=MagicMock())
    return b


@pytest.fixture
async def client(notif_repo, task_repo, bot) -> TestClient:
    api = ApiServer(
        repo=notif_repo,
        bot=bot,
        task_repo=task_repo,
        default_channel_id=12345,
        host="127.0.0.1",
        port=0,
    )
    server = TestServer(api.app)
    c = TestClient(server)
    await c.start_server()
    yield c
    await c.close()


class TestTasksCreate:
    async def test_create_task_returns_201(self, client: TestClient) -> None:
        resp = await client.post(
            "/api/tasks",
            json={
                "name": "github-check",
                "prompt": "Check GitHub Issues",
                "interval_seconds": 3600,
                "channel_id": 12345,
            },
        )
        assert resp.status == 201
        data = await resp.json()
        assert data["status"] == "created"
        assert "id" in data

    async def test_create_task_missing_required_field(self, client: TestClient) -> None:
        resp = await client.post(
            "/api/tasks",
            json={
                "name": "no-prompt",
                "interval_seconds": 3600,
                "channel_id": 12345,
            },
        )
        assert resp.status == 400

    async def test_create_task_with_working_dir(self, client: TestClient) -> None:
        resp = await client.post(
            "/api/tasks",
            json={
                "name": "with-dir",
                "prompt": "p",
                "interval_seconds": 60,
                "channel_id": 1,
                "working_dir": "/home/user/project",
            },
        )
        assert resp.status == 201

    async def test_create_duplicate_name_returns_409(self, client: TestClient) -> None:
        payload = {"name": "dup", "prompt": "p", "interval_seconds": 60, "channel_id": 1}
        await client.post("/api/tasks", json=payload)
        resp = await client.post("/api/tasks", json=payload)
        assert resp.status == 409

    async def test_create_invalid_json_returns_400(self, client: TestClient) -> None:
        resp = await client.post(
            "/api/tasks", data="not-json", headers={"Content-Type": "application/json"}
        )
        assert resp.status == 400


class TestTasksList:
    async def test_list_empty(self, client: TestClient) -> None:
        resp = await client.get("/api/tasks")
        assert resp.status == 200
        data = await resp.json()
        assert data["tasks"] == []

    async def test_list_returns_created_tasks(self, client: TestClient) -> None:
        await client.post(
            "/api/tasks",
            json={
                "name": "task-a",
                "prompt": "p",
                "interval_seconds": 60,
                "channel_id": 1,
            },
        )
        await client.post(
            "/api/tasks",
            json={
                "name": "task-b",
                "prompt": "p",
                "interval_seconds": 120,
                "channel_id": 2,
            },
        )
        resp = await client.get("/api/tasks")
        assert resp.status == 200
        data = await resp.json()
        assert len(data["tasks"]) == 2
        names = {t["name"] for t in data["tasks"]}
        assert names == {"task-a", "task-b"}


class TestTasksDelete:
    async def test_delete_existing_task(self, client: TestClient) -> None:
        resp = await client.post(
            "/api/tasks",
            json={
                "name": "to-delete",
                "prompt": "p",
                "interval_seconds": 60,
                "channel_id": 1,
            },
        )
        task_id = (await resp.json())["id"]
        del_resp = await client.delete(f"/api/tasks/{task_id}")
        assert del_resp.status == 200
        data = await del_resp.json()
        assert data["status"] == "deleted"

    async def test_delete_nonexistent_returns_404(self, client: TestClient) -> None:
        resp = await client.delete("/api/tasks/99999")
        assert resp.status == 404


class TestTasksPatch:
    async def test_disable_task(self, client: TestClient) -> None:
        resp = await client.post(
            "/api/tasks",
            json={
                "name": "to-disable",
                "prompt": "p",
                "interval_seconds": 60,
                "channel_id": 1,
            },
        )
        task_id = (await resp.json())["id"]
        patch_resp = await client.patch(f"/api/tasks/{task_id}", json={"enabled": False})
        assert patch_resp.status == 200
        data = await patch_resp.json()
        assert data["status"] == "updated"

    async def test_update_prompt(self, client: TestClient) -> None:
        resp = await client.post(
            "/api/tasks",
            json={
                "name": "to-update",
                "prompt": "old prompt",
                "interval_seconds": 60,
                "channel_id": 1,
            },
        )
        task_id = (await resp.json())["id"]
        patch_resp = await client.patch(f"/api/tasks/{task_id}", json={"prompt": "new prompt"})
        assert patch_resp.status == 200

    async def test_patch_nonexistent_returns_404(self, client: TestClient) -> None:
        resp = await client.patch("/api/tasks/99999", json={"enabled": False})
        assert resp.status == 404


class TestTasksFollowUp:
    """Tests for thread_id and one_shot parameters in /api/tasks."""

    async def test_create_task_with_thread_id(self, client: TestClient) -> None:
        resp = await client.post(
            "/api/tasks",
            json={
                "name": "followup-task",
                "prompt": "Check pipeline results",
                "interval_seconds": 86400,
                "channel_id": 12345,
                "thread_id": 1234567890,
            },
        )
        assert resp.status == 201

    async def test_create_task_with_one_shot(self, client: TestClient) -> None:
        resp = await client.post(
            "/api/tasks",
            json={
                "name": "one-shot-task",
                "prompt": "Check once",
                "interval_seconds": 86400,
                "channel_id": 12345,
                "one_shot": True,
            },
        )
        assert resp.status == 201

    async def test_created_task_has_thread_id(
        self, client: TestClient, task_repo: TaskRepository
    ) -> None:
        resp = await client.post(
            "/api/tasks",
            json={
                "name": "with-thread",
                "prompt": "p",
                "interval_seconds": 86400,
                "channel_id": 1,
                "thread_id": 9999,
            },
        )
        task_id = (await resp.json())["id"]
        task = await task_repo.get(task_id)
        assert task is not None
        assert task["thread_id"] == 9999

    async def test_created_task_has_one_shot(
        self, client: TestClient, task_repo: TaskRepository
    ) -> None:
        resp = await client.post(
            "/api/tasks",
            json={
                "name": "with-oneshot",
                "prompt": "p",
                "interval_seconds": 86400,
                "channel_id": 1,
                "one_shot": True,
            },
        )
        task_id = (await resp.json())["id"]
        task = await task_repo.get(task_id)
        assert task is not None
        assert task["one_shot"] is True

    async def test_list_shows_thread_id_and_one_shot(self, client: TestClient) -> None:
        await client.post(
            "/api/tasks",
            json={
                "name": "full-followup",
                "prompt": "p",
                "interval_seconds": 86400,
                "channel_id": 1,
                "thread_id": 7777,
                "one_shot": True,
            },
        )
        resp = await client.get("/api/tasks")
        data = await resp.json()
        task = data["tasks"][0]
        assert task["thread_id"] == 7777
        assert task["one_shot"] is True


class TestTasksBackendAndModel:
    """A scheduled task may pin the backend and model it runs on."""

    async def test_create_with_backend_and_model(
        self, client: TestClient, task_repo: TaskRepository
    ) -> None:
        resp = await client.post(
            "/api/tasks",
            json={
                "name": "pinned-task",
                "prompt": "p",
                "interval_seconds": 60,
                "channel_id": 1,
                "backend": "codex",
                "model": "gpt-6-astra",
            },
        )
        assert resp.status == 201
        task = await task_repo.get((await resp.json())["id"])
        assert task is not None
        assert task["backend"] == "codex"
        assert task["model"] == "gpt-6-astra"

    async def test_create_without_pin_stores_nulls(
        self, client: TestClient, task_repo: TaskRepository
    ) -> None:
        resp = await client.post(
            "/api/tasks",
            json={"name": "no-pin", "prompt": "p", "interval_seconds": 60, "channel_id": 1},
        )
        task = await task_repo.get((await resp.json())["id"])
        assert task is not None
        assert task["backend"] is None
        assert task["model"] is None

    async def test_create_with_unknown_backend_is_rejected(self, client: TestClient) -> None:
        resp = await client.post(
            "/api/tasks",
            json={
                "name": "bad-backend",
                "prompt": "p",
                "interval_seconds": 60,
                "channel_id": 1,
                "backend": "not-a-real-backend",
            },
        )
        assert resp.status == 400
        assert "Unknown backend" in (await resp.json())["error"]

    async def test_create_with_model_but_no_backend_is_rejected(self, client: TestClient) -> None:
        """A model id belongs to one backend; alone it would land on whichever
        backend happened to be active and fail there instead."""
        resp = await client.post(
            "/api/tasks",
            json={
                "name": "model-only",
                "prompt": "p",
                "interval_seconds": 60,
                "channel_id": 1,
                "model": "gpt-6-astra",
            },
        )
        assert resp.status == 400
        assert "model requires backend" in (await resp.json())["error"]

    async def test_patch_sets_the_pin(self, client: TestClient, task_repo: TaskRepository) -> None:
        resp = await client.post(
            "/api/tasks",
            json={"name": "to-pin", "prompt": "p", "interval_seconds": 60, "channel_id": 1},
        )
        task_id = (await resp.json())["id"]
        patch = await client.patch(
            f"/api/tasks/{task_id}", json={"backend": "local", "model": "qwen3.5:35b"}
        )
        assert patch.status == 200
        task = await task_repo.get(task_id)
        assert task is not None
        assert task["backend"] == "local"
        assert task["model"] == "qwen3.5:35b"

    async def test_patch_model_alone_uses_the_stored_backend(
        self, client: TestClient, task_repo: TaskRepository
    ) -> None:
        """Pairing is judged on the task's resulting state, not on this request."""
        resp = await client.post(
            "/api/tasks",
            json={
                "name": "already-pinned",
                "prompt": "p",
                "interval_seconds": 60,
                "channel_id": 1,
                "backend": "codex",
            },
        )
        task_id = (await resp.json())["id"]
        patch = await client.patch(f"/api/tasks/{task_id}", json={"model": "gpt-5.6-sol"})
        assert patch.status == 200
        task = await task_repo.get(task_id)
        assert task is not None
        assert task["backend"] == "codex"
        assert task["model"] == "gpt-5.6-sol"

    async def test_patch_clearing_the_backend_under_a_pinned_model_is_rejected(
        self, client: TestClient, task_repo: TaskRepository
    ) -> None:
        resp = await client.post(
            "/api/tasks",
            json={
                "name": "strand-me",
                "prompt": "p",
                "interval_seconds": 60,
                "channel_id": 1,
                "backend": "codex",
                "model": "gpt-6-astra",
            },
        )
        task_id = (await resp.json())["id"]
        patch = await client.patch(f"/api/tasks/{task_id}", json={"backend": None})
        assert patch.status == 400
        task = await task_repo.get(task_id)
        assert task is not None
        assert task["backend"] == "codex"
        assert task["model"] == "gpt-6-astra"

    async def test_patch_clears_both(self, client: TestClient, task_repo: TaskRepository) -> None:
        resp = await client.post(
            "/api/tasks",
            json={
                "name": "to-unpin",
                "prompt": "p",
                "interval_seconds": 60,
                "channel_id": 1,
                "backend": "codex",
                "model": "gpt-6-astra",
            },
        )
        task_id = (await resp.json())["id"]
        patch = await client.patch(f"/api/tasks/{task_id}", json={"backend": None, "model": None})
        assert patch.status == 200
        task = await task_repo.get(task_id)
        assert task is not None
        assert task["backend"] is None
        assert task["model"] is None

    async def test_patch_with_unknown_backend_is_rejected(self, client: TestClient) -> None:
        resp = await client.post(
            "/api/tasks",
            json={"name": "to-pin-bad", "prompt": "p", "interval_seconds": 60, "channel_id": 1},
        )
        task_id = (await resp.json())["id"]
        patch = await client.patch(f"/api/tasks/{task_id}", json={"backend": "nope"})
        assert patch.status == 400

    async def test_patch_pin_on_a_missing_task_is_404(self, client: TestClient) -> None:
        patch = await client.patch("/api/tasks/987654", json={"backend": "codex"})
        assert patch.status == 404

    async def test_list_shows_the_pin(self, client: TestClient) -> None:
        await client.post(
            "/api/tasks",
            json={
                "name": "listed",
                "prompt": "p",
                "interval_seconds": 60,
                "channel_id": 1,
                "backend": "pi",
                "model": "anthropic/claude-opus-5",
            },
        )
        data = await (await client.get("/api/tasks")).json()
        task = data["tasks"][0]
        assert task["backend"] == "pi"
        assert task["model"] == "anthropic/claude-opus-5"
