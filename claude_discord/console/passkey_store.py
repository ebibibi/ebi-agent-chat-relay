"""Registered passkeys and signed-in sessions, in the session database.

Only what verification needs is stored: a passkey's public key and signature
counter, and a session's *hash*. A copy of the database therefore cannot be
replayed as a login — the session tokens themselves only ever live in the
browser's cookie.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import aiosqlite

MAX_PASSKEY_NAME_CHARS = 60

_SCHEMA = """
CREATE TABLE IF NOT EXISTS console_passkeys (
    id TEXT PRIMARY KEY,
    credential_id BLOB NOT NULL UNIQUE,
    public_key BLOB NOT NULL,
    sign_count INTEGER NOT NULL DEFAULT 0,
    rp_id TEXT NOT NULL,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    last_used_at TEXT
);
CREATE TABLE IF NOT EXISTS console_sessions (
    token_hash TEXT PRIMARY KEY,
    identity TEXT NOT NULL,
    passkey_id TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_console_sessions_passkey ON console_sessions(passkey_id);
"""


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class Passkey:
    id: str
    credential_id: bytes
    public_key: bytes
    sign_count: int
    rp_id: str
    name: str
    created_at: str
    last_used_at: str | None

    def to_public(self) -> dict[str, str | None]:
        """What the client may see: never the key material."""
        return {
            "id": self.id,
            "name": self.name,
            "rp_id": self.rp_id,
            "created_at": self.created_at,
            "last_used_at": self.last_used_at,
        }


def _row_to_passkey(row: aiosqlite.Row) -> Passkey:
    return Passkey(
        id=row["id"],
        credential_id=bytes(row["credential_id"]),
        public_key=bytes(row["public_key"]),
        sign_count=int(row["sign_count"]),
        rp_id=row["rp_id"],
        name=row["name"],
        created_at=row["created_at"],
        last_used_at=row["last_used_at"],
    )


class PasskeyStore:
    """CRUD for passkeys and sessions. One short-lived connection per call."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path

    async def init_db(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()

    # -- passkeys ----------------------------------------------------------
    async def count(self) -> int:
        async with aiosqlite.connect(self._db_path) as db:
            rows = list(await db.execute_fetchall("SELECT COUNT(*) FROM console_passkeys"))
        return int(rows[0][0])

    async def list_all(self) -> list[Passkey]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall("SELECT * FROM console_passkeys ORDER BY created_at")
        return [_row_to_passkey(r) for r in rows]

    async def by_credential_id(self, credential_id: bytes) -> Passkey | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = list(
                await db.execute_fetchall(
                    "SELECT * FROM console_passkeys WHERE credential_id = ?", (credential_id,)
                )
            )
        return _row_to_passkey(rows[0]) if rows else None

    async def add(
        self, *, credential_id: bytes, public_key: bytes, sign_count: int, rp_id: str, name: str
    ) -> Passkey:
        clean = " ".join(name.split())[:MAX_PASSKEY_NAME_CHARS] or "passkey"
        passkey = Passkey(
            id=f"pk{uuid.uuid4().hex[:12]}",
            credential_id=credential_id,
            public_key=public_key,
            sign_count=sign_count,
            rp_id=rp_id,
            name=clean,
            created_at=_iso(_now()),
            last_used_at=None,
        )
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT INTO console_passkeys (id, credential_id, public_key, sign_count, rp_id,"
                " name, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    passkey.id,
                    passkey.credential_id,
                    passkey.public_key,
                    passkey.sign_count,
                    passkey.rp_id,
                    passkey.name,
                    passkey.created_at,
                ),
            )
            await db.commit()
        return passkey

    async def record_use(self, passkey_id: str, sign_count: int) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "UPDATE console_passkeys SET sign_count = ?, last_used_at = ? WHERE id = ?",
                (sign_count, _iso(_now()), passkey_id),
            )
            await db.commit()

    async def delete(self, passkey_id: str, *, keep_one: bool) -> str:
        """Remove a passkey and sign out every session it opened.

        Returns ``"deleted"``, ``"missing"`` or ``"last"`` (refused because
        *keep_one* and it is the only passkey). Check and delete happen in one
        write transaction, so two concurrent removals cannot both pass the check.
        """
        async with aiosqlite.connect(self._db_path, isolation_level=None) as db:
            await db.execute("BEGIN IMMEDIATE")
            try:
                rows = list(await db.execute_fetchall("SELECT id FROM console_passkeys"))
                ids = {r[0] for r in rows}
                if passkey_id not in ids:
                    outcome = "missing"
                elif keep_one and len(ids) == 1:
                    outcome = "last"
                else:
                    await db.execute("DELETE FROM console_passkeys WHERE id = ?", (passkey_id,))
                    await db.execute(
                        "DELETE FROM console_sessions WHERE passkey_id = ?", (passkey_id,)
                    )
                    outcome = "deleted"
                await db.execute("COMMIT")
            except BaseException:
                await db.execute("ROLLBACK")
                raise
        return outcome

    # -- sessions ----------------------------------------------------------
    async def create_session(
        self, identity: str, lifetime: timedelta, passkey_id: str | None = None
    ) -> str:
        """Start a session and return its token (only the hash is kept)."""
        token = secrets.token_urlsafe(32)
        now = _now()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("DELETE FROM console_sessions WHERE expires_at <= ?", (_iso(now),))
            await db.execute(
                "INSERT INTO console_sessions (token_hash, identity, passkey_id, created_at,"
                " expires_at) VALUES (?, ?, ?, ?, ?)",
                (_hash(token), identity, passkey_id, _iso(now), _iso(now + lifetime)),
            )
            await db.commit()
        return token

    async def session_identity(self, token: str) -> str | None:
        """Who holds *token*, or ``None`` when it is unknown or expired."""
        async with aiosqlite.connect(self._db_path) as db:
            rows = list(
                await db.execute_fetchall(
                    "SELECT identity FROM console_sessions WHERE token_hash = ? AND expires_at > ?",
                    (_hash(token), _iso(_now())),
                )
            )
        return str(rows[0][0]) if rows else None

    async def end_session(self, token: str) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("DELETE FROM console_sessions WHERE token_hash = ?", (_hash(token),))
            await db.commit()
