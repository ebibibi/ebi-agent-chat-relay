"""Open decision requests: what agents have asked the person, until it is answered.

This table is the one place a request lives. The secretary home and a family's situation panel
both read it, so a parent never copies its child's question: the child's card shows in the
parent's panel as it is. One open request per thread and key: a newer request with the same
key replaces the old one. A request is closed, never deleted, when it is answered, replaced,
dismissed by the person, resolved in later conversation, or its thread is finished.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import aiosqlite

from ..decision_request import DecisionRequestSpec

ORIGIN_MARK = "mark"
#: ``close_reason`` of a request a parent's card covers: ``bundled:<parent request id>`` (#1493).
BUNDLED_PREFIX = "bundled:"


@dataclass(frozen=True)
class DecisionRequest:
    """A stored request. ``thread_id`` is the thread an answer goes to."""

    id: int
    thread_id: int
    key: str
    kind: str
    origin: str
    source_thread_id: int
    title: str
    context: str
    options: list[dict[str, Any]]
    deadline: str
    detail: dict[str, str]
    created_at: str
    deferred_at: str | None = None
    #: ``{"after": ISO 8601, "scope": key}`` when the card may proceed unanswered (#1478).
    if_no_answer: dict[str, str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            # Thread ids exceed 2**53: as a JSON number a browser would round them.
            "thread_id": str(self.thread_id),
            "source_thread_id": str(self.source_thread_id),
            "key": self.key,
            "kind": self.kind,
            "origin": self.origin,
            "card": {
                "title": self.title,
                "context": self.context,
                "options": self.options,
                "deadline": self.deadline,
                **({"if_no_answer": self.if_no_answer} if self.if_no_answer else {}),
            },
            "detail": self.detail,
            "created_at": self.created_at,
            "deferred_at": self.deferred_at,
        }


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _row(row: aiosqlite.Row) -> DecisionRequest:
    card = json.loads(row["card"])
    return DecisionRequest(
        id=row["id"],
        thread_id=row["thread_id"],
        key=row["req_key"],
        kind=row["kind"],
        origin=row["origin"],
        source_thread_id=row["source_thread_id"],
        title=card["title"],
        context=card["context"],
        options=card["options"],
        deadline=card["deadline"],
        detail=json.loads(row["detail"]),
        created_at=row["created_at"],
        deferred_at=row["deferred_at"],
        if_no_answer=card.get("if_no_answer")
        if isinstance(card.get("if_no_answer"), dict)
        else None,
    )


class DecisionRequestRepository:
    """CRUD for the ``decision_requests`` table. One connection per call."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def put(
        self,
        thread_id: int,
        spec: DecisionRequestSpec,
        *,
        origin: str,
        source_thread_id: int,
    ) -> DecisionRequest:
        """Store *spec* for *thread_id*, replacing the open request with the same key."""
        async with aiosqlite.connect(self._db_path) as db:
            new_id = await self._put(db, thread_id, spec, origin, source_thread_id)
            await db.commit()
        return await self._get(new_id)

    async def close(
        self,
        thread_id: int,
        *,
        reason: str,
        origin: str | None = None,
        request_id: int | None = None,
        key: str | None = None,
    ) -> int:
        """Close the open requests of *thread_id* (narrowed by *origin* or *request_id*)."""
        sql = "UPDATE decision_requests SET closed_at = ?, close_reason = ?"
        sql += " WHERE thread_id = ? AND closed_at IS NULL"
        args: list[Any] = [_now(), reason, int(thread_id)]
        if origin is not None:
            sql += " AND origin = ?"
            args.append(origin)
        if request_id is not None:
            sql += " AND id = ?"
            args.append(int(request_id))
        if key is not None:
            sql += " AND req_key = ?"
            args.append(key)
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(sql, args)
            await db.commit()
            return cursor.rowcount

    async def defer(self, thread_id: int, request_id: int, *, deferred: bool) -> int:
        """Move one open request to Later, or restore it; never close or answer it."""
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "UPDATE decision_requests SET deferred_at = ?"
                " WHERE thread_id = ? AND id = ? AND closed_at IS NULL",
                (_now() if deferred else None, int(thread_id), int(request_id)),
            )
            await db.commit()
            return cursor.rowcount

    async def get_open(self, thread_id: int, key: str) -> DecisionRequest | None:
        """The open request of *thread_id* with *key*, or None."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = list(
                await db.execute_fetchall(
                    "SELECT * FROM decision_requests"
                    " WHERE thread_id = ? AND req_key = ? AND closed_at IS NULL",
                    (int(thread_id), key),
                )
            )
        return _row(rows[0]) if rows else None

    async def bundle(self, into_id: int, request_ids: list[int]) -> int:
        """Close the open *request_ids* as covered by request *into_id* (#1493).

        The caller has checked that the requests belong to the covering thread's family. A
        request that is no longer open is skipped. Returns how many were covered.
        """
        if not request_ids:
            return 0
        marks = ",".join("?" for _ in request_ids)
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "UPDATE decision_requests SET closed_at = ?, close_reason = ?"
                f" WHERE closed_at IS NULL AND id != ? AND id IN ({marks})",
                [_now(), f"{BUNDLED_PREFIX}{int(into_id)}", int(into_id)]
                + [int(i) for i in request_ids],
            )
            await db.commit()
            return cursor.rowcount

    async def covered_by(self, request_id: int) -> list[DecisionRequest]:
        """The requests the card *request_id* covers, oldest first."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                "SELECT * FROM decision_requests WHERE close_reason = ? ORDER BY id",
                (f"{BUNDLED_PREFIX}{int(request_id)}",),
            )
        return [_row(r) for r in rows]

    async def list_open(self) -> list[DecisionRequest]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                "SELECT * FROM decision_requests WHERE closed_at IS NULL ORDER BY id"
            )
        return [_row(r) for r in rows]

    async def _put(
        self,
        db: aiosqlite.Connection,
        thread_id: int,
        spec: DecisionRequestSpec,
        origin: str,
        source_thread_id: int,
    ) -> int:
        now = _now()
        replaced = list(
            await db.execute_fetchall(
                "SELECT id FROM decision_requests"
                " WHERE thread_id = ? AND req_key = ? AND closed_at IS NULL",
                (int(thread_id), spec.key),
            )
        )
        await db.execute(
            "UPDATE decision_requests SET closed_at = ?, close_reason = 'replaced'"
            " WHERE thread_id = ? AND req_key = ? AND closed_at IS NULL",
            (now, int(thread_id), spec.key),
        )
        cursor = await db.execute(
            "INSERT INTO decision_requests (thread_id, req_key, kind, origin, source_thread_id,"
            " card, detail, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(thread_id),
                spec.key,
                spec.kind,
                origin,
                int(source_thread_id),
                json.dumps(spec.card(), ensure_ascii=False),
                json.dumps(spec.detail, ensure_ascii=False),
                now,
            ),
        )
        if cursor.lastrowid is None:  # pragma: no cover - SQLite always reports it
            raise RuntimeError("decision request was not stored")
        # A rewritten card keeps the questions it covers (#1493).
        for (old_id,) in replaced:
            await db.execute(
                "UPDATE decision_requests SET close_reason = ? WHERE close_reason = ?",
                (f"{BUNDLED_PREFIX}{cursor.lastrowid}", f"{BUNDLED_PREFIX}{old_id}"),
            )
        return cursor.lastrowid

    async def _get(self, request_id: int) -> DecisionRequest:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = list(
                await db.execute_fetchall(
                    "SELECT * FROM decision_requests WHERE id = ?", (request_id,)
                )
            )
        return _row(rows[0])
