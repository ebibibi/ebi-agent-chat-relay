"""Tests for BackendSettings (resolution + persistence)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import aiosqlite
import pytest

from claude_discord.backend_settings import (
    BACKEND_GLOBAL,
    CODEX_STATUS_GLOBAL,
    BackendSettings,
)
from claude_discord.database.settings_repo import SettingsRepository


async def _new_repo() -> tuple[SettingsRepository, Path]:
    """Create a fresh on-disk SettingsRepository with the schema created."""
    tmp = Path(tempfile.mkdtemp()) / "settings.db"
    async with aiosqlite.connect(str(tmp)) as db:
        await db.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        await db.commit()
    return SettingsRepository(str(tmp)), tmp


class TestResolution:
    async def test_global_only_env_fallback(self) -> None:
        repo, _ = await _new_repo()
        s = BackendSettings(
            repo,
            env_backend="claude",
            env_model_for_claude="sonnet",
            env_model_for_codex="",
        )
        assert await s.current_backend() == "claude"
        assert await s.current_model("claude") == "sonnet"
        assert await s.current_model("codex") is None

    async def test_global_set_overrides_env(self) -> None:
        repo, _ = await _new_repo()
        s = BackendSettings(
            repo,
            env_backend="claude",
            env_model_for_claude="sonnet",
            env_model_for_codex="",
        )
        await s.set_backend("codex")
        assert await s.current_backend() == "codex"

    async def test_thread_overrides_global(self) -> None:
        repo, _ = await _new_repo()
        s = BackendSettings(
            repo,
            env_backend="claude",
            env_model_for_claude="sonnet",
            env_model_for_codex="",
        )
        await s.set_backend("claude")
        await s.set_backend("codex", thread_id=42)
        assert await s.current_backend() == "claude"
        assert await s.current_backend(thread_id=42) == "codex"

    async def test_model_thread_overrides_global(self) -> None:
        repo, _ = await _new_repo()
        s = BackendSettings(
            repo,
            env_backend="claude",
            env_model_for_claude="sonnet",
            env_model_for_codex="",
        )
        await s.set_model("claude", "opus")
        await s.set_model("claude", "haiku", thread_id=99)
        assert await s.current_model("claude") == "opus"
        assert await s.current_model("claude", thread_id=99) == "haiku"
        # other thread sees global
        assert await s.current_model("claude", thread_id=1) == "opus"

    async def test_unknown_backend_in_db_falls_through(self) -> None:
        repo, _ = await _new_repo()
        s = BackendSettings(
            repo,
            env_backend="claude",
            env_model_for_claude="",
            env_model_for_codex="",
        )
        # Inject a corrupted value directly
        await repo.set(BACKEND_GLOBAL, "bogus")
        assert await s.current_backend() == "claude"

    async def test_clear_thread_overrides(self) -> None:
        repo, _ = await _new_repo()
        s = BackendSettings(
            repo,
            env_backend="claude",
            env_model_for_claude="sonnet",
            env_model_for_codex="",
        )
        await s.set_backend("codex", thread_id=7)
        await s.set_model("codex", "gpt-5", thread_id=7)
        deleted = await s.clear_thread_overrides(7)
        assert deleted == 2
        assert await s.current_backend(thread_id=7) == "claude"


class TestCodexStatusMode:
    async def _settings(self) -> BackendSettings:
        repo, _ = await _new_repo()
        return BackendSettings(
            repo,
            env_backend="claude",
            env_model_for_claude="sonnet",
            env_model_for_codex="",
        )

    async def test_default_is_auto(self) -> None:
        s = await self._settings()
        assert await s.codex_status_mode() == "auto"
        assert await s.codex_status_mode(thread_id=5) == "auto"

    async def test_global_set(self) -> None:
        s = await self._settings()
        await s.set_codex_status_mode("on")
        assert await s.codex_status_mode() == "on"

    async def test_thread_overrides_global(self) -> None:
        s = await self._settings()
        await s.set_codex_status_mode("on")
        await s.set_codex_status_mode("off", thread_id=42)
        assert await s.codex_status_mode() == "on"
        assert await s.codex_status_mode(thread_id=42) == "off"
        # other thread sees global
        assert await s.codex_status_mode(thread_id=1) == "on"

    async def test_invalid_value_in_db_falls_through(self) -> None:
        s = await self._settings()
        await s.repo.set(CODEX_STATUS_GLOBAL, "bogus")
        assert await s.codex_status_mode() == "auto"

    async def test_set_rejects_unknown_mode(self) -> None:
        s = await self._settings()
        with pytest.raises(ValueError):
            await s.set_codex_status_mode("sometimes")

    async def test_clear_thread_overrides_removes_codex_status(self) -> None:
        s = await self._settings()
        await s.set_codex_status_mode("off", thread_id=7)
        deleted = await s.clear_thread_overrides(7)
        assert deleted == 1
        assert await s.codex_status_mode(thread_id=7) == "auto"


class TestMutationValidation:
    async def test_set_backend_rejects_unknown(self) -> None:
        repo, _ = await _new_repo()
        s = BackendSettings(
            repo,
            env_backend="claude",
            env_model_for_claude="",
            env_model_for_codex="",
        )
        with pytest.raises(ValueError):
            await s.set_backend("gpt4")  # type: ignore[arg-type]

    async def test_set_model_rejects_empty(self) -> None:
        repo, _ = await _new_repo()
        s = BackendSettings(
            repo,
            env_backend="claude",
            env_model_for_claude="",
            env_model_for_codex="",
        )
        with pytest.raises(ValueError):
            await s.set_model("claude", "")


class TestEffort:
    async def _settings(self) -> BackendSettings:
        repo, _ = await _new_repo()
        return BackendSettings(
            repo,
            env_backend="claude",
            env_model_for_claude="sonnet",
            env_model_for_codex="",
        )

    async def test_no_override_returns_none(self) -> None:
        s = await self._settings()
        assert await s.current_effort("codex") is None
        assert await s.current_effort("claude") is None

    async def test_global_set_and_read(self) -> None:
        s = await self._settings()
        await s.set_effort("codex", "high")
        assert await s.current_effort("codex") == "high"
        # Effort is per-backend: setting codex must not affect claude.
        assert await s.current_effort("claude") is None

    async def test_thread_overrides_global(self) -> None:
        s = await self._settings()
        await s.set_effort("codex", "low")
        await s.set_effort("codex", "xhigh", thread_id=42)
        assert await s.current_effort("codex") == "low"
        assert await s.current_effort("codex", thread_id=42) == "xhigh"

    async def test_clear_effort(self) -> None:
        s = await self._settings()
        await s.set_effort("codex", "high")
        assert await s.clear_effort("codex") is True
        assert await s.current_effort("codex") is None

    async def test_clear_thread_overrides_includes_effort(self) -> None:
        s = await self._settings()
        await s.set_backend("codex", thread_id=7)
        await s.set_effort("codex", "high", thread_id=7)
        deleted = await s.clear_thread_overrides(7)
        assert deleted == 2  # backend + effort
        assert await s.current_effort("codex", thread_id=7) is None

    async def test_set_effort_rejects_empty(self) -> None:
        s = await self._settings()
        with pytest.raises(ValueError):
            await s.set_effort("codex", "")

    async def test_set_effort_rejects_unknown_backend(self) -> None:
        s = await self._settings()
        with pytest.raises(ValueError):
            await s.set_effort("gpt4", "high")  # type: ignore[arg-type]


class TestPiEnvModel:
    """Without CCDB_PI_MODEL, pi picks its own default — possibly one the
    account's credentials cannot call, with no way out from Discord."""

    async def test_pi_env_default_is_resolved(self) -> None:
        repo, _ = await _new_repo()
        s = BackendSettings(
            repo,
            env_backend="pi",
            env_model_for_claude="sonnet",
            env_model_for_codex="",
            env_model_for_pi="anthropic/claude-opus-5",
        )
        assert await s.current_model("pi") == "anthropic/claude-opus-5"
        # It is a pi default only; the other backends keep their own.
        assert await s.current_model("claude") == "sonnet"

    async def test_unset_pi_env_still_defers_to_pi(self) -> None:
        repo, _ = await _new_repo()
        s = BackendSettings(
            repo,
            env_backend="pi",
            env_model_for_claude="sonnet",
            env_model_for_codex="",
        )
        assert await s.current_model("pi") is None

    async def test_model_set_overrides_the_pi_env_default(self) -> None:
        repo, _ = await _new_repo()
        s = BackendSettings(
            repo,
            env_backend="pi",
            env_model_for_claude="sonnet",
            env_model_for_codex="",
            env_model_for_pi="anthropic/claude-opus-5",
        )
        await s.set_model("pi", "ollama/gpt-oss:120b", thread_id=42)
        assert await s.current_model("pi", 42) == "ollama/gpt-oss:120b"
        assert await s.current_model("pi") == "anthropic/claude-opus-5"
