"""Pure selection logic for account pools (claude_code_core.account_pool)."""

from __future__ import annotations

import pytest

from claude_code_core.account_pool import (
    ACCOUNT_SCOPE,
    AccountBinding,
    AccountTurn,
    LimitError,
    PoolConfig,
    PoolCursor,
    ProfileSpec,
    binding_for,
    is_limit_error,
    parse_limit_error,
    rejection_exhausts,
    select_profile,
    unavailable_until,
    utilization,
)
from claude_code_core.types import RateLimitInfo

NOW = 1_000_000.0
LATER = int(NOW) + 3_600
EARLIER = int(NOW) - 60


def _pool(strategy: str = "priority", assign: str = "session", n: int = 3, **kw) -> PoolConfig:
    names = ["a", "b", "c", "d"][:n]
    return PoolConfig(
        backend="claude",
        profiles=tuple(ProfileSpec(name, f"/p/{name}") for name in names),
        strategy=strategy,
        assign=assign,
        **kw,
    )


def _w(kind: str, util: float, resets: int = LATER, status: str = "allowed") -> RateLimitInfo:
    return RateLimitInfo(rate_limit_type=kind, status=status, utilization=util, resets_at=resets)


FULL = [_w("five_hour", 0.99)]


class TestUnavailableUntil:
    def test_no_data_is_available(self) -> None:
        assert unavailable_until(_pool(), [], None, NOW) is None

    def test_below_switch_at_is_available(self) -> None:
        assert unavailable_until(_pool(), [_w("five_hour", 0.94)], None, NOW) is None

    def test_at_switch_at_is_exhausted_until_reset(self) -> None:
        assert unavailable_until(_pool(), [_w("five_hour", 0.95)], None, NOW) == LATER

    def test_window_already_reset_is_ignored(self) -> None:
        assert unavailable_until(_pool(), [_w("five_hour", 1.0, resets=EARLIER)], None, NOW) is None

    def test_unlisted_window_utilization_is_ignored(self) -> None:
        pool = _pool(windows=("five_hour",))
        assert unavailable_until(pool, [_w("seven_day", 0.99)], None, NOW) is None

    def test_unlisted_model_window_rejection_does_not_exhaust(self) -> None:
        """Running out of Opus must not take the login out for Sonnet turns."""
        rejected = [_w("seven_day_opus", 1.0, resets=LATER + 600_000, status="rejected")]
        assert unavailable_until(_pool(), rejected, None, NOW) is None

    def test_listed_model_window_rejection_exhausts(self) -> None:
        pool = _pool(windows=("five_hour", "seven_day", "seven_day_opus"))
        rejected = [_w("seven_day_opus", 1.0, status="rejected")]
        assert unavailable_until(pool, rejected, None, NOW) == LATER

    def test_account_wide_rejection_exhausts_even_if_unlisted(self) -> None:
        pool = _pool(windows=("five_hour",))
        rejected = [_w("seven_day", 0.5, status="rejected")]
        assert unavailable_until(pool, rejected, None, NOW) == LATER

    def test_rejection_marker_counts_until_it_expires(self) -> None:
        assert unavailable_until(_pool(), [], LATER, NOW) == LATER
        assert unavailable_until(_pool(), [], EARLIER, NOW) is None

    def test_latest_of_several_reasons_wins(self) -> None:
        usage = [_w("five_hour", 0.99, resets=LATER), _w("seven_day", 0.99, resets=LATER + 99)]
        assert unavailable_until(_pool(), usage, LATER + 5, NOW) == LATER + 99

    def test_custom_switch_at(self) -> None:
        assert unavailable_until(_pool(switch_at=0.5), [_w("five_hour", 0.5)], None, NOW) == LATER


class TestUtilization:
    def test_highest_live_window(self) -> None:
        usage = [_w("five_hour", 0.2), _w("seven_day", 0.7)]
        assert utilization(_pool(), usage, NOW) == pytest.approx(0.7)

    def test_unknown_is_zero_and_expired_ignored(self) -> None:
        assert utilization(_pool(), [], NOW) == 0.0
        assert utilization(_pool(), [_w("five_hour", 0.9, resets=EARLIER)], NOW) == 0.0


class TestPriority:
    def test_first_profile_when_all_available(self) -> None:
        sel = select_profile(_pool(), {}, {}, NOW)
        assert sel.profile == "a"
        assert not sel.all_exhausted

    def test_drains_in_order(self) -> None:
        sel = select_profile(_pool(), {"a": FULL}, {}, NOW)
        assert sel.profile == "b"
        assert sel.exhausted == ("a",)
        sel = select_profile(_pool(), {"a": FULL, "b": FULL}, {}, NOW)
        assert sel.profile == "c"

    def test_returns_to_earlier_profile_once_it_resets(self) -> None:
        usage = {"a": [_w("five_hour", 0.99, resets=int(NOW) + 10)]}
        assert select_profile(_pool(), usage, {}, NOW).profile == "b"
        assert select_profile(_pool(), usage, {}, NOW + 11).profile == "a"

    def test_session_stays_on_its_profile_while_available(self) -> None:
        sel = select_profile(_pool(), {}, {}, NOW, current="b")
        assert sel.profile == "b"
        assert "session" in sel.reason

    def test_assign_turn_returns_to_first_even_with_a_pinned_session(self) -> None:
        sel = select_profile(_pool(assign="turn"), {}, {}, NOW, current="b")
        assert sel.profile == "a"

    def test_session_moves_when_its_profile_is_exhausted(self) -> None:
        sel = select_profile(_pool(), {"b": FULL}, {}, NOW, current="b")
        assert sel.profile == "a"


class TestSticky:
    def test_first_choice_becomes_sticky(self) -> None:
        sel = select_profile(_pool("sticky"), {}, {}, NOW)
        assert sel.profile == "a"
        assert sel.cursor.sticky == "a"

    def test_switches_on_exhaustion_and_does_not_return(self) -> None:
        pool = _pool("sticky")
        usage = {"a": [_w("five_hour", 0.99, resets=int(NOW) + 10)]}
        sel = select_profile(pool, usage, {}, NOW, cursor=PoolCursor(sticky="a"))
        assert sel.profile == "b"
        assert sel.cursor.sticky == "b"
        # "a" has reset, but sticky stays on "b".
        later = select_profile(pool, usage, {}, NOW + 11, cursor=sel.cursor)
        assert later.profile == "b"
        assert later.cursor.sticky == "b"

    def test_moves_forward_from_the_sticky_profile_wrapping_around(self) -> None:
        pool = _pool("sticky")
        sel = select_profile(pool, {"c": FULL}, {}, NOW, cursor=PoolCursor(sticky="c"))
        assert sel.profile == "a"

    def test_skips_exhausted_successors(self) -> None:
        pool = _pool("sticky")
        sel = select_profile(pool, {"a": FULL, "b": FULL}, {}, NOW, cursor=PoolCursor(sticky="a"))
        assert sel.profile == "c"

    def test_unknown_sticky_name_starts_from_the_top(self) -> None:
        sel = select_profile(_pool("sticky"), {}, {}, NOW, cursor=PoolCursor(sticky="gone"))
        assert sel.profile == "a"


class TestRoundRobin:
    def test_rotates_per_new_session_and_persists_cursor(self) -> None:
        pool = _pool("round_robin")
        cursor = PoolCursor()
        picks = []
        for _ in range(4):
            sel = select_profile(pool, {}, {}, NOW, cursor=cursor)
            picks.append(sel.profile)
            cursor = sel.cursor
        assert picks == ["a", "b", "c", "a"]
        assert cursor.rr_next == 1

    def test_skips_exhausted_profiles(self) -> None:
        sel = select_profile(_pool("round_robin"), {"b": FULL}, {}, NOW, cursor=PoolCursor(1))
        assert sel.profile == "c"
        assert sel.cursor.rr_next == 0

    def test_existing_session_does_not_rotate(self) -> None:
        cursor = PoolCursor(rr_next=2)
        sel = select_profile(_pool("round_robin"), {}, {}, NOW, current="a", cursor=cursor)
        assert sel.profile == "a"
        assert sel.cursor == cursor

    def test_assign_turn_rotates_every_turn(self) -> None:
        sel = select_profile(
            _pool("round_robin", assign="turn"), {}, {}, NOW, current="a", cursor=PoolCursor(1)
        )
        assert sel.profile == "b"

    def test_cursor_beyond_pool_size_wraps(self) -> None:
        sel = select_profile(_pool("round_robin"), {}, {}, NOW, cursor=PoolCursor(rr_next=7))
        assert sel.profile == "b"


class TestMostHeadroom:
    def test_lowest_utilization_wins(self) -> None:
        usage = {
            "a": [_w("five_hour", 0.6)],
            "b": [_w("five_hour", 0.2)],
            "c": [_w("seven_day", 0.4)],
        }
        assert select_profile(_pool("most_headroom"), usage, {}, NOW).profile == "b"

    def test_ties_go_to_pool_order(self) -> None:
        usage = {
            "a": [_w("five_hour", 0.3)],
            "b": [_w("five_hour", 0.3)],
            "c": [_w("five_hour", 0.3)],
        }
        assert select_profile(_pool("most_headroom"), usage, {}, NOW).profile == "a"

    def test_unknown_counts_as_full_headroom(self) -> None:
        usage = {"a": [_w("five_hour", 0.1)]}
        assert select_profile(_pool("most_headroom"), usage, {}, NOW).profile == "b"

    def test_worst_window_decides(self) -> None:
        usage = {
            "a": [_w("five_hour", 0.1), _w("seven_day", 0.9)],
            "b": [_w("five_hour", 0.5), _w("seven_day", 0.5)],
        }
        assert select_profile(_pool("most_headroom", n=2), usage, {}, NOW).profile == "b"

    def test_exhausted_profile_is_never_chosen(self) -> None:
        usage = {"a": [_w("five_hour", 0.1)], "b": [_w("five_hour", 0.2)]}
        sel = select_profile(_pool("most_headroom", n=2), usage, {"a": LATER}, NOW)
        assert sel.profile == "b"


class TestAllExhausted:
    def test_picks_the_one_that_resets_first(self) -> None:
        usage = {
            "a": [_w("five_hour", 0.99, resets=LATER + 100)],
            "b": [_w("five_hour", 0.99, resets=LATER)],
            "c": [_w("five_hour", 0.99, resets=LATER + 50)],
        }
        sel = select_profile(_pool(), usage, {}, NOW)
        assert sel.all_exhausted
        assert sel.profile == "b"
        assert sel.exhausted == ("a", "b", "c")

    def test_cursor_is_not_moved(self) -> None:
        cursor = PoolCursor(rr_next=2, sticky="a")
        sel = select_profile(_pool("round_robin", n=1), {"a": FULL}, {}, NOW, cursor=cursor)
        assert sel.cursor == cursor


class TestBindingAndTurn:
    def test_binding_injects_the_backend_env_var(self) -> None:
        assert binding_for(_pool(), "b").env == {"CLAUDE_CONFIG_DIR": "/p/b"}
        codex = PoolConfig(backend="codex", profiles=(ProfileSpec("x", "/h/x"),))
        assert binding_for(codex, "x").env == {"CODEX_HOME": "/h/x"}

    def test_inheriting_profile_injects_nothing(self) -> None:
        pool = PoolConfig(backend="claude", profiles=(ProfileSpec("own"),))
        assert binding_for(pool, "own").env == {}

    def test_unknown_profile_raises(self) -> None:
        with pytest.raises(KeyError):
            binding_for(_pool(), "zzz")

    @pytest.mark.parametrize(
        ("text", "scope", "resets"),
        [
            # Claude Code 2.1.289 wording ("You've hit your ${label}").
            ("You've hit your limit · resets 3pm (Asia/Tokyo)", ACCOUNT_SCOPE, None),
            ("You\u2019ve hit your limit", ACCOUNT_SCOPE, None),
            ("You've hit your session limit · resets 7pm", "five_hour", None),
            ("You've hit your weekly limit · resets Mon 9am", "seven_day", None),
            ("You've hit your Opus limit · resets Mon 9am", "seven_day_opus", None),
            ("You've hit your Sonnet limit", "seven_day_sonnet", None),
            ("You've hit your fast limit · resets in 5m", "fast", None),
            ("You've hit your monthly spend limit.", ACCOUNT_SCOPE, None),
            ("You've hit your team's shared budget. /model to switch models.", ACCOUNT_SCOPE, None),
            ("You're out of usage credits. Switch to another model", ACCOUNT_SCOPE, None),
            ("Claude AI usage limit reached|1791194400", ACCOUNT_SCOPE, 1791194400),
            # codex-cli 0.160.0 wording.
            ("You've hit your usage limit. Upgrade to Pro", ACCOUNT_SCOPE, None),
            (
                "You've hit your usage limit for gpt-5.6-sol. Try again later",
                "model:gpt-5.6-sol",
                None,
            ),
            ("You hit your spend cap set in your workspace.", ACCOUNT_SCOPE, None),
        ],
    )
    def test_limit_errors_and_their_scope(self, text: str, scope: str, resets: int | None) -> None:
        assert parse_limit_error(text) == LimitError(scope, resets)
        assert is_limit_error(text)

    @pytest.mark.parametrize(
        "text",
        [
            None,
            "",
            "API Error: 500 overloaded",
            "Timed out after 5s",
            "Rate limit reached for requests per min. Please try again in 2s.",
            # Strings in the Claude Code 2.1.289 binary that are not quota limits.
            "Context limit reached · /compact or /clear to continue",
            "Budget limit reached ($5.00 of $5); stopping",
            "Concurrent subagent limit reached. You can run 4 subagents at once.",
            # codex-cli 0.160.0.
            "Agent depth limit reached. Solve the task yourself.",
            "Responses websocket connection limit reached (60 minutes).",
        ],
    )
    def test_other_errors_are_not(self, text: str | None) -> None:
        assert parse_limit_error(text) is None

    def test_turn_exhausts_by_scope(self) -> None:
        pool = _pool()
        binding = AccountBinding(backend="claude", profile="a")
        assert not AccountTurn(binding).exhausts(pool)
        assert AccountTurn(binding, rejections=[_w("five_hour", 1.0, status="rejected")]).exhausts(
            pool
        )
        assert AccountTurn(binding, error="You've hit your limit").exhausts(pool)
        assert AccountTurn(binding, error="You've hit your weekly limit").exhausts(pool)
        opus = [_w("seven_day_opus", 1.0, status="rejected")]
        assert not AccountTurn(binding, rejections=opus).exhausts(pool)
        assert not AccountTurn(binding, error="You've hit your Opus limit").exhausts(pool)
        assert not AccountTurn(binding, error="You've hit your fast limit").exhausts(pool)
        assert not AccountTurn(binding, error="Context limit reached").exhausts(pool)

    def test_rejection_exhausts_rule(self) -> None:
        pool = _pool(windows=("five_hour",))
        assert rejection_exhausts(pool, ACCOUNT_SCOPE)
        assert rejection_exhausts(pool, "five_hour")
        assert rejection_exhausts(pool, "seven_day")  # account-wide, even unlisted
        assert not rejection_exhausts(pool, "seven_day_opus")
        assert not rejection_exhausts(pool, "model:gpt-5")
