"""HTTP endpoints for signing in to the console with a passkey.

The ``/console/api/auth/*`` routes are the only API routes reachable before
sign-in. They still need the CSRF header on every POST. The routes that check a
setup code share one global rate limit — behind a tunnel every caller arrives
from the same loopback address, so a per-address limit would limit nothing.
Passkey sign-in itself is not rate limited: a signature cannot be guessed, and
a shared limit there would let anyone lock the owner out.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import web

from .auth import SESSION_COOKIE, ConsoleAuthConfig, ConsoleAuthenticator, ConsoleAuthError
from .passkey_store import PasskeyStore
from .passkeys import PasskeyError, PasskeyService

logger = logging.getLogger(__name__)

AUTH_PREFIX = "/console/api/auth/"
#: Routes that check a guessable secret (a setup code) or write to the log.
_LIMITED = (f"{AUTH_PREFIX}setup-code", f"{AUTH_PREFIX}passkey/register/")
_LOOPBACK_HOSTS = ("localhost", "127.0.0.1", "[::1]")


def _error(message: str, status: int) -> web.Response:
    return web.json_response({"error": message}, status=status)


def request_origin(request: web.Request, config: ConsoleAuthConfig) -> str:
    """The origin the browser is on, as WebAuthn will see it.

    With ``CCDB_CONSOLE_ORIGIN`` set, only those origins are accepted. Without
    it the origin is read from the request: a proxy in front (tailscale serve,
    cloudflared, nginx) terminates TLS, so the scheme is https unless the
    host is loopback. Trusting ``Host`` here is safe because it only chooses
    which origin to *expect*: the browser signs the origin it really is on,
    and a passkey is bound to its own site, so a forged header gets nothing a
    real browser would sign.
    """
    host = request.host
    if config.origins:
        for origin in config.origins:
            if origin.split("://", 1)[-1] == host:
                return origin
        raise PasskeyError(f"{host} is not listed in CCDB_CONSOLE_ORIGIN")
    forwarded = request.headers.get("X-Forwarded-Proto", "").split(",")[0].strip().lower()
    if forwarded in ("http", "https"):
        scheme = forwarded
    elif host.rsplit(":", 1)[0] in _LOOPBACK_HOSTS:
        scheme = request.scheme
    else:
        scheme = "https"
    return f"{scheme}://{host}"


class AuthRoutes:
    """Passkey sign-in, sign-out and passkey management."""

    def __init__(
        self,
        authenticator: ConsoleAuthenticator,
        store: PasskeyStore,
        service: PasskeyService,
        allow: Callable[[str], bool],
    ) -> None:
        self.auth = authenticator
        self.store = store
        self.passkeys = service
        self._allow = allow

    @property
    def _config(self) -> ConsoleAuthConfig:
        return self.auth.config

    def register(self, router: web.UrlDispatcher, api_prefix: str) -> None:
        a = AUTH_PREFIX
        router.add_get(f"{a}status", self.status)
        router.add_post(f"{a}setup-code", self.setup_code)
        router.add_post(f"{a}passkey/register/options", self.register_options)
        router.add_post(f"{a}passkey/register/verify", self.register_verify)
        router.add_post(f"{a}passkey/login/options", self.login_options)
        router.add_post(f"{a}passkey/login/verify", self.login_verify)
        router.add_post(f"{a}logout", self.logout)
        router.add_get(f"{api_prefix}/passkeys", self.list_passkeys)
        router.add_post(f"{api_prefix}/passkeys/invite", self.invite)
        router.add_delete(f"{api_prefix}/passkeys/{{passkey_id}}", self.delete_passkey)

    async def guard(
        self,
        request: web.Request,
        handler: Callable[[web.Request], Awaitable[web.StreamResponse]],
    ) -> web.StreamResponse:
        """The pre-sign-in checks: CSRF header and the shared rate limit."""
        if request.method not in ("GET", "HEAD"):
            if request.headers.get("X-Console-Request") != "1":
                return _error("X-Console-Request: 1 is required", 403)
            if request.path.startswith(_LIMITED) and not self._allow("auth"):
                return _error("too many sign-in attempts, slow down", 429)
        try:
            return await handler(request)
        except PasskeyError as exc:
            return _error(str(exc), 400)

    async def _manager(self, request: web.Request) -> str | None:
        """Who may add or remove passkeys: a passkey session, or the token.

        Being let in is not enough. An Access or OIDC identity is only as
        durable as the allowlist that admits it; if it could register a
        passkey, it would keep a way in after its email was taken off the
        list.
        """
        who = await self._signed_in(request)
        return who if who is not None and _manages_passkeys(who) else None

    async def _signed_in(self, request: web.Request) -> str | None:
        try:
            return await self.auth.identify(request.headers, request.cookies)
        except ConsoleAuthError:
            return None

    def _secure(self, request: web.Request) -> bool:
        return request_origin(request, self._config).startswith("https://")

    def _with_session(self, payload: Any, token: str, *, secure: bool) -> web.Response:
        response = web.json_response(payload)
        response.set_cookie(
            SESSION_COOKIE,
            token,
            max_age=int(self._config.session_lifetime.total_seconds()),
            path="/",
            httponly=True,
            samesite="Strict",
            secure=secure,
        )
        return response

    # -- public ------------------------------------------------------------
    async def status(self, request: web.Request) -> web.Response:
        config = self._config
        passkeys = await self.store.count() if config.passkeys else 0
        return web.json_response(
            {
                "signed_in": await self._signed_in(request) is not None,
                "methods": {
                    "passkey": config.passkeys,
                    "access": config.access_enabled,
                    "token": bool(config.token),
                },
                "needs_setup": config.passkeys and passkeys == 0,
            }
        )

    async def setup_code(self, request: web.Request) -> web.Response:
        if not self._config.passkeys or await self.store.count() > 0:
            return _error("a passkey is already registered; sign in and add devices there", 409)
        await self.passkeys.open_enrollment()
        return web.json_response({"logged": True})

    async def register_options(self, request: web.Request) -> web.Response:
        if not self._config.passkeys:
            return _error("passkeys are disabled", 404)
        body = await _json(request)
        result = await self.passkeys.registration_options(
            request_origin(request, self._config),
            signed_in=await self._manager(request) is not None,
            code=str(body.get("code") or ""),
        )
        return web.json_response(result)

    async def register_verify(self, request: web.Request) -> web.Response:
        body = await _json(request)
        secure = self._secure(request)
        passkey = await self.passkeys.finish_registration(
            str(body.get("ticket") or ""), _credential(body), str(body.get("name") or "")
        )
        if await self._manager(request) is not None:
            return web.json_response({"passkey": passkey.to_public()})
        token = await self.store.create_session(
            f"passkey:{passkey.name}", self._config.session_lifetime, passkey.id
        )
        return self._with_session({"passkey": passkey.to_public()}, token, secure=secure)

    async def login_options(self, request: web.Request) -> web.Response:
        if not self._config.passkeys:
            return _error("passkeys are disabled", 404)
        return web.json_response(self.passkeys.login_options(request_origin(request, self._config)))

    async def login_verify(self, request: web.Request) -> web.Response:
        body = await _json(request)
        secure = self._secure(request)
        passkey = await self.passkeys.finish_login(str(body.get("ticket") or ""), _credential(body))
        token = await self.store.create_session(
            f"passkey:{passkey.name}", self._config.session_lifetime, passkey.id
        )
        return self._with_session({"identity": f"passkey:{passkey.name}"}, token, secure=secure)

    async def logout(self, request: web.Request) -> web.Response:
        token = request.cookies.get(SESSION_COOKIE)
        if token:
            await self.store.end_session(token)
        response = web.json_response({"ok": True})
        response.del_cookie(SESSION_COOKIE, path="/")
        return response

    # -- signed in (the console middleware has authenticated these) ----------
    async def list_passkeys(self, request: web.Request) -> web.Response:
        if await self._manager(request) is None:
            return _MANAGERS_ONLY()
        return web.json_response({"passkeys": [p.to_public() for p in await self.store.list_all()]})

    async def invite(self, request: web.Request) -> web.Response:
        if not self._config.passkeys:
            return _error("passkeys are disabled", 404)
        if await self._manager(request) is None:
            return _MANAGERS_ONLY()
        return web.json_response({"code": self.passkeys.issue_code()})

    async def delete_passkey(self, request: web.Request) -> web.Response:
        who = await self._manager(request)
        if who is None:
            return _MANAGERS_ONLY()
        target = request.match_info["passkey_id"]
        other_way_in = self._config.access_enabled or bool(self._config.token)
        outcome = await self.store.delete(target, keep_one=not other_way_in)
        if outcome == "missing":
            return _error("no such passkey", 404)
        if outcome == "last":
            return _error("this is the last passkey; add another before removing it", 409)
        logger.info("console: passkey %s removed by %s", target, who)
        return web.json_response({"ok": True})


def _manages_passkeys(identity: str) -> bool:
    return identity == "token" or identity.startswith("passkey:")


def _MANAGERS_ONLY() -> web.Response:  # noqa: N802 - reads as a constant at call sites
    return _error("sign in with a passkey (or the token) to manage passkeys", 403)


async def _json(request: web.Request) -> dict[str, Any]:
    try:
        body = await request.json()
    except Exception as exc:
        raise PasskeyError("the body must be JSON") from exc
    if not isinstance(body, dict):
        raise PasskeyError("the body must be a JSON object")
    return body


def _credential(body: dict[str, Any]) -> dict[str, Any]:
    credential = body.get("credential")
    if not isinstance(credential, dict):
        raise PasskeyError("credential is required")
    return credential
