"""Automatic triage: a fast model reads each open item and guesses how much it matters.

A human rarely fills in priority and due date for every thread, so the board
used to sort most of them as "P2, no deadline". Here a small model (Haiku by
default) reads an item's title and its latest messages and answers with a
priority, a deadline if the conversation names one, why, and the human's next
step. The answer is stored apart from ``work_items`` so it can never overwrite
what a person chose: a priority or due date a human set always wins, and
setting one pins it against later guesses.

The model runs as a one-shot ``claude -p`` with tools, hooks and settings off,
in a scratch directory: it reads the text it is given and nothing else.
An item is triaged again only when it has new activity.

``focus_score`` turns the result into one ranking, "what should I do now",
for the console's Focus view.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import tempfile
import time
from collections.abc import Awaitable, Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import aiosqlite

from claude_code_core.child_env import strip_transport_credentials
from claude_code_core.runner import resolve_cli_argv

from .work_repo import DEFAULT_PRIORITY

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "haiku"
ASK_TIMEOUT_SECONDS = 60.0
#: Seconds between passes over the board.
INTERVAL_SECONDS = 60.0
#: Items triaged per pass; a backlog drains over a few minutes, not in one burst.
PER_PASS = 6
CONCURRENCY = 3
#: A failed item is retried after this long, even without new activity.
RETRY_SECONDS = 30 * 60
MAX_MESSAGES = 16
MAX_MESSAGE_CHARS = 700
MAX_PROMPT_CHARS = 9000
MAX_NOTE_CHARS = 120
MAX_PLACE_CHARS = 40
#: Place labels offered to the model, so it reuses "home" instead of inventing "house".
MAX_KNOWN_PLACES = 20

#: Statuses that are not work to triage.
_SKIP_STATUSES = frozenset({"done", "someday", "running"})
#: Statuses where the next move is the human's.
_HUMAN_STATUSES = frozenset({"waiting", "review", "action", "error"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_triage (
    item_id TEXT PRIMARY KEY,
    priority INTEGER,
    due_at TEXT,
    reason TEXT,
    next_step TEXT,
    basis TEXT,
    model TEXT,
    updated_at TEXT,
    pinned_priority INTEGER NOT NULL DEFAULT 0,
    pinned_due INTEGER NOT NULL DEFAULT 0
);
"""
#: Columns added after the first release of the table: (name, SQL type).
_ADDED_COLUMNS = (
    ("ready", "INTEGER"),
    ("ready_at", "TEXT"),
    ("waiting_for", "TEXT"),
    ("place", "TEXT"),
    ("pinned_place", "INTEGER NOT NULL DEFAULT 0"),
)
#: Part of every basis: a new prompt re-triages everything once.
PROMPT_VERSION = "3"

_PROMPT = """\
You triage a person's work queue. Read one item and judge three separate things:
how important it is, whether the person can act on it right now or must wait,
and whether it can only be done in one place or on one device.

Now: {now} (the person's local time)
Item title: {title}
Item status: {status}
Places this person already uses: {places}
Conversation, oldest first (the person and their AI agent):
{messages}

Answer with one JSON object and nothing else:
{{"priority": 0-3, "due": "YYYY-MM-DD" | "YYYY-MM-DDTHH:MM" | null, "reason": "…", "next": "…",
 "ready": true | false, "ready_at": "YYYY-MM-DDTHH:MM" | null, "waiting_for": "…",
 "place": "…" | null}}

priority: 0 = must happen today, blocks others, a customer or a hard deadline is waiting;
1 = this week, or someone is waiting; 2 = normal; 3 = low, someday, or just information.
due: only a deadline the conversation states or clearly implies, in local time; otherwise null.
reason: why this priority, at most 60 characters.
next: the single next thing the person (not the agent) should do, at most 60 characters.
ready: true only if the person can do "next" right now, at their desk, without waiting.
false if "next" has to wait: for a time or date ("tomorrow at 20:00", "after the meeting"),
for another person's reply or work, for an agent or a job to finish, or for an event.
Importance does not make an item ready: an urgent item that must wait is ready: false.
ready_at: when "next" becomes possible, if the conversation says or implies it; otherwise null.
waiting_for: when ready is false, what the person waits for, at most 40 characters; else "".
place: where "next" can only be done, if it is bound to one: a location (home, the office),
a device or network (the company PC, a lab machine, a phone), or similar. Reuse a label from
"Places this person already uses" when one fits; otherwise at most 12 characters.
null when it can be done anywhere. Being at a place is not waiting: ready stays true.
Write reason, next, waiting_for and place in the conversation's language.
"""

_JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class TriageResult:
    """What the model answered for one item."""

    priority: int
    due_at: str | None
    reason: str
    next_step: str
    ready: bool = True
    ready_at: str | None = None
    waiting_for: str = ""
    place: str | None = None


@dataclass(frozen=True)
class Triage:
    """One row of ``ai_triage``."""

    item_id: str
    priority: int | None
    due_at: str | None
    reason: str | None
    next_step: str | None
    basis: str | None
    updated_at: str | None
    pinned_priority: bool
    pinned_due: bool
    ready: bool | None = None
    ready_at: str | None = None
    waiting_for: str | None = None
    place: str | None = None
    pinned_place: bool = False


class TriageRepository:
    """The model's guesses, one row per item, plus which fields a human pinned."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def init_db(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
            have = {row[1] for row in await db.execute_fetchall("PRAGMA table_info(ai_triage)")}
            for name, kind in _ADDED_COLUMNS:
                if name not in have:
                    await db.execute(f"ALTER TABLE ai_triage ADD COLUMN {name} {kind}")
            await db.commit()

    async def all(self) -> dict[str, Triage]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall("SELECT * FROM ai_triage")
        return {r["item_id"]: _to_triage(r) for r in rows}

    async def save(self, item_id: str, result: TriageResult, *, basis: str, model: str) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO ai_triage (item_id, priority, due_at, reason, next_step, ready,"
                " ready_at, waiting_for, place, basis, model, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(item_id) DO UPDATE SET priority = excluded.priority,"
                " due_at = excluded.due_at, reason = excluded.reason,"
                " next_step = excluded.next_step, ready = excluded.ready,"
                " ready_at = excluded.ready_at, waiting_for = excluded.waiting_for,"
                " place = excluded.place,"
                " basis = excluded.basis, model = excluded.model, updated_at = excluded.updated_at",
                (
                    item_id,
                    result.priority,
                    result.due_at,
                    result.reason,
                    result.next_step,
                    int(result.ready),
                    result.ready_at,
                    result.waiting_for,
                    result.place,
                    basis,
                    model,
                    _now(),
                ),
            )
            await db.commit()

    async def pin(
        self, item_id: str, *, priority: bool = False, due: bool = False, place: bool = False
    ) -> None:
        """A human set these fields: the model's guess no longer applies to them."""
        if not priority and not due and not place:
            return
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO ai_triage (item_id, pinned_priority, pinned_due, pinned_place)"
                " VALUES (?, ?, ?, ?) ON CONFLICT(item_id) DO UPDATE SET"
                " pinned_priority = MAX(pinned_priority, excluded.pinned_priority),"
                " pinned_due = MAX(pinned_due, excluded.pinned_due),"
                " pinned_place = MAX(pinned_place, excluded.pinned_place)",
                (item_id, int(priority), int(due), int(place)),
            )
            await db.commit()


def _to_triage(row: aiosqlite.Row) -> Triage:
    return Triage(
        item_id=row["item_id"],
        priority=row["priority"],
        due_at=row["due_at"],
        reason=row["reason"],
        next_step=row["next_step"],
        basis=row["basis"],
        updated_at=row["updated_at"],
        pinned_priority=bool(row["pinned_priority"]),
        pinned_due=bool(row["pinned_due"]),
        ready=None if row["ready"] is None else bool(row["ready"]),
        ready_at=row["ready_at"],
        waiting_for=row["waiting_for"],
        place=row["place"],
        pinned_place=bool(row["pinned_place"]),
    )


# -- the model -------------------------------------------------------------


def build_prompt(
    *,
    title: str,
    status: str,
    messages: Sequence[tuple[str, str]],
    now: datetime,
    places: Iterable[str] = (),
) -> str:
    """The prompt for one item. *messages* are (who, text), oldest first."""
    lines = [
        f"[{who}] {' '.join(text.split())[:MAX_MESSAGE_CHARS]}"
        for who, text in messages[-MAX_MESSAGES:]
        if text.strip()
    ]
    body = "\n".join(lines)
    if len(body) > MAX_PROMPT_CHARS:
        body = "…" + body[-MAX_PROMPT_CHARS:]
    return _PROMPT.format(
        now=now.astimezone().strftime("%Y-%m-%d %H:%M (%a)"),
        title=" ".join(title.split())[:200] or "(untitled)",
        status=status,
        places=", ".join(sorted({p for p in places if p})[:MAX_KNOWN_PLACES]) or "(none yet)",
        messages=body or "(no messages)",
    )


def _parse_due(value: Any, now: datetime) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            # A date with no time is due by the end of that day.
            local = datetime.fromisoformat(text).replace(hour=23, minute=59)
        else:
            local = datetime.fromisoformat(text)
    except ValueError:
        return None
    moment = local.astimezone(UTC) if local.tzinfo else local.astimezone().astimezone(UTC)
    # A "deadline" years away or long past is a misreading, not a deadline.
    if not now - timedelta(days=365) <= moment <= now + timedelta(days=730):
        return None
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _short(value: Any) -> str:
    return " ".join(str(value or "").split())[:MAX_NOTE_CHARS]


def parse_reply(raw: str, *, now: datetime) -> TriageResult | None:
    """The model's JSON, validated; None when it is not usable."""
    match = _JSON_OBJECT.search(raw or "")
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    priority = data.get("priority")
    if isinstance(priority, bool) or not isinstance(priority, int | float):
        return None
    return TriageResult(
        priority=max(0, min(3, int(priority))),
        due_at=_parse_due(data.get("due"), now),
        reason=_short(data.get("reason")),
        next_step=_short(data.get("next")),
        ready=data.get("ready") is not False,
        ready_at=_parse_due(data.get("ready_at"), now),
        waiting_for=_short(data.get("waiting_for"))[:60],
        place=_short(data.get("place"))[:MAX_PLACE_CHARS] or None,
    )


def triage_env() -> dict[str, str]:
    """The environment for the model call: no relay or console credentials."""
    env = strip_transport_credentials(dict(os.environ))
    return {k: v for k, v in env.items() if not k.startswith("CCDB_CONSOLE")}


async def ask_model(
    prompt: str,
    *,
    command: str = "claude",
    model: str = DEFAULT_MODEL,
    timeout: float = ASK_TIMEOUT_SECONDS,
) -> str | None:
    """One ``claude -p`` call with no tools, hooks or settings; its stdout, or None."""
    argv = resolve_cli_argv(command)
    if argv is None:
        logger.warning("console triage: cannot start %s on this platform", command)
        return None
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            "-p",
            "--model",
            model,
            "--tools",
            "",
            "--setting-sources",
            "",
            "--settings",
            '{"disableAllHooks": true}',
            "--",
            prompt,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=triage_env(),
            cwd=tempfile.gettempdir(),
        )
    except (OSError, ValueError) as exc:
        logger.warning("console triage: could not start %s: %s", command, exc)
        return None
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.communicate()
        logger.warning("console triage: the model did not answer in %ss", timeout)
        return None
    if proc.returncode != 0:
        logger.warning(
            "console triage: exit %s: %s", proc.returncode, stderr.decode(errors="replace")[:300]
        )
        return None
    return stdout.decode(errors="replace")


# -- the board -------------------------------------------------------------


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def apply_triage(
    items: Iterable[dict[str, Any]], rows: dict[str, Triage], *, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Copies of *items* with the model's guesses filled in where a human set nothing.

    A filled-in field is marked (``priority_by`` / ``due_by`` = ``"ai"``) so the
    client can tell a guess from a decision. A priority other than the default
    counts as a human's even without a pin: it was set before triage existed.
    """
    now = now or datetime.now(UTC)
    out = []
    for item in items:
        row = rows.get(item["id"])
        new = dict(item)
        if row is not None and row.priority is not None:
            new["ai"] = {
                "priority": row.priority,
                "due_at": row.due_at,
                "reason": row.reason,
                "next_step": row.next_step,
                "ready": row.ready,
                "ready_at": row.ready_at,
                "waiting_for": row.waiting_for,
                "place": row.place,
                "updated_at": row.updated_at,
            }
            human_priority = row.pinned_priority or item.get("priority") != DEFAULT_PRIORITY
            if not human_priority:
                new["priority"] = row.priority
                new["priority_by"] = "ai"
            if row.place and not item.get("place") and not row.pinned_place:
                new["place"] = row.place
                new["place_by"] = "ai"
            if row.due_at and not item.get("due_at") and not row.pinned_due:
                due = _parse_ts(row.due_at)
                new["due_at"] = row.due_at
                new["due_by"] = "ai"
                new["overdue"] = (
                    due is not None and due < now and item.get("status") not in _SKIP_STATUSES
                )
        new["focus"] = focus_score(new, now=now)
        new["actionable"] = actionable(row, now=now) if new["focus"] is not None else None
        out.append(new)
    return out


def actionable(row: Triage | None, *, now: datetime) -> bool:
    """Whether the human can act on the item now: a second axis, apart from importance.

    Waiting (for a time, a person, a job) is not actionable until the time it
    named has come. With no guess yet, an item counts as actionable.
    """
    if row is None or row.ready is None or row.ready:
        return True
    ready_at = _parse_ts(row.ready_at)
    return ready_at is not None and ready_at <= now


def focus_score(item: dict[str, Any], *, now: datetime) -> int | None:
    """How important this item is to the human; None when it is not theirs at all.

    Priority decides first (P0 above P1 whatever else is true), then how close
    the deadline is, then whose move it is. Each weight is smaller than the gap
    between two priorities, so a lower priority never overtakes a higher one.
    Work an agent is doing, finished or parked work, and snoozed items are out.
    Whether it can be done now is a separate axis: see ``actionable``.
    """
    if item.get("bucket") in ("done", "snoozed", "ai") or item.get("status") in _SKIP_STATUSES:
        return None
    score = (3 - int(item.get("priority", DEFAULT_PRIORITY))) * 100
    due = _parse_ts(item.get("due_at"))
    if due is not None:
        left = due - now
        if left < timedelta(0):
            score += 40
        elif left < timedelta(hours=24):
            score += 30
        elif left < timedelta(days=3):
            score += 15
        elif left < timedelta(days=7):
            score += 5
    if item.get("status") in _HUMAN_STATUSES:
        score += 20
    return score


def known_places(items: Iterable[dict[str, Any]], rows: dict[str, Triage]) -> list[str]:
    """Place labels in use: the ones people set first, then the model's."""
    human = [i["place"] for i in items if i.get("place")]
    guessed = [r.place for r in rows.values() if r.place]
    return list(dict.fromkeys(human + guessed))


# -- the loop --------------------------------------------------------------

ReadMessages = Callable[[dict[str, Any]], Awaitable[list[tuple[str, str]]]]
Ask = Callable[[str], Awaitable[str | None]]


def _basis(item: dict[str, Any]) -> str:
    """What the triage was based on: it is redone when this changes."""
    stamp = item.get("last_activity_at") or item.get("created_at") or ""
    return f"{PROMPT_VERSION}|{stamp}|{item.get('status')}"


class Triager:
    """Triages items with new activity, a few per pass, in the background."""

    def __init__(
        self,
        repo: TriageRepository,
        *,
        ask: Ask,
        read_messages: ReadMessages,
        model: str = DEFAULT_MODEL,
        per_pass: int = PER_PASS,
        concurrency: int = CONCURRENCY,
        registered_places: Callable[[], Awaitable[list[str]]] | None = None,
    ) -> None:
        self.repo = repo
        self._ask = ask
        self._read = read_messages
        self._model = model
        self._per_pass = per_pass
        self._concurrency = concurrency
        self._registered = registered_places
        self._failed: dict[str, tuple[str, float]] = {}
        self._task: asyncio.Task[None] | None = None

    def due(self, items: Iterable[dict[str, Any]], rows: dict[str, Triage]) -> list[dict[str, Any]]:
        """The items to triage this pass, most recently active first."""
        now = time.monotonic()
        todo = []
        for item in items:
            if item.get("status") in _SKIP_STATUSES or item.get("archived"):
                continue
            basis = _basis(item)
            row = rows.get(item["id"])
            if row is not None and row.basis == basis:
                continue
            failed = self._failed.get(item["id"])
            if failed and failed[0] == basis and now - failed[1] < RETRY_SECONDS:
                continue
            todo.append(item)
        todo.sort(key=lambda i: i.get("last_activity_at") or "", reverse=True)
        return todo[: self._per_pass]

    async def run_once(self, items: Sequence[dict[str, Any]]) -> int:
        """Triage what is due; the number of items that got an answer."""
        rows = await self.repo.all()
        batch = self.due(items, rows)
        managed = await self._registered() if self._registered is not None else []
        places = list(dict.fromkeys(managed + known_places(items, rows)))
        gate = asyncio.Semaphore(self._concurrency)

        async def one(item: dict[str, Any]) -> bool:
            async with gate:
                return await self._triage(item, places)

        results = await asyncio.gather(*(one(i) for i in batch))
        return sum(results)

    async def _triage(self, item: dict[str, Any], places: Iterable[str] = ()) -> bool:
        basis = _basis(item)
        try:
            messages = await self._read(item)
            prompt = build_prompt(
                title=item.get("title") or item.get("thread_name") or "",
                status=str(item.get("status")),
                messages=messages,
                now=datetime.now(UTC),
                places=places,
            )
            result = parse_reply(await self._ask(prompt) or "", now=datetime.now(UTC))
            if result is None:
                raise ValueError("no usable answer")
            await self.repo.save(item["id"], result, basis=basis, model=self._model)
        except Exception as exc:
            logger.info("console triage: %s skipped: %s", item["id"], exc)
            self._failed[item["id"]] = (basis, time.monotonic())
            return False
        self._failed.pop(item["id"], None)
        return True

    def start(self, board: Callable[[], Awaitable[list[dict[str, Any]]]], interval: float) -> None:
        async def loop() -> None:
            while True:
                try:
                    await self.run_once(await board())
                except Exception:
                    logger.exception("console triage: pass failed")
                await asyncio.sleep(interval)

        self._task = asyncio.ensure_future(loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
