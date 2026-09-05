"""The loopback control plane must reject accidental proxy exposure."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.ext.api_server import ApiServer


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "relay.example.test"},
        {"Host": "localhost.evil.test"},
        {"Host": "localhost@evil.test"},
        {"X-Forwarded-Host": "relay.example.test"},
        {"Forwarded": "for=192.0.2.1;host=relay.example.test"},
        {"Origin": "https://evil.example.test"},
    ],
)
async def test_control_plane_rejects_nonlocal_requests(headers) -> None:
    bot = MagicMock()
    bot.get_channel.return_value.send = AsyncMock()
    api = ApiServer(repo=MagicMock(), bot=bot, default_channel_id=123)
    async with TestClient(TestServer(api.app)) as client:
        response = await client.post("/api/notify", json={"message": "test"}, headers=headers)
        assert response.status == 403
    bot.get_channel.assert_not_called()


async def test_loopback_callers_keep_working_without_a_secret() -> None:
    bot = MagicMock()
    bot.get_channel.return_value.send = AsyncMock()
    api = ApiServer(repo=MagicMock(), bot=bot, default_channel_id=123)
    async with TestClient(TestServer(api.app)) as client:
        response = await client.post("/api/notify", json={"message": "test"})
        assert response.status == 200


async def test_external_ingest_does_not_inherit_control_plane_guard() -> None:
    api = ApiServer(repo=MagicMock(), bot=MagicMock(), ingest_token="test-token")
    async with TestClient(TestServer(api.external_app)) as client:
        response = await client.post("/api/ingest", json={}, headers={"Host": "relay.example.test"})
        assert response.status == 401


def test_nonlocal_control_plane_requires_an_explicit_secret() -> None:
    with pytest.raises(ValueError, match="secret"):
        ApiServer(repo=MagicMock(), bot=MagicMock(), host="0.0.0.0")


async def test_authenticated_proxy_can_explicitly_disable_host_guard(monkeypatch) -> None:
    monkeypatch.setenv("CCDB_CONTROL_PLANE_HOST_GUARD", "0")
    bot = MagicMock()
    bot.get_channel.return_value.send = AsyncMock()
    api = ApiServer(repo=MagicMock(), bot=bot, default_channel_id=123, api_secret="test-token")
    async with TestClient(TestServer(api.app)) as client:
        response = await client.post(
            "/api/notify",
            json={"message": "test"},
            headers={"Host": "relay.example.test", "Authorization": "Bearer test-token"},
        )
        assert response.status == 200


def test_standalone_loads_optional_api_secret() -> None:
    from claude_discord.main import load_config

    with (
        patch("claude_discord.main.load_dotenv"),
        patch.dict(
            "os.environ",
            {
                "DISCORD_BOT_TOKEN": "fake",
                "DISCORD_CHANNEL_ID": "1",
                "CCDB_API_SECRET": "test-token",
            },
            clear=True,
        ),
    ):
        assert load_config()["api_secret"] == "test-token"
