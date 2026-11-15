"""Canvas mode: one resident secretary agent that lays out the Console (ADR-0025).

The secretary is an ordinary Console conversation. This module only decides **when** it
runs and **what it is shown**; what it concludes and what it places is the agent's job.

* :func:`fingerprint` / :func:`compare` — what changed on the board since the last look. No
  model: status, last activity (of idle threads) and open decision requests.
* :class:`CanvasSecretary` — the watcher loop. It records changes, and wakes the secretary
  with one short message only when something changed, the secretary is idle, the minimum
  interval has passed, the daily cap allows it and the person has not paused it.
* :meth:`CanvasSecretary.digest` — what a wake reads: only the threads that changed since the
  secretary's cursor, each with the secretary's own note, its open request, its family's
  situation and its last few messages.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
import secrets
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from .canvas_charter import CHARTER_OPENING, charter, turn_brief, wake_message
from .canvas_repo import CanvasRepository

if TYPE_CHECKING:
    from .host import ConsoleSessionHost

logger = logging.getLogger(__name__)

STATE_THREAD = "secretary_thread"
STATE_CURSOR = "cursor"
STATE_PAUSED = "paused"
#: The secretary's write key: only its conversation may change the canvas, notes or cursor.
STATE_TOKEN = "token"
#: The focus session the secretary prepared (JSON), until the person finishes it.
STATE_SESSION = "session"
#: Wake limits the person set from the canvas; they override the environment, live.
STATE_LIMITS = "limits"
#: How much of the conversation a fresh turn carries.
RECENT_MESSAGES = 6
RECENT_CHARS = 400
STATE_LAST_WAKE = "last_wake_at"

TICK_SECONDS = 30
#: What the person may set from the canvas (and the environment is clamped to).
LIMIT_RANGES = {"max_wakes_per_day": (0, 500), "min_interval": (60, 86400)}
#: How many changed threads a digest describes in full (with recent messages).
DIGEST_FULL = 15
DIGEST_MESSAGES = 4
DIGEST_MESSAGE_CHARS = 500
DIGEST_ALL_LIMIT = 150
SECRETARY_TITLE = "🗂 Secretary (canvas)"
WATCHER_AUTHOR = "canvas watcher"
_FINISHED = frozenset({"done"})


@dataclass(frozen=True)
class CanvasConfig:
    """Limits for the resident secretary. Read from the environment; defaults are modest."""

    backend: str = "claude"
    model: str | None = "sonnet"
    effort: str | None = None
    min_interval: int = 600
    max_wakes_per_day: int = 8

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> CanvasConfig:
        env = os.environ if env is None else env

        def number(name: str, default: int, low: int, high: int) -> int:
            raw = (env.get(name) or "").strip()
            if not raw:
                return default
            try:
                return max(low, min(high, int(raw)))
            except ValueError:
                logger.warning("canvas: %s=%r is not a number; using %d", name, raw, default)
                return default

        model = (env.get("CCDB_CANVAS_SECRETARY_MODEL") or "").strip()
        # The default cap after the 2026-11-15 trial; the canvas can change it live.
        return cls(
            backend=(env.get("CCDB_CANVAS_SECRETARY_BACKEND") or "").strip() or "claude",
            model=model or cls.model,
            effort=(env.get("CCDB_CANVAS_SECRETARY_EFFORT") or "").strip() or None,
            min_interval=number("CCDB_CANVAS_MIN_INTERVAL", cls.min_interval, 60, 86400),
            max_wakes_per_day=number(
                "CCDB_CANVAS_MAX_WAKES_PER_DAY", cls.max_wakes_per_day, 0, 500
            ),
        )


# -- what changed ---------------------------------------------------------------
Print = tuple[str, str, tuple[int, ...]]


def fingerprint(
    items: Iterable[Mapping[str, Any]],
    requests: Iterable[Mapping[str, Any]],
    *,
    exclude: str | None = None,
) -> dict[str, Print]:
    """Per item: status, last activity (only while idle) and its open request ids.

    A running thread's activity moves with every streamed line; counting it would wake the
    secretary for work in progress. Its status change at the end of the turn is enough.
    """
    by_thread: dict[str, list[int]] = {}
    for request in requests:
        by_thread.setdefault(f"t{request.get('thread_id')}", []).append(int(request["id"]))
    out: dict[str, Print] = {}
    for item in items:
        item_id = str(item.get("id"))
        if item_id == exclude:
            continue
        status = str(item.get("status") or "")
        activity = "" if item.get("running") else str(item.get("last_activity_at") or "")
        out[item_id] = (status, activity, tuple(sorted(by_thread.get(item_id, []))))
    return out


#: More threads than this appearing (or vanishing) between two looks is a partial board read —
#: right after a restart the chat threads are not loaded yet — not real news.
BURST = 10


def compare(old: Mapping[str, Print], new: Mapping[str, Print]) -> list[tuple[str, str]]:
    """``(item_id, what)`` for every difference, in a few plain words."""
    changes: list[tuple[str, str]] = []
    appeared = new.keys() - old.keys()
    vanished = old.keys() - new.keys()
    for item_id, (status, activity, requests) in new.items():
        before = old.get(item_id)
        if before is None:
            if len(appeared) <= BURST:
                changes.append((item_id, f"new ({status})"))
            continue
        old_status, old_activity, old_requests = before
        if status != old_status:
            changes.append((item_id, f"status {old_status} → {status}"))
        if set(requests) - set(old_requests):
            changes.append((item_id, "decision request opened"))
        if set(old_requests) - set(requests):
            changes.append((item_id, "decision request closed"))
        if activity and activity != old_activity and status == old_status:
            changes.append((item_id, "new activity"))
    if len(vanished) <= BURST:
        changes.extend((item_id, "gone from the board") for item_id in vanished)
    return changes


def _day(now: datetime) -> str:
    return now.astimezone().strftime("%Y-%m-%d")


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


def _short_request(request: Mapping[str, Any]) -> dict[str, Any]:
    card = request.get("card") or {}
    return {
        "request_id": request.get("id"),
        "kind": request.get("kind"),
        "title": card.get("title"),
        "context": card.get("context"),
        "options": [
            {
                "label": o.get("label"),
                "effect": o.get("effect"),
                "recommended": o.get("recommended"),
            }
            for o in card.get("options") or []
        ],
        "deadline": card.get("deadline"),
        "since": request.get("created_at"),
    }


class CanvasSecretary:
    """The resident secretary's clock and memory. The agent does the thinking."""

    def __init__(
        self,
        repo: CanvasRepository,
        *,
        board: Callable[[], Awaitable[list[dict[str, Any]]]],
        requests: Callable[[], Awaitable[list[dict[str, Any]]]],
        messages: Callable[[dict[str, Any]], Awaitable[list[tuple[str, str]]]],
        situation: Callable[[int], Awaitable[dict[str, Any] | None]],
        host: ConsoleSessionHost | None,
        settings: Any = None,
        history: Callable[[int, int], Awaitable[list[tuple[str, bool, str]]]] | None = None,
        config: CanvasConfig | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.repo = repo
        self._board = board
        self._requests = requests
        self._messages = messages
        self._situation = situation
        self._host = host
        self._settings = settings
        #: Reads the secretary's own conversation (author, is_bot, content), oldest first.
        self._history = history
        self.config = config or CanvasConfig.from_env()
        self._clock = clock
        self._seen: dict[str, Print] | None = None
        self._secretary_running = False
        self._task: asyncio.Task[None] | None = None
        self._start_lock = asyncio.Lock()

    # -- lifecycle -----------------------------------------------------------
    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.ensure_future(self._loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._task
            self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("canvas: watcher tick failed")
            await asyncio.sleep(TICK_SECONDS)

    # -- the secretary's conversation ------------------------------------------
    async def thread_key(self) -> int | None:
        raw = await self.repo.get_state(STATE_THREAD)
        return int(raw) if raw and raw.isdigit() else None

    async def item_id(self) -> str | None:
        key = await self.thread_key()
        return f"t{key}" if key is not None else None

    async def start_secretary(self, who: str, *, restart: bool = False) -> int:
        """Open the secretary's conversation (once), or a fresh one with *restart*.

        The notes and widgets stay: they live here, not in the conversation.
        """
        if self._host is None:
            raise RuntimeError("this deployment cannot run console conversations")
        async with self._start_lock:
            key = await self.thread_key()
            if key is not None and not restart and await self._host.owns(key):
                return key
            cursor = await self.repo.last_change()
            token = secrets.token_urlsafe(24)
            await self.repo.set_state(STATE_TOKEN, token)
            key = await self._host.spawn(
                prompt=charter(who, token),
                title=SECRETARY_TITLE,
                ai=(self.config.backend, self.config.model, self.config.effort),
            )
            await self.repo.set_state(STATE_THREAD, str(key))
            await self.repo.set_state(STATE_CURSOR, str(cursor))
            # The charter turn reads everything; the first wake waits the usual interval after it.
            await self.repo.set_state(STATE_LAST_WAKE, _stamp(self._clock()))
            self._secretary_running = True
            logger.info("canvas: secretary conversation %s started by %s", key, who)
            return key

    async def authorized(self, presented: str | None) -> bool:
        """Whether *presented* is the current secretary's write key (a restart rotates it)."""
        token = await self.repo.get_state(STATE_TOKEN)
        return bool(token and presented and hmac.compare_digest(token, presented))

    async def session(self) -> dict[str, Any] | None:
        raw = await self.repo.get_state(STATE_SESSION)
        if not raw:
            return None
        try:
            plan = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("canvas: the stored focus session is not JSON; dropping it")
            await self.repo.set_state(STATE_SESSION, None)
            return None
        return plan if isinstance(plan, dict) else None

    async def set_session(self, plan: dict[str, Any] | None) -> None:
        if plan is None:
            await self.repo.set_state(STATE_SESSION, None)
            return
        stored = {**plan, "prepared_at": _stamp(self._clock())}
        await self.repo.set_state(STATE_SESSION, json.dumps(stored, ensure_ascii=False))

    async def limits(self) -> dict[str, int]:
        """The wake limits in force: what the person set on the canvas, else the environment."""
        out = {
            "max_wakes_per_day": self.config.max_wakes_per_day,
            "min_interval": self.config.min_interval,
        }
        raw = await self.repo.get_state(STATE_LIMITS)
        if raw:
            try:
                stored = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("canvas: stored limits are not JSON; using the environment")
                return out
            for name, (low, high) in LIMIT_RANGES.items():
                value = stored.get(name) if isinstance(stored, dict) else None
                if isinstance(value, int) and not isinstance(value, bool) and low <= value <= high:
                    out[name] = value
        return out

    async def set_limits(self, max_wakes_per_day: Any, min_interval: Any) -> dict[str, int]:
        for name, value in (
            ("max_wakes_per_day", max_wakes_per_day),
            ("min_interval", min_interval),
        ):
            low, high = LIMIT_RANGES[name]
            if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
                raise ValueError(f"{name} must be a whole number {low}-{high}")
        await self.repo.set_state(
            STATE_LIMITS,
            json.dumps({"max_wakes_per_day": max_wakes_per_day, "min_interval": min_interval}),
        )
        return await self.limits()

    async def fresh_turn(self, thread_key: int, prompt: str) -> str | None:
        """The prompt for a secretary turn in a fresh session, or None for any other thread.

        The secretary's conversation would otherwise grow with every wake, and every wake
        would read all of it again (2026-11-15: 24 wakes read about 4.1M tokens). Its memory
        is its notes, the digest and the canvas, so a turn needs only the instructions, the
        last few messages and this turn's message.
        """
        if thread_key != await self.thread_key() or prompt.startswith(CHARTER_OPENING):
            return None
        token = await self.repo.get_state(STATE_TOKEN) or ""
        recent = await self._recent(thread_key) if self._history is not None else []
        cursor = int(await self.repo.get_state(STATE_CURSOR) or 0)
        counts = {
            "notes": len(await self.repo.notes()),
            "pending": len({i for _, _, i, _ in await self.repo.changes_since(cursor)}),
            "widgets": await self.repo.count_widgets(),
        }
        return turn_brief(charter("", token, first=False), recent, counts, prompt)

    async def _recent(self, thread_key: int) -> list[tuple[str, str]]:
        assert self._history is not None
        out: list[tuple[str, str]] = []
        for author, is_bot, content in await self._history(thread_key, RECENT_MESSAGES * 4):
            text = " ".join(str(content or "").split())
            # Tool lines, the charter and relay bookkeeping are not the conversation.
            if not text or text.startswith("-#") or text.startswith(CHARTER_OPENING):
                continue
            who = "you" if is_bot else ("watcher" if author == WATCHER_AUTHOR else author)
            out.append((who, text[:RECENT_CHARS]))
        return out[-RECENT_MESSAGES:]

    async def paused(self) -> bool:
        return (await self.repo.get_state(STATE_PAUSED)) == "1"

    async def set_paused(self, paused: bool) -> None:
        await self.repo.set_state(STATE_PAUSED, "1" if paused else None)

    # -- the watcher -----------------------------------------------------------
    async def tick(self) -> str | None:
        """Look once. Returns why a wake was sent or skipped, for tests and logs."""
        secretary = await self.item_id()
        if secretary is None:
            # Nobody started the secretary: read nothing, and start from a fresh baseline later.
            self._seen = None
            return "no secretary"
        items = await self._board()
        requests = await self._requests()
        now_print = fingerprint(items, requests, exclude=secretary)
        if self._seen is None:
            # The first look after a restart is the baseline, not a change.
            self._seen = now_print
        else:
            changes = compare(self._seen, now_print)
            self._seen = now_print
            if changes:
                await self.repo.add_changes(changes)
        mine = next((i for i in items if i.get("id") == secretary), None)
        running = bool(mine and (mine.get("running") or mine.get("queued")))
        if self._secretary_running and not running:
            context = (mine or {}).get("context") or {}
            used = context.get("used") if isinstance(context, Mapping) else None
            await self.repo.record_turn(
                day=_day(self._clock()), woken=False, context_used=used if used else None
            )
        self._secretary_running = running
        return await self._maybe_wake(running)

    async def _maybe_wake(self, running: bool) -> str:
        if running:
            return "running"
        cursor = int(await self.repo.get_state(STATE_CURSOR) or 0)
        pending = await self.repo.changes_since(cursor)
        if not pending:
            return "nothing changed"
        if await self.paused():
            return "paused"
        now = self._clock()
        last = _parse(await self.repo.get_state(STATE_LAST_WAKE))
        limits = await self.limits()
        if last is not None and (now - last).total_seconds() < limits["min_interval"]:
            return "too soon"
        usage = await self.repo.day_usage(_day(now))
        if usage["wakes"] >= limits["max_wakes_per_day"]:
            return "daily cap"
        key = await self.thread_key()
        if key is None or self._host is None:
            return "no secretary"
        items = {item_id for _, _, item_id, _ in pending}
        await self.repo.set_state(STATE_LAST_WAKE, _stamp(now))
        await self.repo.record_turn(day=_day(now), woken=True, context_used=None)
        try:
            await self._host.reply(key, wake_message(len(items)), WATCHER_AUTHOR)
        except LookupError:
            logger.warning("canvas: the secretary conversation %s is gone", key)
            await self.repo.set_state(STATE_THREAD, None)
            return "no secretary"
        self._secretary_running = True
        logger.info("canvas: woke the secretary for %d changed thread(s)", len(items))
        return "woken"

    # -- what a wake reads -------------------------------------------------------
    async def digest(self, *, everything: bool = False, peek: bool = False) -> dict[str, Any]:
        """The changed threads since the cursor (or every open one), then move the cursor."""
        secretary = await self.item_id()
        cursor = int(await self.repo.get_state(STATE_CURSOR) or 0)
        pending = await self.repo.changes_since(cursor)
        last = pending[-1][0] if pending else cursor
        items = [i for i in await self._board() if i.get("id") != secretary]
        by_id = {str(i.get("id")): i for i in items}
        requests: dict[str, list[dict[str, Any]]] = {}
        for request in await self._requests():
            requests.setdefault(f"t{request.get('thread_id')}", []).append(request)
        notes = await self.repo.notes()
        whats: dict[str, list[str]] = {}
        for _, _, item_id, what in pending:
            whats.setdefault(item_id, []).append(what)
        if everything:
            chosen = [
                i for i in items if i.get("status") not in _FINISHED and not i.get("archived")
            ][:DIGEST_ALL_LIMIT]
            entries = [
                await self._entry(i, whats, requests, notes, by_id, full=False) for i in chosen
            ]
        else:
            entries = []
            for index, item_id in enumerate(whats):
                item = by_id.get(item_id)
                if item is None:
                    entries.append({"id": item_id, "changes": whats[item_id], "gone": True})
                    continue
                full = index < DIGEST_FULL
                entries.append(await self._entry(item, whats, requests, notes, by_id, full=full))
        if not peek:
            await self.repo.set_state(STATE_CURSOR, str(last))
        return {
            "generated_at": _stamp(self._clock()),
            "cursor": last,
            "mode": "all" if everything else "changes",
            "threads": entries,
            "counts": _counts(items, requests),
        }

    async def _entry(
        self,
        item: Mapping[str, Any],
        whats: Mapping[str, list[str]],
        requests: Mapping[str, list[dict[str, Any]]],
        notes: Mapping[int, Mapping[str, str]],
        by_id: Mapping[str, Mapping[str, Any]],
        *,
        full: bool,
    ) -> dict[str, Any]:
        item_id = str(item.get("id"))
        thread = item.get("thread_id")
        parent = by_id.get(str(item.get("parent_id") or ""))
        entry: dict[str, Any] = {
            "id": item_id,
            "thread_id": thread,
            "title": item.get("title") or item.get("thread_name"),
            "status": item.get("status"),
            "running": bool(item.get("running")),
            "parent": (parent.get("title") if parent else None),
            "parent_thread_id": (parent.get("thread_id") if parent else None),
            "last_activity_at": item.get("last_activity_at"),
            "due_at": item.get("due_at"),
            "priority": item.get("priority"),
            "changes": list(whats.get(item_id, [])),
            "note": (notes.get(int(thread)) or {}).get("note") if thread else None,
            "requests": [_short_request(r) for r in requests.get(item_id, [])],
        }
        if not full:
            return entry
        if thread:
            try:
                situation = await self._situation(int(thread))
            except Exception:
                logger.exception("canvas: situation of %s unreadable", thread)
                situation = None
            if situation:
                entry["situation"] = {
                    "doing": situation.get("doing") or [],
                    "decided": situation.get("decided") or [],
                }
        try:
            recent = await self._messages(dict(item))
        except Exception:
            logger.exception("canvas: messages of %s unreadable", item_id)
            recent = []
        entry["recent"] = [
            {"who": who, "text": text[:DIGEST_MESSAGE_CHARS]}
            for who, text in recent[-DIGEST_MESSAGES:]
            if text
        ]
        return entry

    # -- what the Console shows ---------------------------------------------------
    async def status(self, items: Iterable[Mapping[str, Any]] | None = None) -> dict[str, Any]:
        secretary = await self.item_id()
        mine: Mapping[str, Any] | None = None
        if secretary is not None and items is not None:
            mine = next((i for i in items if i.get("id") == secretary), None)
        now = self._clock()
        usage = await self.repo.day_usage(_day(now))
        limits = await self.limits()
        cursor = int(await self.repo.get_state(STATE_CURSOR) or 0)
        pending = len({item for _, _, item, _ in await self.repo.changes_since(cursor)})
        return {
            "item_id": secretary,
            "running": bool(mine and (mine.get("running") or mine.get("queued"))),
            "context": (mine or {}).get("context"),
            "model": (mine or {}).get("model"),
            "paused": await self.paused(),
            "pending": pending,
            "last_wake_at": await self.repo.get_state(STATE_LAST_WAKE),
            "usage": {**usage, "max_wakes": limits["max_wakes_per_day"]},
            "min_interval": limits["min_interval"],
            "available": self._host is not None,
        }


def _counts(
    items: Iterable[Mapping[str, Any]], requests: Mapping[str, list[dict[str, Any]]]
) -> dict[str, int]:
    counts = {"decide": 0, "review": 0, "running": 0, "waiting_without_request": 0, "done": 0}
    for item in items:
        status = item.get("status")
        if item.get("running"):
            counts["running"] += 1
        elif status == "done":
            counts["done"] += 1
        elif status in ("waiting", "action", "review", "error"):
            open_requests = requests.get(str(item.get("id")), [])
            if not open_requests:
                counts["waiting_without_request"] += 1
            elif status == "review":
                counts["review"] += 1
            else:
                counts["decide"] += 1
    return counts
