"""The morning brief: one message — done / decide / continuing (#1476).

A person who left work for the night should not walk the board in the morning. This builds
one short message from what the board already knows, with no model, so it says the same
thing twice and costs nothing:

* **done** — threads that finished (or became completion candidates) inside the window,
  each with a link to open it;
* **decide** — the top three open decision requests in the secretary home's order, each
  with its recommended option, and how many more wait;
* **continuing** — threads still running, each with the first line of its family's
  "doing" (the situation panel) when one was written.

It reuses :func:`.secretary.build_secretary` for the order and the rules for which request
still stands, so the brief and the secretary home never disagree.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from .secretary import build_secretary

logger = logging.getLogger(__name__)

DEFAULT_HOURS = 24
MAX_HOURS = 24 * 7
TOP_DECISIONS = 3
MAX_DONE = 15
MAX_CONTINUING = 10
_FINISHED = frozenset({"done", "candidate"})

Situation = Callable[[int], Awaitable[Mapping[str, Any] | None]]

LABELS: dict[str, dict[str, str]] = {
    "ja": {
        "title": "☀️ 朝の1通",
        "done": "✅ できた",
        "decide": "❓ 決めて",
        "continuing": "🔄 続いている",
        "none": "なし",
        "more": "ほか {n} 件",
        "candidate": "完了候補",
        "recommended": "推奨",
        "night": "🌙 夜番が任せたもの",
        "window": "{since} 以降",
    },
    "en": {
        "title": "☀️ Morning brief",
        "done": "✅ Done",
        "decide": "❓ Decide",
        "continuing": "🔄 Continuing",
        "none": "nothing",
        "more": "{n} more",
        "candidate": "completion candidate",
        "recommended": "recommended",
        "night": "🌙 Handed over by the night shift",
        "window": "since {since}",
    },
}


STATUS_ICON = {
    "running": "🔄",
    "candidate": "🏁",
    "done": "✅",
    "waiting": "❓",
    "review": "👀",
    "action": "📋",
    "error": "⚠️",
    "idle": "💤",
}


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _stamp(at: datetime) -> str:
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _title(item: Mapping[str, Any]) -> str:
    return str(item.get("title") or item.get("thread_name") or item.get("id") or "")


def _link(item_id: str, base_url: str) -> str:
    return f"{base_url.rstrip('/')}/#focus/{item_id}"


def clamp_hours(raw: Any) -> int:
    try:
        hours = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_HOURS
    return max(1, min(MAX_HOURS, hours))


def _recommended(card: Mapping[str, Any]) -> str | None:
    for option in card.get("options") or []:
        if isinstance(option, Mapping) and option.get("recommended"):
            return str(option.get("label") or "") or None
    return None


async def build_morning_brief(
    items: Iterable[dict[str, Any]],
    requests: Iterable[Mapping[str, Any]],
    *,
    situation: Situation | None = None,
    now: datetime | None = None,
    hours: int = DEFAULT_HOURS,
    base_url: str = "",
    shift: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The brief for the board *items* and open *requests* over the last *hours*."""
    now = now or datetime.now(UTC)
    since = now - timedelta(hours=hours)
    items = [i for i in items if not i.get("archived")]
    home = build_secretary(items, list(requests), now=now)
    finished = sorted(
        (
            i
            for i in items
            if i.get("status") in _FINISHED
            and (moment := _parse(i.get("last_activity_at"))) is not None
            and moment >= since
        ),
        key=lambda i: i.get("last_activity_at") or "",
        reverse=True,
    )
    cards = home["decide"] + home["review"]
    waiting = len(cards) + home["decide_more"] + home["review_more"]
    working = [i for i in items if i.get("status") == "running" or i.get("running")]
    working.sort(key=lambda i: i.get("last_activity_at") or "", reverse=True)
    return {
        "generated_at": _stamp(now),
        "since": _stamp(since),
        "hours": hours,
        "done": [
            {
                "id": i["id"],
                "title": _title(i),
                "candidate": i.get("status") == "candidate",
                "link": _link(i["id"], base_url),
            }
            for i in finished[:MAX_DONE]
        ],
        "done_more": max(0, len(finished) - MAX_DONE),
        "decide": [
            {
                "id": c["id"],
                "title": (c.get("card") or {}).get("title") or c.get("item_title"),
                "thread": c.get("item_title"),
                "kind": c.get("kind"),
                "recommended": _recommended(c.get("card") or {}),
                "deadline": (c.get("card") or {}).get("deadline"),
                "link": _link(c["id"], base_url),
            }
            for c in cards[:TOP_DECISIONS]
        ],
        "decide_more": max(0, waiting - TOP_DECISIONS),
        "continuing": [await _continuing(i, situation, base_url) for i in working[:MAX_CONTINUING]],
        "continuing_more": max(0, len(working) - MAX_CONTINUING),
        "night": _night(shift, items, since, base_url),
    }


def _night(
    shift: Mapping[str, Any] | None,
    items: list[dict[str, Any]],
    since: datetime,
    base_url: str,
) -> list[dict[str, Any]]:
    """What the night shift handed over inside the window, with each thread's status now."""
    if not shift:
        return []
    by_id = {str(i.get("id")): i for i in items}
    out = []
    for entry in shift.get("items") or []:
        sent = _parse(entry.get("sent_at"))
        if entry.get("state") != "sent" or sent is None or sent < since:
            continue
        item = by_id.get(str(entry.get("id")))
        out.append(
            {
                "id": entry.get("id"),
                "title": _title(item) if item else entry.get("title"),
                "status": (item or {}).get("status"),
                "link": _link(str(entry.get("id")), base_url),
            }
        )
    return out


async def _continuing(
    item: Mapping[str, Any], situation: Situation | None, base_url: str
) -> dict[str, Any]:
    doing: str | None = None
    thread = item.get("thread_id")
    if situation is not None and thread is not None and str(thread).isdigit():
        try:
            written = await situation(int(thread))
        except Exception:
            logger.exception("morning: situation of %s unreadable", thread)
            written = None
        lines = (written or {}).get("doing") or []
        doing = str(lines[0]) if lines else None
    return {
        "id": item["id"],
        "title": _title(item),
        "doing": doing,
        "link": _link(str(item["id"]), base_url),
    }


def render_markdown(brief: Mapping[str, Any], lang: str = "ja") -> str:
    """The brief as one message, ready to post."""
    t = LABELS.get(lang, LABELS["en"])
    since = _parse(brief.get("since"))
    when = since.astimezone().strftime("%m/%d %H:%M") if since else ""
    lines = [f"**{t['title']}** — {t['window'].format(since=when)}", ""]

    def section(name: str, rows: list[str], more: int) -> None:
        lines.append(f"**{t[name]}**")
        lines.extend(rows or [f"- {t['none']}"])
        if more:
            lines.append(f"- {t['more'].format(n=more)}")
        lines.append("")

    section(
        "done",
        [
            f"- [{d['title']}]({d['link']})" + (f"（{t['candidate']}）" if d["candidate"] else "")
            for d in brief["done"]
        ],
        brief["done_more"],
    )
    section(
        "decide",
        [
            f"- [{d['title']}]({d['link']})"
            + (f" — {t['recommended']}: {d['recommended']}" if d.get("recommended") else "")
            for d in brief["decide"]
        ],
        brief["decide_more"],
    )
    section(
        "continuing",
        [
            f"- [{c['title']}]({c['link']})" + (f" — {c['doing']}" if c.get("doing") else "")
            for c in brief["continuing"]
        ],
        brief["continuing_more"],
    )
    if brief.get("night"):
        section(
            "night",
            [
                f"- [{n['title']}]({n['link']}) — {STATUS_ICON.get(n.get('status') or '', '·')}"
                for n in brief["night"]
            ],
            0,
        )
    return "\n".join(lines).rstrip() + "\n"
