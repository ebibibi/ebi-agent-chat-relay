"""Automation AI policy: per-job AI, stored in the relay, read by the scheduler."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.automation_ai import (
    AutomationAIPolicy,
    PolicyError,
    entry_ai,
    normalize_policy,
)
from claude_discord.backend_settings import BackendSettings
from claude_discord.console.auth import ConsoleAuthConfig, ConsoleAuthenticator
from claude_discord.console.server import ConsoleServer
from claude_discord.console.work_repo import WorkItemRepository
from claude_discord.database.models import init_db
from claude_discord.database.settings_repo import SettingsRepository
from claude_discord.ext.api_server import ApiServer
from claude_discord.turn_ai import TurnAI
from tests.test_console_spawn import native  # noqa: F401  (fixture)

TOKEN = "t" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}
WRITE = {**AUTH, "X-Console-Request": "1"}
SCHEDULER_SHAPE = {
    "default": "codex",
    "jobs": {
        "shikumi_next": "claude",
        "payroll": "master",
        "deep": {"backend": "claude", "model": "opus", "effort": "high"},
    },
}


# -- normalization -------------------------------------------------------------------


def test_short_forms_are_stored_long():
    policy = normalize_policy(SCHEDULER_SHAPE)
    assert policy["default"] == {
        "mode": "backend",
        "backend": "codex",
        "model": None,
        "effort": None,
    }
    assert policy["jobs"]["payroll"] == {"mode": "master"}
    assert policy["jobs"]["deep"] == {
        "mode": "backend",
        "backend": "claude",
        "model": "opus",
        "effort": "high",
    }


def test_missing_default_follows_the_master():
    assert normalize_policy({"jobs": {}})["default"] == {"mode": "master"}


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ({"default": "gpt"}, "default"),
        ({"jobs": {"a": {"backend": "codex", "effort": "ludicrous"}}}, "jobs.a"),
        ({"jobs": {"a": {"backend": "codex", "model": "x; rm"}}}, "jobs.a"),
        ({"jobs": {"bad id!": "codex"}}, "not a job id"),
        ({"jobs": {"a": {"mode": "master", "backend": "codex"}}}, "master takes no"),
        ({"jobs": {"a": {"mode": "weird"}}}, "mode must be"),
        ({"jobs": ["a"]}, "jobs must be"),
        ({"surprise": 1}, "unknown fields"),
        ("codex", "policy must be"),
    ],
)
def test_bad_policies_are_refused_with_the_reason(raw, fragment):
    with pytest.raises(PolicyError, match=fragment.replace("!", r"\!")):
        normalize_policy(raw)


def test_every_problem_is_reported_at_once():
    with pytest.raises(PolicyError) as info:
        normalize_policy({"default": "gpt", "jobs": {"a": "nope"}})
    assert "default" in str(info.value) and "jobs.a" in str(info.value)


def test_entry_ai():
    assert entry_ai({"mode": "master"}) is None
    assert entry_ai({"mode": "backend", "backend": "codex", "model": None, "effort": "low"}) == (
        TurnAI(backend="codex", effort="low")
    )


# -- store -----------------------------------------------------------------------------


@pytest.fixture
async def settings(tmp_path):
    db = str(tmp_path / "s.db")
    await init_db(db)
    return BackendSettings(
        SettingsRepository(db),
        env_backend="claude",
        env_model_for_claude="opus",
        env_model_for_codex="",
    )


async def test_view_resolves_the_master_from_the_global_default(settings):
    store = AutomationAIPolicy(settings)
    assert await store.stored() is None
    await settings.set_backend("codex")
    await settings.set_effort("codex", "medium")
    view = await store.save(SCHEDULER_SHAPE, "tester")
    assert view["master"] == {"backend": "codex", "model": None, "effort": "medium"}
    assert view["updated_by"] == "tester" and view["updated_at"]


async def test_seed_never_overwrites(settings):
    store = AutomationAIPolicy(settings)
    assert await store.seed(SCHEDULER_SHAPE, "scheduler") is not None
    assert await store.seed({"default": "claude"}, "scheduler") is None
    assert (await store.stored())["default"]["backend"] == "codex"


async def test_entry_for_falls_back_to_default(settings):
    store = AutomationAIPolicy(settings)
    assert await store.entry_for("x") is None
    await store.save(SCHEDULER_SHAPE, "t")
    assert (await store.entry_for("deep"))["model"] == "opus"
    assert (await store.entry_for("unlisted"))["backend"] == "codex"


# -- control plane (scheduler) ---------------------------------------------------------


@pytest.fixture
async def control(tmp_path, settings):
    bot = MagicMock()
    api = ApiServer(repo=MagicMock(), bot=bot, host="127.0.0.1", port=0)
    api.components = MagicMock()
    api.components.backend_settings = settings
    async with TestClient(TestServer(api.app)) as client:
        yield client, settings


async def test_read_is_404_until_a_policy_exists(control):
    client, _ = control
    # 404, not an empty policy: the scheduler then keeps using its own file.
    assert (await client.get("/api/automation/ai-policy")).status == 404


async def test_seed_then_read(control):
    client, _ = control
    res = await client.post("/api/automation/ai-policy/seed", json=SCHEDULER_SHAPE)
    assert res.status == 201
    again = await client.post("/api/automation/ai-policy/seed", json={"default": "claude"})
    assert again.status == 409
    data = await (await client.get("/api/automation/ai-policy")).json()
    assert data["default"]["backend"] == "codex"
    assert data["jobs"]["payroll"] == {"mode": "master"}
    assert data["master"]["backend"] == "claude"


async def test_seed_rejects_bad_policy(control):
    client, _ = control
    res = await client.post("/api/automation/ai-policy/seed", json={"default": "gpt"})
    assert res.status == 400
    assert (await client.get("/api/automation/ai-policy")).status == 404


async def test_resolve_endpoint(control):
    client, _ = control
    await client.post("/api/automation/ai-policy/seed", json=SCHEDULER_SHAPE)
    data = await (await client.get("/api/automation/ai-policy/jobs/deep")).json()
    assert data["entry"]["model"] == "opus"
    assert data["ai"] == {"backend": "claude", "model": "opus", "effort": "high"}
    master = await (await client.get("/api/automation/ai-policy/jobs/payroll")).json()
    assert master["entry"] == {"mode": "master"}
    assert master["ai"]["backend"] == "claude"  # resolved master
    assert (await client.get("/api/automation/ai-policy/jobs/bad id")).status == 400


# -- spawn with job_id -----------------------------------------------------------------


async def test_spawn_with_job_id_uses_the_policy_and_records_the_run(native):  # noqa: F811
    native.api.components = MagicMock()
    native.api.components.backend_settings = native.settings
    store = AutomationAIPolicy(native.settings)
    await store.save(SCHEDULER_SHAPE, "t")
    res = await native.client.post("/api/spawn", json={"prompt": "Job", "job_id": "deep"})
    assert res.status == 201
    key = int((await res.json())["thread_id"])
    await asyncio.wait_for(native.ready.wait(), timeout=3)
    assert native.observed[0][:3] == ("claude", "opus", "high")
    run = await store.last_run("deep")
    assert run["thread_id"] == str(key) and run["model"] == "opus" and run["mode"] == "backend"


async def test_explicit_ai_wins_over_the_policy(native):  # noqa: F811
    native.api.components = MagicMock()
    native.api.components.backend_settings = native.settings
    await AutomationAIPolicy(native.settings).save(SCHEDULER_SHAPE, "t")
    res = await native.client.post(
        "/api/spawn",
        json={"prompt": "Job", "job_id": "deep", "ai": {"backend": "codex", "effort": "low"}},
    )
    assert res.status == 201
    await asyncio.wait_for(native.ready.wait(), timeout=3)
    assert native.observed[0][0] == "codex"


async def test_master_job_runs_on_the_default_and_records_it(native):  # noqa: F811
    native.api.components = MagicMock()
    native.api.components.backend_settings = native.settings
    store = AutomationAIPolicy(native.settings)
    await store.save(SCHEDULER_SHAPE, "t")
    res = await native.client.post("/api/spawn", json={"prompt": "Job", "job_id": "payroll"})
    assert res.status == 201
    await asyncio.wait_for(native.ready.wait(), timeout=3)
    assert native.observed[0][:2] == ("claude", "sonnet")
    run = await store.last_run("payroll")
    assert run["mode"] == "master" and run["model"] == "sonnet"


async def test_bad_job_id_is_400(native):  # noqa: F811
    res = await native.client.post("/api/spawn", json={"prompt": "Job", "job_id": "no spaces"})
    assert res.status == 400


# -- Console (authenticated writes) ----------------------------------------------------


@pytest.fixture
async def console(tmp_path, settings):
    db = str(tmp_path / "w.db")
    work = WorkItemRepository(db)
    await work.init_db()
    api = MagicMock()
    api.default_channel_id = None
    api.lineage_repo = None
    api.session_repo = None
    api.bot.get_channel.return_value = None
    api.bot.cogs = {}
    api._running_thread_ids.return_value = set()
    api.components.backend_settings = settings
    server = ConsoleServer(api, work, ConsoleAuthenticator(ConsoleAuthConfig(token=TOKEN)), port=0)
    async with TestClient(TestServer(server.app)) as client:
        yield client, settings


async def test_console_needs_auth_and_csrf(console):
    client, _ = console
    url = "/console/api/automation/ai-policy"
    assert (await client.get(url)).status == 401
    assert (await client.put(url, json=SCHEDULER_SHAPE, headers=AUTH)).status in (400, 403)


async def test_console_reads_and_writes(console):
    client, settings = console
    url = "/console/api/automation/ai-policy"
    empty = await (await client.get(url, headers=AUTH)).json()
    assert empty["configured"] is False and empty["master"]["backend"] == "claude"
    res = await client.put(url, json=SCHEDULER_SHAPE, headers=WRITE)
    assert res.status == 200
    body = await res.json()
    assert body["configured"] is True and body["jobs"]["deep"]["model"] == "opus"
    assert (await AutomationAIPolicy(settings).stored())["default"]["backend"] == "codex"
    bad = await client.put(url, json={"default": "gpt"}, headers=WRITE)
    assert bad.status == 400 and "default" in (await bad.json())["error"]


# -- Console job list ------------------------------------------------------------------


@pytest.fixture
async def console_jobs(tmp_path, settings, monkeypatch):
    from claude_discord import automation_routes
    from claude_discord.console import commands

    async def no_discovery(backend):
        return [{"value": f"{backend}-model", "label": ""}]

    monkeypatch.setattr(commands, "model_choices", no_discovery)
    listed = [
        {"id": "deep", "name": "Deep work", "enabled": True, "schedules": ["07:00"]},
        {"id": "plain", "name": "Plain", "enabled": False, "schedules": []},
        {"id": "bad id", "name": "ignored"},
        "junk",
    ]
    state = {"fail": False}

    async def fetch():
        if state["fail"]:
            raise automation_routes.aiohttp.ClientError("down")
        return [j for j in map(automation_routes._clean_job, listed) if j]

    monkeypatch.setattr(automation_routes, "fetch_scheduler_jobs", fetch)
    db = str(tmp_path / "w.db")
    work = WorkItemRepository(db)
    await work.init_db()
    api = MagicMock()
    api.default_channel_id = None
    api.lineage_repo = None
    api.session_repo = None
    api.bot.get_channel.return_value = None
    api.bot.cogs = {}
    api._running_thread_ids.return_value = set()
    api.components.backend_settings = settings
    server = ConsoleServer(api, work, ConsoleAuthenticator(ConsoleAuthConfig(token=TOKEN)), port=0)
    async with TestClient(TestServer(server.app)) as client:
        yield client, settings, state


async def test_job_list_merges_scheduler_jobs_policy_and_last_runs(console_jobs):
    client, settings, _ = console_jobs
    store = AutomationAIPolicy(settings)
    await store.save({**SCHEDULER_SHAPE, "jobs": {**SCHEDULER_SHAPE["jobs"]}}, "t")
    await store.record_run("deep", 99, pinned=True)
    data = await (await client.get("/console/api/automation/jobs", headers=AUTH)).json()
    rows = {r["id"]: r for r in data["jobs"]}
    assert rows["deep"]["name"] == "Deep work" and rows["deep"]["schedules"] == ["07:00"]
    assert rows["deep"]["entry"]["model"] == "opus"
    assert rows["deep"]["last_run"]["thread_id"] == "99"
    assert rows["plain"]["entry"] is None and rows["plain"]["effective"]["backend"] == "codex"
    assert rows["plain"]["enabled"] is False
    # In the policy but not in the scheduler's list: still shown, so it can be removed.
    assert rows["payroll"]["enabled"] is None
    assert "bad id" not in rows
    assert set(data["backends"]) == {"codex", "claude"}
    assert data["backends"]["codex"]["models"] == ["codex-model"]
    assert data["jobs_error"] is None


async def test_job_list_survives_an_unreachable_scheduler(console_jobs):
    client, _, state = console_jobs
    state["fail"] = True
    res = await client.get("/console/api/automation/jobs", headers=AUTH)
    assert res.status == 200
    data = await res.json()
    assert data["jobs"] == [] and data["jobs_error"]
    assert data["configured"] is False


def test_clean_job_accepts_the_ai_policy_cli_shape():
    from claude_discord.automation_routes import _clean_job

    row = _clean_job(
        {
            "job_id": "nightly",
            "name": "Nightly",
            "group": "Reports",
            "enabled": True,
            "active_schedules": 2,
            "ai_routes": ["direct"],
            "mode": "backend",
        }
    )
    assert row == {
        "id": "nightly",
        "name": "Nightly",
        "group": "Reports",
        "enabled": True,
        "schedules": ["×2"],
        "ai_routes": ["direct"],
    }
    assert _clean_job({"id": "x", "active_schedules": True})["schedules"] == []
