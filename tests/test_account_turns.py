"""Account pools wired through the parser, runners, event processor, chat cog and /usage."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from claude_code_core.account_pool import (
    DEFAULT_PROFILE,
    AccountBinding,
    AccountTurn,
    PoolConfig,
    ProfileSpec,
    Selection,
    is_limit_error,
)
from claude_code_core.account_pool_repo import AccountPoolRepository
from claude_code_core.account_router import AccountRouter, Failover, ProfileStatus, TurnPlan
from claude_code_core.codex_runner import CodexRunner
from claude_code_core.parser import parse_line
from claude_code_core.runner import ClaudeRunner
from claude_code_core.session_repo import UsageStatsRepository
from claude_code_core.types import MessageType, RateLimitInfo, StreamEvent
from claude_discord.account_turns import (
    codex_windows,
    failover_notice,
    plan_account_turn,
    plan_notice,
    usage_lines,
)
from claude_discord.cogs.event_processor import EventProcessor, _completion_fields
from claude_discord.cogs.run_config import RunConfig

SID = "ec8eb879-174f-443c-83fd-51f63b962aae"
BINDING = AccountBinding("claude", "work", {"CLAUDE_CONFIG_DIR": "/profiles/work"})


# --- parser: unifiedWindows (measured on Claude Code 2.1.289) --------------------------


RATE_LIMIT_LINE = json.dumps(
    {
        "type": "rate_limit_event",
        "rate_limit_info": {
            "status": "allowed",
            "resetsAt": 1791194400,
            "rateLimitType": "five_hour",
            "overageStatus": "rejected",
            "isUsingOverage": False,
            "unifiedWindows": {
                "five_hour": {"utilization": 0.08, "resetsAt": 1791194400},
                "seven_day": {"utilization": 0.34, "resetsAt": 1791417600},
            },
        },
    }
)


class TestParserUnifiedWindows:
    def test_top_level_utilization_comes_from_its_window(self) -> None:
        event = parse_line(RATE_LIMIT_LINE)
        assert event is not None and event.rate_limit_info is not None
        assert event.rate_limit_info.utilization == pytest.approx(0.08)
        assert event.rate_limit_info.resets_at == 1791194400

    def test_every_window_is_exposed(self) -> None:
        event = parse_line(RATE_LIMIT_LINE)
        assert event is not None
        got = {w.rate_limit_type: (w.utilization, w.resets_at) for w in event.rate_limit_windows}
        assert got == {"five_hour": (0.08, 1791194400), "seven_day": (0.34, 1791417600)}

    def test_rejected_status_stays_on_the_triggering_window(self) -> None:
        data = json.loads(RATE_LIMIT_LINE)
        data["rate_limit_info"]["status"] = "rejected"
        data["rate_limit_info"]["rateLimitType"] = "seven_day"
        event = parse_line(json.dumps(data))
        assert event is not None
        status = {w.rate_limit_type: w.status for w in event.rate_limit_windows}
        assert status == {"five_hour": "allowed", "seven_day": "rejected"}

    def test_legacy_single_window_shape(self) -> None:
        line = json.dumps(
            {
                "type": "rate_limit_event",
                "rate_limit_info": {
                    "status": "allowed",
                    "rateLimitType": "five_hour",
                    "utilization": 0.5,
                    "resetsAt": 10,
                },
            }
        )
        event = parse_line(line)
        assert event is not None and event.rate_limit_info is not None
        assert event.rate_limit_info.utilization == 0.5
        assert event.rate_limit_windows == [event.rate_limit_info]


# --- runners: env injection survives clone() --------------------------------------------


class TestRunnerEnv:
    def test_claude_runner_injects_profile_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/ambient")
        runner = ClaudeRunner()
        assert runner._build_env()["CLAUDE_CONFIG_DIR"] == "/ambient"
        runner.account = BINDING
        assert runner._build_env()["CLAUDE_CONFIG_DIR"] == "/profiles/work"
        assert runner.clone(append_system_prompt="x")._build_env()["CLAUDE_CONFIG_DIR"] == (
            "/profiles/work"
        )

    def test_profile_wins_over_cli_env_overlay(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        overlay = tmp_path / "overlay.env"
        overlay.write_text("CLAUDE_CONFIG_DIR=/overlay\n")
        monkeypatch.setenv("CCDB_CLI_ENV_FILE", str(overlay))
        runner = ClaudeRunner()
        runner.account = BINDING
        assert runner._build_env()["CLAUDE_CONFIG_DIR"] == "/profiles/work"

    def test_codex_runner_injects_codex_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CODEX_HOME", raising=False)
        runner = CodexRunner()
        assert "CODEX_HOME" not in runner._build_env()
        runner.account = AccountBinding("codex", "cx", {"CODEX_HOME": "/codex/cx"})
        assert runner.clone()._build_env()["CODEX_HOME"] == "/codex/cx"

    def test_no_account_by_default(self) -> None:
        assert ClaudeRunner().account is None
        assert CodexRunner().account is None


# --- event processor: attribution, rejection recording, footer ---------------------------


def _rate_event(status: str = "allowed") -> StreamEvent:
    windows = [
        RateLimitInfo("five_hour", status, 0.9, 100),
        RateLimitInfo("seven_day", "allowed", 0.4, 200),
    ]
    return StreamEvent(
        message_type=MessageType.RATE_LIMIT_EVENT,
        rate_limit_info=windows[0],
        rate_limit_windows=windows,
    )


class TestEventProcessor:
    async def test_windows_are_stored_under_the_turn_profile(
        self, thread: MagicMock, runner: MagicMock, tmp_path: Path
    ) -> None:
        usage = UsageStatsRepository(str(tmp_path / "s.db"))
        runner.account = BINDING
        turn = AccountTurn(BINDING)
        config = RunConfig(
            thread=thread, runner=runner, prompt="p", usage_repo=usage, account_turn=turn
        )
        await EventProcessor(config).process(_rate_event())
        grouped = await usage.get_by_profile()
        assert sorted(i.rate_limit_type for i in grouped["work"]) == ["five_hour", "seven_day"]
        assert DEFAULT_PROFILE not in grouped
        assert not turn.exhausts(PoolConfig(backend="claude", profiles=()))

    async def test_without_a_pool_rows_go_to_default(
        self, thread: MagicMock, runner: MagicMock, tmp_path: Path
    ) -> None:
        usage = UsageStatsRepository(str(tmp_path / "s.db"))
        config = RunConfig(thread=thread, runner=runner, prompt="p", usage_repo=usage)
        await EventProcessor(config).process(_rate_event())
        assert len(await usage.get_latest()) == 2

    async def test_rejection_is_recorded_on_the_turn(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        turn = AccountTurn(BINDING)
        config = RunConfig(thread=thread, runner=runner, prompt="p", account_turn=turn)
        await EventProcessor(config).process(_rate_event("rejected"))
        assert turn.rejections and turn.rejections[0].status == "rejected"
        assert [r.rate_limit_type for r in turn.rejections] == ["five_hour"]

    async def test_terminal_error_is_recorded_on_the_turn(
        self, thread: MagicMock, runner: MagicMock
    ) -> None:
        turn = AccountTurn(BINDING)
        config = RunConfig(thread=thread, runner=runner, prompt="p", account_turn=turn)
        processor = EventProcessor(config)
        await processor.process(
            StreamEvent(
                message_type=MessageType.RESULT,
                is_complete=True,
                error="You've hit your limit · resets 3pm",
            )
        )
        assert turn.error is not None and is_limit_error(turn.error)

    def test_footer_names_the_account_only_with_a_pool(self) -> None:
        runner = ClaudeRunner(model="opus")
        event = StreamEvent(message_type=MessageType.RESULT)
        assert ("Account", "work") not in _completion_fields(event, runner)
        runner.account = BINDING
        assert ("Account", "work") in _completion_fields(event, runner)


# --- glue: notices, codex mapping, /usage lines, handoff --------------------------------


def _plan(**kw) -> TurnPlan:
    defaults: dict = {
        "binding": BINDING,
        "selection": Selection(profile="work", cursor=MagicMock(), reason="r"),
        "session_id": SID,
    }
    defaults.update(kw)
    return TurnPlan(**defaults)


class TestNotices:
    def test_quiet_when_nothing_changed(self) -> None:
        assert plan_notice(None) is None
        assert plan_notice(_plan()) is None

    def test_moved_with_copied_transcript(self) -> None:
        text = plan_notice(_plan(moved_from="personal"))
        assert text is not None and "`personal` → `work`" in text and "resuming" in text

    def test_moved_with_handoff(self) -> None:
        text = plan_notice(_plan(moved_from="personal", needs_handoff=True))
        assert text is not None and "fresh session" in text

    def test_all_exhausted(self) -> None:
        sel = Selection(profile="work", cursor=MagicMock(), reason="r", all_exhausted=True)
        text = plan_notice(_plan(selection=sel))
        assert text is not None and "at its limit" in text

    def test_failover_variants(self) -> None:
        assert "Retrying this turn on `b`" in failover_notice(Failover("a", 100, "b", True))
        assert "next message will use `b`" in failover_notice(Failover("a", 100, "b", False))
        assert "No other account" in failover_notice(Failover("a", 100, None, False))
        assert "<t:100:R>" in failover_notice(Failover("a", 100, None, False))


class TestCodexWindows:
    def test_maps_the_measured_payload(self) -> None:
        data = {
            "rateLimits": {
                "primary": {"usedPercent": 1, "windowDurationMins": 10080, "resetsAt": 1791581140},
                "secondary": {"usedPercent": 40, "windowDurationMins": 300, "resetsAt": 5},
                "rateLimitReachedType": None,
            }
        }
        got = [
            (w.rate_limit_type, w.utilization, w.resets_at, w.status) for w in codex_windows(data)
        ]
        assert got == [
            ("seven_day", 0.01, 1791581140, "allowed"),
            ("five_hour", 0.4, 5, "allowed"),
        ]

    def test_reached_window_is_rejected(self) -> None:
        data = {
            "rateLimits": {
                "primary": {"usedPercent": 100, "windowDurationMins": 300, "resetsAt": 9},
                "rateLimitReachedType": "primary",
            }
        }
        assert codex_windows(data)[0].status == "rejected"

    @pytest.mark.parametrize(
        "data", [None, {}, {"rateLimits": None}, {"rateLimits": {"primary": 1}}]
    )
    def test_garbage_is_empty(self, data: object) -> None:
        assert codex_windows(data) == []  # type: ignore[arg-type]

    def test_unusual_duration_keeps_minutes(self) -> None:
        data = {"rateLimits": {"primary": {"usedPercent": 5, "windowDurationMins": 45}}}
        assert codex_windows(data)[0].rate_limit_type == "45m"


class TestUsageLines:
    def test_renders_each_profile(self) -> None:
        rows = [
            ProfileStatus(
                "claude", "a", (RateLimitInfo("five_hour", "allowed", 0.5, 2_000),), None
            ),
            ProfileStatus("claude", "b", (), 1_500),
            ProfileStatus("codex", "cx", (RateLimitInfo("seven_day", "allowed", 0.2, 10),), None),
        ]
        text = "\n".join(usage_lines(rows, now=1_000))
        assert "__**claude**__" in text and "__**codex**__" in text
        assert "**a** — ✅ available" in text
        assert "**50%** five_hour" in text
        assert "**b** — ⛔ exhausted" in text and "<t:1500:R>" in text
        # cx's only window already reset, so it reports no live data
        assert text.count("no usage reported yet") == 2


class TestPlanAccountTurn:
    async def test_no_router_passes_through(self) -> None:
        assert await plan_account_turn(
            None, backend="claude", thread_id=1, session_id=SID, prompt="hi"
        ) == (None, SID, "hi")

    async def test_handoff_prompt_when_transcript_cannot_move(self, tmp_path: Path) -> None:
        source = tmp_path / "a"
        transcript = source / "projects" / "-repo" / f"{SID}.jsonl"
        transcript.parent.mkdir(parents=True)
        lines = [
            {"type": "user", "message": {"content": "remember PELICAN-42"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "OK"}]}},
        ]
        transcript.write_text("\n".join(json.dumps(x) for x in lines))
        router = MagicMock()
        router.plan_turn = AsyncMock(
            return_value=_plan(
                session_id=None, moved_from="a", needs_handoff=True, source_home=source
            )
        )
        plan, session_id, prompt = await plan_account_turn(
            router, backend="claude", thread_id=1, session_id=SID, prompt="what was it?"
        )
        assert plan is not None and session_id is None
        assert "PELICAN-42" in prompt and prompt.endswith("what was it?")
        assert "[Account switch session handoff: Claude (a) → Claude (work)]" in prompt


# --- chat cog: plan, bind, fail over, retry once -----------------------------------------


def _router(tmp_path: Path, retry: bool) -> AccountRouter:
    homes = {}
    for name in ("a", "b"):
        homes[name] = tmp_path / name
        homes[name].mkdir()
    pool = PoolConfig(
        backend="claude",
        profiles=(ProfileSpec("a", str(homes["a"])), ProfileSpec("b", str(homes["b"]))),
        retry_on_exhaustion=retry,
    )
    db = str(tmp_path / "s.db")
    return AccountRouter({"claude": pool}, AccountPoolRepository(db), UsageStatsRepository(db))


class _Status:
    _stall_hard = 300

    async def set_thinking(self) -> None:
        return None


class _StopView:
    def set_message(self, message: object) -> None:
        return None

    async def disable(self, message: object | None = None) -> None:
        return None


def _cog(monkeypatch: pytest.MonkeyPatch, router: AccountRouter, runs: list[RunConfig]):
    import claude_discord.cogs.claude_chat as chat_mod
    from claude_discord.cogs.claude_chat import ClaudeChatCog

    async def fake_run(config: RunConfig) -> None:
        runs.append(config)
        assert config.account_turn is not None
        if len(runs) == 1:
            config.account_turn.error = "You've hit your limit · resets 3pm"

    monkeypatch.setattr(chat_mod, "run_claude_with_config", fake_run)
    monkeypatch.setattr(chat_mod, "StatusManager", lambda *a, **k: _Status())
    monkeypatch.setattr(chat_mod, "StopView", lambda *a, **k: _StopView())
    repo = MagicMock()
    repo.get = AsyncMock(return_value=None)
    cog = ClaudeChatCog(bot=MagicMock(), repo=repo, runner=MagicMock(), account_router=router)
    cog._get_dashboard = lambda: None  # type: ignore[method-assign]
    cog._current_backend_name = AsyncMock(return_value="claude")  # type: ignore[method-assign]

    async def build_runner(**kwargs: object) -> MagicMock:
        runner = MagicMock()
        runner.command = "claude"
        return runner

    cog._build_runner_for_thread = build_runner  # type: ignore[method-assign]
    cog._get_current_model = AsyncMock(return_value=None)  # type: ignore[method-assign]
    cog._get_allowed_tools = AsyncMock(return_value=None)  # type: ignore[method-assign]
    cog._get_current_effort = AsyncMock(return_value=None)  # type: ignore[method-assign]
    return cog


def _thread() -> MagicMock:
    thread = MagicMock(spec=discord.Thread)
    thread.id = 77
    thread.send = AsyncMock()
    return thread


class TestChatCogFailover:
    async def test_retry_on_exhaustion_reruns_once_on_the_next_profile(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        runs: list[RunConfig] = []
        cog = _cog(monkeypatch, _router(tmp_path, retry=True), runs)
        thread = _thread()
        await cog._run_claude(MagicMock(), thread, "do the thing", None)
        assert [r.runner.account.profile for r in runs] == ["a", "b"]
        assert [r.prompt for r in runs] == ["do the thing", "do the thing"]
        sent = " ".join(str(c.args[0]) for c in thread.send.call_args_list if c.args)
        assert "Retrying this turn on `b`" in sent

    async def test_default_only_tells_the_user(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        runs: list[RunConfig] = []
        cog = _cog(monkeypatch, _router(tmp_path, retry=False), runs)
        thread = _thread()
        await cog._run_claude(MagicMock(), thread, "do the thing", None)
        assert [r.runner.account.profile for r in runs] == ["a"]
        sent = " ".join(str(c.args[0]) for c in thread.send.call_args_list if c.args)
        assert "next message will use `b`" in sent
        # And the next message really does.
        await cog._run_claude(MagicMock(), thread, "again", None)
        assert runs[-1].runner.account.profile == "b"

    async def test_without_router_no_account_is_bound(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        runs: list[RunConfig] = []
        cog = _cog(monkeypatch, _router(tmp_path, retry=False), runs)
        cog._account_router = None
        runs_before = len(runs)

        async def plain_run(config: RunConfig) -> None:
            runs.append(config)

        import claude_discord.cogs.claude_chat as chat_mod

        monkeypatch.setattr(chat_mod, "run_claude_with_config", plain_run)
        await cog._run_claude(MagicMock(), _thread(), "hi", None)
        assert runs[runs_before].account_turn is None


# --- /usage --------------------------------------------------------------------------


class TestUsageCommand:
    async def test_lists_profiles_when_pools_are_configured(self, tmp_path: Path) -> None:
        from claude_discord.cogs.session_manage import SessionManageCog

        router = _router(tmp_path, retry=False)
        await router.record_usage("a", [RateLimitInfo("five_hour", "allowed", 0.3, 4_000_000_000)])
        cog = SessionManageCog(bot=MagicMock(), repo=MagicMock(), account_router=router)
        interaction = MagicMock()
        interaction.response.send_message = AsyncMock()
        await cog.usage_show.callback(cog, interaction)  # type: ignore[arg-type]
        embed = interaction.response.send_message.call_args.kwargs["embed"]
        assert embed.title == "📊 Account Pool Usage"
        assert "**a** — ✅ available" in embed.description
        assert "**b** — ✅ available" in embed.description
        assert "**30%** five_hour" in embed.description


class TestPlanningFailure:
    async def test_planning_error_runs_on_the_relay_login(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        runs: list[RunConfig] = []
        router = _router(tmp_path, retry=False)
        router.plan_turn = AsyncMock(side_effect=RuntimeError("db locked"))  # type: ignore[method-assign]
        cog = _cog(monkeypatch, router, runs)

        async def plain_run(config: RunConfig) -> None:
            runs.append(config)

        import claude_discord.cogs.claude_chat as chat_mod

        monkeypatch.setattr(chat_mod, "run_claude_with_config", plain_run)
        await cog._run_claude(MagicMock(), _thread(), "hi", "abc")
        assert len(runs) == 1
        assert runs[0].account_turn is None
        assert runs[0].session_id == "abc"


async def test_concurrent_new_threads_take_distinct_round_robin_slots(tmp_path: Path) -> None:
    import asyncio

    homes = []
    for name in ("a", "b"):
        (tmp_path / name).mkdir()
        homes.append(ProfileSpec(name, str(tmp_path / name)))
    pool = PoolConfig(backend="claude", profiles=tuple(homes), strategy="round_robin")
    db = str(tmp_path / "s.db")
    router = AccountRouter({"claude": pool}, AccountPoolRepository(db), UsageStatsRepository(db))
    plans = await asyncio.gather(
        *(router.plan_turn(backend="claude", thread_id=i, session_id=None) for i in range(4))
    )
    assert sorted(p.profile for p in plans if p is not None) == ["a", "a", "b", "b"]


class TestResultSinkOnRetry:
    async def test_sink_fires_once_with_the_retry_outcome(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        runs: list[RunConfig] = []
        cog = _cog(monkeypatch, _router(tmp_path, retry=True), runs)
        import claude_discord.cogs.claude_chat as chat_mod

        async def run(config: RunConfig) -> None:
            runs.append(config)
            assert config.account_turn is not None and config.result_sink is not None
            if len(runs) == 1:
                config.account_turn.error = "You've hit your limit"
                await config.result_sink(None, "You've hit your limit")
            else:
                await config.result_sink("done", None)

        monkeypatch.setattr(chat_mod, "run_claude_with_config", run)
        outcomes: list[tuple[str | None, str | None]] = []

        async def sink(text: str | None, error: str | None) -> None:
            outcomes.append((text, error))

        await cog._run_claude(MagicMock(), _thread(), "x", None, result_sink=sink)
        assert outcomes == [("done", None)]

    async def test_sink_still_fires_without_a_retry(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        runs: list[RunConfig] = []
        cog = _cog(monkeypatch, _router(tmp_path, retry=False), runs)
        import claude_discord.cogs.claude_chat as chat_mod

        async def run(config: RunConfig) -> None:
            runs.append(config)
            assert config.result_sink is not None
            await config.result_sink("ok", None)

        monkeypatch.setattr(chat_mod, "run_claude_with_config", run)
        outcomes: list[tuple[str | None, str | None]] = []

        async def sink(text: str | None, error: str | None) -> None:
            outcomes.append((text, error))

        await cog._run_claude(MagicMock(), _thread(), "x", None, result_sink=sink)
        assert outcomes == [("ok", None)]


async def test_codex_usage_probe_task_is_referenced(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import asyncio

    import claude_discord.cogs.claude_chat as chat_mod

    started = asyncio.Event()
    release = asyncio.Event()

    async def probe(*args: object) -> None:
        started.set()
        await release.wait()

    monkeypatch.setattr(chat_mod, "refresh_codex_usage", probe)
    router = _router(tmp_path, retry=False)
    cog = _cog(monkeypatch, router, [])
    cog._factory = MagicMock(codex_command="codex")
    turn = AccountTurn(AccountBinding("codex", "cx", {}))
    await cog._finish_account_turn(_thread(), turn)
    await started.wait()
    assert len(cog._background_tasks) == 1
    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert not cog._background_tasks
