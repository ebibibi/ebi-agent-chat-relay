"""Tests for the external (non-localhost) ingest-only listener.

The control plane (``/api/spawn`` etc.) must stay bound to localhost for the
trusted local Claude subprocess. Only the token-gated ingest surface may be
reachable from other LAN hosts. These tests pin that boundary: the external
``web.Application`` exposes exactly ``/api/health``, ``POST /api/ingest`` and
``GET /api/ingest/{id}`` — nothing else — and the ingest token is still
enforced on it.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.ext.api_server import ApiServer


@pytest.fixture
async def repo() -> NotificationRepository:
    r = NotificationRepository(":memory:")
    await r.init_db()
    return r


@pytest.fixture
def bot() -> MagicMock:
    return MagicMock()


def _external_paths(api: ApiServer) -> set[str]:
    """Return the set of route paths registered on the external app."""
    paths: set[str] = set()
    for resource in api.external_app.router.resources():
        info = resource.get_info()
        path = info.get("path") or info.get("formatter")
        if path:
            paths.add(path)
    return paths


class TestExternalAppSurface:
    """The external listener must expose ONLY the safe ingest surface."""

    def test_exposes_only_ingest_surface(
        self, repo: NotificationRepository, bot: MagicMock
    ) -> None:
        api = ApiServer(repo=repo, bot=bot, ingest_token="secret")
        paths = _external_paths(api)
        assert paths == {
            "/api/health",
            "/api/ingest",
            "/api/ingest/summary",
            "/api/ingest/{result_id}",
            # Teams thread sync: token-gated like /api/ingest, writes only under
            # the vault root, spawns nothing.
            "/api/teams/sync/plan",
            "/api/teams/sync/push",
            "/api/teams/sync/ignore",
            # Token-gated and read-only: the reply this extension asked for.
            "/api/teams/reply/status",
        }

    def test_does_not_expose_control_plane(
        self, repo: NotificationRepository, bot: MagicMock
    ) -> None:
        api = ApiServer(repo=repo, bot=bot, ingest_token="secret")
        paths = _external_paths(api)
        # RCE-capable / trusted-only routes must never appear externally.
        assert "/api/spawn" not in paths
        assert "/api/tasks" not in paths
        assert "/api/lounge" not in paths
        assert "/api/notify" not in paths
        assert "/api/mark-resume" not in paths


class TestExternalAppAuth:
    """Ingest token is enforced on the external listener (no global middleware)."""

    @pytest.fixture
    async def ext_client(self, repo: NotificationRepository, bot: MagicMock) -> TestClient:
        api = ApiServer(repo=repo, bot=bot, ingest_token="secret-token")
        server = TestServer(api.external_app)
        client = TestClient(server)
        await client.start_server()
        yield client
        await client.close()

    async def test_health_is_open(self, ext_client: TestClient) -> None:
        resp = await ext_client.get("/api/health")
        assert resp.status == 200

    async def test_ingest_without_token_is_401(self, ext_client: TestClient) -> None:
        resp = await ext_client.post("/api/ingest", json={"content": "hi"})
        assert resp.status == 401

    async def test_ingest_wrong_token_is_401(self, ext_client: TestClient) -> None:
        resp = await ext_client.post(
            "/api/ingest",
            json={"content": "hi"},
            headers={"Authorization": "Bearer wrong"},
        )
        assert resp.status == 401

    async def test_ingest_result_without_token_is_401(self, ext_client: TestClient) -> None:
        resp = await ext_client.get("/api/ingest/abc")
        assert resp.status == 401
