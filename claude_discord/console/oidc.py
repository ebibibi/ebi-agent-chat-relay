"""Optional OpenID Connect sign-in: Google, Microsoft Entra ID, Okta, Keycloak, …

The operator registers a web client with their provider, points
``CCDB_CONSOLE_OIDC_ISSUER`` at it and lists who may come in. The flow is the
authorization code flow with PKCE, ``state`` and ``nonce``:

* the sign-in in progress (``state``, ``nonce``, PKCE verifier) lives in a
  short-lived cookie signed with a per-process key — not in a server-side table
  that anyone could flood until a real sign-in is evicted. A callback URL that
  someone else started cannot be completed in your browser (login CSRF), and a
  completed ``state`` is remembered until it expires, so it cannot be replayed.
* ``nonce`` is checked inside the ID token, so a token minted for another
  sign-in cannot be replayed into this one.
* the ID token's signature, issuer, audience (and ``azp`` when there are
  several audiences) and expiry are verified against the issuer's published
  keys, and the email must be on ``CCDB_CONSOLE_ALLOWED_EMAILS``.
* the provider must assert ``email_verified: true``. An email claim alone
  proves nothing: on some providers a user can set it to any address
  ("nOAuth"). Only an operator whose provider never sends the claim but
  controls every address in it (a single-tenant Entra ID) may waive this,
  with ``CCDB_CONSOLE_OIDC_TRUST_UNVERIFIED_EMAIL=1``.

Whether the provider asks for a second factor is the provider's policy: turn
on 2-step verification (or passkeys) for the accounts on the allowlist.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

CALLBACK_PATH = "/console/api/auth/oidc/callback"
STATE_COOKIE = "ccdb_console_oidc"
FLOW_TTL = 10 * 60.0
#: Completed states kept to refuse a replay. Evicting one only re-opens a replay
#: that the provider refuses anyway (a code is single use), never a lockout.
MAX_USED_STATES = 4096
DISCOVERY_TTL = 3600.0
#: After a failed discovery, answer from memory for this long instead of asking
#: the provider again on every unauthenticated request.
DISCOVERY_RETRY = 30.0
#: Accepted ID-token signature algorithms. ``none`` and HMAC are never accepted.
ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "PS256"]

GetJson = Callable[[str], Awaitable[dict[str, Any]]]
PostForm = Callable[[str, dict[str, str]], Awaitable[dict[str, Any]]]


class OidcError(Exception):
    """A sign-in that must not succeed. The message is safe to show."""


def provider_name(issuer: str) -> str:
    """A label for the sign-in button when the operator did not set one."""
    if "accounts.google.com" in issuer:
        return "Google"
    if "login.microsoftonline.com" in issuer:
        return "Microsoft"
    return "SSO"


@dataclass(frozen=True)
class OidcConfig:
    issuer: str
    client_id: str
    client_secret: str | None
    redirect_uri: str
    allowed_emails: frozenset[str]
    name: str
    scopes: str = "openid email profile"
    trust_unverified_email: bool = False


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


async def _aiohttp_get_json(url: str) -> dict[str, Any]:
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=15)
    async with aiohttp.ClientSession(timeout=timeout) as session, session.get(url) as resp:
        resp.raise_for_status()
        return await resp.json(content_type=None)


async def _aiohttp_post_form(url: str, data: dict[str, str]) -> dict[str, Any]:
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=15)
    headers = {"Accept": "application/json"}
    async with (
        aiohttp.ClientSession(timeout=timeout) as session,
        session.post(url, data=data, headers=headers) as resp,
    ):
        body = await resp.json(content_type=None)
        if resp.status >= 400:
            logger.info("console: OIDC token endpoint said %s: %s", resp.status, body)
            raise OidcError("the identity provider refused the sign-in")
        return body


class OidcClient:
    """Builds the authorization redirect and turns the callback into an email."""

    def __init__(
        self,
        config: OidcConfig,
        *,
        get_json: GetJson = _aiohttp_get_json,
        post_form: PostForm = _aiohttp_post_form,
        jwk_client: Any | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self._get_json = get_json
        self._post_form = post_form
        self._jwk_client = jwk_client
        self._clock = clock
        self._discovery: tuple[float, dict[str, Any]] | None = None
        self._discovery_failed_at: float | None = None
        self._key = secrets.token_bytes(32)
        self._used: dict[str, float] = {}

    async def _metadata(self) -> dict[str, Any]:
        now = self._clock()
        if self._discovery and now - self._discovery[0] < DISCOVERY_TTL:
            return self._discovery[1]
        unreachable = OidcError("the identity provider could not be reached")
        if self._discovery_failed_at is not None and now - self._discovery_failed_at < (
            DISCOVERY_RETRY
        ):
            raise unreachable
        url = self.config.issuer.rstrip("/") + "/.well-known/openid-configuration"
        try:
            meta = await self._get_json(url)
        except Exception as exc:
            self._discovery_failed_at = now
            logger.warning("console: OIDC discovery failed for %s: %s", url, exc)
            raise unreachable from exc
        self._discovery_failed_at = None
        if meta.get("issuer") != self.config.issuer:
            raise OidcError("the identity provider's issuer does not match the configuration")
        self._discovery = (now, meta)
        return meta

    def _seal(self, flow: dict[str, Any]) -> str:
        body = _b64(json.dumps(flow, separators=(",", ":")).encode())
        mac = hmac.new(self._key, body.encode(), hashlib.sha256).digest()
        return f"{body}.{_b64(mac)}"

    def _open(self, sealed: str | None) -> dict[str, Any]:
        """The flow in *sealed*, or an error when it was not made here, now."""
        body, _, mac = (sealed or "").partition(".")
        want = hmac.new(self._key, body.encode(), hashlib.sha256).digest()
        try:
            genuine = hmac.compare_digest(_unb64(mac), want)
            flow = json.loads(_unb64(body)) if genuine else None
        except (ValueError, TypeError):
            flow = None
        if not isinstance(flow, dict) or float(flow.get("exp", 0)) <= self._clock():
            raise OidcError("this sign-in expired or was started in another browser")
        return flow

    def _remember_used(self, state: str) -> None:
        now = self._clock()
        self._used = {k: v for k, v in self._used.items() if v > now}
        while len(self._used) >= MAX_USED_STATES:
            del self._used[next(iter(self._used))]
        self._used[state] = now + FLOW_TTL

    async def start(self) -> tuple[str, str]:
        """Begin a sign-in: ``(authorization URL, signed flow cookie value)``."""
        meta = await self._metadata()
        state = secrets.token_urlsafe(24)
        nonce = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(48)
        sealed = self._seal(
            {"s": state, "n": nonce, "v": verifier, "exp": self._clock() + FLOW_TTL}
        )
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self.config.client_id,
                "redirect_uri": self.config.redirect_uri,
                "scope": self.config.scopes,
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge.rstrip(b"=").decode(),
                "code_challenge_method": "S256",
            }
        )
        return f"{meta['authorization_endpoint']}?{query}", sealed

    async def finish(self, *, state: str, code: str, browser: str | None) -> str:
        """Complete a sign-in and return the allowed, verified email."""
        flow = self._open(browser)
        if not state or not hmac.compare_digest(str(flow.get("s", "")), state):
            raise OidcError("this sign-in was started in another browser")
        if state in self._used:
            raise OidcError("this sign-in was already completed")
        self._remember_used(state)
        if not code:
            raise OidcError("the identity provider returned no code")
        meta = await self._metadata()
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.config.redirect_uri,
            "client_id": self.config.client_id,
            "code_verifier": str(flow["v"]),
        }
        if self.config.client_secret:
            form["client_secret"] = self.config.client_secret
        try:
            tokens = await self._post_form(meta["token_endpoint"], form)
        except OidcError:
            raise
        except Exception as exc:
            logger.warning("console: OIDC token exchange failed: %s", exc)
            raise OidcError("the identity provider could not be reached") from exc
        id_token = tokens.get("id_token")
        if not isinstance(id_token, str):
            raise OidcError("the identity provider returned no ID token")
        claims = await self._verify(id_token, meta)
        audiences = claims["aud"] if isinstance(claims["aud"], list) else [claims["aud"]]
        if len(audiences) > 1 and claims.get("azp") != self.config.client_id:
            raise OidcError("the ID token was issued to another application")
        if claims.get("nonce") != flow["n"]:
            raise OidcError("the ID token was not issued for this sign-in")
        return self._email(claims)

    async def _verify(self, id_token: str, meta: dict[str, Any]) -> dict[str, Any]:
        import jwt

        if self._jwk_client is None:
            self._jwk_client = jwt.PyJWKClient(meta["jwks_uri"], cache_keys=True, lifespan=3600)
        try:
            # PyJWKClient fetches over blocking urllib; keep it off the loop.
            key = await asyncio.to_thread(self._jwk_client.get_signing_key_from_jwt, id_token)
            return jwt.decode(
                id_token,
                key.key,
                algorithms=ALGORITHMS,
                audience=self.config.client_id,
                issuer=self.config.issuer,
                options={"require": ["exp", "iat", "aud", "iss", "sub"]},
            )
        except Exception as exc:  # any failure is the same answer: not you
            logger.info("console: OIDC ID token rejected: %s", exc)
            raise OidcError("the ID token could not be verified") from exc

    def _email(self, claims: dict[str, Any]) -> str:
        email = str(claims.get("email") or "").strip().lower()
        verified = claims.get("email_verified")
        waived = verified is None and self.config.trust_unverified_email
        if verified is not True and verified != "true" and not waived:
            raise OidcError("the identity provider has not verified this email")
        if not email or email not in self.config.allowed_emails:
            logger.info("console: OIDC sign-in by %r refused (not on the allowlist)", email)
            raise OidcError("this account is not allowed to use the console")
        return email
