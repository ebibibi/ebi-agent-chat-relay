"""Which AI each automation job runs on, chosen independently of the Console default.

An external scheduler starts work through ``POST /api/spawn`` (or runs a CLI itself). The
person picks, per job, "follow the master" (the AI they use interactively — the global
default), or a fixed backend with an optional model and effort. A ``default`` entry covers jobs
not listed. The relay is the source of truth: the scheduler reads ``GET
/api/automation/ai-policy`` and falls back to its own file only while nothing is stored here.

Entries are validated with the same rules as the Console screen AI (:func:`parse_turn_ai`), so a
stored policy can never put an arbitrary string on a CLI command line. Short forms are accepted
on write — ``"master"`` or a bare backend name — and always stored in the long form::

    {"mode": "master"}
    {"mode": "backend", "backend": "claude", "model": "opus", "effort": "high"}
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from .turn_ai import TurnAI, TurnAIError, parse_turn_ai

MODE_MASTER = "master"
MODE_BACKEND = "backend"
POLICY_KEY = "automation.ai_policy"
LAST_RUN_PREFIX = "automation.last_run."
MAX_JOBS = 1000
_JOB_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,99}$")
_POLICY_FIELDS = frozenset({"default", "jobs"})


class PolicyError(ValueError):
    """A policy the relay will not store; the message names every problem."""


def valid_job_id(job_id: Any) -> bool:
    return isinstance(job_id, str) and bool(_JOB_ID.match(job_id))


def normalize_entry(raw: Any, where: str) -> dict[str, Any]:
    """One entry in its long form, or :class:`PolicyError`."""
    if isinstance(raw, str):
        raw = {"mode": MODE_MASTER} if raw.strip() == MODE_MASTER else {"backend": raw.strip()}
    if not isinstance(raw, Mapping):
        raise PolicyError(f"{where}: must be 'master', a backend name, or an object")
    mode = raw.get("mode") or (MODE_BACKEND if "backend" in raw else None)
    if mode == MODE_MASTER:
        extra = sorted(set(raw) - {"mode"})
        if extra:
            raise PolicyError(f"{where}: master takes no other fields ({', '.join(extra)})")
        return {"mode": MODE_MASTER}
    if mode != MODE_BACKEND:
        raise PolicyError(f"{where}: mode must be 'master' or 'backend'")
    fields = {k: v for k, v in raw.items() if k != "mode"}
    try:
        ai = parse_turn_ai(fields)
    except TurnAIError as exc:
        raise PolicyError(f"{where}: {exc}") from None
    assert ai is not None
    if ai.connection is not None:
        raise PolicyError(f"{where}: connection is not supported for automation")
    return {"mode": MODE_BACKEND, "backend": ai.backend, "model": ai.model, "effort": ai.effort}


def normalize_policy(raw: Any) -> dict[str, Any]:
    """``{"default": entry, "jobs": {id: entry}}``; every problem is reported at once.

    Strict on purpose: unlike the scheduler's reader (which skips a bad line), this guards a
    write, and the person saving it should see the typo rather than have a job silently fall
    back to the default.
    """
    if not isinstance(raw, Mapping):
        raise PolicyError("policy must be an object with default and jobs")
    problems: list[str] = []
    unknown = sorted(set(raw) - _POLICY_FIELDS - {"master", "updated_at", "updated_by"})
    if unknown:
        problems.append(f"unknown fields: {', '.join(unknown)}")
    default: dict[str, Any] = {"mode": MODE_MASTER}
    try:
        default = normalize_entry(raw.get("default", MODE_MASTER), "default")
    except PolicyError as exc:
        problems.append(str(exc))
    raw_jobs = raw.get("jobs") or {}
    jobs: dict[str, dict[str, Any]] = {}
    if not isinstance(raw_jobs, Mapping):
        problems.append("jobs must be an object of job id → entry")
        raw_jobs = {}
    if len(raw_jobs) > MAX_JOBS:
        problems.append(f"at most {MAX_JOBS} jobs")
        raw_jobs = {}
    for job_id, entry in raw_jobs.items():
        if not valid_job_id(job_id):
            problems.append(f"jobs: {str(job_id)[:40]!r} is not a job id")
            continue
        try:
            jobs[job_id] = normalize_entry(entry, f"jobs.{job_id}")
        except PolicyError as exc:
            problems.append(str(exc))
    if problems:
        raise PolicyError("; ".join(problems))
    return {"default": default, "jobs": dict(sorted(jobs.items()))}


def entry_ai(entry: Mapping[str, Any]) -> TurnAI | None:
    """The AI a backend entry pins; ``None`` for master (the relay's default applies)."""
    if entry.get("mode") != MODE_BACKEND:
        return None
    return TurnAI(backend=entry["backend"], model=entry.get("model"), effort=entry.get("effort"))


class AutomationAIPolicy:
    """The stored policy, its resolution, and the last AI each job ran on.

    *settings* is the relay's :class:`~.backend_settings.BackendSettings`: its key-value store
    holds the policy, and its global default is the "master".
    """

    def __init__(self, settings: Any) -> None:
        self._settings = settings
        self._repo = settings.repo

    async def stored(self) -> dict[str, Any] | None:
        raw = await self._repo.get(POLICY_KEY)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    async def save(self, raw: Any, who: str) -> dict[str, Any]:
        policy = normalize_policy(raw)
        policy["updated_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        policy["updated_by"] = who[:100]
        await self._repo.set(POLICY_KEY, json.dumps(policy, ensure_ascii=False))
        return await self.view(policy)

    async def seed(self, raw: Any, who: str) -> dict[str, Any] | None:
        """Store *raw* only if nothing is stored yet; ``None`` when a policy already exists."""
        if await self.stored() is not None:
            return None
        return await self.save(raw, who)

    async def master(self) -> dict[str, str | None]:
        backend = await self._settings.current_backend(None)
        return {
            "backend": backend,
            "model": await self._settings.current_model(backend, None),
            "effort": await self._settings.current_effort(backend, None),
        }

    async def view(self, policy: dict[str, Any] | None = None) -> dict[str, Any]:
        """What the read API returns: the policy plus the master it resolves against."""
        policy = policy or await self.stored() or {}
        return {**policy, "master": await self.master()}

    async def entry_for(self, job_id: str) -> dict[str, Any] | None:
        """The job's entry (or the default); ``None`` while no policy is stored."""
        policy = await self.stored()
        if policy is None:
            return None
        return policy.get("jobs", {}).get(job_id) or policy.get("default") or {"mode": "master"}

    async def record_run(self, job_id: str, thread_id: int, *, pinned: bool) -> None:
        """Remember the AI the job's latest spawn runs on, read back from the thread itself.

        Reading the thread's resolved settings (rather than echoing the request) is what makes
        "follow the master" show the model it actually got.
        """
        backend = await self._settings.current_backend(thread_id)
        record = {
            "mode": MODE_BACKEND if pinned else MODE_MASTER,
            "backend": backend,
            "model": await self._settings.current_model(backend, thread_id),
            "effort": await self._settings.current_effort(backend, thread_id),
            "thread_id": str(thread_id),
            "at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        await self._repo.set(LAST_RUN_PREFIX + job_id, json.dumps(record))

    async def last_run(self, job_id: str) -> dict[str, Any] | None:
        raw = await self._repo.get(LAST_RUN_PREFIX + job_id)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None
