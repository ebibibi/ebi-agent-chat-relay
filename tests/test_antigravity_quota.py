"""The Antigravity row of the usage strip: quota pools from fetchAvailableModels."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from claude_discord.console import antigravity_quota
from claude_discord.console.antigravity_quota import antigravity_windows
from claude_discord.console.usage import UsageReader

NOW = 1_800_000_000.0
GEMINI_RESET = "2027-01-15T08:00:00Z"
PARTNER_RESET = "2027-01-20T08:00:00Z"


def _model(provider: str, remaining: float | None, reset: str | None, **extra: object) -> dict:
    quota: dict = {}
    if remaining is not None:
        quota["remainingFraction"] = remaining
    if reset is not None:
        quota["resetTime"] = reset
    return {"modelProvider": provider, "quotaInfo": quota, **extra}


def _payload() -> dict:
    return {
        "models": {
            "gemini-flash": _model("MODEL_PROVIDER_GOOGLE", 0.8, GEMINI_RESET),
            "gemini-pro": _model("MODEL_PROVIDER_GOOGLE", 0.6, GEMINI_RESET),
            "claude-sonnet": _model("MODEL_PROVIDER_ANTHROPIC", 1, PARTNER_RESET),
            "gpt-oss": _model("MODEL_PROVIDER_OPENAI", 0.9, PARTNER_RESET),
            "tab_flash": _model("MODEL_PROVIDER_GOOGLE", 1, None),
            "chat_1": _model("MODEL_PROVIDER_GOOGLE", 0.0, GEMINI_RESET, isInternal=True),
        }
    }


def test_models_collapse_into_one_window_per_pool() -> None:
    gemini, partner = antigravity_windows(_payload())
    assert gemini.rate_limit_type == "gemini"
    assert round(gemini.utilization, 4) == 0.4  # the most drained model speaks for the pool
    assert partner.rate_limit_type == "partner"
    assert round(partner.utilization, 4) == 0.1
    assert gemini.resets_at < partner.resets_at
    assert gemini.status == partner.status == "allowed"


def test_an_exhausted_pool_is_rejected() -> None:
    data = {"models": {"g": _model("MODEL_PROVIDER_GOOGLE", 0, GEMINI_RESET)}}
    [window] = antigravity_windows(data)
    assert (window.utilization, window.status) == (1.0, "rejected")


def test_garbage_yields_no_windows() -> None:
    assert antigravity_windows(None) == []
    assert antigravity_windows({"models": []}) == []
    assert antigravity_windows({"models": {"x": {"quotaInfo": {"resetTime": "nope"}}}}) == []
    assert antigravity_windows({"models": {"x": _model("P", True, GEMINI_RESET)}}) == []


async def test_the_reader_shows_an_antigravity_row() -> None:
    fetch = AsyncMock(return_value=_payload())
    reader = UsageReader(agy_command="agy", fetch_antigravity=fetch, clock=lambda: NOW)
    [row] = (await reader.read())["backends"]
    assert row["backend"] == "antigravity"
    assert [w["type"] for w in row["windows"]] == ["gemini", "partner"]
    assert row["available"] is True
    await reader.read()
    fetch.assert_awaited_once_with("agy")  # cached between polls


async def test_a_failed_antigravity_probe_is_shown_not_raised() -> None:
    fetch = AsyncMock(side_effect=OSError("offline"))
    reader = UsageReader(agy_command="agy", fetch_antigravity=fetch, clock=lambda: NOW)
    [row] = (await reader.read())["backends"]
    assert (row["error"], row["windows"]) == ("unavailable", [])


async def test_antigravity_is_skipped_without_a_command() -> None:
    fetch = AsyncMock(return_value=_payload())
    reader = UsageReader(fetch_antigravity=fetch, clock=lambda: NOW)
    assert (await reader.read())["backends"] == []
    fetch.assert_not_awaited()


def _write_token(path: Path, expiry: str) -> None:
    path.write_text(
        json.dumps({"token": {"access_token": "tok", "expiry": expiry}}), encoding="utf-8"
    )


def test_an_expired_token_is_not_used(tmp_path: Path) -> None:
    path = tmp_path / "token"
    _write_token(path, "2020-01-01T00:00:00+09:00")
    assert antigravity_quota._read_token(path, NOW) is None
    _write_token(path, "2099-01-01T00:00:00+09:00")
    assert antigravity_quota._read_token(path, NOW) == "tok"
    assert antigravity_quota._read_token(tmp_path / "missing", NOW) is None


async def test_an_expired_token_is_refreshed_through_the_cli(tmp_path: Path) -> None:
    path = tmp_path / "token"
    _write_token(path, "2020-01-01T00:00:00+09:00")

    async def refresh(command: str) -> None:
        assert command == "agy"
        _write_token(path, "2099-01-01T00:00:00+09:00")

    with (
        patch.object(antigravity_quota, "_refresh_with_cli", side_effect=refresh) as cli,
        patch.object(antigravity_quota.aiohttp, "ClientSession") as session_cls,
    ):
        resp = AsyncMock(status=200)
        resp.json = AsyncMock(return_value=_payload())
        session = MagicMock()
        session_cls.return_value.__aenter__.return_value = session
        session.post.return_value.__aenter__.return_value = resp
        data = await antigravity_quota.fetch_antigravity_quota("agy", path=path)
    cli.assert_awaited_once()
    assert data == _payload()
    headers = session.post.call_args.kwargs["headers"]
    assert headers["Authorization"] == "Bearer tok"


async def test_no_token_even_after_refresh_gives_none(tmp_path: Path) -> None:
    with patch.object(antigravity_quota, "_refresh_with_cli", AsyncMock()):
        assert await antigravity_quota.fetch_antigravity_quota("agy", path=tmp_path / "x") is None
