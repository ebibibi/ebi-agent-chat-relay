"""Tests for the Codex engine status footer helpers."""

from __future__ import annotations

import pytest

from claude_discord.discord_ui.engine_status import (
    CodexStatusProvider,
    codex_status_unavailable_line,
    format_codex_status_line,
)

# Representative `account/rateLimits/read` result (trimmed to what we read).
SAMPLE = {
    "rateLimits": {
        "limitId": "codex",
        "primary": {"usedPercent": 1, "windowDurationMins": 300, "resetsAt": 1782198285},
        "secondary": {"usedPercent": 8, "windowDurationMins": 10080, "resetsAt": 1782361421},
        "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
        "planType": "prolite",
        "rateLimitReachedType": None,
    }
}

WEEKLY_PRIMARY_SAMPLE = {
    "rateLimits": {
        "limitId": "codex",
        "primary": {"usedPercent": 15, "windowDurationMins": 10080},
        "secondary": None,
        "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
        "planType": "prolite",
        "rateLimitReachedType": None,
    }
}


class TestFormat:
    def test_basic_line(self) -> None:
        line = format_codex_status_line(SAMPLE)
        assert line is not None
        assert "Codex" in line
        assert "5h 1%" in line
        assert "週次 8%" in line
        assert "クレジット 0" in line
        assert "(prolite)" in line

    def test_weekly_primary_uses_duration_instead_of_position(self) -> None:
        line = format_codex_status_line(WEEKLY_PRIMARY_SAMPLE)
        assert line is not None
        assert "週次 15%" in line
        assert "5h 15%" not in line

    @pytest.mark.parametrize(
        ("duration_mins", "expected_label"),
        [
            (300, "5h"),
            (10080, "週次"),
            (1440, "1d"),
            (2880, "2d"),
            (90, "90m"),
            (30, "30m"),
        ],
    )
    def test_window_label_uses_reported_duration(
        self, duration_mins: int, expected_label: str
    ) -> None:
        data = {
            "rateLimits": {
                "primary": {
                    "usedPercent": 10,
                    "windowDurationMins": duration_mins,
                }
            }
        }
        line = format_codex_status_line(data)
        assert line is not None
        assert f"{expected_label} 10%" in line

    def test_missing_durations_keep_positional_fallbacks(self) -> None:
        data = {
            "rateLimits": {
                "primary": {"usedPercent": 5},
                "secondary": {"usedPercent": 9},
            }
        }
        line = format_codex_status_line(data)
        assert line is not None
        assert "5h 5%" in line
        assert "週次 9%" in line

    def test_unlimited_credits(self) -> None:
        data = {"rateLimits": {"primary": {"usedPercent": 5}, "credits": {"unlimited": True}}}
        line = format_codex_status_line(data)
        assert line is not None
        assert "クレジット 無制限" in line

    def test_rounds_fractional_percent(self) -> None:
        data = {"rateLimits": {"primary": {"usedPercent": 12.6}}}
        line = format_codex_status_line(data)
        assert line is not None
        assert "5h 13%" in line

    def test_rate_limit_reached_warning(self) -> None:
        data = {
            "rateLimits": {
                "primary": {"usedPercent": 100},
                "rateLimitReachedType": "rate_limit_reached",
            }
        }
        line = format_codex_status_line(data)
        assert line is not None
        assert "上限到達" in line

    @pytest.mark.parametrize("bad", [None, {}, {"rateLimits": None}, {"rateLimits": {}}, "x"])
    def test_returns_none_for_unusable(self, bad: object) -> None:
        assert format_codex_status_line(bad) is None  # type: ignore[arg-type]


class TestStatusLanguage:
    """`CCDB_STATUS_LANG` swaps the status-line labels and nothing else."""

    def test_defaults_to_japanese_when_unset(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CCDB_STATUS_LANG", raising=False)
        line = format_codex_status_line(SAMPLE)
        assert line is not None
        assert "週次 8%" in line
        assert "クレジット 0" in line

    def test_english_labels(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CCDB_STATUS_LANG", "en")
        line = format_codex_status_line(SAMPLE)
        assert line is not None
        assert "7d 8%" in line
        assert "credits 0" in line
        assert "週次" not in line
        assert "クレジット" not in line

    def test_english_weekly_matches_other_duration_labels(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`7d` is the same form `_window_label` already emits for 1d/2d."""
        monkeypatch.setenv("CCDB_STATUS_LANG", "en")
        line = format_codex_status_line(WEEKLY_PRIMARY_SAMPLE)
        assert line is not None
        assert "7d 15%" in line

    def test_english_unlimited_credits(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CCDB_STATUS_LANG", "en")
        data = {"rateLimits": {"primary": {"usedPercent": 5}, "credits": {"unlimited": True}}}
        line = format_codex_status_line(data)
        assert line is not None
        assert "credits unlimited" in line

    def test_english_rate_limit_reached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CCDB_STATUS_LANG", "en")
        data = {
            "rateLimits": {
                "primary": {"usedPercent": 99},
                "rateLimitReachedType": "primary",
            }
        }
        line = format_codex_status_line(data)
        assert line is not None
        assert "⚠ limit reached" in line

    def test_japanese_rate_limit_reached_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CCDB_STATUS_LANG", raising=False)
        data = {
            "rateLimits": {
                "primary": {"usedPercent": 99},
                "rateLimitReachedType": "primary",
            }
        }
        line = format_codex_status_line(data)
        assert line is not None
        assert "⚠ 上限到達" in line

    @pytest.mark.parametrize("value", ["", "  ", "de", "EN-GB", "japanese"])
    def test_unknown_value_falls_back_to_japanese(
        self, monkeypatch: pytest.MonkeyPatch, value: str
    ) -> None:
        """A typo degrades to today's output, never to empty labels."""
        monkeypatch.setenv("CCDB_STATUS_LANG", value)
        line = format_codex_status_line(SAMPLE)
        assert line is not None
        assert "週次 8%" in line

    def test_case_and_whitespace_insensitive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CCDB_STATUS_LANG", "  EN  ")
        line = format_codex_status_line(SAMPLE)
        assert line is not None
        assert "7d 8%" in line

    def test_unavailable_line_follows_the_same_setting(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("CCDB_STATUS_LANG", raising=False)
        assert "残量取得失敗" in codex_status_unavailable_line()
        monkeypatch.setenv("CCDB_STATUS_LANG", "en")
        assert "usage unavailable" in codex_status_unavailable_line()


class TestProviderCache:
    async def test_caches_within_ttl(self) -> None:
        calls = {"n": 0}

        async def fake_fetch(_cmd: str) -> dict:
            calls["n"] += 1
            return SAMPLE

        now = {"t": 1000.0}
        prov = CodexStatusProvider("codex", ttl=90.0, fetcher=fake_fetch, clock=lambda: now["t"])

        first = await prov.get_line()
        assert first is not None
        # Second call within TTL → cached, no extra fetch.
        now["t"] = 1050.0
        await prov.get_line()
        assert calls["n"] == 1

        # After TTL expiry → refetch.
        now["t"] = 1200.0
        await prov.get_line()
        assert calls["n"] == 2

    async def test_failure_uses_short_ttl(self) -> None:
        calls = {"n": 0}

        async def failing_fetch(_cmd: str) -> None:
            calls["n"] += 1
            return None

        now = {"t": 0.0}
        prov = CodexStatusProvider(
            "codex", ttl=90.0, fail_ttl=30.0, fetcher=failing_fetch, clock=lambda: now["t"]
        )

        assert await prov.get_line() is None
        # Within fail_ttl → still cached (no refetch).
        now["t"] = 10.0
        assert await prov.get_line() is None
        assert calls["n"] == 1
        # After fail_ttl → refetch.
        now["t"] = 40.0
        assert await prov.get_line() is None
        assert calls["n"] == 2

    async def test_force_bypasses_cache(self) -> None:
        calls = {"n": 0}

        async def fake_fetch(_cmd: str) -> dict:
            calls["n"] += 1
            return SAMPLE

        prov = CodexStatusProvider("codex", fetcher=fake_fetch, clock=lambda: 0.0)
        await prov.get_line()
        await prov.get_line(force=True)
        assert calls["n"] == 2
