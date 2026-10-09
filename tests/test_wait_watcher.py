"""Tests for WaitWatcherCog — runs due probes and resumes threads when a wait ends."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from claude_discord import waits
from claude_discord.cogs.wait_watcher import WaitWatcherCog
from claude_discord.database.models import init_db
from claude_discord.database.wait_repo import WaitRepository
from claude_discord.waits import MAX_CONSECUTIVE_ERRORS, ProbeResult, parse_wait_spec


@pytest.fixture(autouse=True)
def _cwd_roots(monkeypatch, tmp_path_factory) -> None:
    """Allow probe working directories under pytest's temp root."""
    monkeypatch.setenv("CCDB_WAIT_CWD_ROOTS", str(tmp_path_factory.getbasetemp()))


class FakeClock:
    def __init__(self, now: float) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeProbe:
    def __init__(self, *results: ProbeResult) -> None:
        self.results = list(results)
        self.calls: list[tuple[tuple[str, ...], str | None]] = []

    async def __call__(self, argv: tuple[str, ...], *, cwd: str | None) -> ProbeResult:
        self.calls.append((argv, cwd))
        return self.results.pop(0) if len(self.results) > 1 else self.results[0]


class Delivered:
    def __init__(self, ok: bool = True) -> None:
        self.ok = ok
        self.calls: list[tuple[int, str]] = []

    async def __call__(self, thread_id: int, prompt: str) -> bool:
        self.calls.append((thread_id, prompt))
        return self.ok


@pytest.fixture
async def repo(tmp_path) -> WaitRepository:
    db = str(tmp_path / "sessions.db")
    await init_db(db)
    return WaitRepository(db)


def _cog(repo: WaitRepository, probe: FakeProbe, deliver: Delivered, clock: FakeClock):
    return WaitWatcherCog(MagicMock(), repo, probe=probe, deliver=deliver, clock=clock)


async def _register(repo: WaitRepository, **overrides: object):
    body: dict = {
        "thread_id": 42,
        "argv": ["gh", "pr", "checks", "5"],
        "pending_exit_codes": [8],
        "interval_seconds": 60,
        "timeout_seconds": 600,
        "label": "PR #5 checks",
        "cwd": None,
    }
    body.update(overrides)
    wait, _ = await repo.create(parse_wait_spec(body), now=1000.0)
    return wait


async def test_nothing_runs_before_the_first_interval(repo: WaitRepository) -> None:
    await _register(repo)
    probe, deliver = FakeProbe(ProbeResult(0, "")), Delivered()
    await _cog(repo, probe, deliver, FakeClock(1030.0)).tick()
    assert probe.calls == []


async def test_pending_probe_reschedules(repo: WaitRepository) -> None:
    wait = await _register(repo)
    probe, deliver = FakeProbe(ProbeResult(8, "pending")), Delivered()
    await _cog(repo, probe, deliver, FakeClock(1060.0)).tick()
    loaded = await repo.get(wait.id)
    assert loaded is not None
    assert loaded.status == "active"
    assert loaded.next_check_at == 1120.0
    assert loaded.last_output == "pending"
    assert deliver.calls == []


async def test_done_probe_resumes_the_thread_once(repo: WaitRepository) -> None:
    wait = await _register(repo, note="merge when green")
    probe, deliver = FakeProbe(ProbeResult(1, "build fail")), Delivered()
    cog = _cog(repo, probe, deliver, FakeClock(1060.0))
    await cog.tick()
    await cog.drain()
    loaded = await repo.get(wait.id)
    assert loaded is not None and loaded.status == "done"
    assert len(deliver.calls) == 1
    thread_id, prompt = deliver.calls[0]
    assert thread_id == 42
    assert prompt.startswith(waits.PROMPT_HEADER)
    assert "build fail" in prompt
    assert "merge when green" in prompt
    # A finished wait is not probed again.
    await cog.tick()
    assert len(probe.calls) == 1


async def test_timeout_resumes_with_timeout_outcome(repo: WaitRepository) -> None:
    wait = await _register(repo)
    probe, deliver = FakeProbe(ProbeResult(8, "still pending")), Delivered()
    cog = _cog(repo, probe, deliver, FakeClock(1600.0))
    await cog.tick()
    await cog.drain()
    loaded = await repo.get(wait.id)
    assert loaded is not None and loaded.status == "timeout"
    assert "timed out" in deliver.calls[0][1]


async def test_done_at_the_deadline_wins_over_timeout(repo: WaitRepository) -> None:
    wait = await _register(repo)
    probe, deliver = FakeProbe(ProbeResult(0, "all green")), Delivered()
    cog = _cog(repo, probe, deliver, FakeClock(1600.0))
    await cog.tick()
    await cog.drain()
    loaded = await repo.get(wait.id)
    assert loaded is not None and loaded.status == "done"


async def test_repeated_probe_errors_end_the_wait(repo: WaitRepository) -> None:
    wait = await _register(repo)
    probe = FakeProbe(ProbeResult(None, "", error="gh: not found"))
    deliver = Delivered()
    clock = FakeClock(1060.0)
    cog = _cog(repo, probe, deliver, clock)
    for i in range(MAX_CONSECUTIVE_ERRORS):
        clock.now = 1060.0 + i * 60
        await cog.tick()
    await cog.drain()
    loaded = await repo.get(wait.id)
    assert loaded is not None and loaded.status == "probe_error"
    assert "gh: not found" in deliver.calls[0][1]


async def test_a_success_resets_the_error_count(repo: WaitRepository) -> None:
    wait = await _register(repo)
    probe = FakeProbe(
        ProbeResult(None, "", error="flaky"),
        ProbeResult(8, "pending"),
    )
    clock = FakeClock(1060.0)
    cog = _cog(repo, probe, Delivered(), clock)
    await cog.tick()
    clock.now = 1120.0
    await cog.tick()
    loaded = await repo.get(wait.id)
    assert loaded is not None and loaded.consecutive_errors == 0


async def test_failed_delivery_is_retried_not_lost(repo: WaitRepository) -> None:
    wait = await _register(repo)
    deliver = Delivered(ok=False)
    clock = FakeClock(1060.0)
    cog = _cog(repo, FakeProbe(ProbeResult(0, "green")), deliver, clock)
    await cog.tick()
    await cog.drain()
    loaded = await repo.get(wait.id)
    assert loaded is not None
    assert (loaded.status, loaded.delivery_failures) == ("active", 1)
    # The next look re-probes and delivers again once the thread is reachable.
    deliver.ok = True
    clock.now = loaded.next_check_at
    await cog.tick()
    await cog.drain()
    loaded = await repo.get(wait.id)
    assert loaded is not None and loaded.status == "done"
    assert len(deliver.calls) == 2


async def test_delivery_that_raises_is_retried(repo: WaitRepository) -> None:
    wait = await _register(repo)

    async def boom(thread_id: int, prompt: str) -> bool:
        raise RuntimeError("discord down")

    cog = WaitWatcherCog(
        MagicMock(),
        repo,
        probe=FakeProbe(ProbeResult(0, "")),
        deliver=boom,
        clock=FakeClock(1060.0),
    )
    await cog.tick()
    await cog.drain()
    loaded = await repo.get(wait.id)
    assert loaded is not None and loaded.status == "active"


async def test_undeliverable_after_repeated_failures(repo: WaitRepository) -> None:
    from claude_discord.waits import MAX_DELIVERY_FAILURES

    wait = await _register(repo)
    clock = FakeClock(1060.0)
    cog = _cog(repo, FakeProbe(ProbeResult(0, "")), Delivered(ok=False), clock)
    for _ in range(MAX_DELIVERY_FAILURES):
        await cog.tick()
        await cog.drain()
        loaded = await repo.get(wait.id)
        assert loaded is not None
        clock.now = loaded.next_check_at
    loaded = await repo.get(wait.id)
    assert loaded is not None and loaded.status == "undeliverable"


async def test_resume_is_not_final_until_the_turn_ran(repo: WaitRepository) -> None:
    import asyncio

    wait = await _register(repo)
    gate = asyncio.Event()

    async def slow(thread_id: int, prompt: str) -> bool:
        await gate.wait()
        return True

    cog = WaitWatcherCog(
        MagicMock(),
        repo,
        probe=FakeProbe(ProbeResult(0, "")),
        deliver=slow,
        clock=FakeClock(1060.0),
    )
    await cog.tick()
    await asyncio.sleep(0)
    loaded = await repo.get(wait.id)
    assert loaded is not None and loaded.status == "resuming"
    gate.set()
    await cog.drain()
    loaded = await repo.get(wait.id)
    assert loaded is not None and loaded.status == "done"


async def test_probe_gets_the_registered_cwd(repo: WaitRepository, tmp_path) -> None:
    await _register(repo, cwd=str(tmp_path))
    probe = FakeProbe(ProbeResult(8, ""))
    await _cog(repo, probe, Delivered(), FakeClock(1060.0)).tick()
    assert probe.calls[0][1] == str(tmp_path)


async def test_cog_load_recovers_and_announces(repo: WaitRepository) -> None:
    wait = await _register(repo)
    await repo.claim(wait.id, "done")  # the bot died while this resume was queued
    cog = _cog(repo, FakeProbe(ProbeResult(8, "")), Delivered(), FakeClock(5000.0))
    cog._loop = MagicMock()
    await cog.cog_load()
    try:
        assert waits.watcher_active() is True
        assert waits.watcher_repo() is repo
        loaded = await repo.get(wait.id)
        assert loaded is not None and loaded.status == "active"
    finally:
        await cog.cog_unload()
    assert waits.watcher_active() is False
