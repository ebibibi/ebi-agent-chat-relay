"""Wait repository — the persistent half of :mod:`claude_discord.waits`.

A wait outlives the turn that registered it by design, and often a bot restart
too (a deploy restarts the bot while the session waits on that very deploy).
So waits live in the session database, not in memory.

Lifecycle::

    active ──claim()──▶ resuming ──mark_delivered()──▶ done | timeout | probe_error
      ▲                    │
      └─release_for_retry()┘   (delivery failed, or the bot restarted mid-delivery)
    active ──cancel()──▶ cancelled
    resuming ──give_up()──▶ undeliverable

``resuming`` exists so a resume is never lost: the outcome is only final once
the thread's next turn has run.  A wait still ``resuming`` at startup was cut
off by a restart and goes back to ``active`` — re-probing is cheap, a session
that never wakes up is not.

Times are Unix seconds (``REAL``): the watcher compares them with
``time.time()`` every tick, and SQLite date strings would only add parsing.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

import aiosqlite

from .. import waits
from ..waits import ProbeResult, WaitSpec

logger = logging.getLogger(__name__)

STATUS_ACTIVE = "active"
STATUS_RESUMING = "resuming"
STATUS_CANCELLED = "cancelled"
STATUS_UNDELIVERABLE = "undeliverable"


class WaitLimitError(Exception):
    """Too many active waits — for one thread or for the deployment."""


@dataclass(frozen=True)
class Wait:
    """One stored wait, in any state."""

    id: int
    thread_id: int
    label: str
    argv: tuple[str, ...]
    pending_exit_codes: tuple[int, ...]
    done_values: tuple[str, ...]
    interval_seconds: int
    timeout_seconds: int
    note: str | None
    cwd: str | None
    status: str
    outcome: str | None
    created_at: float
    deadline: float
    next_check_at: float
    consecutive_errors: int
    delivery_failures: int
    last_exit_code: int | None
    last_output: str | None
    last_error: str | None
    finished_at: float | None

    def to_spec(self) -> WaitSpec:
        return WaitSpec(
            thread_id=self.thread_id,
            argv=self.argv,
            pending_exit_codes=self.pending_exit_codes,
            done_values=self.done_values,
            interval_seconds=self.interval_seconds,
            timeout_seconds=self.timeout_seconds,
            label=self.label,
            note=self.note,
            cwd=self.cwd,
        )

    def last_result(self) -> ProbeResult:
        return ProbeResult(
            exit_code=self.last_exit_code, output=self.last_output or "", error=self.last_error
        )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "label": self.label,
            "argv": list(self.argv),
            "pending_exit_codes": list(self.pending_exit_codes),
            "done_values": list(self.done_values),
            "interval_seconds": self.interval_seconds,
            "timeout_seconds": self.timeout_seconds,
            "note": self.note,
            "cwd": self.cwd,
            "status": self.status,
            "outcome": self.outcome,
            "created_at": self.created_at,
            "deadline": self.deadline,
            "next_check_at": self.next_check_at,
            "consecutive_errors": self.consecutive_errors,
            "delivery_failures": self.delivery_failures,
            "last_exit_code": self.last_exit_code,
            "last_output": self.last_output,
            "last_error": self.last_error,
            "finished_at": self.finished_at,
        }


def _row_to_wait(row: aiosqlite.Row) -> Wait:
    return Wait(
        id=row["id"],
        thread_id=row["thread_id"],
        label=row["label"],
        argv=tuple(json.loads(row["argv"])),
        pending_exit_codes=tuple(json.loads(row["pending_exit_codes"])),
        done_values=tuple(json.loads(row["done_values"])),
        interval_seconds=row["interval_seconds"],
        timeout_seconds=row["timeout_seconds"],
        note=row["note"],
        cwd=row["cwd"],
        status=row["status"],
        outcome=row["outcome"],
        created_at=row["created_at"],
        deadline=row["deadline"],
        next_check_at=row["next_check_at"],
        consecutive_errors=row["consecutive_errors"],
        delivery_failures=row["delivery_failures"],
        last_exit_code=row["last_exit_code"],
        last_output=row["last_output"],
        last_error=row["last_error"],
        finished_at=row["finished_at"],
    )


class WaitRepository:
    """CRUD for the ``pending_waits`` table. Short-lived connections, like its siblings."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    def _connect(self) -> aiosqlite.Connection:
        return aiosqlite.connect(self._db_path)

    async def create(self, spec: WaitSpec, *, now: float) -> tuple[Wait, bool]:
        """Store *spec* as an active wait.

        Returns ``(wait, created)``. Registering the same probe for the same
        thread again returns the live wait with ``created=False`` — an agent
        that is told twice to wait must not be resumed twice.

        Raises:
            WaitLimitError: the thread or the deployment has too many live waits.
        """
        argv_json = json.dumps(list(spec.argv))
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            # BEGIN IMMEDIATE takes the write lock before counting, so two
            # concurrent registrations cannot both squeeze under a limit.
            await db.execute("BEGIN IMMEDIATE")
            async with db.execute(
                "SELECT id FROM pending_waits WHERE thread_id = ? AND argv = ? "
                "AND status IN (?, ?) ORDER BY id LIMIT 1",
                (spec.thread_id, argv_json, STATUS_ACTIVE, STATUS_RESUMING),
            ) as cursor:
                existing = await cursor.fetchone()
            if existing is not None:
                await db.rollback()
                wait = await self.get(int(existing["id"]))
                assert wait is not None
                return wait, False
            async with db.execute(
                "SELECT COUNT(*), COALESCE(SUM(thread_id = ?), 0) "
                "FROM pending_waits WHERE status IN (?, ?)",
                (spec.thread_id, STATUS_ACTIVE, STATUS_RESUMING),
            ) as cursor:
                row = await cursor.fetchone()
            total, for_thread = (row[0], row[1]) if row else (0, 0)
            if for_thread >= waits.MAX_WAITS_PER_THREAD:
                await db.rollback()
                raise WaitLimitError(
                    f"thread already has {waits.MAX_WAITS_PER_THREAD} active waits; "
                    "cancel one first"
                )
            if total >= waits.MAX_ACTIVE_WAITS:
                await db.rollback()
                raise WaitLimitError(f"{waits.MAX_ACTIVE_WAITS} waits are already active")
            cursor = await db.execute(
                "INSERT INTO pending_waits (thread_id, label, argv, pending_exit_codes, "
                "done_values, interval_seconds, timeout_seconds, note, cwd, status, "
                "created_at, deadline, next_check_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    spec.thread_id,
                    spec.label,
                    argv_json,
                    json.dumps(list(spec.pending_exit_codes)),
                    json.dumps(list(spec.done_values)),
                    spec.interval_seconds,
                    spec.timeout_seconds,
                    spec.note,
                    spec.cwd,
                    STATUS_ACTIVE,
                    now,
                    now + spec.timeout_seconds,
                    now + spec.interval_seconds,
                ),
            )
            wait_id = cursor.lastrowid
            await db.commit()
        wait = await self.get(int(wait_id or 0))
        assert wait is not None
        return wait, True

    async def get(self, wait_id: int) -> Wait | None:
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM pending_waits WHERE id = ?", (wait_id,)) as cur:
                row = await cur.fetchone()
        return _row_to_wait(row) if row else None

    async def list_active(self, thread_id: int | None = None) -> list[Wait]:
        """Waits not yet finished (``active`` or ``resuming``)."""
        query = "SELECT * FROM pending_waits WHERE status IN (?, ?)"
        params: tuple = (STATUS_ACTIVE, STATUS_RESUMING)
        if thread_id is not None:
            query += " AND thread_id = ?"
            params = (*params, thread_id)
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query + " ORDER BY id", params) as cur:
                rows = await cur.fetchall()
        return [_row_to_wait(r) for r in rows]

    async def due(self, *, now: float) -> list[Wait]:
        """Active waits to look at now: next check reached, or past the deadline."""
        async with self._connect() as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM pending_waits WHERE status = ? "
                "AND (next_check_at <= ? OR deadline <= ?) ORDER BY next_check_at",
                (STATUS_ACTIVE, now, now),
            ) as cur:
                rows = await cur.fetchall()
        return [_row_to_wait(r) for r in rows]

    async def active_thread_ids(self) -> set[int]:
        """Threads still waiting (``active`` only — a resuming thread is already awake)."""
        async with (
            self._connect() as db,
            db.execute(
                "SELECT DISTINCT thread_id FROM pending_waits WHERE status = ?",
                (STATUS_ACTIVE,),
            ) as cur,
        ):
            rows = await cur.fetchall()
        return {int(r[0]) for r in rows}

    async def record_probe(
        self,
        wait_id: int,
        result: ProbeResult,
        *,
        next_check_at: float,
        consecutive_errors: int,
    ) -> None:
        async with self._connect() as db:
            await db.execute(
                "UPDATE pending_waits SET last_exit_code = ?, last_output = ?, "
                "last_error = ?, next_check_at = ?, consecutive_errors = ? "
                "WHERE id = ? AND status = ?",
                (
                    result.exit_code,
                    result.output,
                    result.error,
                    next_check_at,
                    consecutive_errors,
                    wait_id,
                    STATUS_ACTIVE,
                ),
            )
            await db.commit()

    async def _transition(
        self, wait_id: int, from_status: str, to_status: str, extra_sql: str = "", *extra: object
    ) -> bool:
        async with self._connect() as db:
            cursor = await db.execute(
                f"UPDATE pending_waits SET status = ?{extra_sql} WHERE id = ? AND status = ?",
                (to_status, *extra, wait_id, from_status),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def claim(self, wait_id: int, outcome: str) -> bool:
        """active → resuming with *outcome*. True only for the caller that did it.

        This is the guard against a double resume: a cancel, or a second
        watcher racing this one, loses the conditional UPDATE.
        """
        return await self._transition(
            wait_id, STATUS_ACTIVE, STATUS_RESUMING, ", outcome = ?", outcome
        )

    async def mark_delivered(self, wait_id: int, *, now: float) -> bool:
        """resuming → its outcome: the thread's next turn has run."""
        async with self._connect() as db:
            cursor = await db.execute(
                "UPDATE pending_waits SET status = outcome, finished_at = ? "
                "WHERE id = ? AND status = ?",
                (now, wait_id, STATUS_RESUMING),
            )
            await db.commit()
            return cursor.rowcount > 0

    async def release_for_retry(self, wait_id: int, *, next_check_at: float) -> bool:
        """resuming → active, counting a failed delivery. The next probe decides again."""
        return await self._transition(
            wait_id,
            STATUS_RESUMING,
            STATUS_ACTIVE,
            ", next_check_at = ?, delivery_failures = delivery_failures + 1",
            next_check_at,
        )

    async def give_up(self, wait_id: int, *, now: float) -> bool:
        """resuming → undeliverable: the thread could not be reached, repeatedly."""
        return await self._transition(
            wait_id, STATUS_RESUMING, STATUS_UNDELIVERABLE, ", finished_at = ?", now
        )

    async def recover_interrupted(self, *, now: float) -> int:
        """At startup: every ``resuming`` wait was cut off by the restart — retry it."""
        async with self._connect() as db:
            cursor = await db.execute(
                "UPDATE pending_waits SET status = ?, next_check_at = ? WHERE status = ?",
                (STATUS_ACTIVE, now, STATUS_RESUMING),
            )
            await db.commit()
            return cursor.rowcount

    async def cancel(self, wait_id: int, *, thread_id: int | None = None) -> bool:
        """Cancel an active wait, optionally only if *thread_id* owns it."""
        query = "UPDATE pending_waits SET status = ? WHERE id = ? AND status = ?"
        params: tuple = (STATUS_CANCELLED, wait_id, STATUS_ACTIVE)
        if thread_id is not None:
            query += " AND thread_id = ?"
            params = (*params, thread_id)
        async with self._connect() as db:
            cursor = await db.execute(query, params)
            await db.commit()
            return cursor.rowcount > 0
