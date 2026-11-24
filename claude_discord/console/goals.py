"""Goals: what the person is working toward, read from and written back to one Markdown note.

A goal is a ``###`` heading in the goals section of an operator-chosen note in the Ebi
Workspace vault. The lines under it are free prose; only a few labelled bullets are read::

    ## Goals
    ### 1️⃣ Ship the product
    - **Done when**: two customers run it from a config file alone
    - **Due**: 2026-12-31
    - **Projects**: [[02_Contexts/dev/relay/status|relay]]

Labels are matched with aliases (Japanese and English), the way next actions are. A ⏸️ or
✅ in front of the title pauses or completes a goal. The note stays the only copy: this module
parses it, computes which threads work toward which goal, and rewrites only the lines an edit
touches. Nothing here talks to the network or the database.
"""

from __future__ import annotations

import calendar
import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

#: Section headings (``## …``) that hold the goals. Without one, every ``###`` counts.
SECTIONS = ("いま集中するもの", "目標", "Goals", "Current goals", "Focus")
#: Label aliases of the fields a goal may carry; the part before a bracket is compared.
LABELS = {
    "done_when": ("達成の定義", "判定", "完了の定義", "Done when", "Definition of done"),
    "due": ("期限", "Due", "Deadline"),
    "why": ("なぜ", "Why"),
    "projects": ("プロジェクト", "Projects", "Project"),
}
#: What a new field line is written as, per field.
WRITE_LABEL = {"done_when": "達成の定義", "due": "期限", "projects": "プロジェクト"}

STATE_ACTIVE = "active"
STATE_PAUSED = "paused"
STATE_DONE = "done"
STATES = (STATE_ACTIVE, STATE_PAUSED, STATE_DONE)
_STATE_MARK = {STATE_PAUSED: "⏸️", STATE_DONE: "✅"}

#: A person's choice of goal for a thread is a tag on its work item.
TAG_PREFIX = "goal:"
#: The tag that says "this thread works toward no goal".
TAG_NONE = "goal:none"

#: Finished threads stay under their goal this long, so progress is visible.
RECENT_DONE = timedelta(days=7)
#: A goal whose threads have all been quiet this long is flagged.
QUIET_DAYS = 14

MAX_TITLE_CHARS = 200
MAX_FIELD_CHARS = 1000
CONFIG_ENV = "CCDB_CONSOLE_GOALS_NOTE"
LIMIT_ENV = "CCDB_CONSOLE_GOALS_LIMIT"

_KEYCAPS = [f"{n}️⃣" for n in range(10)] + ["🔟"]
_NUMBER = re.compile(r"^(?:[0-9]️?⃣|🔟)\s*")
_MARK = re.compile(r"^(⏸️|⏸|✅)\s*")
_FIELD_BOLD = re.compile(r"^[-*+]\s+\*\*(?P<label>[^*]{1,40}?)\*\*\s*[:：]\s*(?P<value>.*)$")
_FIELD_PLAIN = re.compile(r"^[-*+]\s+(?P<label>[^:：*\[\]]{1,30}?)\s*[:：]\s*(?P<value>.*)$")
_BRACKET = re.compile(r"[（(]([^）)]*)[）)]")
_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|([^\]]*))?\]\]")
_MDLINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_YMD = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_YM = re.compile(r"(\d{4})-(\d{1,2})(?!\d|-\d)")
_STATUS_SUFFIX = re.compile(r"\s*[（(][^）)]*(?:🟢|🟡|🔴|⏸|着手|\d{4}-\d{2}-\d{2})[^）)]*[）)]\s*$")
_MILESTONES = "マイルストーン"


class GoalError(ValueError):
    """A goal edit the note cannot take."""


# -- configuration -------------------------------------------------------------------------
def config_file() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "ebi-workspace" / "console-goals.txt"


def load_config(path: Path | None = None) -> dict[str, Any]:
    """The goals note (vault-relative) and the optional limit of active goals.

    ``CCDB_CONSOLE_GOALS_NOTE`` / ``CCDB_CONSOLE_GOALS_LIMIT`` win over the file, whose first
    non-comment line is the note and an optional ``limit: N`` line the limit.
    """
    note: str | None = os.environ.get(CONFIG_ENV) or None
    limit_text: str | None = os.environ.get(LIMIT_ENV) or None
    source = path or config_file()
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        lines = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("limit:"):
            limit_text = limit_text or line.split(":", 1)[1].strip()
        elif note is None:
            note = line
    limit = None
    if limit_text and limit_text.isdigit() and int(limit_text) > 0:
        limit = int(limit_text)
    return {"note": clean_note_path(note) if note else None, "limit": limit}


def clean_note_path(value: Any) -> str:
    """A vault-relative Markdown path, or GoalError."""
    if not isinstance(value, str) or not value.strip() or len(value) > 1000:
        raise GoalError("The goals note must be a vault-relative path")
    text = value.strip()
    pure = PurePosixPath(text)
    if (
        "\\" in text
        or "\0" in text
        or pure.is_absolute()
        or any(p in ("", ".", "..") for p in pure.parts)
        or pure.suffix.lower() != ".md"
    ):
        raise GoalError("The goals note must be a vault-relative .md path")
    return pure.as_posix()


def save_config(note: str, limit: int | None, path: Path | None = None) -> None:
    target = path or config_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise GoalError("The goals configuration must not be a symbolic link")
    body = clean_note_path(note) + "\n" + (f"limit: {int(limit)}\n" if limit else "")
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(body, encoding="utf-8")
    os.replace(temporary, target)


# -- parsing --------------------------------------------------------------------------------
@dataclass
class Block:
    """One goal's lines: ``start`` is its heading, ``end`` the first line after it."""

    start: int
    end: int


@dataclass
class Parsed:
    lines: list[str]
    section: tuple[int, int]
    blocks: list[Block] = field(default_factory=list)


def goal_id(title: str) -> str:
    """A short stable id for a goal title (the tag a person's choice is stored as)."""
    norm = re.sub(r"\s+", " ", plain(title)).strip().lower()
    return "g" + hashlib.sha1(norm.encode("utf-8")).hexdigest()[:8]


def plain(text: str) -> str:
    """Markdown inline markup reduced to what a person reads."""
    text = _WIKILINK.sub(lambda m: m[2] or PurePosixPath(m[1]).name, text)
    text = _MDLINK.sub(r"\1", text)
    return re.sub(r"\*\*|__|`", "", text).strip()


def _split_heading(raw: str) -> tuple[str, str, str]:
    """(state, number, title) of a ``###`` heading's text."""
    text = raw.strip()
    state = STATE_ACTIVE
    number = ""
    for _ in range(2):
        mark = _MARK.match(text)
        if mark:
            state = STATE_DONE if mark[1] == "✅" else STATE_PAUSED
            text = text[mark.end() :]
            continue
        num = _NUMBER.match(text)
        if num:
            number = num[0].strip()
            text = text[num.end() :]
    return state, number, text.strip()


def _field(line: str) -> tuple[str, str, str] | None:
    """(field, label as written, value) of a labelled bullet, or None."""
    match = _FIELD_BOLD.match(line) or _FIELD_PLAIN.match(line)
    if not match:
        return None
    label = match["label"].strip()
    base = _BRACKET.sub("", label).strip().lower()
    for name, aliases in LABELS.items():
        if any(base == a.lower() for a in aliases):
            return name, label, match["value"].strip()
    return None


def parse_due(text: str) -> str | None:
    """The first date in *text* as ISO, a bare month meaning its last day."""
    if not text:
        return None
    match = _YMD.search(text)
    if match:
        try:
            return date(int(match[1]), int(match[2]), int(match[3])).isoformat()
        except ValueError:
            return None
    match = _YM.search(text)
    if match:
        year, month = int(match[1]), int(match[2])
        if 1 <= month <= 12:
            return date(year, month, calendar.monthrange(year, month)[1]).isoformat()
    return None


def structure(text: str, sections: tuple[str, ...] = SECTIONS) -> Parsed:
    lines = text.split("\n")
    start, end = 0, len(lines)
    wanted = {s.lower() for s in sections}
    for number, line in enumerate(lines):
        if line.startswith("## ") and _BRACKET.sub("", line[3:]).strip().lower() in wanted:
            start = number + 1
            end = next(
                (n for n in range(start, len(lines)) if re.match(r"^#{1,2} ", lines[n])),
                len(lines),
            )
            break
    parsed = Parsed(lines=lines, section=(start, end))
    fence = False
    for number in range(start, end):
        line = lines[number]
        if line.startswith("```"):
            fence = not fence
        if fence or not line.startswith("### "):
            continue
        if parsed.blocks:
            parsed.blocks[-1].end = number
        parsed.blocks.append(Block(number, end))
    return parsed


def _goal(parsed: Parsed, block: Block) -> dict[str, Any]:
    lines = parsed.lines
    state, number, raw_title = _split_heading(lines[block.start][4:])
    title = _STATUS_SUFFIX.sub("", raw_title).strip() or raw_title
    found: dict[str, tuple[str, str, int]] = {}
    links: list[str] = []
    project_links: list[str] = []
    for n in range(block.start + 1, block.end):
        line = lines[n]
        parsed_field = _field(line)
        if parsed_field and parsed_field[0] not in found:
            found[parsed_field[0]] = (parsed_field[1], parsed_field[2], n)
        targets = [m[1].strip() for m in _WIKILINK.finditer(line)]
        links.extend(targets)
        if parsed_field and parsed_field[0] == "projects":
            project_links.extend(targets)
    done_label, done_value, _ = found.get("done_when", ("", "", -1))
    due_value = found.get("due", ("", "", -1))[1]
    due = parse_due(due_value) or parse_due(done_label)
    return {
        "id": goal_id(title),
        "number": number,
        "title": plain(title),
        "heading": raw_title,
        "state": state,
        "done_when": plain(done_value) or None,
        "due": due,
        "why": plain(found.get("why", ("", "", -1))[1]) or None,
        "line": block.start + 1,
        "links": links,
        "project_links": project_links,
    }


def parse_goals(text: str, sections: tuple[str, ...] = SECTIONS) -> list[dict[str, Any]]:
    """Every goal in the note's goals section, in the note's order."""
    parsed = structure(text, sections)
    return [_goal(parsed, b) for b in parsed.blocks]


def resolve_projects(
    goal: dict[str, Any],
    resolve: Any,
    source: str,
    projects: set[str],
) -> list[str]:
    """The Workspace projects a goal links, in the order the note names them.

    A link on the goal's projects line counts wherever it points inside a project; any
    other link in the section counts only when it points at a project's ``status.md`` or
    ``overview.md`` — a goal's prose links to plenty of notes that are not its projects.
    *resolve* maps ``(target, source note)`` to a vault path or None.
    """
    found: list[str] = []
    explicit = set(goal["project_links"])
    for target in goal["links"]:
        path = resolve(target, source)
        if not path or not path.startswith("02_Contexts/"):
            continue
        pure = PurePosixPath(path)
        if target not in explicit and pure.name not in ("status.md", "overview.md"):
            continue
        parts = pure.parent.parts[1:]
        for end in range(len(parts), 0, -1):
            candidate = "/".join(parts[:end])
            if candidate in projects:
                if candidate not in found:
                    found.append(candidate)
                break
    return found


# -- milestones -----------------------------------------------------------------------------
def milestones(status_text: str) -> dict[str, Any]:
    """Progress through a project's ``## マイルストーン`` table: done / total and the next one."""
    inside = False
    done = total = 0
    upcoming: list[tuple[str, str]] = []
    for line in status_text.splitlines():
        if line.startswith("## "):
            inside = line[3:].strip() == _MILESTONES
            continue
        stripped = line.strip()
        if not inside or not stripped.startswith("|") or re.fullmatch(r"[|\s:-]+", stripped):
            continue
        cells = [c.strip() for c in stripped.strip("|").split("|")]
        if not cells or not cells[0] or cells[0] in ("マイルストーン", "Milestone"):
            continue
        if cells[0].startswith(("（未設定", "(未設定", "—")):
            continue
        total += 1
        if "✅" in stripped:
            done += 1
            continue
        when = parse_due(" ".join(cells[1:])) or ""
        upcoming.append((when or "9999-12-31", plain(cells[0])))
    upcoming.sort()
    nxt = (
        {"name": upcoming[0][1], "date": None if upcoming[0][0] == "9999-12-31" else upcoming[0][0]}
        if upcoming
        else None
    )
    return {"done": done, "total": total, "next": nxt}


# -- threads --------------------------------------------------------------------------------
def _under(path: str | None, project: str) -> bool:
    return bool(path) and (path == project or path.startswith(project + "/"))


def _stamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def live(item: dict[str, Any], now: datetime) -> bool:
    """Whether a board item belongs on the goals screen: open, or finished in the last week."""
    if item.get("archived"):
        return False
    if item.get("bucket") != "done":
        return True
    when = _stamp(item.get("last_activity_at")) or _stamp(item.get("created_at"))
    return when is not None and now - when <= RECENT_DONE


def chosen_goal(item: dict[str, Any]) -> str | None:
    """The goal a person chose for *item* (``"none"`` for no goal), from its tags."""
    for tag in item.get("tags") or ():
        if isinstance(tag, str) and tag.startswith(TAG_PREFIX):
            return tag[len(TAG_PREFIX) :] or None
    return None


def with_goal_tag(tags: list[str] | tuple[str, ...], goal: str | None) -> list[str]:
    """*tags* with the person's goal choice replaced; None clears it (automatic again)."""
    kept = [t for t in tags if not t.startswith(TAG_PREFIX)]
    return sorted(kept + [TAG_PREFIX + goal]) if goal else sorted(kept)


def assign(
    items: list[dict[str, Any]],
    goals: list[dict[str, Any]],
    project_kind: dict[str, str],
) -> dict[str, tuple[str, str]]:
    """``item id → (where, why)``; where is ``goal:<id>``, ``project:<path>`` or ``none``.

    Strongest first: a person's choice (``chosen``), the thread's Workspace project linked
    to a goal (``project``), the parent's placement (``parent``). An item bound to a project
    no goal links lands under that project; everything else is ``none``.
    """
    by_id = {i["id"]: i for i in items}
    known = {g["id"] for g in goals}
    out: dict[str, tuple[str, str]] = {}

    def own(item: dict[str, Any]) -> tuple[str, str] | None:
        choice = chosen_goal(item)
        if choice == "none":
            return ("none", "chosen")
        if choice in known:
            return (f"goal:{choice}", "chosen")
        path = item.get("workspace_path")
        if path:
            for goal in goals:
                if any(_under(path, p) for p in goal["projects"]):
                    return (f"goal:{goal['id']}", "project")
        return None

    def place(item: dict[str, Any], seen: set[str]) -> tuple[str, str]:
        if item["id"] in out:
            return out[item["id"]]
        found = own(item)
        if found is None:
            parent = by_id.get(item.get("parent_id") or "")
            if parent is not None and parent["id"] not in seen:
                where, _ = place(parent, seen | {item["id"]})
                if where != "none":
                    found = (where, "parent")
        if found is None:
            path = item.get("workspace_path")
            if path and project_kind.get(path) == "project":
                found = (f"project:{path}", "project")
            else:
                found = ("none", "none")
        out[item["id"]] = found
        return found

    for item in items:
        place(item, set())
    return out


def _counts(members: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"running": 0, "yours": 0, "done": 0, "open": 0}
    for item in members:
        bucket = item.get("bucket")
        if bucket == "done":
            counts["done"] += 1
            continue
        counts["open"] += 1
        if item.get("running") or item.get("status") == "running":
            counts["running"] += 1
        if bucket == "me" or item.get("status") in ("waiting", "review", "action", "error"):
            counts["yours"] += 1
    return counts


def _quiet(members: list[dict[str, Any]], now: datetime) -> bool:
    stamps = [_stamp(i.get("last_activity_at")) for i in members if i.get("bucket") != "done"]
    stamps = [s for s in stamps if s is not None]
    return bool(stamps) and now - max(stamps) > timedelta(days=QUIET_DAYS)


def build(
    goals: list[dict[str, Any]],
    items: list[dict[str, Any]],
    projects: dict[str, dict[str, Any]],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The goals screen: each goal with its projects and threads, then what no goal holds.

    *goals* come from :func:`parse_goals` with ``projects`` (resolved Workspace paths) set;
    *projects* maps every Workspace path to ``{"kind", "name", "milestones"}``.
    """
    now = now or datetime.now(UTC)
    shown = [i for i in items if live(i, now)]
    kinds = {p: v.get("kind", "") for p, v in projects.items()}
    placed = assign(shown, goals, kinds)
    groups: dict[str, list[dict[str, Any]]] = {}
    for item in shown:
        groups.setdefault(placed[item["id"]][0], []).append(item)

    def project_view(path: str) -> dict[str, Any]:
        info = projects.get(path, {})
        return {
            "path": path,
            "name": info.get("name") or PurePosixPath(path).name,
            "milestones": info.get("milestones"),
        }

    out_goals = []
    for goal in goals:
        members = groups.get(f"goal:{goal['id']}", [])
        out_goals.append(
            {
                **{k: goal[k] for k in ("id", "number", "title", "state", "done_when", "due")},
                **{k: goal[k] for k in ("why", "line")},
                "projects": [project_view(p) for p in goal["projects"]],
                "items": [i["id"] for i in members],
                "why_placed": {i["id"]: placed[i["id"]][1] for i in members},
                "counts": _counts(members),
                "quiet": _quiet(members, now),
            }
        )
    outside = []
    for key, members in groups.items():
        if not key.startswith("project:"):
            continue
        path = key[len("project:") :]
        if not any(i.get("bucket") != "done" for i in members):
            continue
        outside.append(
            {**project_view(path), "items": [i["id"] for i in members], "counts": _counts(members)}
        )
    outside.sort(key=lambda p: (-p["counts"]["open"], p["path"]))
    unlinked = [i["id"] for i in groups.get("none", []) if i.get("bucket") != "done"]
    return {"goals": out_goals, "outside": outside, "unlinked": unlinked}


# -- editing --------------------------------------------------------------------------------
def _clean_text(value: Any, name: str, limit: int, *, required: bool = False) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise GoalError(f"{name} must be text")
    text = " ".join(value.split())
    if required and not text:
        raise GoalError(f"{name} must not be empty")
    if len(text) > limit:
        raise GoalError(f"{name} is too long")
    return text


def _clean_due(value: Any) -> str:
    if value in (None, ""):
        return ""
    if not isinstance(value, str) or parse_due(value) is None:
        raise GoalError("due must be a date (YYYY-MM-DD or YYYY-MM)")
    return value.strip()


def _find(parsed: Parsed, gid: str) -> Block:
    for block in parsed.blocks:
        if _goal(parsed, block)["id"] == gid:
            return block
    raise GoalError("Unknown goal")


def _heading(state: str, number: str, title: str) -> str:
    mark = _STATE_MARK.get(state)
    prefix = mark or number
    return f"### {prefix} {title}" if prefix else f"### {title}"


def _set_field(lines: list[str], block: Block, name: str, value: str) -> list[str]:
    """*lines* with the goal's *name* field set to *value* (added after the heading if absent)."""
    for n in range(block.start + 1, block.end):
        found = _field(lines[n])
        if found and found[0] == name:
            if not value:
                return lines[:n] + lines[n + 1 :]
            bold = bool(_FIELD_BOLD.match(lines[n]))
            label = f"**{found[1]}**" if bold else found[1]
            return [*lines[:n], f"- {label}: {value}", *lines[n + 1 :]]
    if not value:
        return lines
    at = block.start + 1
    while at < block.end and not lines[at].strip():
        at += 1
    while at < block.end and _field(lines[at]):
        at += 1
    if at == block.start + 1 or not _field(lines[at - 1]):
        at = block.start + 1
        new = ["", f"- **{WRITE_LABEL[name]}**: {value}"]
        if at < len(lines) and lines[at].strip():
            new.append("")
        return [*lines[:at], *new, *lines[at:]]
    return [*lines[:at], f"- **{WRITE_LABEL[name]}**: {value}", *lines[at:]]


def _renumber(lines: list[str], parsed: Parsed) -> list[str]:
    """Give the active goals that carry a keycap number 1️⃣, 2️⃣, … in note order."""
    out = list(lines)
    count = 0
    for block in parsed.blocks:
        state, number, title = _split_heading(out[block.start][4:])
        if state != STATE_ACTIVE or not number:
            continue
        count += 1
        if count < len(_KEYCAPS):
            out[block.start] = _heading(state, _KEYCAPS[count], title)
    return out


def _uses_numbers(parsed: Parsed) -> bool:
    return any(_split_heading(parsed.lines[b.start][4:])[1] for b in parsed.blocks)


def _block_lines(lines: list[str], block: Block) -> list[str]:
    """A goal's lines without the blank lines and ``---`` rules that close it."""
    end = block.end
    while end > block.start + 1 and lines[end - 1].strip() in ("", "---"):
        end -= 1
    return lines[block.start : end]


def _rebuild(parsed: Parsed, order: list[int]) -> list[str]:
    lines = parsed.lines
    first = parsed.blocks[0].start
    last = parsed.blocks[-1]
    tail_start = last.start + len(_block_lines(lines, last))
    body: list[str] = []
    for index in order:
        body.extend(_block_lines(lines, parsed.blocks[index]))
        body.append("")
    tail = lines[tail_start : last.end]
    while tail and not tail[0].strip():
        tail = tail[1:]
    body.extend(tail)
    return lines[:first] + body + lines[last.end :]


def edit(text: str, action: dict[str, Any], *, sections: tuple[str, ...] = SECTIONS) -> str:
    """The note *text* after one edit; raises GoalError for an edit it cannot take.

    ``action`` is ``{"op": …}``: ``add`` (title, done_when, due), ``update`` (id and any of
    title, done_when, due), ``move`` (id, index among all goals), ``state`` (id, state) and
    ``link`` / ``unlink`` (id, link: a wikilink target such as ``02_Contexts/x/y/status``).
    Only the lines the edit is about change, except ``move``, which re-lays the section.
    """
    op = action.get("op")
    parsed = structure(text, sections)
    lines = parsed.lines
    if op == "add":
        title = _clean_text(action.get("title"), "title", MAX_TITLE_CHARS, required=True)
        done_when = _clean_text(action.get("done_when"), "done_when", MAX_FIELD_CHARS)
        due = _clean_due(action.get("due"))
        if any(_goal(parsed, b)["id"] == goal_id(title) for b in parsed.blocks):
            raise GoalError("A goal with this title already exists")
        active = [b for b in parsed.blocks if _split_heading(lines[b.start][4:])[0] == STATE_ACTIVE]
        numbered = _uses_numbers(parsed)
        number = _KEYCAPS[min(len(active) + 1, len(_KEYCAPS) - 1)] if numbered else ""
        new = [_heading(STATE_ACTIVE, number, title), ""]
        if done_when:
            new.append(f"- **{WRITE_LABEL['done_when']}**: {done_when}")
        if due:
            new.append(f"- **{WRITE_LABEL['due']}**: {due}")
        if active:
            anchor = active[-1]
            at = anchor.start + len(_block_lines(lines, anchor))
            result = [*lines[:at], "", *new, *lines[at:]]
        else:
            at = (
                parsed.section[1]
                if parsed.blocks or parsed.section != (0, len(lines))
                else len(lines)
            )
            while at > parsed.section[0] and not lines[at - 1].strip():
                at -= 1
            result = [*lines[:at], "", *new, "", *lines[at:]]
        return "\n".join(
            _renumber(result, structure("\n".join(result), sections)) if numbered else result
        )
    block = _find(parsed, str(action.get("id") or ""))
    if op == "update":
        state, number, raw = _split_heading(lines[block.start][4:])
        result = list(lines)
        if "title" in action:
            title = _clean_text(action.get("title"), "title", MAX_TITLE_CHARS, required=True)
            suffix = _STATUS_SUFFIX.search(raw)
            keep = suffix[0] if suffix else ""
            if goal_id(title) != goal_id(_STATUS_SUFFIX.sub("", raw).strip() or raw) and any(
                _goal(parsed, b)["id"] == goal_id(title) for b in parsed.blocks
            ):
                raise GoalError("A goal with this title already exists")
            mark = _MARK.match(lines[block.start][4:].strip())
            result[block.start] = _heading(state, number if not mark else "", title + keep)
        for name in ("done_when", "due"):
            if name not in action:
                continue
            value = (
                _clean_due(action[name])
                if name == "due"
                else _clean_text(action[name], name, MAX_FIELD_CHARS)
            )
            reparsed = structure("\n".join(result), sections)
            here = next(b for b in reparsed.blocks if b.start == block.start)
            result = _set_field(result, here, name, value)
        return "\n".join(result)
    if op == "state":
        wanted = action.get("state")
        if wanted not in STATES:
            raise GoalError(f"state must be one of {', '.join(STATES)}")
        _, number, title = _split_heading(lines[block.start][4:])
        numbered = _uses_numbers(parsed)
        result = list(lines)
        result[block.start] = _heading(wanted, number or ("1️⃣" if numbered else ""), title)
        if numbered:
            result = _renumber(result, structure("\n".join(result), sections))
        return "\n".join(result)
    if op == "move":
        index = action.get("index")
        if isinstance(index, bool) or not isinstance(index, int):
            raise GoalError("index must be an integer")
        order = list(range(len(parsed.blocks)))
        current = parsed.blocks.index(block)
        order.pop(current)
        order.insert(max(0, min(index, len(order))), current)
        result = _rebuild(parsed, order)
        if _uses_numbers(parsed):
            result = _renumber(result, structure("\n".join(result), sections))
        return "\n".join(result)
    if op in ("link", "unlink"):
        target = _clean_text(action.get("link"), "link", 1000, required=True)
        if "]]" in target or "[[" in target or "|" in target:
            raise GoalError("link must be a note path")
        name = PurePosixPath(target).parent.name or target
        found_line = None
        for n in range(block.start + 1, block.end):
            parsed_field = _field(lines[n])
            if parsed_field and parsed_field[0] == "projects":
                found_line = n
                break
        result = list(lines)
        if op == "link":
            link = f"[[{target}|{name}]]"
            if found_line is None:
                return "\n".join(_set_field(result, block, "projects", link))
            if target in [m[1].strip() for m in _WIKILINK.finditer(lines[found_line])]:
                return text
            result[found_line] = lines[found_line].rstrip() + f"、{link}"
            return "\n".join(result)
        if found_line is None:
            raise GoalError("This project is not linked on a projects line")
        kept = [m[0] for m in _WIKILINK.finditer(lines[found_line]) if m[1].strip() != target]
        if len(kept) == len(list(_WIKILINK.finditer(lines[found_line]))):
            raise GoalError("This project is not linked on a projects line")
        if not kept:
            return "\n".join(result[:found_line] + result[found_line + 1 :])
        label = _field(lines[found_line])
        bold = bool(_FIELD_BOLD.match(lines[found_line]))
        head = f"**{label[1]}**" if bold and label else (label[1] if label else "プロジェクト")
        result[found_line] = f"- {head}: " + "、".join(kept)
        return "\n".join(result)
    raise GoalError("Unknown goal edit")
