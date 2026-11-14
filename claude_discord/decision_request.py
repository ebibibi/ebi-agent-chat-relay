"""Decision requests: what an agent asks the person, in a form they can answer cold.

The first secretary mode built its cards from a cheap model's one-line summary of each
thread's last message. The person could not answer them: the last message of a thread is
usually a usage footer or a CI notice, and a summary of text with no explanation has no
explanation either (ADR-0024). What worked in the trial was the opposite: the agent that
asked the question writes it out, for someone who has forgotten the whole matter.

So an agent that hands the person a decision sends it in two layers:

* the **card** — always shown, at most six lines, enough to answer on its own: what to
  decide, what it is about, up to three choices (each one line, one of them recommended),
  and the deadline;
* the **detail** — folded: the seven parts of a full decision request, including the
  material to decide with (the draft text, the diff, the numbers).

Everything here is a deterministic check. No model judges a request: a request that misses
a part, runs long or offers nothing to choose is refused with every problem listed, so the
agent rewrites it in the same turn.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

#: The markers that hand the next move to the person; a request carries one of them.
KINDS = ("waiting", "review", "action")

#: The folded full text, in the order the Console shows it.
DETAIL_FIELDS = (
    "about",  # what this is about, from zero
    "now",  # what is happening and why it is asked now
    "ask",  # what to decide
    "choices",  # the choices and what each one leads to
    "recommendation",  # the recommendation and why
    "materials",  # what to decide with: the draft, the diff, the numbers
    "deadline",  # the deadline and what happens without a decision
)
CARD_FIELDS = ("title", "context", "options", "deadline")
_FIELDS = frozenset({*CARD_FIELDS, "detail", "key"})
_OPTION_FIELDS = frozenset({"label", "effect", "recommended"})

# Card limits, in characters. Six lines: the title, the context, up to three choices and the
# deadline. A Japanese card the person approved was about 180 characters.
MAX_TITLE = 80
MIN_TITLE = 6
MAX_CONTEXT = 140
MIN_CONTEXT = 10
MAX_LABEL = 24
MAX_EFFECT = 100
MIN_EFFECT = 2
MAX_DEADLINE = 60
MIN_DEADLINE = 2
MAX_OPTIONS = 3
MAX_CARD_CHARS = 300
MAX_DETAIL_PART = 4000
MAX_DETAIL_CHARS = 16000
_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,40}$")

#: Returned with every refusal, so the agent can rewrite without looking anything up.
FORMAT = (
    "Send the request as JSON: "
    '{"title": what to decide (one sentence), '
    '"context": what this is about, understandable from zero (1-2 sentences; say if it can be '
    "undone), "
    '"options": [{"label": button text, "effect": one line: what happens, can it be undone, '
    'cost, "recommended": true on exactly one}], '
    '"deadline": one line: by when, and what happens without a decision, '
    '"detail": {"about", "now", "ask", "choices", "recommendation", "materials", "deadline"} '
    "— the full text of each part, with the draft, diff or numbers in materials}. "
    "The bar: someone who has forgotten this whole matter can answer from the card alone."
)


class DecisionRequestError(ValueError):
    """A request the Console cannot show as a card. ``problems`` lists every reason."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


@dataclass(frozen=True)
class Option:
    label: str
    effect: str
    recommended: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"label": self.label, "effect": self.effect, "recommended": self.recommended}


@dataclass(frozen=True)
class DecisionRequestSpec:
    """A validated request, not yet stored."""

    kind: str
    key: str
    title: str
    context: str
    options: tuple[Option, ...]
    deadline: str
    detail: dict[str, str]

    def card(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "context": self.context,
            "options": [o.as_dict() for o in self.options],
            "deadline": self.deadline,
        }


def has_request(body: Any) -> bool:
    """Whether a mark's body carries a request, as opposed to the bare mark agents used before."""
    return isinstance(body, Mapping) and any(name in body for name in (*CARD_FIELDS, "detail"))


def refusal(error: DecisionRequestError) -> dict[str, Any]:
    """The 400 body: what is wrong, and the whole format to rewrite against."""
    return {
        "error": "The decision request was refused; rewrite it and send it again",
        "problems": error.problems,
        "format": FORMAT,
    }


def parse_request(body: Any, *, kind: str) -> DecisionRequestSpec:
    """Validate *body* as a request of *kind*, or raise with every problem found."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}")
    if not isinstance(body, Mapping):
        raise DecisionRequestError(["The request must be a JSON object"])
    problems: list[str] = []
    unknown = sorted(set(body) - _FIELDS)
    if unknown:
        problems.append(
            f"Unknown field {unknown[0]!r}; the fields are {', '.join(sorted(_FIELDS))}"
        )
    title = _line(body, "title", MIN_TITLE, MAX_TITLE, problems)
    context = _line(body, "context", MIN_CONTEXT, MAX_CONTEXT, problems)
    deadline = _line(body, "deadline", MIN_DEADLINE, MAX_DEADLINE, problems)
    options = _options(body.get("options"), kind, problems)
    detail = _detail(body.get("detail"), problems)
    key = body.get("key", "")
    if not isinstance(key, str) or (key and not _KEY.match(key)):
        problems.append("key must be 1-40 letters, digits or _.:- (or left out)")
        key = ""
    card_chars = len(title) + len(context) + len(deadline)
    card_chars += sum(len(o.label) + len(o.effect) for o in options)
    if card_chars > MAX_CARD_CHARS:
        problems.append(
            f"The card is {card_chars} characters; keep it within {MAX_CARD_CHARS} and move "
            "the rest into detail"
        )
    if problems:
        raise DecisionRequestError(problems)
    return DecisionRequestSpec(
        kind=kind,
        key=key,
        title=title,
        context=context,
        options=tuple(options),
        deadline=deadline,
        detail=detail,
    )


def _line(body: Mapping[str, Any], name: str, low: int, high: int, problems: list[str]) -> str:
    value = body.get(name)
    if value is None:
        problems.append(f"{name} is required")
        return ""
    return _text(value, name, low, high, problems)


def _text(value: Any, name: str, low: int, high: int, problems: list[str]) -> str:
    if not isinstance(value, str):
        problems.append(f"{name} must be a string")
        return ""
    text = value.strip()
    if "\n" in text:
        problems.append(f"{name} must be one line; put longer text into detail")
    if len(text) < low:
        problems.append(f"{name} is required (at least {low} characters)")
    elif len(text) > high:
        problems.append(f"{name} is {len(text)} characters; keep it within {high}")
    return text


def _options(value: Any, kind: str, problems: list[str]) -> list[Option]:
    if value is None:
        problems.append("options is required: the choices the person can answer with")
        return []
    if not isinstance(value, list):
        problems.append("options must be an array of {label, effect, recommended}")
        return []
    least = 2 if kind == "waiting" else 1
    if len(value) < least:
        problems.append(
            f"options needs at least {least} choice{'s' if least > 1 else ''} for {kind}"
        )
    if len(value) > MAX_OPTIONS:
        problems.append(f"options may hold at most {MAX_OPTIONS} choices; the card has six lines")
    options: list[Option] = []
    for index, entry in enumerate(value[:MAX_OPTIONS]):
        where = f"options[{index}]"
        if not isinstance(entry, Mapping):
            problems.append(f"{where} must be an object with label and effect")
            continue
        unknown = sorted(set(entry) - _OPTION_FIELDS)
        if unknown:
            problems.append(f"{where} has unknown field {unknown[0]!r}")
        label = _text(entry.get("label", ""), f"{where}.label", 1, MAX_LABEL, problems)
        effect = _text(entry.get("effect", ""), f"{where}.effect", MIN_EFFECT, MAX_EFFECT, problems)
        recommended = entry.get("recommended", False)
        if not isinstance(recommended, bool):
            problems.append(f"{where}.recommended must be true or false")
            recommended = False
        options.append(Option(label, effect, recommended))
    labels = [o.label for o in options]
    if len(set(labels)) != len(labels):
        problems.append("options need distinct labels: each one becomes a button")
    marked = sum(o.recommended for o in options)
    if kind == "waiting" and len(options) >= 2 and marked != 1:
        problems.append("Mark exactly one option as recommended")
    elif marked > 1:
        problems.append("At most one option may be recommended")
    return options


def _detail(value: Any, problems: list[str]) -> dict[str, str]:
    if value is None:
        problems.append(f"detail is required, with {', '.join(DETAIL_FIELDS)}")
        return {}
    if not isinstance(value, Mapping):
        problems.append(f"detail must be an object with {', '.join(DETAIL_FIELDS)}")
        return {}
    unknown = sorted(set(value) - set(DETAIL_FIELDS))
    if unknown:
        problems.append(f"detail has unknown part {unknown[0]!r}")
    detail: dict[str, str] = {}
    for name in DETAIL_FIELDS:
        part = value.get(name)
        if not isinstance(part, str) or not part.strip():
            problems.append(f"detail.{name} is required")
            continue
        text = part.strip()
        if len(text) > MAX_DETAIL_PART:
            problems.append(f"detail.{name} is over {MAX_DETAIL_PART} characters")
        detail[name] = text
    total = sum(len(text) for text in detail.values())
    if total > MAX_DETAIL_CHARS:
        problems.append(f"detail is {total} characters; keep it within {MAX_DETAIL_CHARS}")
    return detail


def agent_guide() -> str:
    """The system-prompt section every agent with the control plane gets.

    One place for the format and the bar. Requests ride on the markers only: the secretary home
    and a family's situation panel both show them from the same store.
    """
    return (
        "## Asking the user: decision requests\n"
        "Whenever you hand the user something to decide, review or do (a ❓/👀/📋 marker), "
        "write it as a decision request. The Console shows it as a card the user answers with "
        "one tap, in its secretary home and in your family's situation panel. "
        "The bar: someone who has forgotten this whole matter can answer from the card "
        "alone, without opening the thread or "
        "anything else. Write in the user's language, plainly: no internal shorthand, no IDs "
        "or names without saying what they are.\n"
        f"Card (at most six lines, {MAX_CARD_CHARS} characters in total):\n"
        '- `"title"`: what to decide, one sentence\n'
        '- `"context"`: what this is about, from zero, in one or two sentences; say whether '
        "it can be undone\n"
        '- `"options"`: 2-3 choices for a question (1-3 for a review or a task), each '
        '`{"label": button text, "effect": one line: what happens, can it be undone, cost}`; '
        'put `"recommended": true` on exactly one\n'
        '- `"deadline"`: one line: by when, and what happens without a decision\n'
        'Folded under the card, `"detail"` holds the full text: `"about"`, `"now"` (what is '
        'happening and why you ask now), `"ask"`, `"choices"` (each and its result), '
        '`"recommendation"` (and why), `"materials"` (the draft text, the diff, the numbers '
        'themselves, not a link to them), `"deadline"` (and what happens without a decision).\n'
        "Write the JSON to a file and send it as the body of the marker call: "
        '`curl -s -X POST "$CCDB_API_URL/api/threads/$DISCORD_THREAD_ID/<marker>" '
        '-H "Content-Type: application/json" --data-binary @request.json`. '
        "A request that misses a part, runs long or offers nothing to choose is refused "
        "(400) with every problem listed: rewrite it and send it again. "
        'Two open questions in one thread need distinct `"key"` values. '
        "The user's answer arrives as a reply in the thread the card belongs to, and the "
        "card closes by itself. A parent never copies a child's question: the child's card "
        "already shows in the parent's panel, and the answer goes to the child."
    )
