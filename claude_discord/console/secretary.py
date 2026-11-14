"""Secretary mode: the board narrowed to what a person must decide now (ADR-0024).

The classic Console lists agent threads; a person juggling dozens of them opens
each one that waits on them and re-reads it to learn what is being asked. This
module turns the board into the person's unit instead — decisions — built from the
**decision requests** the asking agents wrote themselves (:mod:`..decision_request`):

* ``decide`` — cards for questions (❓) and tasks (📋), ranked like Focus;
* ``review`` — cards for deliverables to look at (👀);
* ``unexplained`` — items waiting on the person whose agent wrote no request. They are
  listed apart, by title only: v1 summarised them with a cheap model, and a summary of a
  thread's last message (a usage footer, a CI notice) explained nothing;
* ``working`` — what agents are doing, one line each; no progress text;
* ``done_today`` — finished today, including completion candidates to confirm.

A request shows only while it still stands: while its thread carries a ❓/👀/📋 marker and is
not running. The person's reply clears the marker and the agent starts running, so the card
goes away even before anything closes it.

The cards are sent whole, not only the first few: a family's situation panel shows the same
cards for its own threads (situation.js), so the Console reads one list for both screens.

Like :mod:`.guide`, it reads only what it is given and asks no model, so every poll costs
nothing and gives the same answer twice.

:class:`SecretaryEventRepository` keeps the trial's metrics: which threads were
opened, in which mode, and which answers were sent from a card. Metadata only.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

import aiosqlite
from aiohttp import web

from .guide import reasons

if TYPE_CHECKING:
    from .server import ConsoleServer

MAX_CARDS = 100
MAX_UNEXPLAINED = 20
MAX_WORKING = 20
MAX_DONE = 20

#: The markers a request rides on.
_MARKED_STATUSES = frozenset({"waiting", "review", "action"})
#: Statuses where the next move is the person's, request or not.
_PERSON_STATUSES = _MARKED_STATUSES | {"error"}
_FINISHED_STATUSES = frozenset({"done", "candidate"})


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _order(item: Mapping[str, Any]) -> tuple[Any, ...]:
    """Focus's order, a person's own threads before delegated ones."""
    return (
        bool(item.get("parent_id")),
        -int(item.get("focus") or 0),
        item.get("due_at") or "9999",
        item.get("last_activity_at") or "",
    )


def _title(item: Mapping[str, Any]) -> str:
    return item.get("title") or item.get("thread_name") or ""


def _stands(request: Mapping[str, Any], item: Mapping[str, Any] | None) -> bool:
    """Whether *request* still asks something of the person (see the module docstring)."""
    if item is None or item.get("archived"):
        return False
    status = item.get("status")
    if status in _FINISHED_STATUSES or status == "running" or item.get("running"):
        return False
    return status in _MARKED_STATUSES


def _card(request: Mapping[str, Any], item: Mapping[str, Any], *, now: datetime) -> dict[str, Any]:
    return {
        "request_id": request["id"],
        "id": item["id"],
        "thread_id": request.get("thread_id"),
        "item_title": _title(item),
        "kind": request.get("kind"),
        "origin": request.get("origin"),
        "card": request.get("card") or {},
        "detail": request.get("detail") or {},
        "since": request.get("created_at"),
        "reasons": reasons(item, now=now),
        "place": item.get("place"),
        "parent_id": item.get("parent_id"),
    }


def _line(item: Mapping[str, Any]) -> dict[str, Any]:
    return {"id": item["id"], "title": _title(item), "since": item.get("last_activity_at")}


def _local_day(value: str | None) -> date | None:
    moment = _parse(value)
    return moment.astimezone().date() if moment is not None else None


def build_secretary(
    items: Iterable[dict[str, Any]],
    requests: Iterable[Mapping[str, Any]] = (),
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The secretary home for the board *items* and the open *requests*, deterministically."""
    now = now or datetime.now(UTC)
    items = [i for i in items if not i.get("archived")]
    by_thread = {str(i.get("thread_id")): i for i in items if i.get("thread_id") is not None}
    standing = [
        (r, by_thread[str(r.get("thread_id"))])
        for r in requests
        if _stands(r, by_thread.get(str(r.get("thread_id"))))
    ]
    standing.sort(key=lambda pair: (_order(pair[1]), pair[0]["id"]))
    cards = [_card(r, i, now=now) for r, i in standing]
    decide = [c for c in cards if c["kind"] != "review"]
    review = [c for c in cards if c["kind"] == "review"]
    explained = {i["id"] for _, i in standing}
    unexplained = sorted(
        (
            i
            for i in items
            if i.get("status") in _PERSON_STATUSES
            and i.get("focus") is not None
            and i["id"] not in explained
        ),
        key=_order,
    )
    working = sorted(
        (i for i in items if i.get("status") == "running" or i.get("running")),
        key=lambda i: i.get("last_activity_at") or "",
        reverse=True,
    )
    today = now.astimezone().date()
    finished = sorted(
        (
            i
            for i in items
            if i.get("status") in _FINISHED_STATUSES
            and _local_day(i.get("last_activity_at")) == today
        ),
        key=lambda i: i.get("last_activity_at") or "",
        reverse=True,
    )
    return {
        "decide": decide[:MAX_CARDS],
        "decide_more": max(0, len(decide) - MAX_CARDS),
        "review": review[:MAX_CARDS],
        "review_more": max(0, len(review) - MAX_CARDS),
        "unexplained": [
            {**_line(i), "status": i.get("status")} for i in unexplained[:MAX_UNEXPLAINED]
        ],
        "unexplained_more": max(0, len(unexplained) - MAX_UNEXPLAINED),
        "working": [_line(i) for i in working[:MAX_WORKING]],
        "working_more": max(0, len(working) - MAX_WORKING),
        "done_today": [
            {**_line(i), "candidate": i.get("status") == "candidate"} for i in finished[:MAX_DONE]
        ],
        "calm": not decide and not review and not unexplained,
    }


# -- trial metrics ----------------------------------------------------------

EVENT_KINDS = frozenset({"open", "answer"})
MODES = frozenset({"classic", "secretary"})
_ITEM_ID = re.compile(r"^[A-Za-z0-9_:.-]{1,128}$")
#: A wait longer than this is a stale clock, not a measurement.
MAX_WAIT_SECONDS = 400 * 24 * 3600
MAX_STATS_DAYS = 60
#: Rows older than this are dropped when the table is opened.
RETENTION = timedelta(days=180)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS console_secretary_events (
    at TEXT NOT NULL,
    kind TEXT NOT NULL,
    item_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    waited_seconds INTEGER
);
CREATE INDEX IF NOT EXISTS idx_console_secretary_events_at ON console_secretary_events(at);
"""


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class SecretaryEventRepository:
    """Which threads a person opened and which answers a card sent. One connection per call."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def init_db(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
            await db.execute(
                "DELETE FROM console_secretary_events WHERE at < ?",
                (_stamp(datetime.now(UTC) - RETENTION),),
            )
            await db.commit()

    async def record(
        self,
        kind: str,
        item_id: str,
        *,
        mode: str,
        waited_seconds: int | None = None,
        at: datetime | None = None,
    ) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO console_secretary_events (at, kind, item_id, mode, waited_seconds)"
                " VALUES (?, ?, ?, ?, ?)",
                (_stamp(at or datetime.now(UTC)), kind, item_id, mode, waited_seconds),
            )
            await db.commit()

    async def stats(self, *, days: int = 7, now: datetime | None = None) -> dict[str, Any]:
        """Per local day, oldest first, only days with events."""
        now = now or datetime.now(UTC)
        since = _stamp(now - timedelta(days=days))
        async with aiosqlite.connect(self._db_path) as db:
            rows = await db.execute_fetchall(
                "SELECT at, kind, item_id, mode, waited_seconds FROM console_secretary_events"
                " WHERE at >= ? ORDER BY at",
                (since,),
            )
        per_day: dict[date, dict[str, Any]] = {}
        for at, kind, item_id, mode, waited in rows:
            day = _local_day(at)
            if day is None:
                continue
            bucket = per_day.setdefault(
                day, {"opened": set(), "by_mode": {}, "answers": 0, "waits": []}
            )
            if kind == "open":
                bucket["opened"].add(item_id)
                bucket["by_mode"].setdefault(mode, set()).add(item_id)
            elif kind == "answer":
                bucket["answers"] += 1
                if waited is not None:
                    bucket["waits"].append(waited)
        return {
            "days": [
                {
                    "date": day.isoformat(),
                    "opened_threads": len(b["opened"]),
                    "by_mode": {m: len(ids) for m, ids in sorted(b["by_mode"].items())},
                    "card_answers": b["answers"],
                    "answer_wait_seconds_median": (
                        round(statistics.median(b["waits"])) if b["waits"] else None
                    ),
                }
                for day, b in sorted(per_day.items())
            ]
        }


class SecretaryRoutes:
    """``/console/api/secretary/*``: record a trial event, read the trial's numbers."""

    def __init__(self, server: ConsoleServer, repo: SecretaryEventRepository | None) -> None:
        self.server = server
        self.repo = repo

    async def record(self, request: web.Request) -> web.Response:
        if self.repo is None:
            return web.json_response({"error": "metrics are not available"}, status=404)
        body, err = await self.server._json(request)
        if err:
            return err
        event, problem = _event(body)
        if problem:
            return web.json_response({"error": problem}, status=400)
        await self.repo.record(**event)
        return web.Response(status=204)

    async def stats(self, request: web.Request) -> web.Response:
        if self.repo is None:
            return web.json_response({"days": []})
        try:
            days = int(request.query.get("days", "7"))
        except ValueError:
            return web.json_response({"error": "days must be an integer"}, status=400)
        days = max(1, min(MAX_STATS_DAYS, days))
        return web.json_response(await self.repo.stats(days=days))


def _event(body: Mapping[str, Any]) -> tuple[dict[str, Any], str | None]:
    kind = body.get("kind")
    if kind not in EVENT_KINDS:
        return {}, "kind must be open or answer"
    item_id = body.get("item_id")
    if not isinstance(item_id, str) or not _ITEM_ID.match(item_id):
        return {}, "item_id is invalid"
    mode = body.get("mode", "classic")
    if mode not in MODES:
        return {}, "mode must be classic or secretary"
    waited = body.get("waited_seconds")
    if waited is not None and (
        isinstance(waited, bool)
        or not isinstance(waited, int)
        or not 0 <= waited <= MAX_WAIT_SECONDS
    ):
        return {}, "waited_seconds is invalid"
    return {"kind": kind, "item_id": item_id, "mode": mode, "waited_seconds": waited}, None
