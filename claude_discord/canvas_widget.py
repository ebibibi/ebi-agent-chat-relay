"""Canvas widgets: what the resident secretary places on the Console (ADR-0025).

A widget is registered data, like a Console page (ADR-0018): a ``kind`` and structured
``data`` that the Console draws with a fixed renderer per kind. No HTML or script from a model
reaches the page; text is drawn as text, and the ``markdown`` kind goes through the Console's
existing sanitised Markdown renderer.

Every check here is deterministic. A widget that misses a field, runs long or names an unknown
kind is refused with every problem listed, so the secretary rewrites it in the same turn.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

KINDS = ("decision", "diff", "table", "markdown", "timeline", "agents", "metric")
SIZES = ("s", "m", "l")

MAX_WIDGETS = 30
MAX_TITLE = 80
MAX_NOTE = 120
MAX_TEXT = 8000
MAX_DIFF = 12000
MAX_CELL = 300
MAX_COLUMNS = 8
MAX_ROWS = 40
MAX_ENTRIES = 20
MAX_ACTIONS = 4
MAX_ACTION_LABEL = 24
MAX_ACTION_SAY = 500
MAX_THREADS = 20
KEY = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,39}$")
_THREAD = re.compile(r"^[0-9]{1,20}$")
_TOP_FIELDS = frozenset({"kind", "title", "data", "size", "position", "note", "actions"})
TIMELINE_STATES = ("todo", "now", "done", "wait")

#: Returned with every refusal, so the secretary can rewrite without looking anything up.
FORMAT = (
    'Send {"kind": one of ' + ", ".join(KINDS) + ', "title": one line, "data": {...}, '
    '"size": "s"|"m"|"l" (optional), "position": number (optional, smaller first), '
    '"note": one line on why it is here (optional), "actions": [{"label", "say"}] (optional, '
    "a button that sends `say` to you as the person's message)}. data per kind: "
    'decision {"thread_id"}; diff {"diff": unified diff} or {"before", "after"}; '
    'table {"columns": [..], "rows": [[..]]}; markdown {"text"}; '
    'timeline {"entries": [{"time", "label", "state": todo|now|done|wait, "minutes"?}]}; '
    'agents {"thread_ids": [..]} (empty: every running thread); '
    'metric {"value", "label", "sub"?, "percent"? 0-100}.'
)


class WidgetError(ValueError):
    """A widget the Console cannot draw. ``problems`` lists every reason."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


@dataclass(frozen=True)
class WidgetSpec:
    """A validated widget, not yet stored."""

    kind: str
    title: str
    data: dict[str, Any]
    size: str = "m"
    position: float = 100.0
    note: str = ""
    actions: tuple[dict[str, str], ...] = ()


def refusal(exc: WidgetError) -> dict[str, Any]:
    """The 400 body for a refused widget."""
    return {"error": "widget refused", "problems": exc.problems, "format": FORMAT}


def _text(value: Any, name: str, limit: int, problems: list[str], *, required: bool) -> str:
    if value is None or value == "":
        if required:
            problems.append(f"{name} is required")
        return ""
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        problems.append(f"{name} must be text")
        return ""
    text = str(value).strip()
    if required and not text:
        problems.append(f"{name} is required")
    if len(text) > limit:
        problems.append(f"{name} is {len(text)} characters; at most {limit}")
    return text


def _line(value: Any, name: str, limit: int, problems: list[str], *, required: bool) -> str:
    text = _text(value, name, limit, problems, required=required)
    if "\n" in text:
        problems.append(f"{name} must be one line")
    return text


def _decision(data: Mapping[str, Any], problems: list[str]) -> dict[str, Any]:
    thread = str(data.get("thread_id") or "").strip()
    if not _THREAD.match(thread):
        problems.append("data.thread_id must be the asking thread's id (digits)")
    return {"thread_id": thread}


def _diff(data: Mapping[str, Any], problems: list[str]) -> dict[str, Any]:
    if data.get("diff") is not None:
        return {"diff": _text(data.get("diff"), "data.diff", MAX_DIFF, problems, required=True)}
    before = _text(data.get("before"), "data.before", MAX_DIFF // 2, problems, required=False)
    after = _text(data.get("after"), "data.after", MAX_DIFF // 2, problems, required=False)
    if not before and not after:
        problems.append("data needs diff, or before and after")
    return {"before": before, "after": after}


def _table(data: Mapping[str, Any], problems: list[str]) -> dict[str, Any]:
    columns = data.get("columns")
    rows = data.get("rows")
    if not isinstance(columns, list) or not columns or len(columns) > MAX_COLUMNS:
        problems.append(f"data.columns must be a list of 1-{MAX_COLUMNS} headings")
        columns = []
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        problems.append(f"data.rows must be a list of at most {MAX_ROWS} rows")
        rows = []
    heads = [_line(c, "a column", MAX_CELL, problems, required=True) for c in columns]
    body: list[list[str]] = []
    for row in rows:
        if not isinstance(row, list) or len(row) > len(heads):
            problems.append("each row must be a list no longer than columns")
            continue
        body.append([_text(cell, "a cell", MAX_CELL, problems, required=False) for cell in row])
    return {"columns": heads, "rows": body}


def _markdown(data: Mapping[str, Any], problems: list[str]) -> dict[str, Any]:
    return {"text": _text(data.get("text"), "data.text", MAX_TEXT, problems, required=True)}


def _timeline(data: Mapping[str, Any], problems: list[str]) -> dict[str, Any]:
    entries = data.get("entries")
    if not isinstance(entries, list) or not entries or len(entries) > MAX_ENTRIES:
        problems.append(f"data.entries must be a list of 1-{MAX_ENTRIES} entries")
        return {"entries": []}
    out: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, Mapping):
            problems.append("each entry must be an object")
            continue
        state = str(entry.get("state") or "todo")
        if state not in TIMELINE_STATES:
            problems.append(f"entry state must be one of {', '.join(TIMELINE_STATES)}")
        minutes = entry.get("minutes")
        if minutes is not None and (
            not isinstance(minutes, int) or isinstance(minutes, bool) or not 0 < minutes <= 1440
        ):
            problems.append("entry minutes must be a whole number 1-1440")
            minutes = None
        thread = str(entry.get("thread_id") or "").strip()
        if thread and not _THREAD.match(thread):
            problems.append("entry thread_id must be digits")
            thread = ""
        out.append(
            {
                "time": _line(entry.get("time"), "entry time", 20, problems, required=False),
                "label": _line(
                    entry.get("label"), "entry label", MAX_CELL, problems, required=True
                ),
                "state": state,
                "minutes": minutes,
                "thread_id": thread or None,
            }
        )
    return {"entries": out}


def _agents(data: Mapping[str, Any], problems: list[str]) -> dict[str, Any]:
    ids = data.get("thread_ids") or []
    if not isinstance(ids, list) or len(ids) > MAX_THREADS:
        problems.append(f"data.thread_ids must be a list of at most {MAX_THREADS} ids")
        return {"thread_ids": []}
    clean = [str(i).strip() for i in ids]
    if any(not _THREAD.match(i) for i in clean):
        problems.append("data.thread_ids must be digits")
    return {"thread_ids": clean}


def _metric(data: Mapping[str, Any], problems: list[str]) -> dict[str, Any]:
    percent = data.get("percent")
    if percent is not None and (
        not isinstance(percent, (int, float))
        or isinstance(percent, bool)
        or not 0 <= percent <= 100
    ):
        problems.append("data.percent must be a number 0-100")
        percent = None
    return {
        "value": _line(data.get("value"), "data.value", 40, problems, required=True),
        "label": _line(data.get("label"), "data.label", MAX_TITLE, problems, required=True),
        "sub": _line(data.get("sub"), "data.sub", MAX_NOTE, problems, required=False),
        "percent": percent,
    }


_CHECKS: dict[str, Callable[[Mapping[str, Any], list[str]], dict[str, Any]]] = {
    "decision": _decision,
    "diff": _diff,
    "table": _table,
    "markdown": _markdown,
    "timeline": _timeline,
    "agents": _agents,
    "metric": _metric,
}
assert set(_CHECKS) == set(KINDS)


def _actions(raw: Any, problems: list[str]) -> tuple[dict[str, str], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list) or len(raw) > MAX_ACTIONS:
        problems.append(f"actions must be a list of at most {MAX_ACTIONS} buttons")
        return ()
    out = []
    for action in raw:
        if not isinstance(action, Mapping):
            problems.append("each action must be an object with label and say")
            continue
        label = _line(
            action.get("label"), "action label", MAX_ACTION_LABEL, problems, required=True
        )
        say = _text(action.get("say"), "action say", MAX_ACTION_SAY, problems, required=True)
        out.append({"label": label, "say": say})
    return tuple(out)


def parse_widget(body: Any) -> WidgetSpec:
    """Validate *body* into a :class:`WidgetSpec`, or raise :class:`WidgetError`."""
    if not isinstance(body, Mapping):
        raise WidgetError(["the widget must be a JSON object"])
    problems: list[str] = []
    unknown = sorted(set(body) - _TOP_FIELDS)
    if unknown:
        problems.append(f"unknown fields: {', '.join(unknown)}")
    kind = body.get("kind")
    if kind not in KINDS:
        problems.append(f"kind must be one of {', '.join(KINDS)}")
    title = _line(body.get("title"), "title", MAX_TITLE, problems, required=True)
    size = body.get("size") or "m"
    if size not in SIZES:
        problems.append("size must be s, m or l")
    position = body.get("position", 100)
    if not isinstance(position, (int, float)) or isinstance(position, bool):
        problems.append("position must be a number")
        position = 100
    note = _line(body.get("note"), "note", MAX_NOTE, problems, required=False)
    raw_data = body.get("data")
    data: dict[str, Any] = {}
    if not isinstance(raw_data, Mapping):
        problems.append("data must be an object")
    elif kind in _CHECKS:
        data = _CHECKS[kind](raw_data, problems)
    actions = _actions(body.get("actions"), problems)
    if problems:
        raise WidgetError(problems)
    return WidgetSpec(
        kind=str(kind),
        title=title,
        data=data,
        size=str(size),
        position=float(position),
        note=note,
        actions=actions,
    )


def valid_key(key: str) -> bool:
    return bool(KEY.match(key))
