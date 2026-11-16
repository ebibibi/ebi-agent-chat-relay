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
import os
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp
from aiohttp import web

from .automation_ai import AutomationAIPolicy, PolicyError, entry_ai, valid_job_id

logger = logging.getLogger(__name__)

SettingsGetter = Callable[[], Any]
JobsFetcher = Callable[[], Awaitable[list[dict[str, Any]]]]

#: Where the scheduler lists its jobs: a JSON array (or ``{"jobs": [...]}``) of rows shaped like
#: its ``GET /api/jobs/`` or ``ai-policy --json`` output. Unset: only jobs named in the policy.
JOBS_URL_ENV = "CCDB_AUTOMATION_JOBS_URL"
#: The backends the Console offers for automation; the scheduler reads only these two.
AUTOMATION_BACKENDS = ("codex", "claude")
_JOBS_TIMEOUT_SECONDS = 3


def _clean_job(raw: Any) -> dict[str, Any] | None:
    """One job from the scheduler, reduced to what the Console shows; ``None`` if unusable.

    Accepts both the scheduler's ``GET /api/jobs/`` rows (``id``, ``schedules`` list) and its
    ``ai-policy --json`` rows (``job_id``, ``group``, ``active_schedules`` count, ``ai_routes``).
    """
    if not isinstance(raw, dict):
        return None
    job_id = raw.get("id", raw.get("job_id"))
    if not valid_job_id(job_id):
        return None
    schedules = raw.get("schedules")
    active = raw.get("active_schedules")
    if isinstance(schedules, list):
        shown = [str(x)[:60] for x in schedules[:20]]
    elif isinstance(active, int) and not isinstance(active, bool):
        shown = [f"×{active}"] if active else []
    else:
        shown = []
    routes = raw.get("ai_routes")
    group = raw.get("group")
    return {
        "id": job_id,
        "name": str(raw.get("name") or job_id)[:200],
        "group": str(group)[:100] if group else None,
        "enabled": raw.get("enabled") is not False,
        "schedules": shown,
        # None: the scheduler did not say how the job reaches an AI.
        "ai_routes": [str(r)[:30] for r in routes[:10]] if isinstance(routes, list) else None,
    }


async def fetch_scheduler_jobs() -> list[dict[str, Any]]:
    """The scheduler's job list from :data:`JOBS_URL_ENV`; raises on any failure."""
    url = os.environ.get(JOBS_URL_ENV, "").strip()
    if not url:
        return []
    timeout = aiohttp.ClientTimeout(total=_JOBS_TIMEOUT_SECONDS)
    async with aiohttp.ClientSession(timeout=timeout) as session, session.get(url) as response:
        response.raise_for_status()
        data = await response.json(content_type=None)
    if isinstance(data, dict) and isinstance(data.get("jobs"), list):
        data = data["jobs"]
    if not isinstance(data, list):
        raise ValueError("the job list is not a JSON array or an object with jobs")
    return [job for job in map(_clean_job, data) if job is not None]


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
    def __init__(
        self,
        settings: SettingsGetter,
        identity: Callable[[web.Request], str],
        jobs: JobsFetcher | None = None,
    ) -> None:
        self._settings = settings
        self._identity = identity
        self._jobs = jobs

    def register(self, router: web.UrlDispatcher, prefix: str) -> None:
        router.add_get(f"{prefix}/automation/ai-policy", self.read)
        router.add_put(f"{prefix}/automation/ai-policy", self.write)
        router.add_get(f"{prefix}/automation/jobs", self.jobs)

    async def jobs(self, request: web.Request) -> web.Response:
        """Every job with its own entry (or none), the entry in effect, and its last run."""
        store = _store(self._settings)
        if store is None:
            return _error("this deployment has no backend settings", 503)
        stored = await store.stored()
        view = await store.view(stored)
        error = None
        try:
            listed = await (self._jobs or fetch_scheduler_jobs)()
        except (TimeoutError, aiohttp.ClientError, ValueError) as exc:
            logger.warning("console: could not read the automation job list: %s", exc)
            listed, error = [], "the scheduler's job list could not be read"
        known = {job["id"] for job in listed}
        policy_jobs: dict[str, Any] = (stored or {}).get("jobs", {})
        extra = [
            {
                "id": job_id,
                "name": job_id,
                "group": None,
                "enabled": None,
                "schedules": [],
                "ai_routes": None,
            }
            for job_id in sorted(policy_jobs)
            if job_id not in known
        ]
        default = view.get("default") or {"mode": "master"}
        rows = []
        for job in [*listed, *extra]:
            own = policy_jobs.get(job["id"])
            rows.append(
                {
                    **job,
                    "entry": own,
                    "effective": own or default,
                    "last_run": await store.last_run(job["id"]),
                }
            )
        from .console.commands import effort_levels, model_choices

        backends = {}
        for backend in AUTOMATION_BACKENDS:
            try:
                models = [m["value"] for m in await model_choices(backend)]
            except Exception:
                logger.exception("console: model discovery failed for %s", backend)
                models = []
            backends[backend] = {"efforts": effort_levels(backend), "models": models}
        return web.json_response(
            {
                **view,
                "configured": stored is not None,
                "jobs": rows,
                "backends": backends,
                "jobs_error": error,
            }
        )

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
