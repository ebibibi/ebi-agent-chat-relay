"""The usage strip: per-backend windows, reset times and availability."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from aiohttp.test_utils import TestClient, TestServer

from claude_code_core.types import RateLimitInfo
from claude_discord.console.auth import ConsoleAuthConfig, ConsoleAuthenticator
from claude_discord.console.server import ConsoleServer
from claude_discord.console.usage import UsageReader, backend_entry
from claude_discord.console.work_repo import WorkItemRepository

NOW = 1_000_000.0
TOKEN = "k" * 40


def _info(kind: str, used: float, resets_at: float, status: str = "allowed") -> RateLimitInfo:
    return RateLimitInfo(
        rate_limit_type=kind, status=status, utilization=used, resets_at=int(resets_at)
    )


def _codex_payload(used: int = 1, reached: str | None = None) -> dict:
    return {
        "rateLimits": {
            "primary": {"usedPercent": used, "windowDurationMins": 10080, "resetsAt": NOW + 600},
            "secondary": None,
            "planType": "prolite",
            "rateLimitReachedType": reached,
        },
        "rateLimitResetCredits": {"availableCount": 3},
    }


def test_a_window_past_its_reset_reads_as_zero() -> None:
    entry = backend_entry("claude", [_info("five_hour", 0.9, NOW - 1)], now=NOW)
    [window] = entry["windows"]
    assert (window["utilization"], window["reset"]) == (0.0, True)
    assert entry["available"] is True


def test_a_rejected_window_blocks_until_it_resets() -> None:
    entry = backend_entry(
        "claude",
        [_info("five_hour", 1.0, NOW + 300, "rejected"), _info("seven_day", 0.4, NOW + 9000)],
        now=NOW,
    )
    assert entry["available"] is False
    assert entry["unavailable_until"] == NOW + 300


def test_the_latest_blocking_window_decides_when_it_is_back() -> None:
    entry = backend_entry(
        "claude",
        [_info("five_hour", 1.0, NOW + 300, "rejected"), _info("seven_day", 1.0, NOW + 9000)],
        now=NOW,
    )
    assert entry["unavailable_until"] == NOW + 9000


async def test_claude_and_codex_are_both_reported() -> None:
    repo = MagicMock()
    repo.get_latest = AsyncMock(return_value=[_info("five_hour", 0.16, NOW + 60)])
    fetch = AsyncMock(return_value=_codex_payload())
    reader = UsageReader(
        usage_repo=repo, codex_command="codex", fetch_codex=fetch, clock=lambda: NOW
    )

    data = await reader.read()

    claude, codex = data["backends"]
    assert claude["backend"] == "claude"
    assert claude["windows"][0]["utilization"] == 0.16
    assert codex["backend"] == "codex"
    assert codex["windows"][0]["type"] == "seven_day"
    assert (codex["plan"], codex["reset_credits"]) == ("prolite", 3)


async def test_the_codex_probe_is_cached_between_polls() -> None:
    fetch = AsyncMock(return_value=_codex_payload())
    reader = UsageReader(codex_command="codex", fetch_codex=fetch, clock=lambda: NOW)
    await reader.read()
    await reader.read()
    assert fetch.await_count == 1


async def test_a_failed_codex_probe_is_shown_not_raised() -> None:
    fetch = AsyncMock(side_effect=RuntimeError("boom"))
    reader = UsageReader(codex_command="codex", fetch_codex=fetch, clock=lambda: NOW)
    [codex] = (await reader.read())["backends"]
    assert codex["error"] == "unavailable"
    assert codex["windows"] == []


async def test_codex_is_skipped_without_a_command() -> None:
    fetch = AsyncMock()
    reader = UsageReader(fetch_codex=fetch, clock=lambda: NOW)
    assert (await reader.read())["backends"] == []
    fetch.assert_not_awaited()


async def test_pool_profiles_replace_the_implicit_login() -> None:
    router = MagicMock()
    router.pools = {"claude": object()}
    router.statuses = AsyncMock(
        return_value=[
            SimpleNamespace(
                backend="claude",
                profile="work",
                usage=(_info("five_hour", 1.0, NOW + 120, "rejected"),),
                unavailable_until=int(NOW + 120),
            ),
            SimpleNamespace(backend="claude", profile="home", usage=(), unavailable_until=None),
        ]
    )
    repo = MagicMock()
    repo.get_latest = AsyncMock(return_value=[])
    reader = UsageReader(usage_repo=repo, account_router=router, clock=lambda: NOW)

    backends = (await reader.read())["backends"]

    assert [(b["profile"], b["available"]) for b in backends] == [("work", False), ("home", True)]
    repo.get_latest.assert_not_awaited()


async def test_the_endpoint_requires_auth_and_returns_backends(tmp_path) -> None:
    repo = WorkItemRepository(str(tmp_path / "sessions.db"))
    await repo.init_db()
    usage_repo = MagicMock()
    usage_repo.get_latest = AsyncMock(return_value=[_info("seven_day", 0.61, NOW + 60)])
    console = ConsoleServer(
        MagicMock(),
        repo,
        ConsoleAuthenticator(ConsoleAuthConfig(token=TOKEN)),
        port=0,
        usage=UsageReader(usage_repo=usage_repo, clock=lambda: NOW),
    )
    async with TestClient(TestServer(console.app)) as client:
        assert (await client.get("/console/api/usage")).status == 401
        response = await client.get(
            "/console/api/usage", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        assert response.status == 200
        [claude] = (await response.json())["backends"]
        assert claude["windows"][0]["utilization"] == 0.61


async def test_the_endpoint_answers_without_a_reader(tmp_path) -> None:
    repo = WorkItemRepository(str(tmp_path / "sessions.db"))
    await repo.init_db()
    console = ConsoleServer(
        MagicMock(), repo, ConsoleAuthenticator(ConsoleAuthConfig(token=TOKEN)), port=0
    )
    async with TestClient(TestServer(console.app)) as client:
        response = await client.get(
            "/console/api/usage", headers={"Authorization": f"Bearer {TOKEN}"}
        )
        assert (await response.json())["backends"] == []
