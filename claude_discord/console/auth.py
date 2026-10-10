"""Who may use the console, and how a request proves it.

Three independent ways in:

* **Passkeys** (default, see ``passkeys.py``) — a WebAuthn sign-in that ends in
  a server-side session cookie. On unless ``CCDB_CONSOLE_PASSKEYS=0``.
* **Cloudflare Access** — the console sits behind a Cloudflare Tunnel with an
  Access application in front. Access signs a JWT per request
  (``Cf-Access-Jwt-Assertion``). The *signature, audience and issuer* are
  verified here against the team's published keys; the header's mere
  presence proves nothing, because anything that can reach the listener can
  set a header.
* **A static bearer token** — for a local client (a terminal UI on the same
  machine, a script) that does not come through Access.

With none of them available the console refuses to start: an unauthenticated
surface that can start agent turns is not a degraded mode, it is a hole.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
from dataclasses import dataclass, field, replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .passkey_store import PasskeyStore

logger = logging.getLogger(__name__)

ACCESS_HEADER = "Cf-Access-Jwt-Assertion"
ACCESS_COOKIE = "CF_Authorization"
SESSION_COOKIE = "ccdb_console"
DEFAULT_SESSION_DAYS = 30


def passkeys_installed() -> bool:
    try:
        import webauthn  # noqa: F401
    except ImportError:
        return False
    return True


class ConsoleAuthError(Exception):
    """The request is not from someone allowed to use the console."""


@dataclass(frozen=True)
class ConsoleAuthConfig:
    """Read from the environment once, at startup."""

    access_team_domain: str | None = None  # e.g. "myteam.cloudflareaccess.com"
    access_audience: str | None = None  # the Access application's AUD tag
    allowed_emails: frozenset[str] = field(default_factory=frozenset)
    token: str | None = None
    passkeys: bool = True
    #: Origins the browser may sign in from (``https://host:port``). Empty means
    #: "whatever origin the request arrived on" — see ``request_origin``.
    origins: tuple[str, ...] = ()
    session_days: int = DEFAULT_SESSION_DAYS
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: str | None = None
    oidc_name: str | None = None
    oidc_trust_unverified_email: bool = False

    @property
    def access_enabled(self) -> bool:
        return bool(self.access_team_domain and self.access_audience)

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_issuer)

    @property
    def session_lifetime(self) -> timedelta:
        return timedelta(days=self.session_days)

    def effective(self) -> ConsoleAuthConfig:
        """This config with passkeys off when their library is missing but
        another way in exists — an upgrade without the extra must not take a
        working token or Access setup down."""
        if self.passkeys and not passkeys_installed() and (self.access_enabled or self.token):
            logger.warning(
                "console: passkeys disabled — install the [console] extra to enable them"
            )
            return replace(self, passkeys=False)
        return self

    def problems(self) -> list[str]:
        """Why this configuration must not be served. Empty when it is safe."""
        issues: list[str] = []
        if (
            not self.passkeys
            and not self.access_enabled
            and not self.token
            and not self.oidc_enabled
        ):
            issues.append(
                "CCDB_CONSOLE_PASSKEYS=0 leaves no way in: set CCDB_CONSOLE_ACCESS_TEAM_DOMAIN "
                "+ CCDB_CONSOLE_ACCESS_AUD, or CCDB_CONSOLE_TOKEN — the console never runs "
                "unauthenticated"
            )
        issues.extend(self._oidc_problems())
        if self.passkeys and not passkeys_installed():
            issues.append("passkeys need the webauthn package: install the [console] extra")
        for origin in self.origins:
            if not origin.startswith(("https://", "http://localhost", "http://127.0.0.1")):
                issues.append(f"CCDB_CONSOLE_ORIGIN {origin!r}: passkeys need https (or localhost)")
        if self.session_days < 1:
            issues.append("CCDB_CONSOLE_SESSION_DAYS must be at least 1")
        if bool(self.access_team_domain) != bool(self.access_audience):
            issues.append("CCDB_CONSOLE_ACCESS_TEAM_DOMAIN and CCDB_CONSOLE_ACCESS_AUD go together")
        if self.access_enabled and not self.allowed_emails:
            issues.append(
                "CCDB_CONSOLE_ALLOWED_EMAILS is required with Access — an Access policy "
                "edited by mistake must not silently open the console"
            )
        if self.access_enabled:
            try:
                import jwt  # noqa: F401
            except ImportError:
                issues.append("Access needs PyJWT: install the [console] extra")
        if self.token is not None and len(self.token) < 32:
            issues.append("CCDB_CONSOLE_TOKEN must be at least 32 characters")
        return issues

    def _oidc_problems(self) -> list[str]:
        if not self.oidc_enabled:
            return []
        issues: list[str] = []
        if not str(self.oidc_issuer).startswith("https://"):
            issues.append("CCDB_CONSOLE_OIDC_ISSUER must be an https URL")
        if not self.oidc_client_id:
            issues.append("CCDB_CONSOLE_OIDC_CLIENT_ID is required with CCDB_CONSOLE_OIDC_ISSUER")
        if not self.allowed_emails:
            issues.append(
                "CCDB_CONSOLE_ALLOWED_EMAILS is required with OIDC — without it, every "
                "account at the provider could sign in"
            )
        if not self.origins:
            issues.append(
                "CCDB_CONSOLE_ORIGIN is required with OIDC: it is the redirect URI registered "
                "with the provider"
            )
        return issues

    @property
    def oidc_redirect_uri(self) -> str:
        from .oidc import CALLBACK_PATH

        return f"{self.origins[0]}{CALLBACK_PATH}" if self.origins else ""

    @classmethod
    def from_env(cls) -> ConsoleAuthConfig:
        team = (os.getenv("CCDB_CONSOLE_ACCESS_TEAM_DOMAIN") or "").strip()
        team = team.removeprefix("https://").rstrip("/")
        emails = os.getenv("CCDB_CONSOLE_ALLOWED_EMAILS") or ""
        origins = os.getenv("CCDB_CONSOLE_ORIGIN") or ""
        try:
            days = int(os.getenv("CCDB_CONSOLE_SESSION_DAYS") or DEFAULT_SESSION_DAYS)
        except ValueError:
            days = 0  # reported by problems()
        return cls(
            access_team_domain=team or None,
            access_audience=(os.getenv("CCDB_CONSOLE_ACCESS_AUD") or "").strip() or None,
            allowed_emails=frozenset(e.strip().lower() for e in emails.split(",") if e.strip()),
            token=(os.getenv("CCDB_CONSOLE_TOKEN") or "").strip() or None,
            passkeys=(os.getenv("CCDB_CONSOLE_PASSKEYS") or "1").strip() != "0",
            origins=tuple(o.strip().rstrip("/") for o in origins.split(",") if o.strip()),
            session_days=days,
            # Verbatim: it must equal the token's ``iss`` (Auth0's ends in "/").
            oidc_issuer=_env("CCDB_CONSOLE_OIDC_ISSUER") or None,
            oidc_client_id=_env("CCDB_CONSOLE_OIDC_CLIENT_ID") or None,
            oidc_client_secret=_env("CCDB_CONSOLE_OIDC_CLIENT_SECRET") or None,
            oidc_name=_env("CCDB_CONSOLE_OIDC_NAME") or None,
            oidc_trust_unverified_email=_env("CCDB_CONSOLE_OIDC_TRUST_UNVERIFIED_EMAIL") == "1",
        )


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


class ConsoleAuthenticator:
    """Turns a request's headers into an identity, or refuses."""

    def __init__(
        self,
        config: ConsoleAuthConfig,
        jwk_client: Any | None = None,
        sessions: PasskeyStore | None = None,
    ) -> None:
        self._config = config
        self._jwk_client = jwk_client
        self._sessions = sessions
        if config.access_enabled and jwk_client is None:
            import jwt

            self._jwk_client = jwt.PyJWKClient(
                f"https://{config.access_team_domain}/cdn-cgi/access/certs",
                cache_keys=True,
                lifespan=3600,
            )

    @property
    def config(self) -> ConsoleAuthConfig:
        return self._config

    async def identify(self, headers: Any, cookies: Any) -> str:
        """Return who is calling (``passkey:<name>``, an email, or ``"token"``)."""
        auth = headers.get("Authorization", "")
        bad_token = False
        if self._config.token and auth.startswith("Bearer "):
            # Bytes, not str: compare_digest raises on non-ASCII str input.
            if hmac.compare_digest(auth[7:].encode(), self._config.token.encode()):
                return "token"
            # Keep looking: a stale token left in a browser must not hide the
            # passkey session sent alongside it.
            bad_token = True
        session = cookies.get(SESSION_COOKIE)
        if session and self._sessions is not None:
            who = await self._sessions.session_identity(session)
            if who and self._session_still_allowed(who):
                return who
        if self._config.access_enabled:
            assertion = headers.get(ACCESS_HEADER) or cookies.get(ACCESS_COOKIE)
            if assertion:
                return await self._verify_access(assertion)
        raise ConsoleAuthError("invalid token" if bad_token else "not authenticated")

    def _session_still_allowed(self, who: str) -> bool:
        """A session is only as good as the method that opened it, *now*:
        turning a method off, or taking an email off the allowlist, ends the
        sessions it created instead of letting them run out their 30 days."""
        if who.startswith("passkey:"):
            return self._config.passkeys
        if who.startswith("oidc:"):
            return self._config.oidc_enabled and who[5:] in self._config.allowed_emails
        return False

    async def _verify_access(self, assertion: str) -> str:
        import jwt

        try:
            # PyJWKClient fetches over blocking urllib; keep it off the loop.
            key = await asyncio.to_thread(self._jwk_client.get_signing_key_from_jwt, assertion)  # type: ignore[union-attr]
            claims = jwt.decode(
                assertion,
                key.key,
                algorithms=["RS256"],
                audience=self._config.access_audience,
                issuer=f"https://{self._config.access_team_domain}",
                options={"require": ["exp", "iat", "aud", "iss"]},
            )
        except Exception as exc:  # any failure is the same answer: not you
            logger.info("console: Access assertion rejected: %s", exc)
            raise ConsoleAuthError("invalid Access assertion") from exc
        email = str(claims.get("email") or "").lower()
        if not email or email not in self._config.allowed_emails:
            raise ConsoleAuthError("this account is not allowed to use the console")
        return email
