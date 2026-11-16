"""HTTP routes for the automation AI policy (:mod:`claude_discord.automation_ai`).

Two audiences, two trust levels:

- :class:`ControlPlaneRoutes` on the localhost control plane (``/api/automation/...``), for the
  scheduler: read the policy, resolve one job, and seed the policy once from the scheduler's
  own file. Seeding never overwrites, so the Console stays the source of truth once it has
  been used.
- :class:`ConsoleRoutes` behind the Console's authentication and CSRF header, for the person:
  read and replace the policy.

Neither stores or returns secrets: the policy is backend/model/effort names only.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from aiohttp import web

from .automation_ai import AutomationAIPolicy, PolicyError, entry_ai, valid_job_id

logger = logging.getLogger(__name__)

SettingsGetter = Callable[[], Any]


def _error(message: str, status: int) -> web.Response:
    return web.json_response({"error": message}, status=status)


def _store(settings: SettingsGetter) -> AutomationAIPolicy | None:
    backend_settings = settings()
    return AutomationAIPolicy(backend_settings) if backend_settings is not None else None


async def _body(request: web.Request) -> tuple[Any, web.Response | None]:
    try:
        return await request.json(), None
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, _error("invalid JSON", 400)


class ControlPlaneRoutes:
    def __init__(self, settings: SettingsGetter) -> None:
        self._settings = settings

    def register(self, router: web.UrlDispatcher) -> None:
        router.add_get("/api/automation/ai-policy", self.read)
        router.add_post("/api/automation/ai-policy/seed", self.seed)
        router.add_get("/api/automation/ai-policy/jobs/{job_id}", self.resolve)

    async def read(self, request: web.Request) -> web.Response:
        store = _store(self._settings)
        if store is None:
            return _error("this deployment has no backend settings", 503)
        if await store.stored() is None:
            # 404 rather than an empty policy: a caller with its own fallback keeps using it.
            return _error("no automation AI policy has been saved yet", 404)
        return web.json_response(await store.view())

    async def seed(self, request: web.Request) -> web.Response:
        store = _store(self._settings)
        if store is None:
            return _error("this deployment has no backend settings", 503)
        body, err = await _body(request)
        if err:
            return err
        try:
            view = await store.seed(body, "seed")
        except PolicyError as exc:
            return _error(str(exc), 400)
        if view is None:
            return _error("a policy already exists; change it from the Console", 409)
        logger.info("automation AI policy seeded (%d jobs)", len(view.get("jobs", {})))
        return web.json_response(view, status=201)

    async def resolve(self, request: web.Request) -> web.Response:
        job_id = request.match_info["job_id"]
        if not valid_job_id(job_id):
            return _error("job_id must be a job id", 400)
        store = _store(self._settings)
        if store is None:
            return _error("this deployment has no backend settings", 503)
        entry = await store.entry_for(job_id)
        if entry is None:
            return _error("no automation AI policy has been saved yet", 404)
        ai = entry_ai(entry)
        resolved = (
            {"backend": ai.backend, "model": ai.model, "effort": ai.effort}
            if ai is not None
            else await store.master()
        )
        return web.json_response({"job_id": job_id, "entry": entry, "ai": resolved})


class ConsoleRoutes:
    def __init__(self, settings: SettingsGetter, identity: Callable[[web.Request], str]) -> None:
        self._settings = settings
        self._identity = identity

    def register(self, router: web.UrlDispatcher, prefix: str) -> None:
        router.add_get(f"{prefix}/automation/ai-policy", self.read)
        router.add_put(f"{prefix}/automation/ai-policy", self.write)

    async def read(self, request: web.Request) -> web.Response:
        store = _store(self._settings)
        if store is None:
            return _error("this deployment has no backend settings", 503)
        stored = await store.stored()
        view = await store.view(stored)
        return web.json_response({**view, "configured": stored is not None})

    async def write(self, request: web.Request) -> web.Response:
        store = _store(self._settings)
        if store is None:
            return _error("this deployment has no backend settings", 503)
        body, err = await _body(request)
        if err:
            return err
        who = self._identity(request)
        try:
            view = await store.save(body, f"console:{who}")
        except PolicyError as exc:
            return _error(str(exc), 400)
        logger.info("console: automation AI policy saved (%d jobs)", len(view.get("jobs", {})))
        return web.json_response({**view, "configured": True})
