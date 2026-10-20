"""Tests for the configurable request-body size limit.

aiohttp's ``web.Application`` defaults to a 1 MiB body limit. Ingest payloads
carry a full conversation thread plus base64-encoded attachments (which inflate
~4/3), so 1 MiB is easily exceeded and the request fails with HTTP 413. The
server must raise this limit (configurable) on both the localhost app and the
external ingest app.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.ext.api_server import (
    _DEFAULT_MAX_BODY_BYTES,
    _MAX_INGEST_TOTAL_BYTES,
    ApiServer,
)
from claude_discord.ext.teams_store import MAX_ATTACHMENT_BYTES


@pytest.fixture
async def repo() -> NotificationRepository:
    r = NotificationRepository(":memory:")
    await r.init_db()
    return r


@pytest.fixture
def bot() -> MagicMock:
    return MagicMock()


class TestMaxBodySize:
    def test_default_is_well_above_one_mib(self) -> None:
        # The aiohttp default (1 MiB) is the bug we are fixing.
        assert _DEFAULT_MAX_BODY_BYTES > 1024 * 1024

    def test_default_fits_the_largest_teams_attachment_and_the_ingest_total(self) -> None:
        # A Teams sync push carries one attachment of up to MAX_ATTACHMENT_BYTES
        # as base64 (4/3) plus the message JSON; /api/ingest carries its total.
        assert _DEFAULT_MAX_BODY_BYTES >= MAX_ATTACHMENT_BYTES * 4 // 3 + 4 * 1024 * 1024
        assert _DEFAULT_MAX_BODY_BYTES >= _MAX_INGEST_TOTAL_BYTES * 4 // 3

    def test_both_apps_use_the_raised_default(
        self, repo: NotificationRepository, bot: MagicMock
    ) -> None:
        api = ApiServer(repo=repo, bot=bot, ingest_token="secret")
        assert api.app._client_max_size == _DEFAULT_MAX_BODY_BYTES
        assert api.external_app._client_max_size == _DEFAULT_MAX_BODY_BYTES

    def test_explicit_override_applies_to_both_apps(
        self, repo: NotificationRepository, bot: MagicMock
    ) -> None:
        custom = 7 * 1024 * 1024
        api = ApiServer(repo=repo, bot=bot, ingest_token="secret", max_body_bytes=custom)
        assert api.app._client_max_size == custom
        assert api.external_app._client_max_size == custom
