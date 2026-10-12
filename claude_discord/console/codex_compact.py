"""``/compact`` for Codex conversations.

ccdb drives Codex through ``codex exec``, which takes every message — a
leading ``/compact`` included — as plain text for the model: it answers with a
summary-looking reply and the history stays as long as it was. Real compaction
lives in ``codex app-server``: ``thread/resume`` loads the rollout ``exec``
wrote, ``thread/compact/start`` replaces the history with a summary, and the
next ``codex exec resume`` continues from that summary (measured on
codex-cli 0.162.1).

Security: ``create_subprocess_exec`` only (never a shell); the session id is
checked against the same pattern the Codex runner accepts.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import shlex
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

COMPACT_TIMEOUT_SECONDS = 300.0
_SESSION_ID = re.compile(r"^[a-f0-9\-]+$")
_RESUME_ID = 2
_COMPACT_ID = 3


class CompactError(RuntimeError):
    """Compaction did not happen; the message is fit to show the human."""


def _request(req_id: int, method: str, params: Any) -> bytes:
    return (
        json.dumps({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}) + "\n"
    ).encode()


def _rpc_error(msg: Mapping[str, Any]) -> str:
    err = msg.get("error")
    if isinstance(err, Mapping):
        return str(err.get("message") or err)
    return str(err)


async def _exchange(proc: asyncio.subprocess.Process, session_id: str) -> None:
    assert proc.stdin is not None and proc.stdout is not None
    proc.stdin.write(
        _request(1, "initialize", {"clientInfo": {"name": "ccdb-console", "version": "1.0.0"}})
    )
    proc.stdin.write(b'{"jsonrpc":"2.0","method":"initialized"}\n')
    proc.stdin.write(_request(_RESUME_ID, "thread/resume", {"threadId": session_id}))
    await proc.stdin.drain()
    started = False
    while True:
        line = await proc.stdout.readline()
        if not line:
            raise CompactError("Codex stopped before the compaction finished")
        try:
            msg = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        if msg.get("id") in (_RESUME_ID, _COMPACT_ID) and "error" in msg:
            raise CompactError(f"Codex refused: {_rpc_error(msg)}")
        if msg.get("id") == _RESUME_ID and not started:
            proc.stdin.write(
                _request(_COMPACT_ID, "thread/compact/start", {"threadId": session_id})
            )
            await proc.stdin.drain()
            started = True
            continue
        params = msg.get("params")
        item = params.get("item") if isinstance(params, Mapping) else None
        if not isinstance(item, Mapping):
            item = {}
        if (
            started
            and msg.get("method") == "item/completed"
            and item.get("type") == "contextCompaction"
        ):
            return
        if started and msg.get("method") == "turn/completed":
            raise CompactError("Codex finished the turn without compacting")


async def compact_codex_session(
    session_id: str,
    *,
    codex_command: str = "codex",
    env: dict[str, str] | None = None,
    timeout: float = COMPACT_TIMEOUT_SECONDS,
) -> None:
    """Compact the Codex conversation *session_id*, or raise CompactError."""
    if not _SESSION_ID.match(session_id or ""):
        raise CompactError("this conversation has no Codex session to compact yet")
    parts = shlex.split(codex_command) or ["codex"]
    try:
        proc = await asyncio.create_subprocess_exec(
            *parts,
            "app-server",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
    except (OSError, ValueError) as exc:
        raise CompactError(f"could not start Codex: {exc}") from exc
    try:
        await asyncio.wait_for(_exchange(proc, session_id), timeout=timeout)
    except TimeoutError as exc:
        raise CompactError("Codex did not finish compacting in time") from exc
    finally:
        with contextlib.suppress(ProcessLookupError):
            proc.terminate()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=5)
