"""The attention estimator, on fixed timestamps."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from claude_code_core.attention import (
    AttentionConfig,
    AttentionParams,
    HumanActivity,
    build_report,
    estimate_shares,
    local_day_bounds_utc,
)

T0 = datetime(2026, 10, 5, 1, 0, tzinfo=UTC)  # 10:00 in Tokyo
TOKYO = AttentionParams(timezone="Asia/Tokyo")


def act(
    minute: float,
    *,
    thread: str = "t1",
    chars: int = 10,
    author: str = "op",
    msg: str | None = None,
    at: datetime | None = None,
) -> HumanActivity:
    when = at if at is not None else T0 + timedelta(minutes=minute)
    return HumanActivity(
        frontend="discord",
        conversation_id=thread,
        author_id=author,
        occurred_at=when,
        message_id=msg or f"{author}-{thread}-{when.isoformat()}",
        char_count=chars,
        thread_title=f"title {thread}",
    )


def total(shares: list) -> float:
    return sum(s.minutes for s in shares)


class TestBursts:
    def test_single_message_costs_the_lead_in(self) -> None:
        assert total(estimate_shares([act(0)], TOKYO)) == pytest.approx(2.0)

    def test_messages_within_the_idle_gap_merge_into_one_burst(self) -> None:
        shares = estimate_shares([act(0), act(5), act(14)], TOKYO)
        assert {s.burst_index for s in shares} == {0}
        assert total(shares) == pytest.approx(14 + 2)

    def test_a_gap_of_exactly_idle_gap_still_merges(self) -> None:
        shares = estimate_shares([act(0), act(10)], TOKYO)
        assert total(shares) == pytest.approx(12)

    def test_a_longer_silence_starts_a_new_burst_with_its_own_lead_in(self) -> None:
        shares = estimate_shares([act(0), act(30)], TOKYO)
        assert {s.burst_index for s in shares} == {0, 1}
        assert total(shares) == pytest.approx(2 + 2)

    def test_bursts_span_threads_for_one_author(self) -> None:
        shares = estimate_shares([act(0, thread="a"), act(4, thread="b")], TOKYO)
        assert total(shares) == pytest.approx(4 + 2)

    def test_authors_are_estimated_independently(self) -> None:
        shares = estimate_shares([act(0, author="x"), act(4, author="y")], TOKYO)
        assert total(shares) == pytest.approx(2 + 2)

    def test_input_order_does_not_matter(self) -> None:
        items = [act(9), act(0), act(3)]
        assert total(estimate_shares(items, TOKYO)) == pytest.approx(11)

    def test_parameters_change_the_estimate(self) -> None:
        params = AttentionParams(idle_gap_minutes=3, lead_in_minutes=0, timezone="UTC")
        assert total(estimate_shares([act(0), act(5)], params)) == pytest.approx(0)


class TestProportionalSplit:
    def test_minutes_split_by_characters_per_thread(self) -> None:
        items = [act(0, thread="a", chars=30), act(8, thread="b", chars=10)]
        report = build_report(
            items, TOKYO, start=date(2026, 10, 5), end=date(2026, 10, 5), group_by="thread"
        )
        rows = {r["conversation_id"]: r["minutes"] for r in report["rows"]}
        assert rows == {"a": pytest.approx(7.5), "b": pytest.approx(2.5)}

    def test_zero_characters_fall_back_to_message_count(self) -> None:
        items = [
            act(0, thread="a", chars=0),
            act(2, thread="a", chars=0),
            act(4, thread="b", chars=0),
        ]
        report = build_report(
            items, TOKYO, start=date(2026, 10, 5), end=date(2026, 10, 5), group_by="thread"
        )
        rows = {r["conversation_id"]: r["minutes"] for r in report["rows"]}
        assert rows == {"a": pytest.approx(4.0), "b": pytest.approx(2.0)}

    def test_zero_char_message_in_a_written_burst_gets_no_minutes(self) -> None:
        shares = estimate_shares([act(0, chars=0), act(4, chars=20)], TOKYO)
        assert [s.minutes for s in shares] == [0, pytest.approx(6)]


class TestAttachmentWeight:
    def test_attachment_only_message_still_counts(self) -> None:
        shot = HumanActivity(
            "discord", "b", "op", T0 + timedelta(minutes=4), "shot", attachment_count=1
        )
        items = [act(0, thread="a", chars=50), shot]
        report = build_report(
            items, TOKYO, start=date(2026, 10, 5), end=date(2026, 10, 5), group_by="thread"
        )
        rows = {r["conversation_id"]: r["minutes"] for r in report["rows"]}
        assert rows == {"a": pytest.approx(3.0), "b": pytest.approx(3.0)}

    def test_attachment_weight_is_configurable(self) -> None:
        params = AttentionParams(timezone="UTC", attachment_weight=0)
        shot = HumanActivity(
            "discord", "b", "op", T0 + timedelta(minutes=4), "s", attachment_count=3
        )
        shares = estimate_shares([act(0, chars=50), shot], params)
        assert [s.minutes for s in shares] == [pytest.approx(6), 0]
        config = AttentionConfig.from_env({"CCDB_ATTENTION_ATTACHMENT_WEIGHT": "120"})
        assert config.params.attachment_weight == 120
        assert config.params.as_dict()["attachment_weight_chars"] == 120


class TestSources:
    def test_backfill_rows_can_be_left_out(self) -> None:
        backfilled = HumanActivity(
            "discord", "t1", "op", T0 + timedelta(minutes=4), "b", char_count=10, source="backfill"
        )
        items = [act(0), backfilled]
        day = date(2026, 10, 5)
        both = build_report(items, TOKYO, start=day, end=day)
        live = build_report(items, TOKYO, start=day, end=day, include_backfill=False)
        assert (both["total_minutes"], live["total_minutes"]) == (6.0, 2.0)
        assert both["sources"] == {"backfill": 1, "live": 1}
        assert live["sources"] == {"live": 1} and live["include_backfill"] is False

    def test_unknown_source_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            HumanActivity("discord", "t", "op", T0, "m", source="guess")


class TestDayBucketing:
    def test_a_burst_across_local_midnight_is_split_between_days(self) -> None:
        # 23:55 and 00:05 Tokyo time: one burst of 10 + 2 minutes.
        before = datetime(2026, 10, 4, 14, 55, tzinfo=UTC)
        items = [act(0, at=before, chars=10), act(0, at=before + timedelta(minutes=10), chars=30)]
        report = build_report(items, TOKYO, start=date(2026, 10, 4), end=date(2026, 10, 5))
        assert [(r["day"], r["minutes"]) for r in report["rows"]] == [
            ("2026-10-04", 3.0),
            ("2026-10-05", 9.0),
        ]

    def test_the_timezone_decides_the_day(self) -> None:
        moment = datetime(2026, 10, 4, 16, 0, tzinfo=UTC)
        utc = AttentionParams(timezone="UTC")
        assert utc.local_day(moment) == date(2026, 10, 4)
        assert TOKYO.local_day(moment) == date(2026, 10, 5)

    def test_messages_outside_the_range_are_not_counted(self) -> None:
        items = [act(0), act(0, at=T0 - timedelta(days=2))]
        report = build_report(items, TOKYO, start=date(2026, 10, 5), end=date(2026, 10, 5))
        assert report["total_messages"] == 1
        assert report["total_minutes"] == 2.0

    def test_years_outside_the_supported_window_are_rejected(self) -> None:
        with pytest.raises(ValueError):
            local_day_bounds_utc(date(1, 1, 1), date(1, 1, 2), TOKYO)
        with pytest.raises(ValueError):
            local_day_bounds_utc(date(9999, 12, 30), date(9999, 12, 31), TOKYO)

    def test_local_day_bounds(self) -> None:
        first, after = local_day_bounds_utc(date(2026, 10, 5), date(2026, 10, 5), TOKYO)
        assert first == datetime(2026, 10, 4, 15, 0, tzinfo=UTC)
        assert after == datetime(2026, 10, 5, 15, 0, tzinfo=UTC)

    def test_host_local_zone_is_the_default(self) -> None:
        params = AttentionParams()
        assert params.tz is None
        assert params.timezone_label().startswith("local")
        first, after = local_day_bounds_utc(date(2026, 10, 5), date(2026, 10, 5), params)
        assert after - first in (timedelta(hours=23), timedelta(hours=24), timedelta(hours=25))


class TestReport:
    def test_report_marks_itself_an_estimate_and_states_parameters(self) -> None:
        report = build_report([act(0)], TOKYO, start=date(2026, 10, 5), end=date(2026, 10, 5))
        assert report["estimate"] is True
        assert report["parameters"]["idle_gap_minutes"] == 10
        assert report["parameters"]["lead_in_minutes"] == 2
        assert report["parameters"]["timezone"] == "Asia/Tokyo"

    def test_day_rows_count_messages_and_threads(self) -> None:
        items = [act(0, thread="a"), act(1, thread="b"), act(2, thread="a")]
        report = build_report(items, TOKYO, start=date(2026, 10, 5), end=date(2026, 10, 5))
        assert report["rows"] == [
            {"day": "2026-10-05", "minutes": 4.0, "messages": 3, "threads": 2}
        ]

    def test_thread_rows_use_the_newest_title_and_sort_by_minutes(self) -> None:
        old = act(0, thread="a", chars=1)
        renamed = HumanActivity(
            frontend="discord",
            conversation_id="a",
            author_id="op",
            occurred_at=T0 + timedelta(minutes=1),
            message_id="m2",
            char_count=1,
            thread_title="renamed",
        )
        big = act(2, thread="b", chars=50)
        report = build_report(
            [old, renamed, big],
            TOKYO,
            start=date(2026, 10, 5),
            end=date(2026, 10, 5),
            group_by="thread",
        )
        assert [r["conversation_id"] for r in report["rows"]] == ["b", "a"]
        assert report["rows"][1]["title"] == "renamed"

    def test_author_filter(self) -> None:
        items = [act(0, author="x"), act(1, author="y")]
        report = build_report(
            items, TOKYO, start=date(2026, 10, 5), end=date(2026, 10, 5), author_id="x"
        )
        assert report["total_messages"] == 1
        assert report["author"] == "x"

    def test_invalid_group_by_and_range_are_rejected(self) -> None:
        with pytest.raises(ValueError):
            build_report([], TOKYO, start=date(2026, 10, 5), end=date(2026, 10, 5), group_by="x")
        with pytest.raises(ValueError):
            build_report([], TOKYO, start=date(2026, 10, 5), end=date(2026, 10, 4))


class TestValidation:
    def test_naive_timestamp_is_rejected(self) -> None:
        with pytest.raises(ValueError):
            act(0, at=datetime(2026, 10, 5))

    def test_timestamps_are_normalised_to_utc(self) -> None:
        tokyo_time = datetime.fromisoformat("2026-10-05T10:00:00+09:00")
        assert act(0, at=tokyo_time).occurred_at == T0
        assert act(0, at=tokyo_time).occurred_at.tzinfo == UTC

    @pytest.mark.parametrize(
        "kwargs",
        [{"idle_gap_minutes": 0}, {"lead_in_minutes": -1}, {"timezone": "Mars/Base"}],
    )
    def test_bad_parameters_are_rejected(self, kwargs: dict) -> None:
        with pytest.raises(ValueError):
            AttentionParams(**kwargs)


class TestConfig:
    def test_defaults_are_on_with_documented_parameters(self) -> None:
        config = AttentionConfig.from_env({})
        assert config.enabled is True
        assert config.params == AttentionParams()

    def test_env_overrides(self) -> None:
        config = AttentionConfig.from_env(
            {
                "CCDB_ATTENTION_ENABLED": "false",
                "CCDB_ATTENTION_IDLE_GAP_MINUTES": "15",
                "CCDB_ATTENTION_LEAD_IN_MINUTES": "1.5",
                "CCDB_ATTENTION_TIMEZONE": "Asia/Tokyo",
            }
        )
        assert config.enabled is False
        assert config.params == AttentionParams(15, 1.5, "Asia/Tokyo")

    @pytest.mark.parametrize(
        "env",
        [
            {"CCDB_ATTENTION_IDLE_GAP_MINUTES": "ten"},
            {"CCDB_ATTENTION_LEAD_IN_MINUTES": "-3"},
            {"CCDB_ATTENTION_TIMEZONE": "Nowhere/Land"},
            {"CCDB_ATTENTION_ATTACHMENT_WEIGHT": "-1"},
        ],
    )
    def test_malformed_settings_name_the_variable(self, env: dict[str, str]) -> None:
        with pytest.raises(ValueError, match="CCDB_ATTENTION"):
            AttentionConfig.from_env(env)
