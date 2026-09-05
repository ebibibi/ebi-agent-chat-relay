"""REST API server for Discord bot push notifications.

Optional extension — requires aiohttp. Install with:
    pip install claude-code-discord-bridge[api]

Provides endpoints for sending immediate and scheduled notifications
to Discord channels via the bot.

Security:
- Binds to 127.0.0.1 by default (localhost only)
- Optional Bearer token authentication via api_secret
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import contextlib
import hmac
import ipaddress
import json
import logging
import os
import re
import time
import uuid
import zipfile
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from aiohttp import web

from claude_code_core.thread_search import run_thread_search
from claude_code_core.transcript_search import default_transcripts_root

from ..discord_ui.file_sender import send_file_blobs
from ..lounge import length_hint
from ..relay import MODE_INTERRUPT, MODE_QUEUE, VALID_MODES, RelayGuard, build_relay_prompt
from ..session_view import STATE_HISTORY, STATE_RUNNING, build_session_views
from ..thread_policy import THREAD_AUTO_ARCHIVE_MINUTES
from . import ingest_manifest, teams_sync
from .teams_store import TeamsVaultStore
from .teams_sync import ThreadRef

if TYPE_CHECKING:
    import discord
    from discord.ext.commands import Bot

    from ..database.claims_repo import ClaimRepository
    from ..database.ingest_repo import IngestResultRepository
    from ..database.lounge_repo import LoungeRepository
    from ..database.notification_repo import NotificationRepository
    from ..database.repository import SessionRepository
    from ..database.resume_repo import PendingResumeRepository
    from ..database.summary_repo import ThreadSummaryRepository
    from ..database.task_repo import TaskRepository

# /api/ingest — authenticated spawn for untrusted external clients (browser
# extensions, mobile shortcuts, webhooks) that may carry file attachments.
_MAX_INGEST_ATTACHMENTS = 20
_MAX_INGEST_TOTAL_BYTES = 50 * 1024 * 1024
# Clients may bundle many files into a single ``.zip`` attachment (so a whole
# Teams thread's attachments arrive as one upload rather than hitting the
# per-request count cap). Such archives are expanded server-side so the spawned
# Claude session reads files by path instead of inflating the prompt. Guards
# below bound the cost of a malicious or accidental zip bomb.
_MAX_INGEST_UNZIP_TOTAL_BYTES = 200 * 1024 * 1024
_MAX_INGEST_UNZIP_MEMBERS = 5000
# /api/spawn — trusted localhost callers may forward attachments to post into
# the spawned thread (e.g. files attached to a Forgejo Issue). Capped to keep a
# single request from buffering an unbounded amount of base64 in memory.
_MAX_SPAWN_ATTACHMENTS = 10
_MAX_SPAWN_TOTAL_BYTES = 25 * 1024 * 1024
_MAX_DISCORD_THREAD_NAME_LENGTH = 100

# /api/sessions and /api/threads/{id}/messages — cross-session observability.
# Bounded so one session peeking at another can never pull an unbounded amount
# of history into its own context window.
_DEFAULT_SESSION_LIMIT = 20
_MAX_SESSION_LIMIT = 100
_DEFAULT_THREAD_MESSAGE_LIMIT = 30
_MAX_THREAD_MESSAGE_LIMIT = 100
_DEFAULT_SEARCH_LIMIT = 15
_MAX_SEARCH_LIMIT = 50
# How far back to scan the lounge when attaching each thread's latest note.
_LOUNGE_LOOKBACK = 50
# Per-message cap; a single Claude reply can be many KB of code.
_MAX_THREAD_MESSAGE_CHARS = 2000
# Cap on a relayed message. A relay is a short coordination note ("I started at
# 13:02 on branch X, stand down"), not a payload channel.
_MAX_RELAY_TEXT_CHARS = 4000


def _serialize_thread_message(message: Any) -> dict[str, object]:
    """Reduce a discord.Message to the fields another session needs."""
    content = str(getattr(message, "content", "") or "")
    truncated = len(content) > _MAX_THREAD_MESSAGE_CHARS
    author: Any = getattr(message, "author", None)
    created_at: Any = getattr(message, "created_at", None)
    return {
        "id": getattr(message, "id", None),
        "author": str(getattr(author, "display_name", None) or author or "unknown"),
        "is_bot": bool(getattr(author, "bot", False)),
        "content": content[:_MAX_THREAD_MESSAGE_CHARS],
        "truncated": truncated,
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else None,
        "jump_url": getattr(message, "jump_url", None),
    }


# Max accepted request body. aiohttp defaults to 1 MiB, which 413s any real
# ingest (a full conversation thread plus base64 attachments). Base64 inflates
# the decoded payload ~4/3, so the body limit must exceed the decoded
# attachment cap by that factor, plus headroom for the surrounding JSON.
_DEFAULT_MAX_BODY_BYTES = _MAX_INGEST_TOTAL_BYTES * 4 // 3 + 1024 * 1024
# Characters allowed in a saved attachment filename; everything else → "_".
_UNSAFE_FILENAME_RE = re.compile(r"[^\w.\-]+")


def _safe_attachment_name(raw: object, index: int) -> str:
    """Reduce a client-supplied filename to a safe basename.

    Strips any directory component (path-traversal guard), replaces unsafe
    characters, and drops leading dots so attachments can't masquerade as
    hidden/dotfiles. Falls back to ``attachment_{index}`` when nothing usable
    remains.
    """
    name = os.path.basename(str(raw or "")).strip()
    name = _UNSAFE_FILENAME_RE.sub("_", name).lstrip(".")
    return name or f"attachment_{index}"


def _decode_spawn_attachments(
    attachments: object,
) -> tuple[list[tuple[str, bytes]], web.Response | None]:
    """Decode ``/api/spawn`` base64 attachments into ``(filename, bytes)`` pairs.

    Each item must be an object ``{filename?, data}`` where ``data`` is
    base64-encoded file bytes. Filenames are reduced to a safe basename. On any
    validation failure the second element is a ready-to-return 400 response and
    the first is empty.
    """
    if not attachments:
        return [], None
    if not isinstance(attachments, list):
        return [], web.json_response({"error": "attachments must be a list"}, status=400)
    if len(attachments) > _MAX_SPAWN_ATTACHMENTS:
        return [], web.json_response(
            {"error": f"Too many attachments (max {_MAX_SPAWN_ATTACHMENTS})"},
            status=400,
        )

    decoded: list[tuple[str, bytes]] = []
    total = 0
    for i, att in enumerate(attachments):
        if not isinstance(att, dict):
            return [], web.json_response({"error": f"Attachment {i} must be an object"}, status=400)
        data_b64 = att.get("data")
        if not data_b64:
            return [], web.json_response({"error": f"Attachment {i} missing 'data'"}, status=400)
        try:
            blob = base64.b64decode(str(data_b64), validate=True)
        except (binascii.Error, ValueError):
            return [], web.json_response(
                {"error": f"Attachment {i} has invalid base64 'data'"}, status=400
            )
        total += len(blob)
        if total > _MAX_SPAWN_TOTAL_BYTES:
            return [], web.json_response(
                {"error": "Attachments exceed total size limit"}, status=413
            )
        decoded.append((_safe_attachment_name(att.get("filename"), i), blob))
    return decoded, None


logger = logging.getLogger(__name__)


def _sanitize_log(value: object) -> str:
    """Sanitize user-provided values before writing to logs.

    Strips newline and carriage-return characters to prevent log injection
    attacks where an attacker embeds fake log entries in a single field.
    """
    return re.sub(r"[\r\n]", " ", str(value))


def _normalize_thread_name(value: object) -> str | None:
    """Return a Discord-safe thread name, or None when no usable name was supplied."""
    if value is None:
        return None
    name = str(value).strip()
    if not name:
        return None
    return name[:_MAX_DISCORD_THREAD_NAME_LENGTH]


class ApiServer:
    """Embedded REST API server for Discord bot notifications.

    Usage::

        from claude_discord.database.notification_repo import NotificationRepository
        from claude_discord.ext.api_server import ApiServer

        repo = NotificationRepository("data/notifications.db")
        await repo.init_db()
        api = ApiServer(repo=repo, bot=bot, default_channel_id=12345)
        await api.start()
        # ... bot runs ...
        await api.stop()
    """

    def __init__(
        self,
        repo: NotificationRepository,
        bot: Bot,
        default_channel_id: int | None = None,
        host: str = "127.0.0.1",
        port: int = 8080,
        api_secret: str | None = None,
        ingest_token: str | None = None,
        ingest_host: str | None = None,
        ingest_port: int | None = None,
        max_body_bytes: int | None = None,
        working_dir: str | None = None,
        task_repo: TaskRepository | None = None,
        lounge_repo: LoungeRepository | None = None,
        lounge_channel_id: int | None = None,
        resume_repo: PendingResumeRepository | None = None,
        session_repo: SessionRepository | None = None,
        ingest_repo: IngestResultRepository | None = None,
        summary_repo: ThreadSummaryRepository | None = None,
        claims_repo: ClaimRepository | None = None,
        transcripts_path: str | None = None,
        ingest_require_complete: bool | None = None,
        teams_vault_root: str | None = None,
    ) -> None:
        if host.lower() != "localhost":
            try:
                local_bind = ipaddress.ip_address(host).is_loopback
            except ValueError:
                local_bind = False
            if not local_bind and not api_secret:
                raise ValueError("A non-loopback control-plane bind requires an API secret")
        self.repo = repo
        self.bot = bot
        self.default_channel_id = default_channel_id
        self.host = host
        self.port = port
        self.api_secret = api_secret
        self.ingest_token = ingest_token
        self.ingest_host = ingest_host
        self.ingest_port = ingest_port
        self.max_body_bytes = max_body_bytes or _DEFAULT_MAX_BODY_BYTES
        self.working_dir = working_dir
        self.task_repo = task_repo
        self.lounge_repo = lounge_repo
        self.resume_repo = resume_repo
        self.session_repo = session_repo
        self.ingest_repo = ingest_repo
        self.summary_repo = summary_repo
        self.claims_repo = claims_repo
        # Where Claude Code transcripts live, for /api/search?body=1. Falls back
        # to the standard ~/.claude/projects location so body search is
        # Zero-Config wherever Claude Code has run.
        self.transcripts_path = transcripts_path or default_transcripts_root()
        # Reject an ingest whose manifest proves attachments went missing,
        # instead of starting a session on partial evidence. Off by default: a
        # partial export is usually still worth answering, now that its gaps are
        # stated in the prompt. Turn on where the attachments *are* the request.
        if ingest_require_complete is None:
            ingest_require_complete = os.getenv(
                "CCDB_INGEST_REQUIRE_COMPLETE", ""
            ).strip().lower() in (
                "1",
                "true",
                "yes",
            )
        self.ingest_require_complete = ingest_require_complete
        # Where /api/teams/sync mirrors upstream threads. Defaults to
        # {working_dir}/teams, beside the ingest tree; point it at a notes vault
        # or anywhere else with CCDB_TEAMS_VAULT_ROOT.
        self.teams_vault_root = teams_vault_root
        # Loop/rate brake for thread-to-thread relays. Process-local by design:
        # after a restart there are no in-flight relay chains to protect.
        self.relay_guard = RelayGuard()
        # AI Lounge Discord mirror (OPTIONAL, human-facing). When a channel is
        # configured, lounge messages are echoed there so a human can watch the
        # AI-to-AI chatter in Discord. Leave it unset to run the lounge DB-only:
        # the prompt-injected lounge and every coordination API keep working, so
        # a deployment whose humans don't read the channel loses nothing.
        # Falls back to COORDINATION_CHANNEL_ID for backward compatibility.
        if lounge_channel_id is None:
            ch_str = os.getenv("COORDINATION_CHANNEL_ID", "")
            lounge_channel_id = int(ch_str) if ch_str.isdigit() else None
        self.lounge_channel_id = lounge_channel_id
        # Set once the mirror channel is found to be gone (deleted / no access),
        # so a removed channel doesn't log a warning on every single post.
        self._lounge_mirror_disabled = False

        self.app = web.Application(client_max_size=self.max_body_bytes)
        if os.getenv("CCDB_CONTROL_PLANE_HOST_GUARD", "1").lower() not in ("0", "false", "no"):
            self.app.middlewares.append(self._host_middleware)
        if self.api_secret:
            self.app.middlewares.append(self._auth_middleware)
        self._setup_routes()
        # Separate app for the externally reachable (non-localhost) listener.
        # It exposes ONLY the token-gated ingest surface so the RCE-capable
        # control plane (/api/spawn etc.) never leaves localhost.
        self.external_app = self._build_external_app()
        self._runner: web.AppRunner | None = None
        self._ext_runner: web.AppRunner | None = None

    def _setup_routes(self) -> None:
        self.app.router.add_get("/api/health", self.health)
        self.app.router.add_get("/obsidian", self.open_obsidian)
        self.app.router.add_post("/api/notify", self.notify)
        self.app.router.add_post("/api/schedule", self.schedule)
        self.app.router.add_get("/api/scheduled", self.list_scheduled)
        self.app.router.add_delete("/api/scheduled/{id}", self.cancel_scheduled)
        # Scheduled task routes (requires task_repo)
        self.app.router.add_post("/api/tasks", self.create_task)
        self.app.router.add_get("/api/tasks", self.list_tasks)
        self.app.router.add_delete("/api/tasks/{id}", self.delete_task)
        self.app.router.add_patch("/api/tasks/{id}", self.patch_task)
        # AI Lounge routes (requires lounge_repo)
        self.app.router.add_get("/api/lounge", self.get_lounge)
        self.app.router.add_post("/api/lounge", self.post_lounge)
        # Advisory resource claims (requires claims_repo)
        self.app.router.add_post("/api/claims", self.create_claim)
        self.app.router.add_get("/api/claims", self.list_claims)
        self.app.router.add_delete("/api/claims", self.delete_claim)
        # Cross-session observability routes (requires session_repo)
        self.app.router.add_get("/api/sessions", self.list_sessions)
        self.app.router.add_get("/api/search", self.search_sessions)
        self.app.router.add_get("/api/threads/{thread_id}/messages", self.get_thread_messages)
        self.app.router.add_post("/api/threads/{thread_id}/message", self.relay_thread_message)
        # Session spawn route
        self.app.router.add_post("/api/spawn", self.spawn)
        # Authenticated external ingest route (browser extension / webhooks)
        self.app.router.add_post("/api/ingest", self.ingest)
        # Running per-thread summaries. GET (external, token) reads the stored
        # summary+marker; POST (internal control plane) lets the Claude session
        # save an updated summary. Register the fixed `/summary` paths BEFORE the
        # dynamic `/{result_id}` so "summary" is never captured as a result id.
        self.app.router.add_get("/api/ingest/summary", self.get_thread_summary)
        self.app.router.add_post("/api/ingest/summary", self.save_thread_summary)
        self.app.router.add_delete("/api/ingest/summary", self.delete_thread_summary)
        # Poll an ingest session's final result (requires ingest_repo)
        self.app.router.add_get("/api/ingest/{result_id}", self.get_ingest_result)
        # Teams thread sync (have/want). Mirrors an upstream thread into the
        # vault as one file per message; the client keeps no state of its own.
        self.app.router.add_post("/api/teams/sync/plan", self.teams_sync_plan)
        self.app.router.add_post("/api/teams/sync/push", self.teams_sync_push)
        # Startup resume routes
        self.app.router.add_post("/api/mark-resume", self.mark_resume)

    def _build_external_app(self) -> web.Application:
        """Build the app served on the externally reachable listener.

        Only the safe, token-gated ingest surface is registered here. The
        ``ingest`` and ``get_ingest_result`` handlers enforce ``ingest_token``
        themselves (constant-time compare), so no global middleware is needed.
        ``/api/health`` is intentionally open for liveness probes.
        """
        app = web.Application(client_max_size=self.max_body_bytes)
        app.router.add_get("/api/health", self.health)
        app.router.add_post("/api/ingest", self.ingest)
        # External clients read the running summary/marker to compute their diff.
        # Registered before the dynamic result route (see _setup_routes). The
        # summary POST is intentionally NOT exposed here — writing a summary is a
        # localhost control-plane action performed by the Claude session, not the
        # untrusted external client.
        app.router.add_get("/api/ingest/summary", self.get_thread_summary)
        app.router.add_get("/api/ingest/{result_id}", self.get_ingest_result)
        # The Teams sync pair is the browser extension's main surface, so it has
        # to be reachable on the same listener as /api/ingest. Both handlers
        # enforce the ingest token themselves and write only under the vault
        # root, never spawning anything.
        app.router.add_post("/api/teams/sync/plan", self.teams_sync_plan)
        app.router.add_post("/api/teams/sync/push", self.teams_sync_push)
        return app

    @web.middleware
    async def _host_middleware(
        self,
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        """Refuse accidental proxying/rebinding of the privileged local app.

        This is defense in depth: a proxy that strips every forwarding header
        and rewrites Host can conceal itself. Never expose this app publicly.
        Deliberate authenticated proxies must explicitly disable this guard.
        """
        try:
            address = urlsplit("//" + request.headers.get("Host", ""))
            host = address.hostname or ""
            valid_port = address.port is None or 0 < address.port < 65536
            local = host.lower() == "localhost" or ipaddress.ip_address(host).is_loopback
            valid = (
                local
                and valid_port
                and not (
                    address.username
                    or address.password
                    or address.path
                    or address.query
                    or address.fragment
                )
            )
            origin = request.headers.get("Origin")
            if origin:
                source = urlsplit(origin)
                valid = (
                    valid and source.scheme in ("http", "https") and source.netloc == address.netloc
                )
        except ValueError:
            valid = False
        if any(
            name in request.headers
            for name in ("Forwarded", "X-Forwarded-For", "X-Forwarded-Host", "X-Forwarded-Proto")
        ):
            valid = False
        if not valid:
            return web.json_response(
                {"error": "Control plane accepts direct loopback requests only"}, status=403
            )
        return await handler(request)

    @web.middleware
    async def _auth_middleware(
        self,
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        """Bearer token authentication middleware."""
        if request.path == "/api/health" or request.path == "/obsidian":
            return await handler(request)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return web.json_response({"error": "Missing Authorization header"}, status=401)

        token = auth_header[7:]
        # 定数時間比較でタイミング攻撃を防ぐ（`==` は一致長で実行時間が変わる）。
        # この middleware は api_secret 設定時のみ登録される（secret は str 確定）。
        secret = self.api_secret or ""
        if not hmac.compare_digest(token, secret):
            return web.json_response({"error": "Invalid token"}, status=401)

        return await handler(request)

    async def start(self) -> None:
        """Start the API server."""
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port, reuse_address=True)
        await site.start()
        logger.info("REST API started: http://%s:%d", self.host, self.port)
        await self._start_external_listener()

    async def _start_external_listener(self) -> None:
        """Start the ingest-only listener on a non-localhost interface.

        Enabled only when both ``ingest_host`` and ``ingest_port`` are set. A
        missing ``ingest_token`` is a hard refusal (not a silent skip): exposing
        an unauthenticated surface to the LAN would be a security regression, so
        we log a warning and leave the listener down.
        """
        if not (self.ingest_host and self.ingest_port):
            return
        if not self.ingest_token:
            logger.warning(
                "External ingest listener requested (%s:%d) but no ingest_token "
                "is set — refusing to expose an unauthenticated surface. "
                "Set CCDB_INGEST_TOKEN to enable it.",
                self.ingest_host,
                self.ingest_port,
            )
            return
        self._ext_runner = web.AppRunner(self.external_app)
        await self._ext_runner.setup()
        ext_site = web.TCPSite(
            self._ext_runner, self.ingest_host, self.ingest_port, reuse_address=True
        )
        await ext_site.start()
        logger.info(
            "External ingest API started: http://%s:%d (ingest-only, token-gated)",
            self.ingest_host,
            self.ingest_port,
        )

    async def stop(self) -> None:
        """Stop the API server."""
        if self._runner:
            await self._runner.cleanup()
        if self._ext_runner:
            await self._ext_runner.cleanup()

    async def health(self, request: web.Request) -> web.Response:
        """GET /api/health — health check, including delivery backlog.

        A scheduled notification that is stored but never sent used to be
        undetectable from outside: create returned 201, the list endpoint
        showed it, and only the human waiting for it ever found out.  The
        backlog is therefore part of "healthy" — a row whose time has passed
        and is still pending means the dispatcher is not draining the queue.
        """
        overdue = 0
        try:
            now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
            overdue = len(await self.repo.get_pending(before=now))
        except Exception:
            # A liveness probe must answer even when the store is unreadable.
            logger.exception("Health check could not read the notification backlog")

        return web.json_response(
            {
                "status": "degraded" if overdue else "ok",
                "overdue_notifications": overdue,
                "timestamp": datetime.now().isoformat(),
            }
        )

    async def open_obsidian(self, request: web.Request) -> web.Response:
        """GET /open/obsidian — redirect to ``obsidian://open`` URI.

        No authentication required.  Used by Discord link buttons to open
        Obsidian notes on the user's machine.
        """
        from urllib.parse import quote

        vault = request.rel_url.query.get("vault", "")
        file_path = request.rel_url.query.get("file", "")
        if not vault or not file_path:
            return web.json_response(
                {"error": "vault and file query parameters are required"}, status=400
            )
        target = f"obsidian://open?vault={quote(vault, safe='')}&file={quote(file_path, safe='')}"
        raise web.HTTPFound(location=target)

    async def notify(self, request: web.Request) -> web.Response:
        """POST /api/notify — send an immediate notification."""
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        message = data.get("message")
        if not message:
            return web.json_response({"error": "message is required"}, status=400)

        channel_id = data.get("channel_id") or self.default_channel_id
        if not channel_id:
            return web.json_response({"error": "No channel specified"}, status=400)

        raw_channel = self.bot.get_channel(channel_id)
        if not raw_channel:
            try:
                raw_channel = await self.bot.fetch_channel(channel_id)
            except Exception as e:
                return web.json_response({"error": str(e)}, status=500)

        if not hasattr(raw_channel, "send"):
            return web.json_response({"error": "Channel is not messageable"}, status=400)

        # Build poll if specified
        poll_data = data.get("poll")
        poll_obj = None
        if poll_data:
            poll_result = self._build_poll(poll_data)
            if isinstance(poll_result, web.Response):
                return poll_result
            poll_obj = poll_result

        thread_name = _normalize_thread_name(data.get("thread_name"))
        fmt = data.get("format", "text" if thread_name else "embed")

        # Determine target: create a new thread or send directly to channel
        if thread_name:
            if not hasattr(raw_channel, "create_thread"):
                return web.json_response({"error": "Channel does not support threads"}, status=400)
            thread_result = await raw_channel.create_thread(  # type: ignore[union-attr]
                name=thread_name,
                auto_archive_duration=THREAD_AUTO_ARCHIVE_MINUTES,
            )
            # create_thread may return Thread or ThreadWithMessage depending on discord.py version
            thread = thread_result.thread if hasattr(thread_result, "thread") else thread_result  # type: ignore[union-attr]
            target = thread
        else:
            target = raw_channel
            thread = None

        if poll_obj:
            await target.send(content=message, poll=poll_obj)  # type: ignore[union-attr]
        elif fmt == "text":
            await target.send(message)  # type: ignore[union-attr]
        else:
            title = data.get("title")
            embed = self._build_embed(message=message, title=title, color=data.get("color"))
            await target.send(embed=embed)  # type: ignore[union-attr]

        result: dict[str, str] = {"status": "sent"}
        if thread is not None:
            result["thread_id"] = str(thread.id)  # type: ignore[union-attr]
            result["thread_name"] = thread.name  # type: ignore[union-attr]
        return web.json_response(result)

    async def schedule(self, request: web.Request) -> web.Response:
        """POST /api/schedule — schedule a notification for later."""
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        message = data.get("message")
        scheduled_at = data.get("scheduled_at")

        if not message:
            return web.json_response({"error": "message is required"}, status=400)
        if not scheduled_at:
            return web.json_response({"error": "scheduled_at is required"}, status=400)

        try:
            dt = datetime.fromisoformat(scheduled_at)
            scheduled_str = dt.strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return web.json_response(
                {"error": "scheduled_at must be ISO 8601 format"},
                status=400,
            )

        notification_id = await self.repo.create(
            message=message,
            scheduled_at=scheduled_str,
            title=data.get("title"),
            color=data.get("color", 0x00BFFF),
            source="api",
            channel_id=data.get("channel_id"),
        )

        return web.json_response({"status": "scheduled", "id": notification_id})

    async def list_scheduled(self, request: web.Request) -> web.Response:
        """GET /api/scheduled — list pending notifications."""
        pending = await self.repo.get_pending()
        return web.json_response({"notifications": pending})

    async def cancel_scheduled(self, request: web.Request) -> web.Response:
        """DELETE /api/scheduled/{id} — cancel a pending notification."""
        try:
            notification_id = int(request.match_info["id"])
        except (ValueError, KeyError):
            return web.json_response({"error": "Invalid ID"}, status=400)

        success = await self.repo.cancel(notification_id)
        if success:
            return web.json_response({"status": "cancelled"})
        return web.json_response(
            {"error": "Not found or already processed"},
            status=404,
        )

    # ------------------------------------------------------------------
    # Scheduled task endpoints (/api/tasks)
    # ------------------------------------------------------------------

    def _require_task_repo(self) -> web.Response | None:
        """Return a 503 response if task_repo is not configured."""
        if self.task_repo is None:
            return web.json_response(
                {"error": "SchedulerCog not configured (task_repo is None)"},
                status=503,
            )
        return None

    @staticmethod
    def _parse_anchor_time(raw: str | None) -> tuple[int | None, int | None]:
        """Parse ``"HH:MM"`` into (hour, minute).  Returns (None, None) if absent."""
        if not raw:
            return None, None
        parts = raw.split(":")
        if len(parts) != 2:  # noqa: PLR2004
            raise ValueError(f"anchor_time must be HH:MM, got {raw!r}")
        return int(parts[0]), int(parts[1])

    async def create_task(self, request: web.Request) -> web.Response:
        """POST /api/tasks — register a scheduled Claude Code task.

        Body (JSON):
            name: Unique task identifier.
            prompt: Claude Code prompt to run on schedule.
            interval_seconds: How often to run (seconds).
            channel_id: Discord channel ID for thread creation.
            working_dir: (optional) Working directory for Claude.
            run_immediately: (optional, default true) Fire on next loop tick.
            anchor_time: (optional) Wall-clock time ``"HH:MM"`` to snap to,
                preventing time drift. When set, next_run_at is calculated as
                the next future occurrence of that time.
            thread_id: (optional) Discord thread ID for follow-up mode.
                When set, the scheduler posts into this existing thread
                instead of creating a new one.
            one_shot: (optional, default false) If true, auto-disable after
                a single execution.
        """
        if err := self._require_task_repo():
            return err
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        for field in ("name", "prompt", "interval_seconds", "channel_id"):
            if not data.get(field):
                return web.json_response({"error": f"{field} is required"}, status=400)

        try:
            anchor_hour, anchor_minute = self._parse_anchor_time(data.get("anchor_time"))
        except ValueError as exc:
            return web.json_response({"error": str(exc)}, status=400)

        # Parse optional follow-up parameters
        raw_thread_id = data.get("thread_id")
        thread_id: int | None = None
        if raw_thread_id is not None:
            with contextlib.suppress(ValueError, TypeError):
                thread_id = int(raw_thread_id)

        one_shot = bool(data.get("one_shot", False))

        try:
            task_id = await self.task_repo.create(  # type: ignore[union-attr]
                name=str(data["name"]),
                prompt=str(data["prompt"]),
                interval_seconds=int(data["interval_seconds"]),
                channel_id=int(data["channel_id"]),
                working_dir=data.get("working_dir"),
                run_immediately=bool(data.get("run_immediately", True)),
                anchor_hour=anchor_hour,
                anchor_minute=anchor_minute,
                thread_id=thread_id,
                one_shot=one_shot,
            )
        except Exception as exc:
            # Most likely a UNIQUE constraint violation on name
            logger.warning("Failed to create task: %s", exc)
            return web.json_response({"error": "Task name already exists"}, status=409)

        logger.info("Task registered via API: id=%d, name=%s", task_id, _sanitize_log(data["name"]))
        return web.json_response({"status": "created", "id": task_id}, status=201)

    async def list_tasks(self, request: web.Request) -> web.Response:
        """GET /api/tasks — list all registered tasks."""
        if err := self._require_task_repo():
            return err
        tasks = await self.task_repo.get_all()  # type: ignore[union-attr]
        return web.json_response({"tasks": tasks})

    async def delete_task(self, request: web.Request) -> web.Response:
        """DELETE /api/tasks/{id} — remove a scheduled task."""
        if err := self._require_task_repo():
            return err
        try:
            task_id = int(request.match_info["id"])
        except (ValueError, KeyError):
            return web.json_response({"error": "Invalid ID"}, status=400)

        deleted = await self.task_repo.delete(task_id)  # type: ignore[union-attr]
        if deleted:
            return web.json_response({"status": "deleted"})
        return web.json_response({"error": "Task not found"}, status=404)

    async def patch_task(self, request: web.Request) -> web.Response:
        """PATCH /api/tasks/{id} — update a task.

        Body (JSON, all optional):
            enabled: bool
            prompt: str
            interval_seconds: int
            working_dir: str
            anchor_time: ``"HH:MM"`` to set, or ``null`` to clear
            next_run_at: float (epoch) — manual schedule reset
        """
        if err := self._require_task_repo():
            return err
        try:
            task_id = int(request.match_info["id"])
        except (ValueError, KeyError):
            return web.json_response({"error": "Invalid ID"}, status=400)

        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        updated = False
        if "enabled" in data:
            result = await self.task_repo.set_enabled(task_id, enabled=bool(data["enabled"]))  # type: ignore[union-attr]
            updated = updated or result

        patch_kwargs: dict[str, object] = {}
        if "prompt" in data:
            patch_kwargs["prompt"] = str(data["prompt"])
        if "interval_seconds" in data:
            patch_kwargs["interval_seconds"] = int(data["interval_seconds"])
        if "working_dir" in data:
            patch_kwargs["working_dir"] = str(data["working_dir"])

        # anchor_time: "HH:MM" to set, null to clear
        if "anchor_time" in data:
            raw_anchor = data["anchor_time"]
            if raw_anchor is None:
                patch_kwargs["anchor_hour"] = -1  # sentinel: clear
            else:
                try:
                    h, m = self._parse_anchor_time(raw_anchor)
                except ValueError as exc:
                    return web.json_response({"error": str(exc)}, status=400)
                patch_kwargs["anchor_hour"] = h
                patch_kwargs["anchor_minute"] = m

        if patch_kwargs:
            result = await self.task_repo.update(task_id, **patch_kwargs)  # type: ignore[union-attr]
            updated = updated or result

        # Manual next_run_at override
        if "next_run_at" in data:
            await self.task_repo._db_execute(  # type: ignore[union-attr]
                "UPDATE scheduled_tasks SET next_run_at = ? WHERE id = ?",
                (float(data["next_run_at"]), task_id),
            )
            updated = True

        if updated:
            return web.json_response({"status": "updated"})
        return web.json_response({"error": "Task not found"}, status=404)

    # ------------------------------------------------------------------
    # AI Lounge endpoints (/api/lounge)
    # ------------------------------------------------------------------

    def _require_lounge_repo(self) -> web.Response | None:
        """Return a 503 response if lounge_repo is not configured."""
        if self.lounge_repo is None:
            return web.json_response(
                {"error": "AI Lounge not configured (lounge_repo is None)"},
                status=503,
            )
        return None

    async def get_lounge(self, request: web.Request) -> web.Response:
        """GET /api/lounge — list recent AI Lounge messages.

        Query params:
            limit: Maximum number of messages to return (default 10, max 50).
        """
        if err := self._require_lounge_repo():
            return err

        try:
            raw_limit = request.rel_url.query.get("limit", "10")
            limit = max(1, min(50, int(raw_limit)))
        except ValueError:
            return web.json_response({"error": "limit must be an integer"}, status=400)

        messages = await self.lounge_repo.get_recent(limit=limit)  # type: ignore[union-attr]
        return web.json_response(
            {
                "messages": [
                    {
                        "id": m.id,
                        "label": m.label,
                        "message": m.message,
                        "thread_id": m.thread_id,
                        "posted_at": m.posted_at,
                    }
                    for m in messages
                ]
            }
        )

    async def post_lounge(self, request: web.Request) -> web.Response:
        """POST /api/lounge — post a message to the AI Lounge.

        Body (JSON):
            message: The lounge message text (required).
            label: The sender's label/nickname (optional, default "AI").

        The message is stored in SQLite and forwarded to the configured
        lounge Discord channel (if lounge_channel_id is set).
        """
        if err := self._require_lounge_repo():
            return err

        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        message = data.get("message", "").strip()
        if not message:
            return web.json_response({"error": "message is required"}, status=400)

        label = str(data.get("label", "AI")).strip() or "AI"

        # thread_id is optional — allows tracing which Discord thread posted the message
        raw_thread_id = data.get("thread_id")
        thread_id: int | None = None
        if raw_thread_id is not None:
            with contextlib.suppress(ValueError, TypeError):
                thread_id = int(raw_thread_id)

        stored = await self.lounge_repo.post(message=message, label=label, thread_id=thread_id)  # type: ignore[union-attr]

        # Forward to Discord lounge channel if configured
        if self.lounge_channel_id:
            await self._send_lounge_to_discord(stored.label, stored.message, stored.posted_at)

        payload: dict[str, Any] = {
            "status": "posted",
            "id": stored.id,
            "label": stored.label,
            "message": stored.message,
            "thread_id": stored.thread_id,
            "posted_at": stored.posted_at,
        }
        if hint := length_hint(stored.message):
            payload["hint"] = hint

        return web.json_response(payload, status=201)

    # ------------------------------------------------------------------
    # Session spawn endpoint (/api/spawn)
    # ------------------------------------------------------------------

    async def relay_thread_message(self, request: web.Request) -> web.Response:
        """POST /api/threads/{thread_id}/message — talk to another live session.

        The write side of cross-session coordination: a session that has seen a
        peer working on the same task can say so, and ask it to stand down.

        ``on_message`` ignores anything a bot wrote (that guard is what stops
        the bot from talking to itself), so relays go through this endpoint —
        the same shape as ``/api/spawn``, which also bypasses it deliberately.

        Body (JSON):
            text: What to say. Wrapped in a marker so the receiver cannot
                mistake it for its human's instruction.
            from_thread: The sending session's thread ID (required — a relay
                without an origin is not answerable).
            mode: ``queue`` (default) waits for the receiver's current turn to
                finish; ``interrupt`` SIGINTs it. Use ``interrupt`` only for
                "stop now", since it can cost the receiver uncommitted work.
            hop: Chain depth, 0 for a fresh conversation. A reply passes
                ``hop + 1``; the guard refuses beyond MAX_HOP.

        Returns 202 when delivered (Claude runs in the background), 429 when
        the relay guard refuses (loop/cooldown/rate), 404 for unknown threads.
        """
        try:
            thread_id = int(request.match_info["thread_id"])
        except (ValueError, KeyError):
            return web.json_response({"error": "Invalid thread_id"}, status=400)

        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        text = str(data.get("text") or "").strip()
        if not text:
            return web.json_response({"error": "text is required"}, status=400)
        if len(text) > _MAX_RELAY_TEXT_CHARS:
            return web.json_response(
                {"error": f"text must be at most {_MAX_RELAY_TEXT_CHARS} characters"},
                status=400,
            )

        try:
            from_thread = int(data["from_thread"])
        except (KeyError, TypeError, ValueError):
            return web.json_response({"error": "from_thread is required (integer)"}, status=400)

        mode = str(data.get("mode") or MODE_QUEUE).lower()
        if mode not in VALID_MODES:
            return web.json_response(
                {"error": f"mode must be one of {', '.join(VALID_MODES)}"}, status=400
            )

        try:
            hop = int(data.get("hop", 0))
        except (TypeError, ValueError):
            return web.json_response({"error": "hop must be an integer"}, status=400)

        now = time.monotonic()
        refusal = self.relay_guard.check(
            from_thread=from_thread, to_thread=thread_id, hop=hop, now=now
        )
        if refusal is not None:
            logger.info("Relay refused (%s → %s): %s", from_thread, thread_id, refusal)
            return web.json_response({"error": refusal}, status=429)

        from ..cogs.claude_chat import ClaudeChatCog

        cog: ClaudeChatCog | None = self.bot.cogs.get("ClaudeChatCog")  # type: ignore[assignment]
        if cog is None:
            return web.json_response({"error": "ClaudeChatCog is not loaded"}, status=503)

        import discord as _discord

        thread = self.bot.get_channel(thread_id)
        if thread is None:
            try:
                thread = await self.bot.fetch_channel(thread_id)
            except Exception as exc:
                return web.json_response({"error": str(exc)}, status=404)
        if not isinstance(thread, _discord.Thread):
            return web.json_response({"error": "Target must be a thread"}, status=400)

        prompt = build_relay_prompt(text=text, from_thread=from_thread, hop=hop)
        self.relay_guard.record(from_thread=from_thread, to_thread=thread_id, now=now)

        # Run in the background so the sender is not blocked for the length of
        # the receiver's turn.
        asyncio.create_task(
            cog.deliver_relayed_message(thread, prompt, interrupt=mode == MODE_INTERRUPT)
        )
        logger.info(
            "Relayed message: thread %s → thread %s (mode=%s, hop=%s)",
            from_thread,
            thread_id,
            mode,
            hop,
        )
        return web.json_response(
            {"status": "delivered", "thread_id": thread_id, "mode": mode, "hop": hop},
            status=202,
        )

    def _require_claims_repo(self) -> web.Response | None:
        """Return a 503 response if claims_repo is not configured."""
        if self.claims_repo is None:
            return web.json_response(
                {"error": "claims_repo is not configured"},
                status=503,
            )
        return None

    def _claim_json(self, claim: object) -> dict[str, object]:
        """Serialize a Claim, annotating the holder so 409s are actionable."""
        thread_id = getattr(claim, "thread_id", None)
        return {
            "resource": getattr(claim, "resource", None),
            "thread_id": thread_id,
            "note": getattr(claim, "note", None),
            "created_at": getattr(claim, "created_at", None),
            "expires_at": getattr(claim, "expires_at", None),
            "holder_state": (
                STATE_RUNNING if thread_id in self._running_thread_ids() else STATE_HISTORY
            ),
            "holder_thread_name": self._thread_names({thread_id}).get(thread_id)
            if isinstance(thread_id, int)
            else None,
        }

    async def create_claim(self, request: web.Request) -> web.Response:
        """POST /api/claims — claim a resource, or learn who already holds it.

        The cheap half of cross-session coordination: no LLM round trip, no
        negotiation.  A session claims what it is about to work on; a second
        session asking for the same thing gets 409 and steps aside before doing
        any work.  Claims are advisory and expire.

        Body (JSON):
            resource: Free-form name agreed by convention, e.g. ``repo:ccdb``,
                ``repo:ccdb#issue-123``, ``file:claude_discord/bot.py``.
            thread_id: The claiming session's Discord thread ID.
            ttl_seconds: Optional lifetime (default 2h, max 24h).
            note: Optional human-readable intent shown to whoever collides.

        Returns 201 when acquired or renewed, 409 when another live thread
        holds it (body carries the holder, its state and its note).
        """
        if err := self._require_claims_repo():
            return err
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        from ..database.claims_repo import (
            DEFAULT_TTL_SECONDS,
            MAX_NOTE_LENGTH,
            MAX_RESOURCE_LENGTH,
            normalize_resource,
        )

        resource = normalize_resource(data.get("resource"))
        if not resource:
            return web.json_response({"error": "resource is required"}, status=400)
        if len(resource) > MAX_RESOURCE_LENGTH:
            return web.json_response(
                {"error": f"resource must be at most {MAX_RESOURCE_LENGTH} characters"},
                status=400,
            )

        try:
            thread_id = int(data["thread_id"])
        except (KeyError, TypeError, ValueError):
            return web.json_response({"error": "thread_id is required (integer)"}, status=400)

        try:
            ttl_seconds = int(data.get("ttl_seconds", DEFAULT_TTL_SECONDS))
        except (TypeError, ValueError):
            return web.json_response({"error": "ttl_seconds must be an integer"}, status=400)

        note = data.get("note")
        note = str(note)[:MAX_NOTE_LENGTH] if note else None

        acquired, claim = await self.claims_repo.acquire(  # type: ignore[union-attr]
            resource, thread_id, ttl_seconds=ttl_seconds, note=note
        )
        if not acquired:
            logger.info(
                "Claim denied: %s wanted by thread %s, held by thread %s",
                _sanitize_log(resource),
                thread_id,
                claim.thread_id,
            )
            return web.json_response(
                {"status": "held", "claim": self._claim_json(claim)},
                status=409,
            )
        return web.json_response(
            {"status": "acquired", "claim": self._claim_json(claim)},
            status=201,
        )

    async def list_claims(self, request: web.Request) -> web.Response:
        """GET /api/claims — list live claims.

        Query params:
            resource: Exact resource name to look up (optional).
        """
        if err := self._require_claims_repo():
            return err

        from ..database.claims_repo import normalize_resource

        raw = request.rel_url.query.get("resource")
        resource = normalize_resource(raw) if raw else None
        claims = await self.claims_repo.list_active(resource)  # type: ignore[union-attr]
        return web.json_response({"claims": [self._claim_json(c) for c in claims]})

    async def delete_claim(self, request: web.Request) -> web.Response:
        """DELETE /api/claims?resource=X&thread_id=Y[&force=true] — release a claim.

        A session releases what it finished.  ``force=true`` lets a session take
        over a resource pinned by a peer that is no longer running (the 409 body
        from ``POST /api/claims`` reports the holder's state, which is how a
        caller justifies forcing).
        """
        if err := self._require_claims_repo():
            return err

        from ..database.claims_repo import normalize_resource

        resource = normalize_resource(request.rel_url.query.get("resource"))
        if not resource:
            return web.json_response({"error": "resource is required"}, status=400)

        force = request.rel_url.query.get("force", "").lower() in ("1", "true", "yes")
        raw_thread = request.rel_url.query.get("thread_id")
        thread_id = 0
        if raw_thread:
            try:
                thread_id = int(raw_thread)
            except ValueError:
                return web.json_response({"error": "thread_id must be an integer"}, status=400)
        elif not force:
            return web.json_response(
                {"error": "thread_id is required unless force=true"}, status=400
            )

        released = await self.claims_repo.release(  # type: ignore[union-attr]
            resource, thread_id, force=force
        )
        if not released:
            return web.json_response(
                {"error": "No matching claim (not held, expired, or held by another thread)"},
                status=404,
            )
        return web.json_response({"status": "released", "resource": resource})

    def _require_session_repo(self) -> web.Response | None:
        """Return a 503 response if session_repo is not configured."""
        if self.session_repo is None:
            return web.json_response(
                {"error": "session_repo is not configured"},
                status=503,
            )
        return None

    def _running_thread_ids(self) -> set[int]:
        """Threads with a Claude turn in flight right now.

        Two independent signals agree in practice: the SessionRegistry (entry
        exists only between turn start and turn end) and ClaudeChatCog's live
        runner map.  Their union is used so a session is never reported idle
        while it is actually working.  ``isinstance`` guards keep MagicMock
        bots (tests, embedded setups) from producing junk.
        """
        from ..concurrency import SessionRegistry

        running: set[int] = set()

        registry = getattr(self.bot, "session_registry", None)
        if isinstance(registry, SessionRegistry):
            running.update(s.thread_id for s in registry.list_active())

        cog = self.bot.cogs.get("ClaudeChatCog") if hasattr(self.bot, "cogs") else None
        active_runners = getattr(cog, "_active_runners", None)
        if isinstance(active_runners, dict):
            running.update(int(tid) for tid in active_runners)

        return running

    def _active_sessions(self) -> list:
        """Registry entries describing what each live session is doing."""
        from ..concurrency import SessionRegistry

        registry = getattr(self.bot, "session_registry", None)
        if isinstance(registry, SessionRegistry):
            return registry.list_active()
        return []

    async def list_sessions(self, request: web.Request) -> web.Response:
        """GET /api/sessions — what every other Claude session is doing.

        Lets a session discover its peers before touching a shared repository:
        which threads are alive, where they are working, and what they last
        announced in the AI Lounge.  Read-only.

        Query params:
            limit: Max persisted sessions to consider (default 20, max 100).
                   Live sessions are always included regardless of this cap.
            state: ``running`` to return only sessions with a turn in flight.
            exclude_thread: Thread ID to omit (typically the caller's own).
        """
        if err := self._require_session_repo():
            return err

        try:
            raw_limit = request.rel_url.query.get("limit", str(_DEFAULT_SESSION_LIMIT))
            limit = max(1, min(_MAX_SESSION_LIMIT, int(raw_limit)))
        except ValueError:
            return web.json_response({"error": "limit must be an integer"}, status=400)

        exclude_raw = request.rel_url.query.get("exclude_thread")
        exclude_thread: int | None = None
        if exclude_raw:
            try:
                exclude_thread = int(exclude_raw)
            except ValueError:
                return web.json_response({"error": "exclude_thread must be an integer"}, status=400)

        records = await self.session_repo.list_all(limit=limit)  # type: ignore[union-attr]
        lounge_messages = (
            await self.lounge_repo.get_recent(limit=_LOUNGE_LOOKBACK)
            if self.lounge_repo is not None
            else []
        )

        active = self._active_sessions()
        thread_ids = {r.thread_id for r in records} | {s.thread_id for s in active}
        views = build_session_views(
            records=records,
            active=active,
            running_thread_ids=self._running_thread_ids(),
            lounge_messages=lounge_messages,
            thread_names=self._thread_names(thread_ids),
        )

        if request.rel_url.query.get("state") == STATE_RUNNING:
            views = [v for v in views if v["state"] == STATE_RUNNING]
        if exclude_thread is not None:
            views = [v for v in views if v["thread_id"] != exclude_thread]

        return web.json_response({"sessions": views})

    async def search_sessions(self, request: web.Request) -> web.Response:
        """GET /api/search — find a past thread by keyword.

        Discord threads vanish from the sidebar once they auto-archive, and
        their titles are often vague.  This searches the persistent per-thread
        ``summary`` (the opening prompt, already stored for every session) plus
        the working directory, and returns a Discord deep-link so an archived
        thread can be reopened with one click.  No AI tokens, no new storage —
        just a ``LIKE`` query over data ccdb already keeps.

        Query params:
            q: Keyword (required, non-blank). Matched against summary and
               working_dir (case-insensitive substring).
            origin: ``discord`` or ``cli`` to filter by session origin.
            limit: Max summary results (default 15, max 50).
            body: ``1`` to also grep local Claude transcripts for the keyword
                  (token-free), surfacing threads whose *conversation* mentioned
                  it even when the opening summary did not.
        """
        if err := self._require_session_repo():
            return err

        query = (request.rel_url.query.get("q") or "").strip()
        if not query:
            return web.json_response({"error": "q must be a non-blank string"}, status=400)

        try:
            raw_limit = request.rel_url.query.get("limit", str(_DEFAULT_SEARCH_LIMIT))
            limit = max(1, min(_MAX_SEARCH_LIMIT, int(raw_limit)))
        except ValueError:
            return web.json_response({"error": "limit must be an integer"}, status=400)

        origin = request.rel_url.query.get("origin")
        if origin not in (None, "discord", "cli"):
            return web.json_response({"error": "origin must be 'discord' or 'cli'"}, status=400)

        include_body = request.rel_url.query.get("body") in ("1", "true", "yes")

        results = await run_thread_search(
            session_repo=self.session_repo,  # type: ignore[arg-type]
            query=query,
            origin=origin,
            limit=limit,
            include_body=include_body,
            transcripts_root=self.transcripts_path,
        )
        names = self._thread_names({r.thread_id for r in results if r.thread_id is not None})
        guild_id = self._guild_id()

        payload = [
            {
                "thread_id": r.thread_id,
                "session_id": r.session_id,
                "thread_name": names.get(r.thread_id) if r.thread_id is not None else None,
                "summary": r.summary,
                "working_dir": r.working_dir,
                "origin": r.origin,
                "last_used_at": r.last_used_at,
                "snippet": r.snippet,
                "source": r.source,
                "deep_link": (
                    f"https://discord.com/channels/{guild_id}/{r.thread_id}"
                    if guild_id is not None and r.thread_id is not None
                    else None
                ),
            }
            for r in results
        ]
        return web.json_response({"query": query, "results": payload})

    def _guild_id(self) -> int | None:
        """Best-effort guild ID for building thread deep-links.

        Tries the configured default channel's guild first, then any guild the
        bot is a member of.  Deep-links work for archived threads too, so this
        does not depend on the thread being in the channel cache.
        """
        if self.default_channel_id is not None:
            channel = self.bot.get_channel(self.default_channel_id)
            gid = getattr(getattr(channel, "guild", None), "id", None)
            if isinstance(gid, int):
                return gid
        for guild in getattr(self.bot, "guilds", None) or []:
            gid = getattr(guild, "id", None)
            if isinstance(gid, int):
                return gid
        return None

    def _thread_names(self, thread_ids: set[int]) -> dict[int, str]:
        """Resolve Discord thread titles from the bot's cache (no API calls)."""
        names: dict[int, str] = {}
        for thread_id in thread_ids:
            channel = self.bot.get_channel(thread_id)
            name = getattr(channel, "name", None)
            if isinstance(name, str):
                names[thread_id] = name
        return names

    async def get_thread_messages(self, request: web.Request) -> web.Response:
        """GET /api/threads/{thread_id}/messages — read another thread's conversation.

        The companion to ``/api/sessions``: once a session knows *that* another
        thread is working on the same thing, this is how it reads *what* that
        thread actually did.  Sessions have no Discord token of their own, so
        the bot performs the read and this endpoint stays localhost-only.

        Query params:
            limit: Number of most recent messages (default 30, max 100).

        Returns messages oldest-first, each truncated to keep responses small.
        """
        try:
            thread_id = int(request.match_info["thread_id"])
        except (ValueError, KeyError):
            return web.json_response({"error": "Invalid thread_id"}, status=400)

        try:
            raw_limit = request.rel_url.query.get("limit", str(_DEFAULT_THREAD_MESSAGE_LIMIT))
            limit = max(1, min(_MAX_THREAD_MESSAGE_LIMIT, int(raw_limit)))
        except ValueError:
            return web.json_response({"error": "limit must be an integer"}, status=400)

        channel = self.bot.get_channel(thread_id)
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(thread_id)
            except Exception as exc:
                return web.json_response({"error": str(exc)}, status=404)

        # Threads and text channels expose history(); categories and forums do
        # not. discord.py's union of channel types has no common protocol for
        # this, hence the runtime check plus an untyped handle.
        history = getattr(channel, "history", None)
        if history is None:
            return web.json_response(
                {"error": "Channel does not support message history"}, status=400
            )

        try:
            messages: list[Any] = [msg async for msg in history(limit=limit)]
        except Exception as exc:  # forbidden, deleted thread, transient API error
            return web.json_response({"error": str(exc)}, status=502)

        messages.reverse()  # Discord returns newest-first; read order is oldest-first
        return web.json_response(
            {
                "thread_id": thread_id,
                "thread_name": getattr(channel, "name", None),
                "messages": [_serialize_thread_message(m) for m in messages],
            }
        )

    async def spawn(self, request: web.Request) -> web.Response:
        """POST /api/spawn — create a new Discord thread and optionally start Claude Code.

        Unlike posting a message to the channel directly, this endpoint
        bypasses the ``on_message`` bot-author guard and works even when
        called from within another Claude Code session.

        Body (JSON):
            prompt: The instruction to send to Claude (required).
            channel_id: Parent channel ID (optional; defaults to the
                ``default_channel_id`` configured at startup).
            thread_name: Custom thread title (optional; defaults to the
                first 100 characters of *prompt*).
            auto_start: Whether to immediately start a Claude Code session
                (optional; defaults to ``true``).  When ``false``, only the
                thread and seed message are created — a Claude session will
                start when a user replies in the thread.
            user_id: Discord user to add to the new thread (optional), so the
                thread appears in their joined list instead of having to be
                found in the channel. Mirrors what ``/api/ingest`` does for the
                bot owner.

        Returns (201):
            ``{"status": "spawned", "thread_id": "...", "thread_name": "..."}``
        """
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        prompt = (data.get("prompt") or "").strip()
        if not prompt:
            return web.json_response({"error": "prompt is required"}, status=400)

        raw_channel_id = data.get("channel_id") or self.default_channel_id
        if not raw_channel_id:
            return web.json_response({"error": "No channel specified"}, status=400)

        # Resolve ClaudeChatCog lazily from the bot (zero-config; no constructor change).
        from ..cogs.claude_chat import ClaudeChatCog  # avoid circular import at module level

        cog: ClaudeChatCog | None = self.bot.cogs.get("ClaudeChatCog")  # type: ignore[assignment]
        if cog is None:
            return web.json_response(
                {"error": "ClaudeChatCog is not loaded"},
                status=503,
            )

        try:
            channel_id = int(raw_channel_id)
        except (TypeError, ValueError):
            return web.json_response({"error": "channel_id must be an integer"}, status=400)

        import discord as _discord

        raw = self.bot.get_channel(channel_id)
        if raw is None:
            try:
                raw = await self.bot.fetch_channel(channel_id)
            except Exception as exc:
                return web.json_response({"error": str(exc)}, status=500)

        if not isinstance(raw, _discord.TextChannel):
            return web.json_response(
                {"error": "Channel must be a text channel that supports threads"},
                status=400,
            )

        thread_name: str | None = data.get("thread_name") or None
        auto_start: bool = data.get("auto_start", True)

        # Validated here rather than swallowed downstream: a typo'd user_id is a
        # caller bug and should say so, while a Discord-side failure to add the
        # member is only a visibility miss and must never fail the spawn.
        raw_user_id = data.get("user_id")
        invite_user_id: int | None = None
        if raw_user_id is not None:
            try:
                invite_user_id = int(raw_user_id)
            except (TypeError, ValueError):
                return web.json_response({"error": "user_id must be an integer"}, status=400)
            if invite_user_id <= 0:
                return web.json_response({"error": "user_id must be positive"}, status=400)

        # Optional attachments to post into the new thread (e.g. files attached
        # to a Forgejo Issue forwarded by a watcher). Decoded here; posting is
        # handled inside spawn_session right after the seed prompt.
        decoded_attachments, att_err = _decode_spawn_attachments(data.get("attachments"))
        if att_err is not None:
            return att_err

        try:
            thread = await cog.spawn_session(
                raw,
                prompt,
                thread_name=thread_name,
                auto_start=auto_start,
                attachments=decoded_attachments or None,
                invite_user_id=invite_user_id,
            )
        except Exception as exc:
            logger.error("spawn_session failed: %s", exc, exc_info=True)
            return web.json_response({"error": str(exc)}, status=500)

        logger.info("Spawned new Claude session in thread %s (%s)", thread.id, thread.name)
        return web.json_response(
            {
                "status": "spawned",
                "thread_id": str(thread.id),
                "thread_name": thread.name,
            },
            status=201,
        )

    # ------------------------------------------------------------------
    # Authenticated external ingest endpoint (/api/ingest)
    # ------------------------------------------------------------------

    def _ingest_root(self) -> Path:
        """Directory under which ingested attachments are saved."""
        return Path(self.working_dir or os.getcwd()) / "ingest"

    def _contained_path(self, path: Path) -> Path | None:
        """Resolve ``path`` and return it only if it stays inside the ingest root.

        Filenames and zip member names are attacker-controlled. They are already
        reduced to a safe basename upstream, but that sanitiser lives far from
        the filesystem calls it protects, so this re-establishes containment
        *at* the sink: every path ccdb opens, stats or writes under the ingest
        tree passes through here first. Returns ``None`` for anything that
        resolves outside the root (or cannot be resolved at all), which callers
        treat as a refusal rather than a path to use.

        The check is written as ``os.path.realpath`` + a ``startswith`` prefix
        test rather than ``Path.resolve()`` + ``relative_to()``. The two are
        equivalent, but only the former is recognised as a path sanitiser by
        static analysis — with the pathlib spelling, CodeQL still reported every
        call below as a live path injection and the hardening was invisible to
        the very tool meant to check it. Comparing against ``root + os.sep``
        (not bare ``root``) is what stops a sibling directory whose name merely
        starts with the root's from passing.
        """
        try:
            root = os.path.realpath(str(self._ingest_root()))
            resolved = os.path.realpath(str(path))
        except OSError:
            return None
        if resolved != root and not resolved.startswith(root + os.sep):
            return None
        return Path(resolved)

    def _unique_path(self, path: Path) -> Path | None:
        """Return ``path``, or the first free ``name_2.ext``-style variant.

        Two attachments in one request routinely share a filename (Teams names
        every pasted screenshot ``image.png``). Writing both to the same path
        would leave one file where two were sent — a silent loss that looks
        identical to success in the saved count.

        Returns ``None`` when the requested path escapes the ingest root; the
        containment check happens before any filesystem access, and every
        candidate variant is re-checked because ``with_name`` takes a value
        derived from the same untrusted filename.

        The check is written out here rather than delegated to
        ``_contained_path``. The two are identical, but a guard that lives in a
        helper does not propagate across the call boundary for static analysis:
        with the delegated version CodeQL still reported both ``exists()`` calls
        below as live path injections. Keeping the ``realpath`` + prefix test in
        the same function as the filesystem call it protects makes the guarantee
        local — to a reader and to the analyser alike.
        """
        try:
            root = os.path.realpath(str(self._ingest_root()))
        except OSError:
            return None

        stem, suffix = path.stem, path.suffix
        candidates = [path] + [path.with_name(f"{stem}_{n}{suffix}") for n in range(2, 1001)]
        for candidate in candidates:
            try:
                resolved = os.path.realpath(str(candidate))
            except OSError:
                return None
            # root + os.sep, not bare root: "/…/ingest-evil" has "/…/ingest" as
            # a string prefix without being inside it.
            if resolved != root and not resolved.startswith(root + os.sep):
                return None
            if not os.path.exists(resolved):
                return Path(resolved)
        # 1000 files of the same name in one request is not a real export; refuse
        # it rather than inventing a random name nothing else can predict.
        return None

    def _save_ingest_attachments(
        self, attachments: list[dict], thread_id: str
    ) -> tuple[list[Path], web.Response | None]:
        """Decode base64 attachments to disk under a per-request directory.

        Returns ``(saved_paths, error_response)``. On any validation failure the
        second element is a ready-to-return :class:`web.Response` and the first
        is empty.
        """
        if len(attachments) > _MAX_INGEST_ATTACHMENTS:
            return [], web.json_response(
                {"error": f"Too many attachments (max {_MAX_INGEST_ATTACHMENTS})"},
                status=400,
            )

        dest_dir = self._ingest_root() / thread_id
        saved: list[Path] = []
        total = 0
        for i, att in enumerate(attachments):
            if not isinstance(att, dict):
                return [], web.json_response(
                    {"error": f"Attachment {i} must be an object"}, status=400
                )
            data_b64 = att.get("data")
            if not data_b64:
                return [], web.json_response(
                    {"error": f"Attachment {i} missing 'data'"}, status=400
                )
            try:
                blob = base64.b64decode(str(data_b64), validate=True)
            except (binascii.Error, ValueError):
                return [], web.json_response(
                    {"error": f"Attachment {i} has invalid base64 'data'"}, status=400
                )
            total += len(blob)
            if total > _MAX_INGEST_TOTAL_BYTES:
                return [], web.json_response(
                    {"error": "Attachments exceed total size limit"}, status=413
                )
            filename = _safe_attachment_name(att.get("filename"), i)
            dest_dir.mkdir(parents=True, exist_ok=True)
            path = self._unique_path(dest_dir / filename)
            if path is None:
                logger.warning("Refusing ingest attachment outside the ingest root: %s", i)
                return [], web.json_response(
                    {"error": f"Attachment {i} has an unusable filename"}, status=400
                )
            try:
                path.write_bytes(blob)
            except OSError as exc:
                logger.error("Failed to write ingest attachment: %s", exc)
                return [], web.json_response({"error": "Failed to persist attachment"}, status=500)
            saved.append(path)
            logger.info("Saved ingest attachment %s (%d bytes)", _sanitize_log(path), len(blob))
        return saved, None

    def _write_attachment_report(
        self, result: ingest_manifest.Reconciliation, request_id: str
    ) -> None:
        """Write the delivery ledger next to the files it describes.

        The prompt only carries the headline; this is the auditable record of
        what the client declared versus what landed, so a loss can be diagnosed
        after the fact instead of being reconstructed from logs. Best-effort:
        failing to write a report must never fail the ingest.
        """
        with contextlib.suppress(OSError):
            dest_dir = self._ingest_root() / request_id
            dest_dir.mkdir(parents=True, exist_ok=True)
            (dest_dir / "ATTACHMENTS-REPORT.md").write_text(
                ingest_manifest.render_report(result), encoding="utf-8"
            )

    def _expand_zip_bundles(self, saved_paths: list[Path]) -> list[Path]:
        """Expand any ``.zip`` among ``saved_paths`` into its member files.

        A single zip lets a client bundle a whole thread's attachments into one
        upload (sidestepping the per-request count cap) and keeps the prompt to
        paths only — the spawned session reads files selectively rather than
        receiving every byte inline.

        Each archive is extracted into its own directory next to the zip; the
        zip is removed and replaced in the returned list by its extracted files.
        Extraction is bounded (``_MAX_INGEST_UNZIP_*``) and refuses members that
        would escape the target directory (zip-slip). On any failure the zip is
        left untouched in the list so nothing is silently lost.

        The original is deleted **only** when extraction actually produced
        files. ``zipfile.is_zipfile()`` looks for the end-of-central-directory
        signature near the end of a file; it does not require the file to start
        like an archive. Any binary can end up containing a well-formed empty
        EOCD by chance — Windows event logs (.evtx), dumps and captures are
        exactly the large opaque blobs where that happens — and such a file was
        "expanded" into nothing and then unlinked, destroying the attachment
        while the count still read as a success. Replacing a file with nothing
        is never an improvement, so if there is nothing to replace it with, it
        stays.
        """
        result: list[Path] = []
        for path in saved_paths:
            if not zipfile.is_zipfile(path):
                result.append(path)
                continue
            extracted = self._safe_extract_zip(path)
            if not extracted:
                # Refused, failed, or an "archive" with no members at all —
                # almost certainly not a bundle. Keep the original.
                if extracted is not None:
                    logger.info(
                        "Ingest attachment %s looks like a zip but holds no files — "
                        "keeping it as-is",
                        _sanitize_log(path),
                    )
                result.append(path)
                continue
            result.extend(extracted)
            with contextlib.suppress(OSError):
                path.unlink()
        return result

    def _safe_extract_zip(self, zip_path: Path) -> list[Path] | None:
        """Extract ``zip_path`` into a sibling dir; return extracted files.

        Returns ``None`` (and writes nothing) when the archive is malformed or
        exceeds the size/member guards. Members that would escape the target
        directory are skipped individually.
        """
        dest_dir = zip_path.parent / f"{zip_path.stem}_files"
        dest_root = dest_dir.resolve()
        extracted: list[Path] = []
        try:
            with zipfile.ZipFile(zip_path) as zf:
                infos = [i for i in zf.infolist() if not i.is_dir()]
                if len(infos) > _MAX_INGEST_UNZIP_MEMBERS:
                    logger.warning(
                        "Ingest zip %s has too many members (%d) — left unextracted",
                        _sanitize_log(zip_path),
                        len(infos),
                    )
                    return None
                total = sum(i.file_size for i in infos)
                if total > _MAX_INGEST_UNZIP_TOTAL_BYTES:
                    logger.warning(
                        "Ingest zip %s uncompressed size %d exceeds cap — left unextracted",
                        _sanitize_log(zip_path),
                        total,
                    )
                    return None
                for info in infos:
                    member = info.filename
                    if member.startswith("__MACOSX/"):
                        continue
                    dest = (dest_dir / member).resolve()
                    if dest != dest_root and dest_root not in dest.parents:
                        logger.warning(
                            "Skipping zip member escaping target dir: %s",
                            _sanitize_log(member),
                        )
                        continue
                    # Two members can carry the same name (some writers allow
                    # duplicates); overwriting would drop one file while still
                    # reporting both as extracted. This also re-checks
                    # containment right before the write.
                    unique = self._unique_path(dest)
                    if unique is None:
                        logger.warning(
                            "Skipping zip member escaping the ingest root: %s",
                            _sanitize_log(member),
                        )
                        continue
                    unique.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info) as src, open(unique, "wb") as out:
                        out.write(src.read())
                    extracted.append(unique)
        except (zipfile.BadZipFile, OSError) as exc:
            logger.error("Failed to extract ingest zip %s: %s", _sanitize_log(zip_path), exc)
            return None
        return extracted

    def _build_ingest_prompt(
        self,
        *,
        content: str,
        saved_paths: list[Path],
        summary_key: str | None,
        stored_summary: str,
        result_id: str | None,
        reconciliation: ingest_manifest.Reconciliation | None = None,
    ) -> str:
        """Assemble the session prompt: caller content + attachments + summary.

        When ``summary_key`` is set this ingest is one turn of a long running
        thread, so the prompt (a) prepends the stored running summary as context
        and (b) — when a ``result_id`` exists to key it — asks the session to
        save an updated summary back to ccdb via the control-plane endpoint. The
        marker is NOT the session's concern: ccdb advances it from the ingest row
        when the summary is saved.
        """
        parts: list[str] = []

        # A missing attachment leads the prompt. Buried at the bottom it reads
        # as a footnote; at the top it is a precondition on the whole reply.
        if reconciliation is not None:
            warning = ingest_manifest.render_prompt_warning(reconciliation)
            if warning:
                parts.append(warning)

        if summary_key and stored_summary:
            parts.append(
                "このスレッドは継続的にやり取りされている長いスレッドです。\n"
                "以下は【これまでの要約】（過去の全履歴を圧縮したもの）です。"
                "今回の添付は前回要約以降の【新規メッセージ（差分）】です。\n\n"
                "===== これまでの要約 =====\n"
                f"{stored_summary}\n"
                "==========================\n"
            )
        elif summary_key:
            parts.append(
                "このスレッドは継続的にやり取りされる長いスレッドとして扱います"
                "（今回が最初の取り込みのため、まだ保存された要約はありません）。\n"
            )

        parts.append(content)

        if saved_paths:
            result = reconciliation or ingest_manifest.Reconciliation(verified=False)
            parts.append(ingest_manifest.render_attachment_section(result, saved_paths))

        if summary_key and result_id:
            parts.append(
                "【重要・次回のための要約保存】返信ドラフトを作成したあと、"
                "このスレッド全体（これまでの要約＋今回の差分）を反映した"
                "**最新の要約**を必ず保存してください。次回はこの要約＋新しい差分だけで"
                "文脈を完全に復元できるようにするのが目的です。"
                "Bash ツールで次を実行します（要約は JSON として正しくエスケープすること）:\n"
                f'  curl -sS -X POST "$CCDB_API_URL/api/ingest/summary" \\\n'
                f'    -H "Content-Type: application/json" \\\n'
                f"    --data-binary @/tmp/ccdb_summary_{result_id}.json\n"
                f"  # 事前に /tmp/ccdb_summary_{result_id}.json へ "
                f'{{"result_id":"{result_id}","summary":"..."}} を書き出しておくと安全です。\n'
                "要約には、決定事項・未解決の論点・相手の関心事・重要な固有名詞や専門用語・"
                "次のアクションを簡潔に含めてください（冗長にせず、文脈復元に必要な密度で）。"
            )

        return "\n\n".join(parts)

    async def ingest(self, request: web.Request) -> web.Response:
        """POST /api/ingest — authenticated spawn for untrusted external clients.

        Unlike ``/api/spawn`` (trusted, unauthenticated localhost callers), this
        endpoint is meant for clients such as a browser extension. It requires a
        dedicated bearer token (``ingest_token``) and can carry base64 file
        attachments that are written to ``{working_dir}/ingest/{thread_id}/`` so
        the spawned Claude session can read them.

        The endpoint is opt-in: when no ``ingest_token`` is configured it
        responds ``503`` so it can never run unauthenticated by accident. It is
        independent of the global ``api_secret`` middleware, so enabling it does
        not change the auth requirements of any other route.

        Body (JSON):
            content: The message body / instruction (required). ``prompt`` is
                accepted as an alias.
            thread_name: Custom thread title (optional).
            channel_id: Parent channel ID (optional; defaults to startup value).
            auto_start: Start a Claude session immediately (optional, default true).
            attachments: Optional list of ``{filename, mimetype?, data}`` where
                ``data`` is base64-encoded file bytes.

        Returns (201):
            ``{"status": "spawned", "thread_id": "...", "thread_name": "...",
               "attachments_saved": N}``
        """
        # --- Opt-in + auth (independent of the global api_secret middleware) ---
        if not self.ingest_token:
            return web.json_response({"error": "Ingest endpoint is disabled"}, status=503)

        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return web.json_response({"error": "Missing Authorization header"}, status=401)
        if not hmac.compare_digest(auth_header[7:], self.ingest_token):
            return web.json_response({"error": "Invalid token"}, status=401)

        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        content = (data.get("content") or data.get("prompt") or "").strip()
        if not content:
            return web.json_response({"error": "content is required"}, status=400)

        raw_channel_id = data.get("channel_id") or self.default_channel_id
        if not raw_channel_id:
            return web.json_response({"error": "No channel specified"}, status=400)

        attachments = data.get("attachments") or []
        if not isinstance(attachments, list):
            return web.json_response({"error": "attachments must be a list"}, status=400)

        # What the client says it found upstream, and how each one fared. Used
        # below to prove the bytes that arrived are the bytes that were meant to.
        manifest_entries, manifest_err = ingest_manifest.parse_manifest(
            data.get("attachments_manifest")
        )
        if manifest_err is not None:
            return web.json_response({"error": manifest_err}, status=400)

        # Optional running-summary linkage. When the client supplies a stable
        # summary_key, this ingest is one turn of a long upstream thread: ccdb
        # injects the stored summary as context and asks the session to save an
        # updated summary at the end. latest_marker is the newest upstream
        # message id in this (diff) export; it becomes the summary's marker once
        # the session saves a summary. Both are opaque strings.
        summary_key = self._valid_summary_key(data.get("summary_key"))
        raw_marker = data.get("latest_marker")
        latest_marker = str(raw_marker) if raw_marker not in (None, "") else None

        from ..cogs.claude_chat import ClaudeChatCog  # avoid circular import

        cog: ClaudeChatCog | None = self.bot.cogs.get("ClaudeChatCog")  # type: ignore[assignment]
        if cog is None:
            return web.json_response({"error": "ClaudeChatCog is not loaded"}, status=503)

        try:
            channel_id = int(raw_channel_id)
        except (TypeError, ValueError):
            return web.json_response({"error": "channel_id must be an integer"}, status=400)

        import discord as _discord

        raw = self.bot.get_channel(channel_id)
        if raw is None:
            try:
                raw = await self.bot.fetch_channel(channel_id)
            except Exception as exc:
                return web.json_response({"error": str(exc)}, status=500)

        if not isinstance(raw, _discord.TextChannel):
            return web.json_response(
                {"error": "Channel must be a text channel that supports threads"},
                status=400,
            )

        # Save attachments under a stable per-request id reused for the dir name.
        request_id = uuid.uuid4().hex
        saved_paths, err = self._save_ingest_attachments(attachments, request_id)
        if err is not None:
            return err
        # Expand any bundled .zip so the session reads individual files by path.
        saved_paths = self._expand_zip_bundles(saved_paths)

        # Verify the delivery against the manifest before anything is spawned:
        # a shortfall changes what the session is told, and (in strict mode)
        # whether a session is worth starting at all.
        # Only hash files proven to live under the ingest root — the names came
        # from the client, so containment is re-checked at the read as well.
        contained = [p for p in (self._contained_path(p) for p in saved_paths) if p is not None]
        reconciliation = ingest_manifest.reconcile(
            manifest_entries, ingest_manifest.hash_files(contained)
        )
        self._write_attachment_report(reconciliation, request_id)
        if not reconciliation.is_complete:
            logger.warning(
                "Ingest %s is missing %d attachment(s) the client declared: %s",
                request_id,
                len(reconciliation.lost),
                _sanitize_log(", ".join(e.name for e in reconciliation.lost)),
            )
            if self.ingest_require_complete:
                # Opt-in: refuse a lossy ingest outright so the client retries
                # with the full set instead of a session answering on partial
                # evidence. Off by default — a partial export is still useful,
                # as long as its gaps are stated (which they now are).
                return web.json_response(
                    {
                        "error": "Attachment delivery is incomplete",
                        "attachments": ingest_manifest.summarize_for_response(reconciliation),
                    },
                    status=409,
                )

        thread_name: str | None = data.get("thread_name") or None
        auto_start: bool = data.get("auto_start", True)

        # Generate the result id up front so it can be embedded in the prompt's
        # "save the updated summary via curl" instruction. Only produced when a
        # session actually starts and the ingest store exists.
        result_id: str | None = None
        if self.ingest_repo is not None and auto_start:
            result_id = uuid.uuid4().hex

        # Pull any stored running summary for this thread so the session has the
        # full historical context even though the attachment is just the diff.
        stored_summary = ""
        if summary_key and self.summary_repo is not None:
            existing = await self.summary_repo.get(summary_key)
            if existing is not None:
                stored_summary = existing["summary"] or ""

        prompt = self._build_ingest_prompt(
            content=content,
            saved_paths=saved_paths,
            summary_key=summary_key,
            stored_summary=stored_summary,
            result_id=result_id,
            reconciliation=reconciliation,
        )

        # When result retrieval is available, register a result row up front and
        # wire a sink that captures the session's final reply. Only meaningful
        # when the session actually starts (auto_start); otherwise no result is
        # ever produced. The row is created before spawn so the sink (which only
        # fires after the whole Claude run completes) can never race ahead of it.
        result_sink: Callable[[str | None, str | None], Awaitable[None]] | None = None
        # One-element holder for the spawned thread, so the completion sink (which
        # fires minutes later, after the session ends) can ping the owner in it.
        # Populated right after spawn_session returns — long before the sink runs.
        spawned_thread: list[discord.Thread] = []
        if result_id is not None and self.ingest_repo is not None:
            await self.ingest_repo.create(
                result_id=result_id,
                summary_key=summary_key,
                pending_marker=latest_marker,
            )
            ingest_repo = self.ingest_repo
            captured_id = result_id

            async def _capture_result(text: str | None, error: str | None) -> None:
                if error is not None:
                    await ingest_repo.set_error(captured_id, error)
                    body = "⚠️ セッションがエラーで終了しました。"
                else:
                    answer = text or ""
                    await ingest_repo.set_result(captured_id, answer)
                    if answer and spawned_thread:
                        await send_file_blobs(
                            spawned_thread[0],
                            [("ccdb-answer.md", answer.encode("utf-8"))],
                            content="-# 📎 Answer attached",
                        )
                    body = "✅ 回答ができました（このスレッドの最新メッセージ）。"
                # Ping the owner so a long-running result is delivered over
                # Discord without anyone watching a foreground poller.
                if spawned_thread:
                    await self._ingest_notify_owner(spawned_thread[0], body)

            result_sink = _capture_result

        try:
            thread = await cog.spawn_session(
                raw,
                prompt,
                thread_name=thread_name,
                auto_start=auto_start,
                result_sink=result_sink,
            )
        except Exception as exc:
            logger.error("ingest spawn_session failed: %s", exc, exc_info=True)
            if result_id is not None and self.ingest_repo is not None:
                await self.ingest_repo.set_error(result_id, str(exc))
            return web.json_response({"error": str(exc)}, status=500)

        if result_sink is not None:
            spawned_thread.append(thread)

        # Attach thread info for traceability. The completion sink already has
        # the thread reference above, before this await can yield to the session
        # task.
        if result_id is not None and self.ingest_repo is not None:
            await self.ingest_repo.set_thread(result_id, str(thread.id), thread.name)

        # Pull the owner into the thread and ping them on start, so the
        # unattended session is visible and they're notified again on completion
        # (via the result sink). Only when a session actually started.
        if result_sink is not None:
            await self._ingest_add_owner(thread)
            await self._ingest_notify_owner(
                thread,
                "🧵 Teams ingest セッションを開始しました。完了したらここで通知します。",
            )

        logger.info(
            "Ingested external session in thread %s (%s), %d attachment(s)",
            thread.id,
            _sanitize_log(thread.name),
            len(saved_paths),
        )
        response: dict[str, object] = {
            "status": "spawned",
            "thread_id": str(thread.id),
            "thread_name": thread.name,
            "attachments_saved": len(saved_paths),
            # The sender is the only party that can re-send a lost attachment,
            # so it gets the verdict rather than a bare count it cannot check.
            "attachments": ingest_manifest.summarize_for_response(reconciliation),
        }
        if result_id is not None:
            # Poll GET /api/ingest/{result_id} to retrieve the final reply.
            response["result_id"] = result_id
        return web.json_response(response, status=201)

    async def _ingest_add_owner(self, thread: discord.Thread) -> None:
        """Add the configured bot owner to an ingest thread (no-op if unset).

        An ingested session runs unattended and may take many minutes; adding
        the owner as a thread member makes the thread show up in their joined
        list instead of having to be searched for. Errors are suppressed — this
        is best-effort visibility, never a hard failure.
        """
        owner_id = getattr(self.bot, "owner_id", None)
        if not owner_id:
            return
        with contextlib.suppress(Exception):
            import discord as _discord

            await thread.add_user(_discord.Object(id=int(owner_id)))

    async def _ingest_notify_owner(self, thread: discord.Thread, body: str) -> None:
        """Post a message in *thread* that @mentions the bot owner (no-op if unset).

        Used to ping the owner when an ingest session starts and again when it
        finishes, so a long-running result is delivered asynchronously over
        Discord without anyone watching a foreground poller. Mentioning a user
        also adds them to the thread. Errors are suppressed.
        """
        owner_id = getattr(self.bot, "owner_id", None)
        if not owner_id:
            return
        with contextlib.suppress(Exception):
            from ..discord_ui.mentions import user_mention_kwargs

            kwargs = user_mention_kwargs(int(owner_id))
            kwargs["content"] = f"{kwargs.get('content', '')} {body}".strip()
            await thread.send(**kwargs)

    def _require_ingest_repo(self) -> web.Response | None:
        """Return a 503 response if ingest result retrieval is not configured."""
        if self.ingest_repo is None:
            return web.json_response(
                {"error": "Ingest result retrieval not configured (ingest_repo is None)"},
                status=503,
            )
        return None

    async def get_ingest_result(self, request: web.Request) -> web.Response:
        """GET /api/ingest/{result_id} — poll the final result of an ingest run.

        Shares the ingest authentication model (dedicated ``ingest_token``,
        independent of the global ``api_secret`` middleware) so the same
        external client that posted the work can retrieve its answer.
        """
        if not self.ingest_token:
            return web.json_response({"error": "Ingest endpoint is disabled"}, status=503)
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return web.json_response({"error": "Missing Authorization header"}, status=401)
        if not hmac.compare_digest(auth_header[7:], self.ingest_token):
            return web.json_response({"error": "Invalid token"}, status=401)

        if err := self._require_ingest_repo():
            return err

        result_id = request.match_info["result_id"]
        record = await self.ingest_repo.get(result_id)  # type: ignore[union-attr]
        if record is None:
            return web.json_response({"error": "ingest result not found"}, status=404)

        return web.json_response(
            {
                "result_id": record["result_id"],
                "status": record["status"],
                "result": record["result"],
                "error": record["error"],
                "thread_id": record["thread_id"],
                "thread_name": record["thread_name"],
            }
        )

    # ------------------------------------------------------------------
    # Running thread summaries (/api/ingest/summary)
    # ------------------------------------------------------------------

    #: Upper bound on a client-provided summary key; it is opaque otherwise.
    MAX_SUMMARY_KEY_LEN = 512

    def _require_summary_repo(self) -> web.Response | None:
        """Return a 503 response if the thread summary store is not configured."""
        if self.summary_repo is None:
            return web.json_response(
                {"error": "Thread summaries not configured (summary_repo is None)"},
                status=503,
            )
        return None

    def _check_ingest_token(self, request: web.Request) -> web.Response | None:
        """Enforce the dedicated ingest bearer token (shared with /api/ingest)."""
        if not self.ingest_token:
            return web.json_response({"error": "Ingest endpoint is disabled"}, status=503)
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return web.json_response({"error": "Missing Authorization header"}, status=401)
        if not hmac.compare_digest(auth_header[7:], self.ingest_token):
            return web.json_response({"error": "Invalid token"}, status=401)
        return None

    # ------------------------------------------------------------------
    # Teams thread sync (/api/teams/sync) — raw messages, one file each
    # ------------------------------------------------------------------

    def _teams_store(self) -> TeamsVaultStore:
        return TeamsVaultStore(self.teams_vault_root, working_dir=self.working_dir)

    async def _read_teams_request(
        self, request: web.Request, *, require_body: bool
    ) -> tuple[tuple[ThreadRef, list, dict], None] | tuple[None, web.Response]:
        """Auth + parse the shared body shape of both sync endpoints."""
        if err := self._check_ingest_token(request):
            return None, err
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return None, web.json_response({"error": "Invalid JSON"}, status=400)
        try:
            ref = teams_sync.parse_thread_ref(data.get("thread"))
            messages = teams_sync.parse_messages(data.get("messages"), require_body=require_body)
        except teams_sync.ValidationError as exc:
            return None, web.json_response({"error": str(exc)}, status=400)
        raw_coverage = data.get("coverage")
        coverage: dict = {}
        if isinstance(raw_coverage, dict):
            oldest = str(raw_coverage.get("oldest_seen_mid") or "").strip()
            if oldest.isdigit():
                coverage["oldest_seen_mid"] = oldest
            if raw_coverage.get("full_scan"):
                # A full scan means nothing above is unexamined any more.
                coverage["oldest_seen_mid"] = None
                coverage["full_scan"] = True
            raw_scan = raw_coverage.get("attachment_scan")
            if isinstance(raw_scan, dict):
                try:
                    detected = int(raw_scan.get("detected", 0))
                    ignored = int(raw_scan.get("ignored", 0))
                except (TypeError, ValueError):
                    detected = ignored = -1
                if 0 <= ignored <= detected <= 100_000:
                    coverage["attachment_scan"] = {
                        "detected": detected,
                        "ignored": ignored,
                    }
        return (ref, messages, coverage), None

    async def teams_sync_plan(self, request: web.Request) -> web.Response:
        """POST /api/teams/sync/plan — "what don't you have?".

        The client sends the mid + content hash of every message it can see (no
        bodies, so the request stays small) and gets back the subset the vault is
        missing or holds at a different hash. Nothing is written here.

        Answering from the files rather than from a stored marker is the point of
        the whole design: the client keeps no sync state, so there is no state to
        drift, and pressing the button twice is harmless.
        """
        parsed, err = await self._read_teams_request(request, require_body=False)
        if err is not None:
            return err
        ref, messages, _coverage = parsed  # type: ignore[misc]

        store = self._teams_store()
        thread_dir = store.find_thread_dir(ref)
        stored = store.load_stored(thread_dir) if thread_dir else {}
        meta = store.read_meta(thread_dir) if thread_dir else {}
        plan = teams_sync.build_plan(
            messages,
            stored,
            pending=meta.get("pending_attachments") or [],
            unavailable=meta.get("unavailable_attachments") or [],
        )
        return web.json_response(
            {
                "folder": str(thread_dir) if thread_dir else None,
                "exists": thread_dir is not None,
                "have": plan.have,
                "want_messages": plan.want_messages,
                "want_attachments": plan.want_attachments,
                # The client uses this exactly like the old summary marker, to
                # stop scrolling early. Its meaning is weaker on purpose: "this
                # is stored raw", not "this was folded into a summary". Getting
                # it wrong costs one extra sync, not a hole in the history.
                "newest_have_mid": plan.newest_have_mid,
                "pending": meta.get("pending_attachments") or [],
                "unavailable": meta.get("unavailable_attachments") or [],
                "attachment_summary": meta.get("attachment_summary")
                or {
                    "detected": 0,
                    "saved": 0,
                    "unavailable": 0,
                    "ignored": 0,
                    "pending": len(meta.get("pending_attachments") or []),
                },
                "message_count": int(meta.get("message_count") or len(stored)),
                # What this server supports, so a client can tell it apart from
                # an older one. `conversation_scope` is the permission a client
                # needs before it may upload a partially-scrolled chat: against
                # a server without it, each partial scan reports a different
                # root mid and lands in its own folder.
                "capabilities": {"conversation_scope": True},
            }
        )

    async def teams_sync_push(self, request: web.Request) -> web.Response:
        """POST /api/teams/sync/push — store the messages the plan asked for.

        Writes one Markdown file per message, the attachment bytes beside it, and
        one append-only line per message to ``chain.jsonl``. Re-sending a message
        that has not changed is a no-op beyond a refreshed timestamp, so a client
        that skips the plan step still converges — it just sends more.
        """
        parsed, err = await self._read_teams_request(request, require_body=True)
        if err is not None:
            return err
        ref, messages, coverage = parsed  # type: ignore[misc]
        if not messages:
            return web.json_response({"error": "messages is empty"}, status=400)

        store = self._teams_store()
        try:
            thread_dir = store.ensure_thread_dir(ref)
        except (OSError, ValueError) as exc:
            logger.error("Teams sync could not open a thread folder: %s", exc)
            return web.json_response({"error": "Could not open the thread folder"}, status=500)

        # `prev` links each message to the one before it in time. Known mids come
        # from the chain; the batch's own mids join them so a first sync links up
        # correctly too. mid is Unix-ms, so numeric order is chronological order.
        known = sorted(
            {int(m) for m in store.latest_chain(thread_dir)} | {int(m.mid) for m in messages}
        )
        ordered = sorted(messages, key=lambda m: int(m.mid))

        created = updated = attachments_saved = 0
        fresh_pending: list[dict] = []
        fresh_unavailable: list[dict] = []
        for msg in ordered:
            position = known.index(int(msg.mid))
            prev_mid = str(known[position - 1]) if position > 0 else None
            try:
                report = store.save_message(thread_dir, msg, ref, prev_mid=prev_mid)
            except (OSError, ValueError) as exc:
                logger.error("Teams sync failed to store a message: %s", exc)
                return web.json_response({"error": "Could not store a message"}, status=500)
            if report["action"] == "created":
                created += 1
            else:
                updated += 1
            attachments_saved += report["attachments_saved"]
            fresh_pending.extend(report["pending"])
            fresh_unavailable.extend(report["unavailable"])

        unavailable = store.merge_unavailable(thread_dir, fresh_unavailable, pushed=ordered)
        pending = store.merge_pending(
            thread_dir,
            fresh_pending,
            pushed=ordered,
            unavailable=unavailable,
        )
        meta = store.write_meta(
            thread_dir,
            ref,
            pending=pending,
            unavailable=unavailable,
            coverage=coverage,
        )
        logger.info(
            "Teams sync: %s (+%d new, %d updated, %d attachments, %d pending)",
            _sanitize_log(thread_dir.name),
            created,
            updated,
            attachments_saved,
            len(pending),
        )
        return web.json_response(
            {
                "folder": str(thread_dir),
                "created": created,
                "updated": updated,
                "attachments_saved": attachments_saved,
                # Never reported as an empty success: whatever did not arrive is
                # listed here, in thread.json and in the folder's README.
                "pending": pending,
                "unavailable": unavailable,
                "attachment_summary": meta["attachment_summary"],
                "message_count": meta["message_count"],
            }
        )

    @staticmethod
    def _valid_summary_key(value: object) -> str | None:
        """Return a trimmed key if it is a usable non-empty string, else None."""
        if not isinstance(value, str):
            return None
        key = value.strip()
        if not key or len(key) > ApiServer.MAX_SUMMARY_KEY_LEN:
            return None
        return key

    async def get_thread_summary(self, request: web.Request) -> web.Response:
        """GET /api/ingest/summary?key=... — read the running summary for a thread.

        Token-gated (same model as /api/ingest) so the external client that owns
        the thread can fetch the stored summary and ``marker`` before exporting,
        and send only the messages newer than ``marker`` (the diff).

        Returns 200 with ``exists=false`` and empty fields when the key is
        unknown — friendlier for a first-run client than a 404.
        """
        if err := self._check_ingest_token(request):
            return err
        if err := self._require_summary_repo():
            return err

        key = self._valid_summary_key(request.query.get("key"))
        if key is None:
            return web.json_response({"error": "key is required"}, status=400)

        record = await self.summary_repo.get(key)  # type: ignore[union-attr]
        if record is None:
            return web.json_response({"key": key, "exists": False, "summary": "", "marker": None})
        return web.json_response(
            {
                "key": key,
                "exists": True,
                "summary": record["summary"],
                "marker": record["marker"],
                "updated_at": record["updated_at"],
            }
        )

    async def save_thread_summary(self, request: web.Request) -> web.Response:
        """POST /api/ingest/summary — save an updated running summary.

        Internal control-plane endpoint (localhost, no token — same trust model
        as /api/tasks): the Claude session spawned by an ingest run calls this
        with its own ``result_id`` after drafting a reply. ccdb resolves the
        ``summary_key`` and the pending marker from the ingest row, so the marker
        only advances when a summary is actually saved (a failed session leaves
        the marker untouched and the same diff is re-exported next time).

        Body (JSON), either:
            {"result_id": "...", "summary": "..."}  (primary: marker from the row)
            {"key": "...", "summary": "...", "marker": "..."}  (direct, for tests)
        """
        if err := self._require_summary_repo():
            return err
        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        summary = data.get("summary")
        if not isinstance(summary, str):
            return web.json_response({"error": "summary is required"}, status=400)

        key: str | None = None
        marker: str | None = None
        result_id = data.get("result_id")
        if result_id:
            if self.ingest_repo is None:
                return web.json_response(
                    {"error": "result_id given but ingest_repo is not configured"}, status=503
                )
            row = await self.ingest_repo.get(str(result_id))
            if row is None:
                return web.json_response({"error": "unknown result_id"}, status=404)
            key = self._valid_summary_key(row.get("summary_key"))
            if key is None:
                return web.json_response(
                    {"error": "this ingest run has no summary_key to update"}, status=400
                )
            marker = row.get("pending_marker")
        else:
            key = self._valid_summary_key(data.get("key"))
            if key is None:
                return web.json_response({"error": "key or result_id is required"}, status=400)
            raw_marker = data.get("marker")
            marker = str(raw_marker) if raw_marker is not None else None

        record = await self.summary_repo.upsert(  # type: ignore[union-attr]
            key, summary=summary, marker=marker
        )
        return web.json_response({"status": "saved", "key": key, "marker": record["marker"]})

    async def delete_thread_summary(self, request: web.Request) -> web.Response:
        """DELETE /api/ingest/summary?key=... — drop a stored summary (reset).

        Localhost admin/reset path (registered only on the internal app). Forces
        the next export to be treated as a first run. Token-gated when an ingest
        token is configured, for parity with the other ingest routes.
        """
        if err := self._check_ingest_token(request):
            return err
        if err := self._require_summary_repo():
            return err
        key = self._valid_summary_key(request.query.get("key"))
        if key is None:
            return web.json_response({"error": "key is required"}, status=400)
        removed = await self.summary_repo.delete(key)  # type: ignore[union-attr]
        return web.json_response({"status": "deleted" if removed else "not_found", "key": key})

    # ------------------------------------------------------------------
    # Startup resume endpoint (/api/mark-resume)
    # ------------------------------------------------------------------

    def _require_resume_repo(self) -> web.Response | None:
        """Return a 503 response if resume_repo is not configured."""
        if self.resume_repo is None:
            return web.json_response(
                {"error": "PendingResumeRepository not configured (resume_repo is None)"},
                status=503,
            )
        return None

    async def mark_resume(self, request: web.Request) -> web.Response:
        """POST /api/mark-resume — mark a thread for resumption after bot restart.

        Call this **before** running ``systemctl restart discord-bot`` (or any
        equivalent restart command) from within a Claude Code session.  On the
        next bot startup the ``on_ready`` handler will detect the marker,
        re-spawn Claude in this thread, and then delete the marker.

        Body (JSON):
            thread_id: Discord thread ID (required).
            session_id: Claude session ID for ``--resume`` continuity (optional).
            reason: Human-readable reason string (optional, default ``self_restart``).
            resume_prompt: The message to post + send to Claude on resume
                           (optional; a sensible default is used if omitted).

        Returns (201):
            ``{"status": "marked", "id": <row_id>}``
        """
        if err := self._require_resume_repo():
            return err

        try:
            data = await request.json()
        except json.JSONDecodeError:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        raw_thread_id = data.get("thread_id")
        if not raw_thread_id:
            return web.json_response({"error": "thread_id is required"}, status=400)

        try:
            thread_id = int(raw_thread_id)
        except (TypeError, ValueError):
            return web.json_response({"error": "thread_id must be an integer"}, status=400)

        session_id: str | None = data.get("session_id") or None
        reason: str = str(data.get("reason") or "self_restart")
        resume_prompt: str | None = data.get("resume_prompt") or None

        # Auto-resolve session_id from the sessions table if not provided.
        if session_id is None and self.session_repo is not None:
            try:
                record = await self.session_repo.get(thread_id)
                if record is not None:
                    session_id = record.session_id
                    logger.debug(
                        "mark-resume: auto-resolved session_id=%s for thread %d",
                        session_id,
                        thread_id,
                    )
            except Exception:
                logger.warning(
                    "mark-resume: failed to auto-resolve session_id for thread %d",
                    thread_id,
                    exc_info=True,
                )

        row_id = await self.resume_repo.mark(  # type: ignore[union-attr]
            thread_id,
            session_id=session_id,
            reason=reason,
            resume_prompt=resume_prompt,
        )
        logger.info(
            "Thread %d marked for resume (reason=%s, session_id=%s)",
            thread_id,
            _sanitize_log(reason),
            _sanitize_log(session_id),
        )
        return web.json_response({"status": "marked", "id": row_id}, status=201)

    async def _send_lounge_to_discord(self, label: str, message: str, posted_at: str) -> None:
        """Mirror a lounge message to the configured Discord lounge channel.

        Best-effort and self-healing: if the channel has been deleted or the bot
        can no longer see it, the mirror disables itself for the rest of the
        process so a removed channel doesn't warn on every post. The lounge DB
        record is already saved by the caller, so disabling the mirror never
        loses a message — it only stops the human-facing echo.
        """
        import discord

        if self._lounge_mirror_disabled or not self.lounge_channel_id:
            return
        try:
            channel = self.bot.get_channel(self.lounge_channel_id)
            if channel is None:
                channel = await self.bot.fetch_channel(self.lounge_channel_id)
            if hasattr(channel, "send"):
                timestamp = posted_at[11:16] if len(posted_at) >= 16 else posted_at
                await channel.send(f"**[{label}]** {message} *({timestamp})*")  # type: ignore[union-attr]
        except (discord.NotFound, discord.Forbidden):
            self._lounge_mirror_disabled = True
            logger.warning(
                "Lounge mirror channel %s is gone or inaccessible; disabling the "
                "Discord mirror for this process (the DB lounge is unaffected).",
                self.lounge_channel_id,
            )
        except Exception:
            logger.warning("Failed to forward lounge message to Discord", exc_info=True)

    @staticmethod
    def _build_poll(poll_data: dict) -> discord.Poll | web.Response:
        """Build a discord.Poll from API request data.

        Returns a Poll on success, or a web.Response (400) on validation error.
        """
        import discord

        question = poll_data.get("question")
        if not question:
            return web.json_response({"error": "poll.question is required"}, status=400)

        answers = poll_data.get("answers")
        if not answers or len(answers) < 2:
            return web.json_response(
                {"error": "poll.answers must have at least 2 items"}, status=400
            )

        duration_hours = poll_data.get("duration_hours", 24)
        allow_multiselect = poll_data.get("allow_multiselect", False)

        poll = discord.Poll(
            question=question,
            duration=timedelta(hours=duration_hours),
            multiple=allow_multiselect,
        )

        for answer in answers:
            if isinstance(answer, str):
                poll.add_answer(text=answer)
            elif isinstance(answer, dict):
                kwargs: dict = {"text": answer["text"]}
                if "emoji" in answer:
                    kwargs["emoji"] = answer["emoji"]
                poll.add_answer(**kwargs)

        return poll

    @staticmethod
    def _build_embed(
        message: str,
        title: str | None = None,
        color: int | None = None,
    ) -> discord.Embed:
        """Build a Discord embed for notification display."""
        import discord

        return discord.Embed(
            title=title or "Notification",
            description=message,
            color=color or 0x00BFFF,
            timestamp=datetime.now(),
        )
