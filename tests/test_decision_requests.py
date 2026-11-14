"""Decision requests: the format an agent must use to ask the person something (ADR-0024 v2).

The card has to be answerable by someone who has forgotten the whole matter, so the format is
enforced by deterministic checks — never by a model — and a bad request is refused with every
problem listed, for the agent to rewrite.
"""

from __future__ import annotations

import copy

import pytest

from claude_discord.database.decision_repo import DecisionRequestRepository
from claude_discord.database.models import init_db
from claude_discord.decision_request import (
    DETAIL_FIELDS,
    MAX_CARD_CHARS,
    DecisionRequestError,
    has_request,
    parse_request,
    refusal,
)

THREAD = 1567765910595440741
CHILD = 1567765910595440999

GOOD: dict = {
    "title": "hccjp を消す前に、中のデータを書き出して残すか",
    "context": "GA v2 本番の、HCCJP コミュニティ用の環境を11/17に削除する。消すとほぼ戻せない。",
    "options": [
        {
            "label": "A で消す",
            "effect": "追加作業なし。中身はほぼ自動テストで、人の受験記録は4件だけ。",
            "recommended": True,
        },
        {"label": "B 残す", "effect": "30分〜1時間の作業。保管費は月数十円。"},
    ],
    "deadline": "11/17の削除の前",
    "detail": {
        "about": "本番の hccjp 環境を削除する。",
        "now": "削除のPRは検証済みで、11/17以降に実行する。",
        "ask": "削除の前にデータを書き出すか。",
        "choices": "A 残さない / B 書き出す",
        "recommendation": "A。中身はほぼ自動テスト。",
        "materials": "| 置き場所 | 中身 |\n|---|---|\n| DB | 67件 |",
        "deadline": "11/17。決まらない間は削除しない。",
    },
}


def _good(**over) -> dict:
    body = copy.deepcopy(GOOD)
    body.update(over)
    return body


def _problems(body: dict, kind: str = "waiting") -> list[str]:
    with pytest.raises(DecisionRequestError) as caught:
        parse_request(body, kind=kind)
    return caught.value.problems


# -- the format -------------------------------------------------------------


def test_the_example_the_person_approved_is_accepted() -> None:
    spec = parse_request(_good(), kind="waiting")
    assert spec.kind == "waiting"
    assert spec.key == ""
    assert spec.title == GOOD["title"]
    assert [o.label for o in spec.options] == ["A で消す", "B 残す"]
    assert [o.recommended for o in spec.options] == [True, False]
    assert set(spec.detail) == set(DETAIL_FIELDS)
    card = spec.card()
    assert card["options"][0] == {
        "label": "A で消す",
        "effect": GOOD["options"][0]["effect"],
        "recommended": True,
    }


def test_text_is_trimmed() -> None:
    spec = parse_request(_good(title="  決めてほしいことを一文で書く  "), kind="waiting")
    assert spec.title == "決めてほしいことを一文で書く"


@pytest.mark.parametrize("field", ["title", "context", "options", "deadline", "detail"])
def test_every_part_of_the_card_is_required(field: str) -> None:
    body = _good()
    del body[field]
    assert any(field in p for p in _problems(body))


def test_every_problem_is_reported_at_once() -> None:
    body = _good(title="", deadline="")
    del body["context"]
    problems = _problems(body)
    assert len(problems) >= 3


def test_a_card_line_cannot_hold_a_paragraph() -> None:
    assert any("one line" in p for p in _problems(_good(context="一行目。\n二行目。")))


def test_a_card_that_is_too_long_is_refused() -> None:
    long_effect = "あ" * 99
    body = _good(
        options=[
            {"label": "A", "effect": long_effect, "recommended": True},
            {"label": "B", "effect": long_effect},
            {"label": "C", "effect": long_effect},
        ]
    )
    assert any(str(MAX_CARD_CHARS) in p for p in _problems(body))


def test_a_question_needs_choices_and_one_recommendation() -> None:
    one = _good(options=[GOOD["options"][0]])
    assert any("at least 2" in p for p in _problems(one))
    none_marked = _good(
        options=[{**o, "recommended": False} for o in GOOD["options"]],
    )
    assert any("recommended" in p for p in _problems(none_marked))
    two_marked = _good(options=[{**o, "recommended": True} for o in GOOD["options"]])
    assert any("recommended" in p for p in _problems(two_marked))


def test_more_than_three_choices_would_not_fit_the_card() -> None:
    options = [{"label": f"案{i}", "effect": "結果を一行で"} for i in range(4)]
    options[0]["recommended"] = True
    assert any("at most 3" in p for p in _problems(_good(options=options)))


def test_a_review_or_a_task_may_offer_a_single_answer() -> None:
    body = _good(options=[{"label": "やりました", "effect": "AIが続きを進める"}])
    assert parse_request(body, kind="action").options[0].label == "やりました"
    assert parse_request(body, kind="review").kind == "review"


def test_choices_need_distinct_labels() -> None:
    body = _good(options=[GOOD["options"][0], {**GOOD["options"][1], "label": "A で消す"}])
    assert any("distinct" in p for p in _problems(body))


def test_the_detail_needs_all_seven_parts() -> None:
    body = _good()
    del body["detail"]["materials"]
    assert any("materials" in p for p in _problems(body))
    body = _good()
    body["detail"]["extra"] = "x"
    assert any("extra" in p for p in _problems(body))


def test_an_unknown_field_is_refused_rather_than_ignored() -> None:
    assert any("recommend" in p for p in _problems(_good(recommend="A")))


def test_a_key_separates_two_questions_of_one_thread() -> None:
    assert parse_request(_good(key="hccjp-data"), kind="waiting").key == "hccjp-data"
    assert any("key" in p for p in _problems(_good(key="not ok!")))


def test_the_kind_must_be_one_the_person_acts_on() -> None:
    with pytest.raises(ValueError):
        parse_request(_good(), kind="done")


def test_has_request_tells_a_bare_mark_from_a_request() -> None:
    assert not has_request(None)
    assert not has_request({})
    assert not has_request({"from_thread": 1})
    assert has_request({"title": "x"})


def test_the_refusal_lists_problems_and_the_bar() -> None:
    with pytest.raises(DecisionRequestError) as caught:
        parse_request(_good(title=""), kind="waiting")
    body = refusal(caught.value)
    assert body["problems"]
    assert "forgotten" in body["format"]


# -- the store ----------------------------------------------------------------


@pytest.fixture
async def repo(tmp_path) -> DecisionRequestRepository:
    path = str(tmp_path / "sessions.db")
    await init_db(path)
    return DecisionRequestRepository(path)


def _spec(**over):
    return parse_request(_good(**over), kind="waiting")


async def test_a_request_is_stored_and_listed_open(repo) -> None:
    stored = await repo.put(THREAD, _spec(), origin="mark", source_thread_id=THREAD)
    (listed,) = await repo.list_open()
    assert listed.id == stored.id
    data = listed.as_dict()
    assert data["thread_id"] == str(THREAD)
    assert data["card"]["title"] == GOOD["title"]
    assert data["detail"]["materials"].startswith("| 置き場所")
    assert data["created_at"]


async def test_a_new_request_replaces_the_open_one_with_the_same_key(repo) -> None:
    await repo.put(THREAD, _spec(), origin="mark", source_thread_id=THREAD)
    await repo.put(THREAD, _spec(deadline="明日まで"), origin="mark", source_thread_id=THREAD)
    await repo.put(THREAD, _spec(key="other"), origin="mark", source_thread_id=THREAD)
    open_ = await repo.list_open()
    assert sorted((r.key, r.deadline) for r in open_) == [
        ("", "明日まで"),
        ("other", GOOD["deadline"]),
    ]


async def test_closing_by_thread_by_origin_and_by_id(repo) -> None:
    a = await repo.put(THREAD, _spec(), origin="mark", source_thread_id=THREAD)
    await repo.put(THREAD, _spec(key="b"), origin="mark", source_thread_id=THREAD)
    await repo.put(CHILD, _spec(), origin="mark", source_thread_id=CHILD)
    assert await repo.close(THREAD, reason="answered", request_id=a.id) == 1
    assert sorted((r.thread_id, r.key) for r in await repo.list_open()) == [
        (THREAD, "b"),
        (CHILD, ""),
    ]
    assert await repo.close(THREAD, reason="superseded", origin="mark") == 1
    assert [r.thread_id for r in await repo.list_open()] == [CHILD]
