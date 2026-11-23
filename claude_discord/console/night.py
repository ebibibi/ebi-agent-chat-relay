"""Leave instructions at night, read one message in the morning (#1476).

Two small pieces of the "work through the night" flow live here; the morning brief itself is
:mod:`.morning`.

* :func:`night_template` — the text the Console's 🌙 button puts into the capture or reply box:
  what to reach by morning, what the agent may do alone, what it must never do (send, publish,
  pay, change production), when to stop, and that the morning gets one message. A deployment
  replaces the built-in text with its own file (``CCDB_NIGHT_TEMPLATE_FILE``).
* :class:`NightSettings` — the person's quiet hours: a window in which the Console does not
  chime or raise desktop notifications, except for the urgent conditions they chose. Stored in
  the Console state table (:mod:`.console_state`), so every browser reads the same choice.
  **Off by default**: an unset window changes nothing.

Nothing here approves anything. The template asks agents to keep sending, publishing, paying
and production changes for the morning; quiet hours only change when the Console rings.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: The Console state key the settings are stored under (JSON).
STATE_NIGHT = "night"
TEMPLATE_ENV = "CCDB_NIGHT_TEMPLATE_FILE"
MAX_TEMPLATE_CHARS = 4000
MAX_KEYWORDS = 20
MAX_KEYWORD_CHARS = 40
MAX_PLACE_CHARS = 200
#: How many threads one night shift hands over at most (#1511); the person may change it.
DEFAULT_SHIFT_LIMIT = 8
SHIFT_LIMIT_RANGE = (1, 20)
#: Board statuses a person may mark as urgent enough to ring at night.
URGENT_STATUSES = frozenset({"error", "waiting", "action", "review"})
_HHMM = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")

TEMPLATES: dict[str, str] = {
    "ja": (
        "目的：朝までに［成果］を［完了条件］まで進める\n"
        "優先順：［A → B → C］／参照：［資料・案件］\n"
        "任せる範囲：調査・実装・検証・下書き・PR作成\n"
        "やらないこと：送信・公開・課金・本番変更（これだけは判断カードにまとめて朝に）\n"
        "迷ったら：推奨案で進め、選んだ理由を書き残す\n"
        "止まる条件：同じ失敗が3回続いたら、原因・試したこと・再開条件を記録して次へ\n"
        "完了：条件を満たしたら自分で完了。親が子とCIを最後まで追う\n"
        "朝：「できた（リンク）／決めて（上位3件）／続いている（次の動き）」の1通だけ\n"
    ),
    "en": (
        "Goal: by morning, take [outcome] to [done condition]\n"
        "Order: [A → B → C] / Read: [material, project]\n"
        "You may: research, implement, verify, draft, open PRs\n"
        "Never: send, publish, pay or change production (collect these as decision cards for "
        "the morning)\n"
        "When unsure: go with the recommended option and write down why\n"
        "Stop when: the same failure repeats 3 times — record the cause, what you tried and "
        "how to resume, then move on\n"
        "Done: finish it yourself once the condition is met; a parent follows its children "
        "and CI to the end\n"
        "Morning: one message only — done (links) / decide (top 3) / continuing (next move)\n"
    ),
}


def night_template(lang: str = "ja", env: Mapping[str, str] | None = None) -> str:
    """The night-instruction text: the deployment's file when it is readable, else built in."""
    env = os.environ if env is None else env
    path = (env.get(TEMPLATE_ENV) or "").strip()
    if path:
        try:
            text = Path(path).expanduser().read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            logger.warning(
                "night: %s=%s is not readable; using the built-in template", TEMPLATE_ENV, path
            )
        else:
            if text:
                return text[:MAX_TEMPLATE_CHARS] + "\n"
    return TEMPLATES.get(lang, TEMPLATES["en"])


class NightSettingsError(ValueError):
    """Settings the person sent that cannot be stored; ``problems`` lists every one."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class NightSettings:
    """Quiet hours and the morning brief's delivery, as the person chose them.

    ``quiet_start``/``quiet_end`` are local ``HH:MM``; a window may cross midnight. Both unset
    (the default) means no quiet hours. ``urgent_statuses`` and ``urgent_keywords`` (matched in
    a thread's title) still ring inside the window. ``morning_at`` and ``morning_place`` record
    when and where the deployment delivers the morning brief; the relay itself only stores them.
    """

    quiet_start: str | None = None
    quiet_end: str | None = None
    urgent_statuses: tuple[str, ...] = ()
    urgent_keywords: tuple[str, ...] = field(default_factory=tuple)
    morning_at: str | None = None
    morning_place: str | None = None
    shift_limit: int = DEFAULT_SHIFT_LIMIT

    @property
    def quiet_enabled(self) -> bool:
        return bool(self.quiet_start and self.quiet_end and self.quiet_start != self.quiet_end)

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["urgent_statuses"] = list(self.urgent_statuses)
        out["urgent_keywords"] = list(self.urgent_keywords)
        out["quiet_enabled"] = self.quiet_enabled
        return out

    def in_quiet(self, now: datetime) -> bool:
        """Whether *now* (read in local time) falls inside the quiet window."""
        if not self.quiet_enabled:
            return False
        assert self.quiet_start and self.quiet_end
        start, end = _clock(self.quiet_start), _clock(self.quiet_end)
        at = now.astimezone().time().replace(second=0, microsecond=0)
        if start < end:
            return start <= at < end
        return at >= start or at < end

    def is_urgent(self, item: Mapping[str, Any]) -> bool:
        """Whether a board item rings even inside the quiet window."""
        if item.get("status") in self.urgent_statuses:
            return True
        title = str(item.get("title") or item.get("thread_name") or "").casefold()
        return any(word.casefold() in title for word in self.urgent_keywords)


def _clock(text: str) -> time:
    hours, minutes = text.split(":")
    return time(int(hours), int(minutes))


def _hhmm(name: str, value: Any, problems: list[str]) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or not _HHMM.match(value):
        problems.append(f"{name} must be HH:MM (24-hour, local time) or null")
        return None
    return value


def _strings(name: str, value: Any, problems: list[str], *, limit: int) -> list[str] | None:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        problems.append(f"{name} must be a list of strings")
        return None
    cleaned = [v.strip() for v in value if v.strip()]
    if len(cleaned) > MAX_KEYWORDS:
        problems.append(f"{name} holds at most {MAX_KEYWORDS} entries")
    if any(len(v) > limit for v in cleaned):
        problems.append(f"each of {name} is at most {limit} characters")
    return cleaned


def _statuses(value: Any, problems: list[str]) -> list[str]:
    statuses = _strings("urgent_statuses", value, problems, limit=20) or []
    unknown = sorted(set(statuses) - URGENT_STATUSES)
    if unknown:
        problems.append(
            f"urgent_statuses may hold only {', '.join(sorted(URGENT_STATUSES))}; "
            f"not {', '.join(unknown)}"
        )
    return statuses


def parse_night_settings(body: Any, current: NightSettings | None = None) -> NightSettings:
    """Validate *body*; a field left out keeps its *current* value."""
    if not isinstance(body, dict):
        raise NightSettingsError(["the body must be a JSON object"])
    known = set(NightSettings.__dataclass_fields__)
    problems = [f"unknown field: {k}" for k in sorted(body) if k not in known]
    base = current or NightSettings()

    def pick(name: str) -> Any:
        return body[name] if name in body else getattr(base, name)

    quiet_start = _hhmm("quiet_start", pick("quiet_start"), problems)
    quiet_end = _hhmm("quiet_end", pick("quiet_end"), problems)
    if bool(quiet_start) != bool(quiet_end):
        problems.append("quiet_start and quiet_end are set together (or both null)")
    statuses = _statuses(_listish(pick("urgent_statuses")), problems)
    keywords = _strings(
        "urgent_keywords", _listish(pick("urgent_keywords")), problems, limit=MAX_KEYWORD_CHARS
    )
    morning_at = _hhmm("morning_at", pick("morning_at"), problems)
    place = pick("morning_place")
    if place is not None and (not isinstance(place, str) or len(place) > MAX_PLACE_CHARS):
        problems.append(f"morning_place must be text of at most {MAX_PLACE_CHARS} characters")
        place = None
    limit = pick("shift_limit")
    low, high = SHIFT_LIMIT_RANGE
    if isinstance(limit, bool) or not isinstance(limit, int) or not low <= limit <= high:
        problems.append(f"shift_limit must be a whole number {low}-{high}")
        limit = DEFAULT_SHIFT_LIMIT
    if problems:
        raise NightSettingsError(problems)
    return NightSettings(
        shift_limit=limit,
        quiet_start=quiet_start,
        quiet_end=quiet_end,
        urgent_statuses=tuple(dict.fromkeys(statuses)),
        urgent_keywords=tuple(dict.fromkeys(keywords or [])),
        morning_at=morning_at,
        morning_place=(place.strip() or None) if isinstance(place, str) else None,
    )


def _listish(value: Any) -> Any:
    return list(value) if isinstance(value, tuple) else value


def load_night_settings(raw: str | None) -> NightSettings:
    """The stored settings, or the defaults (quiet hours off) when absent or unreadable."""
    if not raw:
        return NightSettings()
    try:
        return parse_night_settings(json.loads(raw))
    except (json.JSONDecodeError, NightSettingsError):
        logger.warning("night: stored settings are unreadable; using the defaults")
        return NightSettings()


def dump_night_settings(settings: NightSettings) -> str:
    data = settings.as_dict()
    data.pop("quiet_enabled")
    return json.dumps(data, ensure_ascii=False)
