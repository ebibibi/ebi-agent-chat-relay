"""The night shift: one tap hands the board's waiting AI work over for the night (#1511).

The 🌙 template (:mod:`.night`) still left the person writing the instructions. Here the tap
is enough: the night shift looks at the open threads, picks those whose next move is the
agent's own, and sends each one the night instruction — continue within the approved scope,
never merge, send, publish, pay, delete or change production, and leave decisions as cards for
the morning. An optional line from the person goes first, as the top-priority instruction.

What it picks, re-checked when it sends:

* the agent stopped without saying whose move it is (status ``idle``) — nothing waits on the
  person;
* no open decision request in the thread: a card is the person's, and the morning brief shows
  it under *decide*;
* not running, queued, snoozed, archived, finished, and no active wait (the wait resumes it);
* active within the last :data:`MAX_AGE_DAYS` days, and not already handed over in the last
  :data:`REPEAT_HOURS` hours;
* at most the person's ``shift_limit`` (default 8), most important first.

Nothing is sent at once: the plan waits :data:`GRACE_SECONDS` so the person can take items out
(or cancel all) from the answer the button shows. The scope comes from the owner's decision
defaults (``CCDB_DECISION_DEFAULTS_FILE``, #1478) when configured; otherwise from a fixed,
conservative one. The message says it is automatic: it is not the person's answer to anything.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from .canvas_repo import CanvasRepository

logger = logging.getLogger(__name__)

STATE_SHIFT = "night_shift"
GRACE_SECONDS = 120
TICK_SECONDS = 20
MAX_AGE_DAYS = 30
REPEAT_HOURS = 12
MAX_NOTE_CHARS = 1000
#: Item states inside a shift.
PLANNED, SENT, CANCELLED, SKIPPED = "planned", "sent", "cancelled", "skipped"
_PICKABLE = frozenset({"idle"})

Deliver = Callable[[str, str], Awaitable[bool]]

DEFAULT_SCOPE = {
    "ja": "調査・実装・検証・下書き・PR作成まで（マージはしない）",
    "en": "research, implement, verify, draft, open pull requests (do not merge)",
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


def eligible(
    item: Mapping[str, Any],
    *,
    asking: set[str],
    waiting: set[str],
    now: datetime,
) -> bool:
    """Whether the agent holds this item's next move and nothing waits on the person."""
    thread = item.get("thread_id")
    if thread is None or item.get("status") not in _PICKABLE:
        return False
    if item.get("running") or item.get("queued") or item.get("archived"):
        return False
    if item.get("snoozed") or item.get("bucket") == "snoozed" or item.get("canvas_secretary"):
        return False
    if str(thread) in asking or str(thread) in waiting:
        return False
    last = _parse(item.get("last_activity_at"))
    return last is not None and now - last <= timedelta(days=MAX_AGE_DAYS)


def _ranked(items: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Pinned first, then focus, then priority; within those, the most recently active."""
    recent_first = sorted(items, key=lambda i: str(i.get("last_activity_at") or ""), reverse=True)
    return sorted(
        recent_first,
        key=lambda i: (
            not i.get("pinned_at"),
            -int(i.get("focus") or 0),
            int(i.get("priority") or 9),
        ),
    )


def pick(
    items: Iterable[Mapping[str, Any]],
    requests: Iterable[Mapping[str, Any]],
    *,
    waiting: Iterable[int | str] = (),
    recent: Iterable[str] = (),
    limit: int,
    now: datetime,
) -> list[Mapping[str, Any]]:
    """The items the night shift takes, most important first, at most *limit*."""
    asking = {str(r.get("thread_id")) for r in requests if not r.get("deferred_at")}
    held = {str(w) for w in waiting}
    skip = set(recent)
    chosen = [
        i
        for i in items
        if str(i.get("id")) not in skip and eligible(i, asking=asking, waiting=held, now=now)
    ]
    return _ranked(chosen)[: max(0, limit)]


def instruction(note: str | None, scope: str, lang: str = "ja") -> str:
    """The message each picked thread receives."""
    if lang == "en":
        lines = [
            "🌙 Night shift (automatic — the owner handed this over for the night with one tap; "
            "this is not their answer to anything)",
        ]
        if note:
            lines.append(f"Top priority from the owner: {note}")
        lines += [
            "Take this thread as far as you can by morning.",
            f"You may: {scope}",
            "Never: merge, send, publish, pay, delete or change production. Collect anything "
            "like that as a decision card for the morning.",
            "If the next step needs the owner's decision, do nothing else here: send a decision "
            "card and stop.",
            "When unsure: take the recommended option and write down why. Stop after the same "
            "failure three times; record cause, attempts and how to resume.",
            "When the done condition is met, mark the thread as a completion candidate.",
        ]
        return "\n".join(lines)
    lines = [
        "🌙 夜番からの依頼（本人が寝る前に🌙で任せた自動の依頼です。本人の返事ではありません）"
    ]
    if note:
        lines.append(f"本人からの最優先の指示: {note}")
    lines += [
        "このスレッドの続きを、朝までに進められるところまで進めてください。",
        f"任せる範囲: {scope}",
        "やらないこと: マージ・送信・公開・課金・削除・本番変更。"
        "必要なら判断カードにまとめて朝に回す。",
        "本人の判断が要る一手なら、ほかは何もせず判断カードを出して止まる。",
        "迷ったら推奨案で進め、理由を書き残す。"
        "同じ失敗が3回続いたら、原因・試したこと・再開条件を記録して止まる。",
        "完了条件を満たしたら完了候補（🏁）にする。",
    ]
    return "\n".join(lines)


def scope_text(labels: Iterable[str], lang: str = "ja") -> str:
    """The approved scopes as one line, or the conservative default."""
    joined = " / ".join(label for label in labels if label)
    return joined or DEFAULT_SCOPE.get(lang, DEFAULT_SCOPE["en"])


class NightShiftError(ValueError):
    """A request the night shift refuses, with a message fit to show the person."""


class NightShift:
    """Plans, holds for the grace period, then hands the picked threads their instruction."""

    def __init__(
        self,
        repo: CanvasRepository,
        *,
        board: Callable[[], Awaitable[list[dict[str, Any]]]],
        requests: Callable[[], Awaitable[list[dict[str, Any]]]],
        deliver: Deliver,
        waiting: Callable[[], Awaitable[set[int]]] | None = None,
        scopes: Callable[[], Iterable[str]] = lambda: (),
        limit: Callable[[], Awaitable[int]] | None = None,
        exclude: Callable[[], Awaitable[str | None]] | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.repo = repo
        self._board = board
        self._requests = requests
        self._deliver = deliver
        self._waiting = waiting
        self._scopes = scopes
        self._limit = limit
        #: The canvas secretary's own item: a standing service, never handed work.
        self._exclude = exclude
        self._clock = clock
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    # -- state ---------------------------------------------------------------
    async def current(self) -> dict[str, Any] | None:
        raw = await self.repo.get_state(STATE_SHIFT)
        if not raw:
            return None
        try:
            shift = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("night shift: stored shift is not JSON; dropping it")
            return None
        return shift if isinstance(shift, dict) else None

    async def _save(self, shift: dict[str, Any]) -> None:
        await self.repo.set_state(STATE_SHIFT, json.dumps(shift, ensure_ascii=False))

    async def _held_threads(self) -> set[int]:
        if self._waiting is None:
            return set()
        try:
            return await self._waiting()
        except Exception:
            logger.exception("night shift: active waits unreadable; treating none as held")
            return set()

    # -- the person's side -----------------------------------------------------
    async def plan(self, note: Any, who: str, *, lang: str = "ja") -> dict[str, Any]:
        """Pick tonight's threads and hold them for the grace period."""
        if note is not None and not isinstance(note, str):
            raise NightShiftError("note must be text")
        note = (note or "").strip() or None
        if note and len(note) > MAX_NOTE_CHARS:
            raise NightShiftError(f"the note is at most {MAX_NOTE_CHARS} characters")
        async with self._lock:
            now = self._clock()
            previous = await self.current() or {}
            recent = {
                str(e.get("id"))
                for e in previous.get("items") or []
                if e.get("state") == SENT
                and (at := _parse(e.get("sent_at"))) is not None
                and now - at < timedelta(hours=REPEAT_HOURS)
            }
            limit = await self._limit() if self._limit is not None else 8
            if self._exclude is not None and (own := await self._exclude()):
                recent.add(own)
            chosen = pick(
                await self._board(),
                await self._requests(),
                waiting=await self._held_threads(),
                recent=recent,
                limit=limit,
                now=now,
            )
            kept = [e for e in previous.get("items") or [] if e.get("state") == SENT]
            shift = {
                "started_at": _stamp(now),
                "dispatch_at": _stamp(now + timedelta(seconds=GRACE_SECONDS)),
                "by": who,
                "note": note,
                "lang": "en" if lang == "en" else "ja",
                "limit": limit,
                "items": kept
                + [
                    {"id": str(i["id"]), "title": _title(i), "state": PLANNED, "sent_at": None}
                    for i in chosen
                ],
            }
            await self._save(shift)
            return shift

    async def cancel(self, item_id: str | None = None) -> dict[str, Any] | None:
        """Take one planned item (or every planned item) out before it is sent."""
        async with self._lock:
            shift = await self.current()
            if shift is None:
                return None
            for entry in shift.get("items") or []:
                if entry.get("state") == PLANNED and (item_id is None or entry["id"] == item_id):
                    entry["state"] = CANCELLED
            await self._save(shift)
            return shift

    # -- the loop --------------------------------------------------------------
    async def tick(self) -> int:
        """Send the due planned items that are still eligible. Returns how many were sent."""
        async with self._lock:
            shift = await self.current()
            if not shift:
                return 0
            due = _parse(shift.get("dispatch_at"))
            now = self._clock()
            planned = [e for e in shift.get("items") or [] if e.get("state") == PLANNED]
            if not planned or due is None or now < due:
                return 0
            items = {str(i.get("id")): i for i in await self._board()}
            requests = await self._requests()
            asking = {str(r.get("thread_id")) for r in requests if not r.get("deferred_at")}
            waiting = {str(t) for t in await self._held_threads()}
            lang = shift.get("lang") or "ja"
            text = instruction(shift.get("note"), scope_text(self._scopes(), lang), lang)
            sent = 0
            for entry in planned:
                item = items.get(entry["id"])
                if item is None or not eligible(item, asking=asking, waiting=waiting, now=now):
                    entry["state"] = SKIPPED
                    continue
                try:
                    ok = await self._deliver(entry["id"], text)
                except Exception:
                    logger.exception("night shift: could not hand over %s", entry["id"])
                    ok = False
                entry["state"] = SENT if ok else SKIPPED
                if ok:
                    entry["sent_at"] = _stamp(now)
                    sent += 1
            await self._save(shift)
            if sent:
                logger.info("night shift: handed %d thread(s) over for the night", sent)
            return sent

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
                logger.exception("night shift: tick failed")
            await asyncio.sleep(TICK_SECONDS)
