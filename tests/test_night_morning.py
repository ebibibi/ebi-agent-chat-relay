"""The night-instruction preset, quiet hours and the morning brief (#1476)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from claude_discord.console.morning import build_morning_brief, clamp_hours, render_markdown
from claude_discord.console.night import (
    TEMPLATES,
    NightSettings,
    NightSettingsError,
    dump_night_settings,
    load_night_settings,
    night_template,
    parse_night_settings,
)

NOW = datetime(2026, 11, 24, 7, 0, tzinfo=UTC)


def _item(item_id: str, status: str, at: str = "2026-11-24T02:00:00Z", **extra: Any):
    return {
        "id": item_id,
        "thread_id": int(item_id[1:]),
        "title": f"Thread {item_id}",
        "status": status,
        "running": status == "running",
        "last_activity_at": at,
        "focus": 1,
        **extra,
    }


def _request(rid: int, thread: int, kind: str = "question", title: str | None = None):
    return {
        "id": rid,
        "thread_id": thread,
        "kind": kind,
        "card": {
            "title": title or f"Decide {rid}",
            "options": [{"label": "A"}, {"label": "B", "recommended": True}],
            "deadline": "Tue morning",
        },
        "created_at": "2026-11-24T01:00:00Z",
    }


# -- the template -------------------------------------------------------------


def test_template_names_what_agents_must_never_do_alone() -> None:
    ja = night_template("ja", env={})
    assert "送信・公開・課金・本番変更" in ja and "朝" in ja
    assert "send, publish, pay" in night_template("en", env={})
    assert night_template("xx", env={}) == TEMPLATES["en"]


def test_a_deployment_file_replaces_the_template(tmp_path) -> None:
    own = tmp_path / "night.md"
    own.write_text("my own night text\n", encoding="utf-8")
    assert night_template("ja", env={"CCDB_NIGHT_TEMPLATE_FILE": str(own)}) == "my own night text\n"
    missing = {"CCDB_NIGHT_TEMPLATE_FILE": str(tmp_path / "absent.md")}
    assert night_template("ja", env=missing) == TEMPLATES["ja"]


# -- quiet hours --------------------------------------------------------------


def test_defaults_leave_quiet_hours_off() -> None:
    settings = NightSettings()
    assert not settings.quiet_enabled
    assert not settings.in_quiet(NOW)
    assert load_night_settings(None) == settings
    assert load_night_settings("not json") == settings


def test_a_window_across_midnight(monkeypatch) -> None:
    monkeypatch.setenv("TZ", "UTC")
    import time

    time.tzset()
    try:
        s = parse_night_settings({"quiet_start": "23:00", "quiet_end": "07:00"})
        assert s.in_quiet(datetime(2026, 11, 23, 23, 30, tzinfo=UTC))
        assert s.in_quiet(datetime(2026, 11, 24, 6, 59, tzinfo=UTC))
        assert not s.in_quiet(datetime(2026, 11, 24, 7, 0, tzinfo=UTC))
        day = parse_night_settings({"quiet_start": "13:00", "quiet_end": "14:00"})
        assert day.in_quiet(datetime(2026, 11, 24, 13, 30, tzinfo=UTC))
        assert not day.in_quiet(datetime(2026, 11, 24, 14, 0, tzinfo=UTC))
    finally:
        monkeypatch.undo()
        time.tzset()


def test_urgent_conditions_still_ring() -> None:
    s = parse_night_settings(
        {
            "quiet_start": "23:00",
            "quiet_end": "07:00",
            "urgent_statuses": ["error"],
            "urgent_keywords": ["障害"],
        }
    )
    assert s.is_urgent({"status": "error", "title": "x"})
    assert s.is_urgent({"status": "waiting", "title": "本番障害の対応"})
    assert not s.is_urgent({"status": "waiting", "title": "ブログの下書き"})


def test_bad_settings_list_every_problem() -> None:
    with pytest.raises(NightSettingsError) as caught:
        parse_night_settings(
            {
                "quiet_start": "25:00",
                "urgent_statuses": ["done"],
                "morning_place": 3,
                "surprise": True,
            }
        )
    problems = " ".join(caught.value.problems)
    for word in ("quiet_start", "urgent_statuses", "morning_place", "surprise"):
        assert word in problems
    with pytest.raises(NightSettingsError):
        parse_night_settings({"quiet_start": "23:00"})  # without its end
    with pytest.raises(NightSettingsError):
        parse_night_settings([])


def test_a_partial_update_keeps_the_rest_and_null_clears() -> None:
    first = parse_night_settings(
        {"quiet_start": "23:00", "quiet_end": "07:00", "morning_at": "07:00"}
    )
    second = parse_night_settings({"morning_place": "Discord DM"}, first)
    assert second.quiet_start == "23:00" and second.morning_place == "Discord DM"
    cleared = parse_night_settings({"quiet_start": None, "quiet_end": None}, second)
    assert not cleared.quiet_enabled and cleared.morning_at == "07:00"
    assert load_night_settings(dump_night_settings(second)) == second


# -- the morning brief ----------------------------------------------------------


async def test_the_brief_has_done_decide_and_continuing() -> None:
    items = [
        _item("t1", "done"),
        _item("t2", "candidate"),
        _item("t3", "done", at="2026-11-20T02:00:00Z"),  # before the window
        _item("t4", "waiting"),
        _item("t5", "waiting"),
        _item("t6", "review"),
        _item("t7", "action"),
        _item("t8", "waiting"),
        _item("t9", "running"),
        _item("t10", "done", archived=True),
    ]
    requests = [
        _request(1, 4),
        _request(2, 5),
        _request(3, 6, kind="review"),
        _request(4, 7, kind="task"),
        _request(5, 8),
    ]

    async def situation(thread: int) -> dict[str, Any] | None:
        return {"doing": ["waiting for CI on PR 12"]} if thread == 9 else None

    brief = await build_morning_brief(
        items, requests, situation=situation, now=NOW, base_url="https://console.example"
    )
    assert [d["id"] for d in brief["done"]] == ["t1", "t2"]
    assert brief["done"][1]["candidate"] is True
    assert brief["done"][0]["link"] == "https://console.example/#focus/t1"
    assert len(brief["decide"]) == 3 and brief["decide_more"] == 2
    assert brief["decide"][0]["recommended"] == "B"
    assert brief["continuing"] == [
        {
            "id": "t9",
            "title": "Thread t9",
            "doing": "waiting for CI on PR 12",
            "link": "https://console.example/#focus/t9",
        }
    ]


async def test_a_calm_night_says_nothing_happened() -> None:
    brief = await build_morning_brief([], [], now=NOW)
    text = render_markdown(brief, "ja")
    assert text.count("- なし") == 3
    assert "朝の1通" in text
    en = render_markdown(brief, "en")
    assert "Morning brief" in en and en.count("- nothing") == 3


async def test_markdown_links_and_counts() -> None:
    brief = await build_morning_brief(
        [_item("t1", "candidate"), _item("t4", "waiting")], [_request(1, 4)], now=NOW
    )
    text = render_markdown(brief, "ja")
    assert "[Thread t1](/#focus/t1)（完了候補）" in text
    assert "[Decide 1](/#focus/t4) — 推奨: B" in text


async def test_an_unreadable_situation_does_not_break_the_brief() -> None:
    async def broken(thread: int) -> dict[str, Any] | None:
        raise RuntimeError("db gone")

    brief = await build_morning_brief([_item("t9", "running")], [], situation=broken, now=NOW)
    assert brief["continuing"][0]["doing"] is None


def test_hours_are_clamped() -> None:
    assert clamp_hours(None) == 24
    assert clamp_hours("x") == 24
    assert clamp_hours("0") == 1
    assert clamp_hours("9999") == 24 * 7
