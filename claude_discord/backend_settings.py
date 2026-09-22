"""Persistent, runtime-mutable backend/model selection.

Reads and writes the current backend (claude/codex/local/agui/pi) and per-backend
model preference to ``SettingsRepository`` (sqlite key-value store).

Resolution order for any field:
    1. Thread-scoped override (when ``thread_id`` is given)
    2. Global setting
    3. Environment default (passed to constructor)
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .database.settings_repo import SettingsRepository

logger = logging.getLogger(__name__)

# Valid backend names. Keep in sync with claude_code_core.backend.create_backend().
ALL_BACKENDS = ("claude", "codex", "local", "agui", "pi")

# Settings keys
BACKEND_GLOBAL = "backend.global"
BACKEND_THREAD_PREFIX = "backend.thread."  # + thread_id
MODEL_GLOBAL_PREFIX = "model.global."  # + backend
MODEL_THREAD_PREFIX = "model.thread."  # + thread_id + "." + backend
EFFORT_GLOBAL_PREFIX = "effort.global."  # + backend
EFFORT_THREAD_PREFIX = "effort.thread."  # + thread_id + "." + backend

# Codex status footer toggle (2-layer: global default + per-thread override).
#   "auto" — show the Codex status line only when it can actually be fetched
#            (codex installed + logged in). Invisible for Claude-only users.
#   "on"   — always attempt; surface a hint when the fetch fails.
#   "off"  — never show the Codex status line.
CODEX_STATUS_GLOBAL = "status.codex.global"
CODEX_STATUS_THREAD_PREFIX = "status.codex.thread."  # + thread_id
CODEX_STATUS_MODES = ("auto", "on", "off")
CODEX_STATUS_DEFAULT = "auto"


def session_is_resumable(stored_backend: str | None, current_backend: str) -> bool:
    """Can ``current_backend`` resume a session ID minted by ``stored_backend``?

    Claude and Codex keep separate session stores, so handing a Codex rollout ID
    to ``claude --resume`` (or vice versa) fails at the CLI level. When the
    stored backend is unknown (records written before the ``backend`` column
    existed) we assume it is compatible — the old behaviour.
    """
    if not stored_backend:
        return True
    return stored_backend == current_backend


class BackendSettings:
    """Thin wrapper around SettingsRepository that resolves backend/model."""

    def __init__(
        self,
        repo: SettingsRepository,
        *,
        env_backend: str,
        env_model_for_claude: str,
        env_model_for_codex: str,
    ) -> None:
        self.repo = repo
        self._env_backend = env_backend if env_backend in ALL_BACKENDS else "claude"
        self._env_model = {
            "claude": env_model_for_claude or "",
            "codex": env_model_for_codex or "",
            # The local model has no env default on purpose: /ollama use (or
            # /model) is the only place it is chosen, so what /ollama list
            # marks as selected is what a thread actually runs.
            "local": "",
            # AG-UI identifies the model on the remote agent, not in ccdb.
            "agui": "",
            # pi resolves its own default provider/model from its config; an
            # env default here would pin a provider the operator never chose.
            "pi": "",
        }

    # ── Resolution ──────────────────────────────────────────

    async def current_backend(self, thread_id: int | None = None) -> str:
        """Return the active backend for the given thread (or globally)."""
        if thread_id is not None:
            v = await self.repo.get(f"{BACKEND_THREAD_PREFIX}{thread_id}")
            if v in ALL_BACKENDS:
                return v
        v = await self.repo.get(BACKEND_GLOBAL)
        if v in ALL_BACKENDS:
            return v
        return self._env_backend

    async def explicit_model(self, backend: str, thread_id: int | None = None) -> str | None:
        """Return only the EXPLICITLY-stored model — env fallback is NOT consulted.

        Use this when callers want to distinguish 'user explicitly set a
        per-backend model via /model' from 'we fell back to whatever was
        in .env'. ``current_model()`` mixes those two together; this
        method keeps them apart.

        Resolution: thread > global > None.
        """
        if backend not in ALL_BACKENDS:
            return None
        if thread_id is not None:
            v = await self.repo.get(f"{MODEL_THREAD_PREFIX}{thread_id}.{backend}")
            if v:
                return v
        v = await self.repo.get(f"{MODEL_GLOBAL_PREFIX}{backend}")
        return v if v else None

    async def current_model(self, backend: str, thread_id: int | None = None) -> str | None:
        """Return the model for the given backend, or None if no override.

        When ``None`` is returned the caller should fall back to the
        backend factorys built-in default (e.g. "sonnet" / "gpt-5.4").
        """
        if backend not in ALL_BACKENDS:
            return None
        if thread_id is not None:
            v = await self.repo.get(f"{MODEL_THREAD_PREFIX}{thread_id}.{backend}")
            if v:
                return v
        v = await self.repo.get(f"{MODEL_GLOBAL_PREFIX}{backend}")
        if v:
            return v
        return self._env_model.get(backend) or None

    async def current_effort(self, backend: str, thread_id: int | None = None) -> str | None:
        """Return the reasoning-effort override for ``backend``, or None.

        ``None`` means "no override stored" — the caller should let the
        backend CLI use its own default (e.g. Codex's ``model_reasoning_effort``
        in config.toml). Resolution: thread > global > None.
        """
        if backend not in ALL_BACKENDS:
            return None
        if thread_id is not None:
            v = await self.repo.get(f"{EFFORT_THREAD_PREFIX}{thread_id}.{backend}")
            if v:
                return v
        v = await self.repo.get(f"{EFFORT_GLOBAL_PREFIX}{backend}")
        return v if v else None

    async def codex_status_mode(self, thread_id: int | None = None) -> str:
        """Return the Codex status footer mode for this thread (or globally).

        Resolution: thread override > global > ``CODEX_STATUS_DEFAULT`` (auto).
        """
        if thread_id is not None:
            v = await self.repo.get(f"{CODEX_STATUS_THREAD_PREFIX}{thread_id}")
            if v in CODEX_STATUS_MODES:
                return v
        v = await self.repo.get(CODEX_STATUS_GLOBAL)
        if v in CODEX_STATUS_MODES:
            return v
        return CODEX_STATUS_DEFAULT

    # ── Mutation ────────────────────────────────────────────

    async def set_codex_status_mode(self, mode: str, *, thread_id: int | None = None) -> None:
        if mode not in CODEX_STATUS_MODES:
            raise ValueError(f"unknown codex status mode {mode!r}")
        if thread_id is not None:
            await self.repo.set(f"{CODEX_STATUS_THREAD_PREFIX}{thread_id}", mode)
            logger.info("codex status set: thread=%d -> %s", thread_id, mode)
        else:
            await self.repo.set(CODEX_STATUS_GLOBAL, mode)
            logger.info("codex status set: global -> %s", mode)

    async def set_backend(self, backend: str, *, thread_id: int | None = None) -> None:
        if backend not in ALL_BACKENDS:
            raise ValueError(f"unknown backend {backend!r}")
        if thread_id is not None:
            await self.repo.set(f"{BACKEND_THREAD_PREFIX}{thread_id}", backend)
            logger.info("backend set: thread=%d -> %s", thread_id, backend)
        else:
            await self.repo.set(BACKEND_GLOBAL, backend)
            logger.info("backend set: global -> %s", backend)

    async def set_model(self, backend: str, model: str, *, thread_id: int | None = None) -> None:
        if backend not in ALL_BACKENDS:
            raise ValueError(f"unknown backend {backend!r}")
        if not model:
            raise ValueError("model must not be empty")
        if thread_id is not None:
            await self.repo.set(f"{MODEL_THREAD_PREFIX}{thread_id}.{backend}", model)
            logger.info("model set: thread=%d backend=%s -> %s", thread_id, backend, model)
        else:
            await self.repo.set(f"{MODEL_GLOBAL_PREFIX}{backend}", model)
            logger.info("model set: global backend=%s -> %s", backend, model)

    async def set_effort(self, backend: str, effort: str, *, thread_id: int | None = None) -> None:
        if backend not in ALL_BACKENDS:
            raise ValueError(f"unknown backend {backend!r}")
        if not effort:
            raise ValueError("effort must not be empty")
        if thread_id is not None:
            await self.repo.set(f"{EFFORT_THREAD_PREFIX}{thread_id}.{backend}", effort)
            logger.info("effort set: thread=%d backend=%s -> %s", thread_id, backend, effort)
        else:
            await self.repo.set(f"{EFFORT_GLOBAL_PREFIX}{backend}", effort)
            logger.info("effort set: global backend=%s -> %s", backend, effort)

    async def clear_effort(self, backend: str, *, thread_id: int | None = None) -> bool:
        """Remove a stored effort override. Returns True if something was deleted."""
        if backend not in ALL_BACKENDS:
            raise ValueError(f"unknown backend {backend!r}")
        if thread_id is not None:
            return await self.repo.delete(f"{EFFORT_THREAD_PREFIX}{thread_id}.{backend}")
        return await self.repo.delete(f"{EFFORT_GLOBAL_PREFIX}{backend}")

    async def clear_thread_overrides(self, thread_id: int) -> int:
        """Remove all thread-scoped overrides. Returns count deleted."""
        deleted = 0
        if await self.repo.delete(f"{BACKEND_THREAD_PREFIX}{thread_id}"):
            deleted += 1
        for b in ALL_BACKENDS:
            if await self.repo.delete(f"{MODEL_THREAD_PREFIX}{thread_id}.{b}"):
                deleted += 1
            if await self.repo.delete(f"{EFFORT_THREAD_PREFIX}{thread_id}.{b}"):
                deleted += 1
        if await self.repo.delete(f"{CODEX_STATUS_THREAD_PREFIX}{thread_id}"):
            deleted += 1
        return deleted
