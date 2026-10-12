"""The console listener: a narrow, authenticated API plus the web client.

This is deliberately *not* the control plane. ``ApiServer`` on
``127.0.0.1:API_PORT`` can schedule tasks, spawn arbitrary prompts with
attachments and post as the bot, and it trusts anything that reaches it. The
console exposes a handful of operations a human triaging work needs —
read the board, read a thread, change an item, reply, mark done, start a
captured item — and authenticates every request (see ``auth.py``).

The JSON API under ``/console/api`` is the contract. The bundled web client is
one consumer of it; a terminal or desktop client can be another.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from aiohttp import web

from ..message_actions import message_actions
from ..thread_marker import OUTCOME_DONE, thread_outcome
from ..thread_status import request_thread_outcome, schedule_thread_outcome
from .auth import ConsoleAuthConfig, ConsoleAuthenticator, ConsoleAuthError
from .auth_routes import AUTH_PREFIX, AuthRoutes
from .board import SessionInfo, ThreadSnapshot, build_board
from .codex_compact import CompactError, compact_codex_session
from .commands import (
    CONTROL_COMMANDS,
    HANDLED_BUILTINS,
    CommandError,
    SkillLoader,
    catalog,
    default_skill_loader,
    run_command,
    skill_prompt,
)
from .conversations import ConversationRepository
from .files import resolve_file
from .logsafe import q as _q
from .messages import serialize_message, with_kind
from .oidc import OidcClient, OidcConfig, provider_name
from .passkey_store import PasskeyStore
from .passkeys import PasskeyService
from .places import PlaceError, PlaceRepository
from .places import clean_name as clean_place
from .ratelimit import RateLimiter
from .surface import apply_outcome
from .triage import INTERVAL_SECONDS as TRIAGE_INTERVAL_SECONDS
from .triage import (
    Triager,
    TriageRepository,
    apply_triage,
)
from .triage_models import CLAUDE_TRIAGE_MODEL, BackendAsk, installed
from .uploads import (
    MAX_ATTACHMENTS_PER_REPLY,
    UploadError,
    resolve_upload,
    save_upload,
    size_limit,
    with_attachments,
)
from .usage import UsageReader
from .work_repo import (
    STATE_DONE,
    STATE_OPEN,
    WorkItemError,
    WorkItemRepository,
    thread_item_id,
)

if TYPE_CHECKING:
    from ..ext.api_server import ApiServer
    from .host import ConsoleSessionHost

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
API_PREFIX = "/console/api"
#: Mutations must carry this header. A cross-site form or fetch cannot set a
#: custom header without a CORS preflight, which this listener never answers.
CSRF_HEADER = "X-Console-Request"

MAX_REPLY_CHARS = 4000
MAX_HISTORY = 100
DEFAULT_HISTORY = 50
MAX_TRIAGE_MESSAGES = 20
#: How long the archived-thread listing is reused. Listing archives is a REST
#: call per channel; the board is polled every few seconds.
ARCHIVE_CACHE_SECONDS = 120.0
ARCHIVE_LIMIT_PER_CHANNEL = 50
#: Mutations per identity per minute.
MUTATION_RATE_PER_MINUTE = 60
#: Setup-code checks per minute, across every caller (see ``auth_routes``).
#: High enough that flooding it is a flood, low enough that guessing a ~50-bit
#: code within its 15 minutes is hopeless.
AUTH_RATE_PER_MINUTE = 120
MAX_BODY_BYTES = 64 * 1024
IDENTITY = web.RequestKey("console_identity", str)

_STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".svg": "image/svg+xml",
    ".webmanifest": "application/manifest+json",
}
_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        # Discord's CDN is allowed for images only: attachments in chat threads preview inline.
        "default-src 'self'; img-src 'self' data: https://cdn.discordapp.com "
        "https://media.discordapp.net; style-src 'self'; script-src 'self'; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Cache-Control": "no-store",
}


def _listed(value: object) -> list[object]:
    """A JSON list field as a list; anything else that is set becomes one bad entry."""
    if value is None:
        return []
    return list(value) if isinstance(value, list) else [value]


def _error(message: str, status: int) -> web.Response:
    return web.json_response({"error": message}, status=status)


def _iso(value: Any) -> str | None:
    if value is None or not hasattr(value, "astimezone"):
        return None
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class ConsoleServer:
    """Owns the console listener. Reads ccdb state through ``api_server``."""

    def __init__(
        self,
        api_server: ApiServer,
        work_repo: WorkItemRepository,
        authenticator: ConsoleAuthenticator,
        *,
        host: str = "127.0.0.1",
        port: int,
        usage: UsageReader | None = None,
        conversations: ConversationRepository | None = None,
        host_session: ConsoleSessionHost | None = None,
        passkeys: PasskeyService | None = None,
        oidc: OidcClient | None = None,
        store: PasskeyStore | None = None,
        skills: SkillLoader | None = None,
        codex_compactor: Callable[..., Awaitable[None]] | None = None,
        triage: TriageRepository | None = None,
        places: PlaceRepository | None = None,
    ) -> None:
        self.api = api_server
        #: Lists skills per backend for slash-command autocomplete; None reads the disk.
        self._skills = skills
        #: Runs ``/compact`` on a Codex session; injected so tests spawn nothing.
        self._codex_compact = codex_compactor or compact_codex_session
        #: Threads being compacted; a reply waits instead of racing the rewrite.
        self._compacting: set[int] = set()
        self.work_repo = work_repo
        #: The model's priority and deadline guesses; None turns automatic triage off.
        self.triage = triage
        #: The managed list of places work can be bound to.
        self.places = places
        self._triager: Triager | None = None
        #: The console's own conversations. Work started here lives in them,
        #: not in a chat thread; chat threads are still shown alongside.
        self.conversations = conversations
        self.sessions = host_session
        self.usage = usage
        self.auth = authenticator
        self.host = host
        self.port = port
        self._limiter = RateLimiter(MUTATION_RATE_PER_MINUTE)
        auth_limiter = RateLimiter(AUTH_RATE_PER_MINUTE)
        store = store or (passkeys.store if passkeys is not None else None)
        self.auth_routes = (
            AuthRoutes(authenticator, store, passkeys, auth_limiter.allow, oidc=oidc)
            if store is not None and (passkeys is not None or oidc is not None)
            else None
        )
        self._archive_cache: tuple[float, list[Any]] | None = None
        self._archive_lock = asyncio.Lock()
        self._runner: web.AppRunner | None = None
        self._start_locks: dict[str, asyncio.Lock] = {}
        # Strong references: the loop keeps only weak ones to running tasks.
        self._tasks: set[asyncio.Task[Any]] = set()
        self.app = self._build_app()

    def _background(self, coro: Any) -> None:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._task_finished)

    def _task_finished(self, task: asyncio.Task[Any]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("console: background delivery failed", exc_info=task.exception())

    # -- app ---------------------------------------------------------------
    def _build_app(self) -> web.Application:
        app = web.Application(client_max_size=MAX_BODY_BYTES, middlewares=[self._middleware])
        r = app.router
        r.add_get("/", self.index)
        r.add_get("/static/{name}", self.static)
        r.add_get(f"{API_PREFIX}/me", self.me)
        r.add_get(f"{API_PREFIX}/board", self.board)
        r.add_get(f"{API_PREFIX}/usage", self.usage_read)
        r.add_get(f"{API_PREFIX}/places", self.places_read)
        r.add_post(f"{API_PREFIX}/places", self.place_add)
        r.add_patch(f"{API_PREFIX}/places", self.place_rename)
        r.add_delete(f"{API_PREFIX}/places", self.place_delete)
        r.add_post(f"{API_PREFIX}/items", self.create_item)
        r.add_patch(f"{API_PREFIX}/items/{{item_id}}", self.patch_item)
        r.add_get(f"{API_PREFIX}/items/{{item_id}}/messages", self.messages)
        r.add_post(f"{API_PREFIX}/items/{{item_id}}/reply", self.reply)
        r.add_post(
            f"{API_PREFIX}/items/{{item_id}}/messages/{{message_id}}/actions/{{action_id}}",
            self.press_action,
        )
        r.add_post(f"{API_PREFIX}/uploads", self.upload)
        r.add_get(f"{API_PREFIX}/items/{{item_id}}/commands", self.commands)
        r.add_post(f"{API_PREFIX}/items/{{item_id}}/command", self.run_command)
        r.add_post(f"{API_PREFIX}/items/{{item_id}}/done", self.done)
        r.add_post(f"{API_PREFIX}/items/{{item_id}}/reopen", self.reopen)
        r.add_post(f"{API_PREFIX}/items/{{item_id}}/start", self.start_item)
        r.add_get(f"{API_PREFIX}/items/{{item_id}}/live", self.live)
        r.add_post(f"{API_PREFIX}/items/{{item_id}}/stop", self.stop_item)
        r.add_get(f"{API_PREFIX}/files/{{key}}/{{token}}/{{name}}", self.file)
        if self.auth_routes is not None:
            self.auth_routes.register(r, API_PREFIX)
        return app

    @web.middleware
    async def _middleware(
        self,
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        response = await self._guarded(request, handler)
        for name, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    async def _guarded(
        self,
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        # The shell (HTML/JS/CSS) carries no data; every byte of data is
        # behind the API, which authenticates. Behind Access the shell is
        # protected anyway.
        if not request.path.startswith(API_PREFIX):
            return await handler(request)
        if self.auth_routes is not None and request.path.startswith(AUTH_PREFIX):
            return await self.auth_routes.guard(request, handler)
        try:
            who = await self.auth.identify(request.headers, request.cookies)
        except ConsoleAuthError as exc:
            return _error(str(exc), 401)
        if request.method not in ("GET", "HEAD"):
            if request.headers.get(CSRF_HEADER) != "1":
                return _error(f"{CSRF_HEADER}: 1 is required", 403)
            if not self._limiter.allow(who):
                return _error("too many changes, slow down", 429)
        request[IDENTITY] = who
        return await handler(request)

    async def start(self) -> None:
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.port, reuse_address=True)
        await site.start()
        logger.info("Relay Console started: http://%s:%d", self.host, self.port)

    def start_triage(self, ask: Callable[[str], Awaitable[str | None]], *, model: str) -> None:
        """Triage open items in the background with *ask* (one model call per item)."""
        if self.triage is None or self._triager is not None:
            return
        places = self.places
        self._triager = Triager(
            self.triage,
            ask=ask,
            read_messages=self.triage_messages,
            model=model,
            registered_places=places.names if places is not None else None,
        )
        self._triager.start(self._board_items, TRIAGE_INTERVAL_SECONDS)
        logger.info("Relay Console: automatic triage on (%s)", model)

    async def stop(self) -> None:
        if self._triager is not None:
            await self._triager.stop()
        if self._runner:
            await self._runner.cleanup()

    # -- shell -------------------------------------------------------------
    async def index(self, request: web.Request) -> web.StreamResponse:
        return self._file("index.html")

    async def static(self, request: web.Request) -> web.StreamResponse:
        return self._file(request.match_info["name"])

    def _file(self, name: str) -> web.StreamResponse:
        # A fixed directory listing, not a path join: no traversal to reason about.
        allowed = {p.name: p for p in STATIC_DIR.iterdir() if p.is_file()}
        path = allowed.get(name)
        if path is None or path.suffix not in _STATIC_TYPES:
            raise web.HTTPNotFound()
        return web.Response(
            body=path.read_bytes(),
            content_type=None,
            headers={
                "Content-Type": _STATIC_TYPES[path.suffix],
            },
        )

    # -- reads -------------------------------------------------------------
    async def me(self, request: web.Request) -> web.Response:
        return web.json_response({"identity": request[IDENTITY]})

    async def _lineage(self) -> dict[int, int]:
        if self.api.lineage_repo is None:
            return {}
        try:
            return {
                row.thread_id: row.parent_thread_id
                for row in await self.api.lineage_repo.list_all(limit=2000)
            }
        except Exception:
            # Who spawned whom is garnish; the board still answers without it.
            logger.exception("console: could not read lineage")
            return {}

    async def board(self, request: web.Request) -> web.Response:
        items = await self._board_items()
        if self.triage is not None:
            try:
                items = apply_triage(items, await self.triage.all())
            except Exception:
                # Guesses are garnish; the board still answers without them.
                logger.exception("console: could not read triage")
        return web.json_response(
            {
                "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "items": items,
                "slots": self._slots(),
                "places": await self._place_list(items),
            }
        )

    # -- places ------------------------------------------------------------
    async def _place_list(self, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Every place: the managed list plus labels in use, with how many open items carry each."""
        registered = await self.places.names() if self.places is not None else []
        counts: dict[str, int] = {}
        for item in items:
            if item.get("place") and item.get("bucket") != "done":
                counts[item["place"]] = counts.get(item["place"], 0) + 1
        names = sorted(set(registered) | set(counts))
        return [
            {"name": n, "count": counts.get(n, 0), "registered": n in registered} for n in names
        ]

    async def places_read(self, request: web.Request) -> web.Response:
        return web.json_response({"places": await self._place_list(await self._items())})

    async def _items(self) -> list[dict[str, Any]]:
        items = await self._board_items()
        if self.triage is not None:
            items = apply_triage(items, await self.triage.all())
        return items

    async def _place_change(
        self, request: web.Request, change: Callable[[dict[str, Any]], Awaitable[Any]]
    ) -> web.Response:
        if self.places is None:
            return _error("places are not available", 404)
        body, err = await self._json(request)
        if err:
            return err
        try:
            await change(body)
        except PlaceError as exc:
            return _error(str(exc), 400)
        return await self.places_read(request)

    async def place_add(self, request: web.Request) -> web.Response:
        async def add(body: dict[str, Any]) -> None:
            assert self.places is not None
            await self.places.add(body.get("name"))

        return await self._place_change(request, add)

    async def place_rename(self, request: web.Request) -> web.Response:
        async def rename(body: dict[str, Any]) -> None:
            assert self.places is not None
            await self.places.rename(clean_place(body.get("from")), body.get("name"))

        return await self._place_change(request, rename)

    async def place_delete(self, request: web.Request) -> web.Response:
        async def delete(body: dict[str, Any]) -> None:
            assert self.places is not None
            await self.places.delete(clean_place(body.get("name")))

        return await self._place_change(request, delete)

    async def _board_items(self) -> list[dict[str, Any]]:
        threads = await self._thread_snapshots() + await self._conversation_snapshots()
        overlays = await self.work_repo.list_all()
        lineage = await self._lineage()
        sessions = await self._sessions()
        running = self.api._running_thread_ids()
        items = build_board(
            threads=threads,
            overlays=overlays,
            lineage=lineage,
            running=running,
            sessions=sessions,
        )
        queued = self._queued_thread_ids()
        for item in items:
            item["queued"] = item["thread_id"] in queued
        return items

    async def triage_messages(self, item: dict[str, Any]) -> list[tuple[str, str]]:
        """What the triage model reads for *item*: its human and agent messages."""
        if not item.get("thread_id"):
            return [("note", item.get("note") or "")]
        limit = MAX_TRIAGE_MESSAGES
        key = await self._conversation_key(item["id"])
        if key is not None and self.conversations is not None:
            raw = [with_kind(m.as_dict()) for m in await self.conversations.history(key, limit)]
        else:
            thread, err = await self._thread_for(item["id"])
            if err:
                return []
            history = [m async for m in thread.history(limit=limit)]
            raw = [serialize_message(m) for m in reversed(history)]
        return [
            ("person" if m["kind"] == "human" else "agent", str(m.get("content") or ""))
            for m in raw
            if m["kind"] != "activity"
        ]

    async def usage_read(self, request: web.Request) -> web.Response:
        if self.usage is None:
            return web.json_response({"now": int(time.time()), "backends": []})
        return web.json_response(await self.usage.read())

    async def messages(self, request: web.Request) -> web.Response:
        try:
            limit = max(
                1, min(MAX_HISTORY, int(request.rel_url.query.get("limit", DEFAULT_HISTORY)))
            )
        except ValueError:
            return _error("limit must be an integer", 400)
        key = await self._conversation_key(request.match_info["item_id"])
        if key is not None and self.conversations is not None:
            history = await self.conversations.history(key, limit)
            return web.json_response({"messages": [with_kind(m.as_dict()) for m in history]})
        thread, err = await self._thread_for(request.match_info["item_id"])
        if err:
            return err
        try:
            history = [m async for m in thread.history(limit=limit)]
        except Exception as exc:
            logger.warning("console: history of %s failed: %s", thread.id, exc)
            return _error("could not read the thread", 502)
        history.reverse()
        return web.json_response(
            {
                "messages": [
                    serialize_message(m, actions=message_actions.for_message(thread.id, m.id))
                    for m in history
                ]
            }
        )

    async def live(self, request: web.Request) -> web.Response:
        """What a console conversation's running turn is doing right now."""
        key = await self._conversation_key(request.match_info["item_id"])
        if key is None or self.sessions is None:
            return _error("this item has no console conversation", 404)
        state = self.sessions.frontend.live.get(key)
        running = key in self.api._running_thread_ids()
        body = state.as_dict() if state is not None else None
        return web.json_response({"running": running, "live": body})

    async def file(self, request: web.Request) -> web.StreamResponse:
        """A file an agent delivered in a console conversation."""
        files_dir = self.sessions.frontend.files_dir if self.sessions is not None else None
        path = resolve_file(
            files_dir,
            request.match_info["key"],
            request.match_info["token"],
            request.match_info["name"],
        )
        if path is None:
            return _error("unknown file", 404)
        return web.FileResponse(
            path,
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(path.name)}",
                "Content-Type": "application/octet-stream",
            },
        )

    # -- writes ------------------------------------------------------------
    async def stop_item(self, request: web.Request) -> web.Response:
        """Stop the running turn of a console conversation, like the chat Stop button."""
        key = await self._conversation_key(request.match_info["item_id"])
        if key is None or self.sessions is None:
            return _error("this item has no console conversation", 404)
        if not await self.sessions.stop(key, request[IDENTITY]):
            return _error("nothing is running", 409)
        logger.info("console: %s stopped conversation %s", _q(request[IDENTITY]), key)
        return web.json_response({"status": "stopping"}, status=202)

    async def press_action(self, request: web.Request) -> web.Response:
        """Press a button on a thread message, as clicking it in Discord would."""
        thread, err = await self._thread_for(request.match_info["item_id"])
        if err:
            return err
        message_id = request.match_info["message_id"]
        if not message_id.isdigit():
            return _error("message_id must be a number", 400)
        who = request[IDENTITY]
        try:
            pressed = await message_actions.invoke(
                thread.id, int(message_id), request.match_info["action_id"], who
            )
        except Exception:
            logger.exception("console: action on message %s failed", message_id)
            return _error("the action failed", 500)
        if not pressed:
            return _error("this button is no longer waiting for an answer", 404)
        return web.json_response({"status": "pressed"})

    async def create_item(self, request: web.Request) -> web.Response:
        body, err = await self._json(request)
        if err:
            return err
        start = body.pop("start", False)
        if not isinstance(start, bool):
            return _error("start must be a boolean", 400)
        try:
            item = await self.work_repo.create_capture(body)
        except WorkItemError as exc:
            return _error(str(exc), 400)
        if not start:
            return web.json_response({"item": item.as_dict()}, status=201)
        # Hand it to an agent in the same request. A failed start keeps the
        # capture: the words are never lost, and the "Hand to AI" button can
        # retry once the cause is fixed.
        started = await self._start_guarded(item.id, {}, request[IDENTITY])
        if started.status == 201:
            return started
        reason = json.loads(started.text or "{}").get("error", "could not start the conversation")
        return web.json_response({"item": item.as_dict(), "start_error": reason}, status=201)

    async def patch_item(self, request: web.Request) -> web.Response:
        body, err = await self._json(request)
        if err:
            return err
        try:
            implicit = {
                thread_item_id(child): thread_item_id(parent)
                for child, parent in (await self._lineage()).items()
            }
            item = await self.work_repo.update(
                request.match_info["item_id"], body, implicit_parents=implicit
            )
        except WorkItemError as exc:
            return _error(str(exc), 400)
        if self.places is not None and item.place:
            # A place typed on an item joins the managed list.
            await self.places.add(item.place)
        if self.triage is not None and {"priority", "due_at", "place"} & set(body):
            # What a human chose is no longer the model's to guess.
            await self.triage.pin(
                item.id, priority="priority" in body, due="due_at" in body, place="place" in body
            )
        return web.json_response({"item": item.as_dict()})

    async def done(self, request: web.Request) -> web.Response:
        item_id = request.match_info["item_id"]
        try:
            item = await self.work_repo.update(
                item_id, {"state": STATE_DONE, "snoozed_until": None}
            )
        except WorkItemError as exc:
            return _error(str(exc), 400)
        key = await self._conversation_key(item_id)
        if key is not None and self.conversations is not None:
            await apply_outcome(self.conversations, key, OUTCOME_DONE)
        elif item.thread_id is not None:
            thread, _ = await self._thread_for(item_id)
            if thread is not None:
                # Same marker the agent would set, so the chat sidebar agrees.
                request_thread_outcome(thread, OUTCOME_DONE)
        return web.json_response({"item": item.as_dict()})

    async def reopen(self, request: web.Request) -> web.Response:
        item_id = request.match_info["item_id"]
        try:
            item = await self.work_repo.update(item_id, {"state": STATE_OPEN})
        except WorkItemError as exc:
            return _error(str(exc), 400)
        key = await self._conversation_key(item_id)
        if key is not None and self.conversations is not None:
            conversation = await self.conversations.get(key)
            if conversation is not None and thread_outcome(conversation.name) == OUTCOME_DONE:
                await apply_outcome(self.conversations, key, None)
        elif item.thread_id is not None:
            thread, _ = await self._thread_for(item_id)
            # The ✅ that done() put on the title would keep the item in Done.
            if thread is not None and thread_outcome(thread.name or "") == OUTCOME_DONE:
                schedule_thread_outcome(thread, None)
        return web.json_response({"item": item.as_dict()})

    async def reply(self, request: web.Request) -> web.Response:
        """Hand the human's answer to the thread's session, as if typed there.

        The text is posted into the thread first (the conversation stays
        whole for anyone reading it in chat), the outcome marker is cleared
        the way a human reply clears it, and the turn queues behind any turn
        already running — a reply never interrupts work in flight.
        """
        body, err = await self._json(request)
        if err:
            return err
        text = str(body.get("text") or "").strip()
        # "images" is the field older clients send; "files" carries any attachment.
        files, err = self._reply_files([*_listed(body.get("images")), *_listed(body.get("files"))])
        if err:
            return err
        if not text and not files:
            return _error("text is required", 400)
        if len(text) > MAX_REPLY_CHARS:
            return _error(f"text must be at most {MAX_REPLY_CHARS} characters", 400)
        text = with_attachments(text, files)
        return await self._deliver(request.match_info["item_id"], text, request[IDENTITY])

    async def _deliver(self, item_id: str, text: str, who: str) -> web.Response:
        """Queue *text* as the human's next message in the item's conversation."""
        busy = _error("the conversation is being compacted; send again in a moment", 409)
        key = await self._conversation_key(item_id)
        if key is not None:
            return (
                busy
                if key in self._compacting
                else await self._reply_in_console(item_id, key, text, who)
            )
        thread, err = await self._thread_for(item_id)
        if err:
            return err
        if int(thread.id) in self._compacting:
            return busy
        cog = self._chat_cog()
        if cog is None:
            return _error("the chat cog is not loaded", 503)
        try:
            await self.work_repo.update(item_id, {"state": STATE_OPEN, "snoozed_until": None})
        except WorkItemError as exc:
            return _error(str(exc), 400)
        schedule_thread_outcome(thread, None)
        prompt = f"{text}\n\n-# 🖥️ via Relay Console ({who})"
        self._background(cog.deliver_relayed_message(thread, prompt, interrupt=False))
        logger.info("console: %s replied in thread %s", _q(who), thread.id)
        return web.json_response({"status": "delivered"}, status=202)

    # -- slash commands ----------------------------------------------------
    def _backend_settings(self) -> Any:
        return getattr(getattr(self.api, "components", None), "backend_settings", None)

    async def _command_key(self, item_id: str) -> tuple[int | None, web.Response | None]:
        """The thread key commands apply to: a console conversation or a watched chat thread."""
        key = await self._conversation_key(item_id)
        if key is not None:
            return key, None
        thread, err = await self._thread_for(item_id)
        if err:
            return None, err
        return int(thread.id), None

    def _skill_loader(self) -> SkillLoader:
        cogs = getattr(self.api.bot, "cogs", None)
        skill_cog = cogs.get("SkillCommandCog") if isinstance(cogs, dict) else None
        return self._skills or default_skill_loader(skill_cog)

    async def commands(self, request: web.Request) -> web.Response:
        """What the reply box autocompletes for this item: commands, models, efforts, skills."""
        settings = self._backend_settings()
        if settings is None:
            return _error("this deployment has no backend settings", 503)
        key, err = await self._command_key(request.match_info["item_id"])
        if err:
            return err
        assert key is not None
        return web.json_response(await catalog(settings, key, self._skill_loader()))

    async def run_command(self, request: web.Request) -> web.Response:
        """Run a slash command typed into the reply box.

        ``backend``/``model``/``effort`` change this conversation's settings and
        answer at once; any other name is a skill of the current backend and is
        sent to the agent as the next message.
        """
        body, err = await self._json(request)
        if err:
            return err
        settings = self._backend_settings()
        if settings is None:
            return _error("this deployment has no backend settings", 503)
        item_id = request.match_info["item_id"]
        key, err = await self._command_key(item_id)
        if err:
            return err
        assert key is not None
        name = str(body.get("name") or "").strip().lstrip("/")
        arg = str(body.get("arg") or "")
        if len(arg) > MAX_REPLY_CHARS:
            return _error(f"arguments must be at most {MAX_REPLY_CHARS} characters", 400)
        who = request[IDENTITY]
        if name in {n for n, _ in CONTROL_COMMANDS}:
            try:
                result = await run_command(settings, key, name, arg)
            except CommandError as exc:
                return _error(str(exc), 400)
            logger.info(
                "console: %s ran /%s %s on %s", _q(who), _q(name), _q(arg.strip()[:60]), _q(item_id)
            )
            return web.json_response(
                {
                    "message": result.message,
                    "catalog": await catalog(settings, key, self._skill_loader()),
                }
            )
        backend = await settings.current_backend(key)
        if name in HANDLED_BUILTINS.get(backend, frozenset()):
            return await self._compact_codex(key, item_id, who)
        if name not in {s["name"] for s in self._skill_loader()(backend)}:
            return _error(f"Unknown command `/{name}` for `{backend}`.", 400)
        logger.info("console: %s ran skill /%s on %s", _q(who), _q(name), _q(item_id))
        return await self._deliver(item_id, skill_prompt(backend, name, arg), who)

    async def _compact_codex(self, key: int, item_id: str, who: str) -> web.Response:
        """``/compact`` on a Codex conversation, through ``codex app-server``."""
        if key in self.api._running_thread_ids() or key in self._compacting:
            return _error("wait until the current turn finishes, then compact", 409)
        repo = self.api.session_repo
        record = await repo.get(key) if repo is not None else None
        if record is None or record.backend != "codex":
            return _error("this conversation has no Codex session to compact yet", 409)
        factory = vars(getattr(self.api, "components", None) or object()).get("backend_factory")
        command = getattr(factory, "codex_command", None) or "codex"
        self._compacting.add(key)
        try:
            await self._codex_compact(record.session_id, codex_command=command)
        except CompactError as exc:
            logger.warning("console: /compact failed on %s: %s", _q(item_id), _q(exc))
            return _error(str(exc), 502)
        finally:
            self._compacting.discard(key)
        logger.info("console: %s compacted Codex session of %s", _q(who), _q(item_id))
        return web.json_response(
            {"message": "Conversation compacted. The next reply continues from the summary."}
        )

    def _files_dir(self) -> Path | None:
        return self.sessions.frontend.files_dir if self.sessions is not None else None

    def _reply_files(self, raw: list[object]) -> tuple[list[tuple[str, Path]], web.Response | None]:
        """Attachment URLs from a reply body, resolved to stored files."""
        if not raw:
            return [], None
        if len(raw) > MAX_ATTACHMENTS_PER_REPLY:
            return [], _error(f"at most {MAX_ATTACHMENTS_PER_REPLY} attachments per reply", 400)
        out: list[tuple[str, Path]] = []
        for url in raw:
            if not isinstance(url, str):
                return [], _error("unknown attachment", 400)
            path = resolve_upload(self._files_dir(), url)
            if path is None:
                return [], _error("unknown attachment", 400)
            out.append((url, path))
        return out, None

    async def upload(self, request: web.Request) -> web.Response:
        """Store a file attached to a reply; answer with the URL the reply names it by.

        The body is the raw file and ``X-Filename`` its percent-encoded name. It is
        read here with its own limit because the listener's JSON limit is far
        smaller than a screenshot. Images are capped lower than other files.
        """
        files_dir = self._files_dir()
        if files_dir is None:
            return _error("this deployment cannot keep files", 503)
        content_type = request.headers.get("Content-Type", "")
        limit = size_limit(content_type)
        too_big = UploadError(f"the file is larger than {limit // (1024 * 1024)} MB", 413)
        try:
            if (request.content_length or 0) > limit:
                raise too_big
            data = bytearray()
            async for chunk in request.content.iter_chunked(64 * 1024):
                data.extend(chunk)
                if len(data) > limit:
                    raise too_big
            url = await save_upload(
                files_dir, bytes(data), content_type, request.headers.get("X-Filename")
            )
        except UploadError as exc:
            return _error(str(exc), exc.status)
        logger.info("console: %s uploaded %s", _q(request[IDENTITY]), _q(url))
        return web.json_response({"url": url}, status=201)

    async def start_item(self, request: web.Request) -> web.Response:
        """Hand a captured item to an agent: open a console conversation and start a turn.

        No chat thread is created. The conversation lives in the console and
        runs through the same session runner the chat frontends use.
        """
        body, err = await self._json(request)
        if err:
            return err
        return await self._start_guarded(request.match_info["item_id"], body, request[IDENTITY])

    async def _start_guarded(self, item_id: str, body: dict[str, Any], who: str) -> web.Response:
        # A double click must not open two conversations for one item.
        lock = self._start_locks.setdefault(item_id, asyncio.Lock())
        if lock.locked():
            return _error("this item is already being started", 409)
        async with lock:
            try:
                return await self._start_locked(item_id, body, who)
            finally:
                self._start_locks.pop(item_id, None)

    async def _start_locked(self, item_id: str, body: dict[str, Any], who: str) -> web.Response:
        item = await self.work_repo.get(item_id)
        if item is None or item.thread_id is not None:
            return _error("only a captured item without a thread can be started", 400)
        extra = str(body.get("prompt") or "").strip()
        prompt = "\n\n".join(p for p in (item.title, item.note, extra) if p)
        if len(prompt) > MAX_REPLY_CHARS:
            return _error(f"the prompt must be at most {MAX_REPLY_CHARS} characters", 400)
        if self.sessions is None:
            return _error("the console cannot run agents in this deployment", 503)
        parent_thread_id = None
        if item.parent_id and item.parent_id.startswith("t") and item.parent_id[1:].isdigit():
            parent_thread_id = int(item.parent_id[1:])
        try:
            thread_key = await self.sessions.start(
                external_id=item_id,
                title=item.title or prompt[:100],
                prompt=prompt,
                author=who,
            )
        except Exception:
            logger.exception("console: start of %s failed", _q(item_id))
            return _error("could not start the conversation", 502)
        if parent_thread_id is not None and self.api.lineage_repo is not None:
            from ..thread_marker import family_code

            try:
                await self.api.lineage_repo.record(
                    thread_key, parent_thread_id, family_code(parent_thread_id)
                )
            except Exception:
                logger.exception("console: lineage for %s not recorded", thread_key)
        try:
            overlay = await self.work_repo.attach_thread(item_id, thread_key)
        except WorkItemError:
            logger.exception(
                "console: conversation %s started but %s could not follow", thread_key, _q(item_id)
            )
            return _error("the conversation started, but the item could not be linked to it", 500)
        return web.json_response({"item": overlay.as_dict()}, status=201)

    async def _reply_in_console(
        self, item_id: str, thread_key: int, text: str, who: str
    ) -> web.Response:
        if self.sessions is None:
            return _error("the console cannot run agents in this deployment", 503)
        try:
            await self.work_repo.update(item_id, {"state": STATE_OPEN, "snoozed_until": None})
        except WorkItemError as exc:
            return _error(str(exc), 400)
        try:
            await self.sessions.reply(thread_key, text, who)
        except LookupError:
            return _error("unknown conversation", 404)
        logger.info("console: %s replied in conversation %s", _q(who), thread_key)
        return web.json_response({"status": "delivered"}, status=202)

    # -- helpers -----------------------------------------------------------
    async def _json(self, request: web.Request) -> tuple[dict[str, Any], web.Response | None]:
        try:
            body = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}, _error("invalid JSON", 400)
        if not isinstance(body, dict):
            return {}, _error("the body must be a JSON object", 400)
        return body, None

    def _chat_cog(self) -> Any:
        cogs = getattr(self.api.bot, "cogs", None)
        return cogs.get("ClaudeChatCog") if cogs else None

    async def _thread_for(self, item_id: str) -> tuple[Any, web.Response | None]:
        import discord

        if not item_id.startswith("t") or not item_id[1:].isdigit():
            return None, _error("this item has no thread", 400)
        thread_id = int(item_id[1:])
        thread = self.api.bot.get_channel(thread_id)
        if thread is None:
            try:
                thread = await self.api.bot.fetch_channel(thread_id)
            except Exception:
                return None, _error("unknown thread", 404)
        # Only threads under a watched channel: a console user must not be able
        # to aim the bot at an arbitrary channel id.
        if not isinstance(thread, discord.Thread) or thread.parent_id not in (
            self._watched_channel_ids()
        ):
            return None, _error("unknown thread", 404)
        return thread, None

    async def _conversation_key(self, item_id: str) -> int | None:
        """The key of the console conversation behind *item_id*, if it is one."""
        if self.conversations is None or not item_id.startswith("t") or not item_id[1:].isdigit():
            return None
        key = int(item_id[1:])
        return key if await self.conversations.get(key) is not None else None

    async def _conversation_snapshots(self) -> list[ThreadSnapshot]:
        if self.conversations is None:
            return []
        try:
            conversations = await self.conversations.list_all()
        except Exception:
            logger.exception("console: could not read conversations")
            return []
        return [
            ThreadSnapshot(
                thread_id=c.thread_key,
                name=c.name,
                created_at=c.created_at,
                last_activity_at=c.last_activity_at,
            )
            for c in conversations
        ]

    def _watched_channel_ids(self) -> set[int]:
        ids: set[int] = set()
        if self.api.default_channel_id:
            ids.add(int(self.api.default_channel_id))
        cog = self._chat_cog()
        extra = getattr(cog, "_channel_ids", None)
        if isinstance(extra, set):
            ids.update(int(i) for i in extra)
        return ids

    async def _thread_snapshots(self) -> list[ThreadSnapshot]:
        import discord

        threads: dict[int, Any] = {}
        channels = []
        for channel_id in self._watched_channel_ids():
            channel = self.api.bot.get_channel(channel_id)
            if isinstance(channel, discord.TextChannel):
                channels.append(channel)
                for thread in channel.threads:
                    threads[thread.id] = thread
        for thread in await self._archived_threads(channels):
            threads.setdefault(thread.id, thread)
        return [self._snapshot(t) for t in threads.values()]

    async def _archived_threads(self, channels: list[Any]) -> list[Any]:
        async with self._archive_lock:
            now = time.monotonic()
            if self._archive_cache and now - self._archive_cache[0] < ARCHIVE_CACHE_SECONDS:
                return self._archive_cache[1]
            found: list[Any] = []
            for channel in channels:
                try:
                    found.extend(
                        [t async for t in channel.archived_threads(limit=ARCHIVE_LIMIT_PER_CHANNEL)]
                    )
                except Exception as exc:
                    logger.warning("console: archived threads of %s: %s", channel.id, exc)
            self._archive_cache = (now, found)
            return found

    @staticmethod
    def _snapshot(thread: Any) -> ThreadSnapshot:
        import discord

        last_id = getattr(thread, "last_message_id", None)
        last = discord.utils.snowflake_time(last_id) if last_id else None
        meta = getattr(thread, "archive_timestamp", None)
        return ThreadSnapshot(
            thread_id=thread.id,
            name=thread.name or "",
            archived=bool(getattr(thread, "archived", False)),
            created_at=_iso(getattr(thread, "created_at", None)),
            last_activity_at=_iso(last) or _iso(meta),
            url=getattr(thread, "jump_url", None),
        )

    async def _sessions(self) -> dict[int, SessionInfo]:
        repo = self.api.session_repo
        if repo is None:
            return {}
        try:
            records = await repo.list_all(limit=500)
        except Exception:
            logger.exception("console: could not read sessions")
            return {}
        return {
            r.thread_id: SessionInfo(
                working_dir=r.working_dir,
                backend=r.backend,
                model=r.model,
                last_used_at=r.last_used_at,
                context_window=r.context_window,
                context_used=r.context_used,
            )
            for r in records
        }

    @staticmethod
    def _slots() -> dict[str, Any]:
        from ..session_slots import get_session_slots

        slots = get_session_slots()
        if slots is None:
            return {"max": None, "running": 0, "waiting": 0}
        snapshot = slots.snapshot()
        return {
            "max": slots.max_slots,
            "running": sum(1 for s in snapshot if s.running),
            "waiting": sum(1 for s in snapshot if not s.running),
        }

    @staticmethod
    def _queued_thread_ids() -> set[str]:
        from ..session_slots import get_session_slots

        slots = get_session_slots()
        if slots is None:
            return set()
        return {str(s.thread_key) for s in slots.snapshot() if not s.running}


async def maybe_start_console(api_server: ApiServer) -> ConsoleServer | None:
    """Start the console when ``CCDB_CONSOLE_PORT`` is set. Zero-config otherwise.

    A configuration that would serve the console unauthenticated is refused
    loudly, never started in a weaker mode.
    """
    raw_port = (os.getenv("CCDB_CONSOLE_PORT") or "").strip()
    if not raw_port:
        return None
    # The console is optional; whatever goes wrong with it must not take the
    # control plane and the bot down with it.
    try:
        return await _start_console(api_server, raw_port)
    except Exception:
        logger.exception("Relay Console NOT started")
        return None


async def _start_console(api_server: ApiServer, raw_port: str) -> ConsoleServer | None:
    config = ConsoleAuthConfig.from_env().effective()
    problems = config.problems()
    if problems:
        logger.error("Relay Console NOT started: %s", "; ".join(problems))
        return None
    if api_server.session_repo is None:
        logger.error("Relay Console NOT started: the session repository is not wired")
        return None
    host = (os.getenv("CCDB_CONSOLE_HOST") or "127.0.0.1").strip()
    work_repo = WorkItemRepository(api_server.session_repo.db_path)
    await work_repo.init_db()
    bot = api_server.bot
    usage = UsageReader(
        usage_repo=getattr(bot, "usage_repo", None),
        account_router=getattr(bot, "account_router", None),
        codex_command=(os.getenv("CCDB_CODEX_COMMAND") or "").strip() or None,
    )
    conversations = ConversationRepository(api_server.session_repo.db_path)
    await conversations.init_db()
    store = PasskeyStore(api_server.session_repo.db_path)
    await store.init_db()
    passkeys = PasskeyService(store) if config.passkeys else None
    oidc = _oidc_client(config)
    triage_ask = _triage_ask(api_server)
    triage = TriageRepository(api_server.session_repo.db_path) if triage_ask else None
    places = PlaceRepository(api_server.session_repo.db_path)
    await places.init_db()
    if triage is not None:
        await triage.init_db()
    console = ConsoleServer(
        api_server,
        work_repo,
        ConsoleAuthenticator(config, sessions=store),
        host=host,
        port=int(raw_port),
        usage=usage,
        conversations=conversations,
        host_session=await _build_session_host(api_server, conversations),
        passkeys=passkeys,
        oidc=oidc,
        store=store,
        triage=triage,
        places=places,
    )
    await console.start()
    if triage_ask is not None:
        console.start_triage(triage_ask, model=triage_ask.model)
    if passkeys is not None:
        await passkeys.open_enrollment(force=os.getenv("CCDB_CONSOLE_ENROLL") == "1")
    return console


async def _build_session_host(
    api_server: ApiServer, conversations: ConversationRepository
) -> ConsoleSessionHost | None:
    """Wire the console to the session runner, if this deployment can run agents.

    The console then owns the conversations it starts: no chat thread is
    created. Without the session wiring (an embedded setup with no backend
    factory) the console still triages and replies to chat threads, but
    refuses to start new work.
    """
    from .host import ConsoleSessionHost
    from .surface import ConsoleFrontend

    components = api_server.components
    factory = getattr(components, "backend_factory", None)
    settings = getattr(components, "backend_settings", None)
    ledger = getattr(components, "frontend_threads", None)
    if factory is None or settings is None or ledger is None:
        logger.warning("Relay Console: no session wiring; starting work from the console is off")
        return None
    bot = api_server.bot
    frontend = ConsoleFrontend(
        conversations,
        ledger,
        working_dir=getattr(factory, "working_dir", None),
        files_dir=Path(conversations.db_path).parent / "console_files",
    )
    router = getattr(components, "frontend", None)
    if getattr(bot, "headless", False) is True and hasattr(router, "replace_primary"):
        # No Discord login: the console is where new conversations open,
        # scheduled tasks included.
        router.replace_primary(frontend)  # type: ignore[union-attr]
    elif router is not None and hasattr(router, "add"):
        # Scheduled tasks, waits and the REST API resolve a console
        # conversation the same way they resolve a Discord or Teams one.
        router.add(frontend)
    api_server.console_conversations = conversations
    host = ConsoleSessionHost(
        frontend=frontend,
        session_repo=api_server.session_repo,
        backend_factory=factory,
        backend_settings=settings,
        lounge_repo=getattr(components, "lounge_repo", None),
        ask_repo=getattr(components, "ask_repo", None),
        usage_repo=getattr(components, "usage_repo", None),
        registry=getattr(bot, "session_registry", None),
        worktree_manager=getattr(bot, "worktree_manager", None),
    )
    # The wait watcher resumes a conversation after CI; it finds this one here.
    bot.console_sessions = host  # type: ignore[attr-defined]
    return host


def _triage_ask(api_server: ApiServer) -> BackendAsk | None:
    """The model call for automatic triage, or None when it is off or no CLI is installed.

    On by default (``CCDB_CONSOLE_TRIAGE=0`` turns it off). It follows the
    backend chosen with ``/backend``: Haiku on Claude, Codex's cheapest model
    on Codex, falling back to whichever CLI is installed.
    """
    if (os.getenv("CCDB_CONSOLE_TRIAGE") or "1").strip().lower() in ("0", "false", "off", "no"):
        return None
    claude = installed((os.getenv("CCDB_CONSOLE_TRIAGE_COMMAND") or "claude").strip())
    codex = installed((os.getenv("CCDB_CODEX_COMMAND") or "codex").strip())
    if claude is None and codex is None:
        return None
    settings = getattr(api_server.components, "backend_settings", None)

    async def current_backend() -> str:
        return str(await settings.current_backend()) if settings is not None else "claude"

    return BackendAsk(
        current_backend,
        claude_command=claude,
        codex_command=codex,
        claude_model=(os.getenv("CCDB_CONSOLE_TRIAGE_MODEL") or "").strip() or CLAUDE_TRIAGE_MODEL,
        codex_model=(os.getenv("CCDB_CONSOLE_TRIAGE_CODEX_MODEL") or "").strip() or None,
    )


def _oidc_client(config: ConsoleAuthConfig) -> OidcClient | None:
    if not config.oidc_enabled:
        return None
    issuer = str(config.oidc_issuer)
    return OidcClient(
        OidcConfig(
            issuer=issuer,
            client_id=str(config.oidc_client_id),
            client_secret=config.oidc_client_secret,
            redirect_uri=config.oidc_redirect_uri,
            allowed_emails=config.allowed_emails,
            name=config.oidc_name or provider_name(issuer),
            trust_unverified_email=config.oidc_trust_unverified_email,
        )
    )


__all__ = ["ConsoleServer", "maybe_start_console", "thread_item_id"]
