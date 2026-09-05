"""Tests for live Claude model discovery behind the /model autocomplete.

The suggestions used to be a hardcoded list that went stale on every model
launch. These tests pin the behaviour we actually care about: the list comes
from whatever models the local credentials can see, aliases point at the newest
model of each family, and a failed lookup degrades to the static fallback
instead of breaking the command.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from claude_discord import model_catalog
from claude_discord.model_catalog import ModelInfo

FALLBACK = [("sonnet", "balanced"), ("opus", "deep reasoning")]


def _models_payload() -> dict:
    """Shape mirrors GET /v1/models (newest first, but not relied upon)."""
    return {
        "data": [
            {
                "id": "claude-sonnet-5",
                "display_name": "Claude Sonnet 5",
                "created_at": "2026-06-29T00:00:00Z",
            },
            {
                "id": "claude-opus-5",
                "display_name": "Claude Opus 5",
                "created_at": "2026-07-24T00:00:00Z",
            },
            {
                "id": "claude-opus-4-8",
                "display_name": "Claude Opus 4.8",
                "created_at": "2026-04-01T00:00:00Z",
            },
            {
                "id": "claude-haiku-4-5-20251001",
                "display_name": "Claude Haiku 4.5",
                "created_at": "2025-10-01T00:00:00Z",
            },
        ]
    }


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    model_catalog.reset_cache()


class TestParseModels:
    def test_parses_id_display_name_and_created_at(self) -> None:
        models = model_catalog.parse_models(_models_payload())

        assert [m.id for m in models] == [
            "claude-opus-5",
            "claude-sonnet-5",
            "claude-opus-4-8",
            "claude-haiku-4-5-20251001",
        ]
        assert models[0].display_name == "Claude Opus 5"

    def test_skips_entries_without_an_id(self) -> None:
        payload = {"data": [{"display_name": "nameless"}, {"id": "claude-opus-5"}]}

        assert [m.id for m in model_catalog.parse_models(payload)] == ["claude-opus-5"]


class TestBuildChoices:
    def test_alias_points_at_the_newest_model_of_its_family(self) -> None:
        choices = model_catalog.build_choices(model_catalog.parse_models(_models_payload()))
        by_value = dict(choices)

        assert "claude-opus-5" in by_value["opus"]
        assert "claude-opus-4-8" not in by_value["opus"]

    def test_aliases_come_first_newest_family_first(self) -> None:
        choices = model_catalog.build_choices(model_catalog.parse_models(_models_payload()))

        assert [v for v, _ in choices][:3] == ["opus", "sonnet", "haiku"]

    def test_full_model_ids_follow_the_aliases(self) -> None:
        choices = model_catalog.build_choices(model_catalog.parse_models(_models_payload()))
        values = [v for v, _ in choices]

        assert "claude-opus-4-8" in values
        assert values.index("claude-opus-5") > values.index("opus")

    def test_omits_aliases_with_no_matching_model(self) -> None:
        models = [ModelInfo("claude-opus-5", "Claude Opus 5", "2026-07-24T00:00:00Z")]

        values = [v for v, _ in model_catalog.build_choices(models)]

        assert "fable" not in values
        assert values[0] == "opus"

    def test_ignores_non_claude_ids(self) -> None:
        models = model_catalog.parse_models({"data": [{"id": "gpt-5.6-sol"}]})

        assert model_catalog.build_choices(models) == []


class TestAuthHeaders:
    def test_prefers_an_explicit_api_key(self, tmp_path: Path) -> None:
        headers = model_catalog.auth_headers({"ANTHROPIC_API_KEY": "sk-ant-api-x"})

        assert headers is not None
        assert headers["x-api-key"] == "sk-ant-api-x"
        assert "Authorization" not in headers

    def test_falls_back_to_the_cli_oauth_credentials(self, tmp_path: Path) -> None:
        creds = tmp_path / ".credentials.json"
        creds.write_text(
            json.dumps(
                {
                    "claudeAiOauth": {
                        "accessToken": "sk-ant-oat-x",
                        "expiresAt": int((time.time() + 3600) * 1000),
                    }
                }
            )
        )

        headers = model_catalog.auth_headers({"CLAUDE_CONFIG_DIR": str(tmp_path)})

        assert headers is not None
        assert headers["Authorization"] == "Bearer sk-ant-oat-x"
        assert headers["anthropic-beta"] == model_catalog.OAUTH_BETA

    def test_ignores_expired_oauth_credentials(self, tmp_path: Path) -> None:
        creds = tmp_path / ".credentials.json"
        creds.write_text(
            json.dumps(
                {
                    "claudeAiOauth": {
                        "accessToken": "sk-ant-oat-x",
                        "expiresAt": int((time.time() - 60) * 1000),
                    }
                }
            )
        )

        assert model_catalog.auth_headers({"CLAUDE_CONFIG_DIR": str(tmp_path)}) is None

    def test_returns_none_for_third_party_providers(self, tmp_path: Path) -> None:
        env = {"ANTHROPIC_API_KEY": "sk-ant-api-x", "CLAUDE_CODE_USE_BEDROCK": "1"}

        assert model_catalog.auth_headers(env) is None

    def test_returns_none_when_no_credentials_exist(self, tmp_path: Path) -> None:
        assert model_catalog.auth_headers({"CLAUDE_CONFIG_DIR": str(tmp_path)}) is None


class TestClaudeModelChoices:
    async def test_returns_discovered_models(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(model_catalog, "_get_json", lambda *a, **k: _models_payload())
        monkeypatch.setattr(model_catalog, "auth_headers", lambda env: {"x-api-key": "k"})

        choices = await model_catalog.claude_model_choices(fallback=FALLBACK, env={})

        assert [v for v, _ in choices][0] == "opus"
        assert "claude-opus-5" in [v for v, _ in choices]

    async def test_falls_back_when_the_lookup_fails(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*args: object, **kwargs: object) -> dict:
            raise OSError("no network")

        monkeypatch.setattr(model_catalog, "_get_json", boom)
        monkeypatch.setattr(model_catalog, "auth_headers", lambda env: {"x-api-key": "k"})

        assert await model_catalog.claude_model_choices(fallback=FALLBACK, env={}) == FALLBACK

    async def test_falls_back_when_no_credentials_are_available(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(model_catalog, "auth_headers", lambda env: None)

        assert await model_catalog.claude_model_choices(fallback=FALLBACK, env={}) == FALLBACK

    async def test_falls_back_when_discovery_is_disabled(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(model_catalog, "auth_headers", lambda env: {"x-api-key": "k"})
        calls: list[int] = []
        monkeypatch.setattr(
            model_catalog, "_get_json", lambda *a, **k: (calls.append(1), _models_payload())[1]
        )

        choices = await model_catalog.claude_model_choices(
            fallback=FALLBACK, env={"CCDB_MODEL_DISCOVERY": "0"}
        )

        assert choices == FALLBACK
        assert calls == []

    async def test_caches_successful_lookups(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[int] = []
        monkeypatch.setattr(model_catalog, "auth_headers", lambda env: {"x-api-key": "k"})
        monkeypatch.setattr(
            model_catalog, "_get_json", lambda *a, **k: (calls.append(1), _models_payload())[1]
        )

        first = await model_catalog.claude_model_choices(fallback=FALLBACK, env={})
        second = await model_catalog.claude_model_choices(fallback=FALLBACK, env={})

        assert first == second
        assert len(calls) == 1, "autocomplete fires per keystroke — one lookup must be reused"

    async def test_refetches_after_the_cache_expires(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[int] = []
        clock = [1000.0]
        monkeypatch.setattr(model_catalog, "auth_headers", lambda env: {"x-api-key": "k"})
        monkeypatch.setattr(
            model_catalog, "_get_json", lambda *a, **k: (calls.append(1), _models_payload())[1]
        )
        monkeypatch.setattr(model_catalog.time, "monotonic", lambda: clock[0])

        await model_catalog.claude_model_choices(fallback=FALLBACK, env={})
        clock[0] += model_catalog.CACHE_TTL_SECONDS + 1
        await model_catalog.claude_model_choices(fallback=FALLBACK, env={})

        assert len(calls) == 2

    async def test_retries_a_failed_lookup_only_after_the_short_ttl(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[int] = []
        clock = [1000.0]

        def boom(*args: object, **kwargs: object) -> dict:
            calls.append(1)
            raise OSError("no network")

        monkeypatch.setattr(model_catalog, "auth_headers", lambda env: {"x-api-key": "k"})
        monkeypatch.setattr(model_catalog, "_get_json", boom)
        monkeypatch.setattr(model_catalog.time, "monotonic", lambda: clock[0])

        await model_catalog.claude_model_choices(fallback=FALLBACK, env={})
        await model_catalog.claude_model_choices(fallback=FALLBACK, env={})
        assert len(calls) == 1

        clock[0] += model_catalog.FAILURE_TTL_SECONDS + 1
        await model_catalog.claude_model_choices(fallback=FALLBACK, env={})
        assert len(calls) == 2


def _write_codex_cache(home: Path, models: list[dict]) -> None:
    (home / model_catalog.CODEX_MODELS_CACHE).write_text(
        json.dumps({"models": models}), encoding="utf-8"
    )


class TestCodexModelChoices:
    """Codex discovery reads the catalog the Codex CLI already fetched itself."""

    def test_reads_the_cli_catalog_in_priority_order(self, tmp_path: Path) -> None:
        _write_codex_cache(
            tmp_path,
            [
                {"slug": "old", "display_name": "Old", "priority": 2, "visibility": "list"},
                {
                    "slug": "gpt-6-astra",
                    "display_name": "GPT-6-Astra",
                    "description": "Most capable",
                    "priority": 1,
                    "visibility": "list",
                },
            ],
        )

        choices = model_catalog.codex_model_choices(
            fallback=FALLBACK, env={"CODEX_HOME": str(tmp_path)}
        )

        assert choices == [("gpt-6-astra", "Most capable"), ("old", "Old")]

    def test_hidden_models_are_not_offered(self, tmp_path: Path) -> None:
        """``hide`` marks internal models (auto-review, reserve capacity)."""
        _write_codex_cache(
            tmp_path,
            [
                {"slug": "gpt-6-astra", "display_name": "GPT-6-Astra", "visibility": "list"},
                {"slug": "codex-auto-review", "display_name": "Auto Review", "visibility": "hide"},
            ],
        )

        choices = model_catalog.codex_model_choices(
            fallback=FALLBACK, env={"CODEX_HOME": str(tmp_path)}
        )

        assert [slug for slug, _ in choices] == ["gpt-6-astra"]

    def test_missing_catalog_falls_back(self, tmp_path: Path) -> None:
        """A host that never ran the Codex CLI still gets suggestions."""
        assert (
            model_catalog.codex_model_choices(
                fallback=FALLBACK, env={"CODEX_HOME": str(tmp_path / "nope")}
            )
            == FALLBACK
        )

    def test_malformed_catalog_falls_back(self, tmp_path: Path) -> None:
        (tmp_path / model_catalog.CODEX_MODELS_CACHE).write_text("{not json", encoding="utf-8")

        assert (
            model_catalog.codex_model_choices(fallback=FALLBACK, env={"CODEX_HOME": str(tmp_path)})
            == FALLBACK
        )

    def test_empty_catalog_falls_back(self, tmp_path: Path) -> None:
        _write_codex_cache(tmp_path, [])

        assert (
            model_catalog.codex_model_choices(fallback=FALLBACK, env={"CODEX_HOME": str(tmp_path)})
            == FALLBACK
        )

    def test_discovery_can_be_disabled(self, tmp_path: Path) -> None:
        _write_codex_cache(tmp_path, [{"slug": "gpt-6-astra", "display_name": "GPT-6-Astra"}])

        assert (
            model_catalog.codex_model_choices(
                fallback=FALLBACK,
                env={"CODEX_HOME": str(tmp_path), "CCDB_MODEL_DISCOVERY": "0"},
            )
            == FALLBACK
        )
