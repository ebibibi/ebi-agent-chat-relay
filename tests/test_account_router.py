"""AccountRouter: planning turns, moving transcripts, reacting to rejections."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_code_core.account_pool import (
    AccountBinding,
    AccountTurn,
    PoolConfig,
    ProfileSpec,
)
from claude_code_core.account_pool_repo import AccountPoolRepository
from claude_code_core.account_router import RELAY_DEFAULT_LABEL, AccountRouter
from claude_code_core.account_transcripts import (
    ambient_home,
    copy_transcript,
    find_transcript,
)
from claude_code_core.session_repo import UsageStatsRepository
from claude_code_core.types import RateLimitInfo

NOW = 2_000_000_000.0
SID = "ec8eb879-174f-443c-83fd-51f63b962aae"


class Clock:
    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture()
def homes(tmp_path: Path) -> dict[str, Path]:
    out = {}
    for name in ("a", "b", "ambient"):
        path = tmp_path / name
        path.mkdir()
        out[name] = path
    return out


def _claude_transcript(home: Path, sid: str = SID, text: str = "{}\n") -> Path:
    path = home / "projects" / "-work-repo" / f"{sid}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def _router(
    tmp_path: Path, homes: dict[str, Path], clock: Clock | None = None, **pool_kw
) -> AccountRouter:
    pool = PoolConfig(
        backend="claude",
        profiles=(ProfileSpec("a", str(homes["a"])), ProfileSpec("b", str(homes["b"]))),
        **pool_kw,
    )
    db = str(tmp_path / "s.db")
    return AccountRouter(
        {"claude": pool},
        AccountPoolRepository(db),
        UsageStatsRepository(db),
        clock=clock or Clock(),
    )


def _full(resets: int = int(NOW) + 600) -> RateLimitInfo:
    return RateLimitInfo("five_hour", "allowed_warning", 0.99, resets)


class TestTranscripts:
    def test_ambient_home(self, tmp_path: Path) -> None:
        assert ambient_home("claude", {"CLAUDE_CONFIG_DIR": str(tmp_path)}) == tmp_path
        assert ambient_home("claude", {}) == Path.home() / ".claude"
        assert ambient_home("codex", {"CODEX_HOME": str(tmp_path)}) == tmp_path
        assert ambient_home("codex", {}) == Path.home() / ".codex"
        with pytest.raises(ValueError):
            ambient_home("pi", {})

    def test_claude_copy_keeps_relative_path_and_sidecar(self, homes: dict[str, Path]) -> None:
        src = _claude_transcript(homes["a"], text="history\n")
        sidecar = src.with_suffix("") / "subagents" / "agent-1.jsonl"
        sidecar.parent.mkdir(parents=True)
        sidecar.write_text("sub\n")
        assert copy_transcript("claude", SID, homes["a"], homes["b"])
        copied = homes["b"] / "projects" / "-work-repo" / f"{SID}.jsonl"
        assert copied.read_text() == "history\n"
        assert (
            homes["b"] / "projects" / "-work-repo" / SID / "subagents" / "agent-1.jsonl"
        ).exists()

    def test_copy_overwrites_a_stale_copy(self, homes: dict[str, Path]) -> None:
        _claude_transcript(homes["a"], text="old\n")
        _claude_transcript(homes["b"], text="old\nnewer turns\n")
        assert copy_transcript("claude", SID, homes["b"], homes["a"])
        assert find_transcript("claude", homes["a"], SID).read_text() == "old\nnewer turns\n"  # type: ignore[union-attr]

    def test_codex_rollout(self, homes: dict[str, Path]) -> None:
        sid = "01a10a8c-6b4c-7063-ac4b-b186c499d71d"
        rel = Path("sessions/2026/10/05") / f"rollout-2026-10-05T14-32-17-{sid}.jsonl"
        (homes["a"] / rel).parent.mkdir(parents=True)
        (homes["a"] / rel).write_text("rollout\n")
        assert copy_transcript("codex", sid, homes["a"], homes["b"])
        assert (homes["b"] / rel).read_text() == "rollout\n"

    def test_missing_transcript_and_invalid_id(self, homes: dict[str, Path]) -> None:
        assert not copy_transcript("claude", SID, homes["a"], homes["b"])
        assert find_transcript("claude", homes["a"], "../../etc/passwd") is None
        assert find_transcript("codex", homes["a"], SID) is None

    def test_same_home_is_a_no_op(self, homes: dict[str, Path]) -> None:
        assert copy_transcript("claude", SID, homes["a"], homes["a"])


class TestPlanTurn:
    async def test_backend_without_pool(self, tmp_path: Path, homes: dict[str, Path]) -> None:
        router = _router(tmp_path, homes)
        assert await router.plan_turn(backend="codex", thread_id=1, session_id=None) is None

    async def test_new_session_gets_first_profile_and_is_pinned(
        self, tmp_path: Path, homes: dict[str, Path]
    ) -> None:
        router = _router(tmp_path, homes)
        plan = await router.plan_turn(backend="claude", thread_id=1, session_id=None)
        assert plan is not None
        assert plan.profile == "a"
        assert plan.binding.env == {"CLAUDE_CONFIG_DIR": str(homes["a"])}
        assert plan.moved_from is None
        assert await router._repo.get_pin(1) == ("claude", "a")

    async def test_pinned_session_stays_put(self, tmp_path: Path, homes: dict[str, Path]) -> None:
        router = _router(tmp_path, homes)
        await router._repo.set_pin(1, "claude", "b")
        plan = await router.plan_turn(backend="claude", thread_id=1, session_id=SID)
        assert plan is not None
        assert (plan.profile, plan.session_id, plan.moved_from) == ("b", SID, None)

    async def test_exhausted_profile_moves_session_by_copying_transcript(
        self, tmp_path: Path, homes: dict[str, Path]
    ) -> None:
        router = _router(tmp_path, homes)
        await router._repo.set_pin(1, "claude", "a")
        _claude_transcript(homes["a"])
        await router._usage.upsert(_full(), profile="a")
        plan = await router.plan_turn(backend="claude", thread_id=1, session_id=SID)
        assert plan is not None
        assert (plan.profile, plan.session_id, plan.moved_from) == ("b", SID, "a")
        assert not plan.needs_handoff
        assert find_transcript("claude", homes["b"], SID) is not None
        assert await router._repo.get_pin(1) == ("claude", "b")

    async def test_missing_transcript_falls_back_to_handoff(
        self, tmp_path: Path, homes: dict[str, Path]
    ) -> None:
        router = _router(tmp_path, homes)
        await router._repo.set_pin(1, "claude", "a")
        await router._repo.set_block("a", int(NOW) + 60)
        plan = await router.plan_turn(backend="claude", thread_id=1, session_id=SID)
        assert plan is not None
        assert plan.session_id is None
        assert plan.needs_handoff
        assert plan.source_home == homes["a"]

    async def test_session_from_before_the_pool_moves_from_ambient(
        self, tmp_path: Path, homes: dict[str, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(homes["ambient"]))
        _claude_transcript(homes["ambient"])
        router = _router(tmp_path, homes)
        plan = await router.plan_turn(backend="claude", thread_id=7, session_id=SID)
        assert plan is not None
        assert plan.moved_from == RELAY_DEFAULT_LABEL
        assert plan.session_id == SID
        assert find_transcript("claude", homes["a"], SID) is not None

    async def test_pin_for_another_backend_is_ignored(
        self, tmp_path: Path, homes: dict[str, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(homes["ambient"]))
        router = _router(tmp_path, homes, strategy="priority")
        await router._repo.set_pin(1, "codex", "b")
        plan = await router.plan_turn(backend="claude", thread_id=1, session_id=None)
        assert plan is not None and plan.profile == "a"

    async def test_round_robin_cursor_is_persisted(
        self, tmp_path: Path, homes: dict[str, Path]
    ) -> None:
        router = _router(tmp_path, homes, strategy="round_robin")
        picks = []
        for thread in range(3):
            plan = await router.plan_turn(backend="claude", thread_id=thread, session_id=None)
            assert plan is not None
            picks.append(plan.profile)
        assert picks == ["a", "b", "a"]

    async def test_all_exhausted_is_flagged(self, tmp_path: Path, homes: dict[str, Path]) -> None:
        router = _router(tmp_path, homes)
        await router._repo.set_block("a", int(NOW) + 600)
        await router._repo.set_block("b", int(NOW) + 60)
        plan = await router.plan_turn(backend="claude", thread_id=1, session_id=None)
        assert plan is not None
        assert plan.selection.all_exhausted
        assert plan.profile == "b"


class TestFinishTurn:
    def _turn(self, profile: str = "a", **kw) -> AccountTurn:
        return AccountTurn(AccountBinding("claude", profile, {}), **kw)

    async def test_successful_turn_does_nothing(
        self, tmp_path: Path, homes: dict[str, Path]
    ) -> None:
        router = _router(tmp_path, homes)
        assert await router.finish_turn(self._turn()) is None
        assert await router._repo.get_blocks() == {}

    async def test_rejection_marks_until_resets_at_and_names_next(
        self, tmp_path: Path, homes: dict[str, Path]
    ) -> None:
        router = _router(tmp_path, homes)
        resets = int(NOW) + 900
        turn = self._turn(rejections=[RateLimitInfo("five_hour", "rejected", 1.0, resets)])
        failover = await router.finish_turn(turn)
        assert failover is not None
        assert (failover.profile, failover.blocked_until, failover.next_profile) == (
            "a",
            resets,
            "b",
        )
        assert failover.retry is False
        assert await router._repo.get_blocks() == {"a": resets}

    async def test_text_rejection_uses_cooldown(
        self, tmp_path: Path, homes: dict[str, Path]
    ) -> None:
        router = _router(tmp_path, homes, cooldown_seconds=120, retry_on_exhaustion=True)
        failover = await router.finish_turn(self._turn(error="You've hit your limit"))
        assert failover is not None
        assert failover.blocked_until == int(NOW) + 120
        assert failover.retry is True

    async def test_no_next_profile_when_everything_is_out(
        self, tmp_path: Path, homes: dict[str, Path]
    ) -> None:
        router = _router(tmp_path, homes, retry_on_exhaustion=True)
        await router._repo.set_block("b", int(NOW) + 60)
        failover = await router.finish_turn(self._turn(error="usage limit reached"))
        assert failover is not None
        assert failover.next_profile is None
        assert failover.retry is False

    async def test_next_turn_after_rejection_runs_on_the_other_profile(
        self, tmp_path: Path, homes: dict[str, Path]
    ) -> None:
        """Done-when from the issue: first profile rate-limited -> next turn on the second."""
        clock = Clock()
        router = _router(tmp_path, homes, clock=clock)
        first = await router.plan_turn(backend="claude", thread_id=1, session_id=None)
        assert first is not None and first.profile == "a"
        await router.finish_turn(
            self._turn(rejections=[RateLimitInfo("five_hour", "rejected", 1.0, int(NOW) + 300)])
        )
        second = await router.plan_turn(backend="claude", thread_id=2, session_id=None)
        assert second is not None and second.profile == "b"
        clock.now = NOW + 301  # window reset: priority returns to "a" for new sessions
        third = await router.plan_turn(backend="claude", thread_id=3, session_id=None)
        assert third is not None and third.profile == "a"


class TestStatuses:
    async def test_lists_every_profile(self, tmp_path: Path, homes: dict[str, Path]) -> None:
        router = _router(tmp_path, homes)
        await router.record_usage("a", [RateLimitInfo("five_hour", "allowed", 0.3, int(NOW) + 9)])
        await router._repo.set_block("b", int(NOW) + 60)
        rows = await router.statuses()
        assert [(r.backend, r.profile, r.unavailable_until) for r in rows] == [
            ("claude", "a", None),
            ("claude", "b", int(NOW) + 60),
        ]
        assert rows[0].usage[0].utilization == 0.3


async def test_removed_profile_is_still_the_transcript_source(
    tmp_path: Path, homes: dict[str, Path]
) -> None:
    """A pin to a profile no longer in the pool copies from that profile's stored home."""
    gone = tmp_path / "gone"
    gone.mkdir()
    _claude_transcript(gone)
    router = _router(tmp_path, homes)
    await router._repo.set_pin(1, "claude", "retired", str(gone))
    plan = await router.plan_turn(backend="claude", thread_id=1, session_id=SID)
    assert plan is not None
    assert (plan.profile, plan.moved_from, plan.session_id) == ("a", "retired", SID)
    assert find_transcript("claude", homes["a"], SID) is not None
    assert await router._repo.get_pin_home(1) == ("claude", "a", str(homes["a"]))


class TestReviewFollowUps:
    async def test_pinless_session_stays_on_the_inheriting_profile(
        self, tmp_path: Path, homes: dict[str, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A thread from before the pool keeps the relay's own login (and its cache)."""
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(homes["ambient"]))
        _claude_transcript(homes["ambient"])
        pool = PoolConfig(
            backend="claude",
            profiles=(ProfileSpec("work", str(homes["a"])), ProfileSpec("own")),
        )
        db = str(tmp_path / "s.db")
        router = AccountRouter(
            {"claude": pool}, AccountPoolRepository(db), UsageStatsRepository(db), clock=Clock()
        )
        assert router.ambient_profile("claude") == "own"
        plan = await router.plan_turn(backend="claude", thread_id=5, session_id=SID)
        assert plan is not None
        assert (plan.profile, plan.moved_from, plan.session_id) == ("own", None, SID)
        assert find_transcript("claude", homes["a"], SID) is None  # nothing copied
        # A brand-new thread still follows the strategy.
        fresh = await router.plan_turn(backend="claude", thread_id=6, session_id=None)
        assert fresh is not None and fresh.profile == "work"

    async def test_pinless_session_leaves_the_ambient_profile_once_exhausted(
        self, tmp_path: Path, homes: dict[str, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(homes["ambient"]))
        _claude_transcript(homes["ambient"])
        pool = PoolConfig(
            backend="claude",
            profiles=(ProfileSpec("work", str(homes["a"])), ProfileSpec("own")),
        )
        db = str(tmp_path / "s.db")
        router = AccountRouter(
            {"claude": pool}, AccountPoolRepository(db), UsageStatsRepository(db), clock=Clock()
        )
        await router._repo.set_block("own", int(NOW) + 60)
        plan = await router.plan_turn(backend="claude", thread_id=5, session_id=SID)
        assert plan is not None
        assert (plan.profile, plan.moved_from) == ("work", RELAY_DEFAULT_LABEL)
        assert find_transcript("claude", homes["a"], SID) is not None

    def test_profile_named_default_or_pointing_at_ambient_is_ambient(
        self, tmp_path: Path, homes: dict[str, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(homes["ambient"]))
        db = str(tmp_path / "s.db")
        explicit = PoolConfig(
            backend="claude",
            profiles=(ProfileSpec("x", str(homes["a"])), ProfileSpec("y", str(homes["ambient"]))),
        )
        named = PoolConfig(
            backend="claude",
            profiles=(ProfileSpec("x", str(homes["a"])), ProfileSpec("default", str(homes["b"]))),
        )
        none = PoolConfig(backend="claude", profiles=(ProfileSpec("x", str(homes["a"])),))
        for pool, want in ((explicit, "y"), (named, "default"), (none, None)):
            router = AccountRouter(
                {"claude": pool}, AccountPoolRepository(db), UsageStatsRepository(db)
            )
            assert router.ambient_profile("claude") == want

    async def test_model_scoped_rejection_does_not_block(
        self, tmp_path: Path, homes: dict[str, Path]
    ) -> None:
        router = _router(tmp_path, homes, retry_on_exhaustion=True)
        binding = AccountBinding("claude", "a", {})
        opus = RateLimitInfo("seven_day_opus", "rejected", 1.0, int(NOW) + 600_000)
        turn = AccountTurn(binding, rejections=[opus], error="You've hit your Opus limit")
        assert await router.finish_turn(turn) is None
        assert await router._repo.get_blocks() == {}

    async def test_context_limit_error_does_not_block(
        self, tmp_path: Path, homes: dict[str, Path]
    ) -> None:
        router = _router(tmp_path, homes, retry_on_exhaustion=True)
        turn = AccountTurn(AccountBinding("claude", "a", {}), error="Context limit reached")
        assert await router.finish_turn(turn) is None

    async def test_legacy_error_reset_time_is_used(
        self, tmp_path: Path, homes: dict[str, Path]
    ) -> None:
        router = _router(tmp_path, homes)
        resets = int(NOW) + 1234
        turn = AccountTurn(
            AccountBinding("claude", "a", {}), error=f"Claude AI usage limit reached|{resets}"
        )
        failover = await router.finish_turn(turn)
        assert failover is not None and failover.blocked_until == resets

    def test_ambient_home_honours_cli_env_overlay(self, tmp_path: Path) -> None:
        overlay = tmp_path / "overlay.env"
        overlay.write_text("# comment\nCLAUDE_CONFIG_DIR=/overlay/claude\n")
        env = {"CCDB_CLI_ENV_FILE": str(overlay), "CLAUDE_CONFIG_DIR": "/env/claude"}
        assert ambient_home("claude", env) == Path("/overlay/claude")
        assert ambient_home("claude", {"CCDB_CLI_ENV_FILE": str(tmp_path / "missing")}) == (
            Path.home() / ".claude"
        )
        # CodexRunner does not apply the overlay, so neither does its ambient home.
        overlay.write_text("CODEX_HOME=/overlay/codex\n")
        assert ambient_home("codex", {"CCDB_CLI_ENV_FILE": str(overlay)}) == Path.home() / ".codex"

    def test_env_credentials_are_warned_about(
        self, tmp_path: Path, homes: dict[str, Path], monkeypatch: pytest.MonkeyPatch, caplog
    ) -> None:
        import logging

        from claude_code_core.account_pool_config import ENV_VAR
        from claude_code_core.account_router import build_account_router

        pools = tmp_path / "pools.toml"
        pools.write_text(
            f'[pools.claude]\n[[pools.claude.profiles]]\nname = "a"\nconfig_dir = "{homes["a"]}"\n'
        )
        monkeypatch.setenv(ENV_VAR, str(pools))
        monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
        with caplog.at_level(logging.WARNING, logger="claude_code_core.account_router"):
            assert build_account_router(str(tmp_path / "s.db")) is not None
        assert "ANTHROPIC_API_KEY is set" in caplog.text
