"""Who may use the console, and how a request proves it.

Two independent ways in, either of which may be configured:

* **Cloudflare Access** — the console sits behind a Cloudflare Tunnel with an
  Access application in front. Access signs a JWT per request
  (``Cf-Access-Jwt-Assertion``). The *signature, audience and issuer* are
  verified here against the team's published keys; the header's mere
  presence proves nothing, because anything that can reach the listener can
  set a header.
* **A static bearer token** — for a local client (a terminal UI on the same
  machine, a script) that does not come through Access.

With neither configured the console refuses to start: an unauthenticated
surface that can start agent turns is not a degraded mode, it is a hole.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

ACCESS_HEADER = "Cf-Access-Jwt-Assertion"
ACCESS_COOKIE = "CF_Authorization"


class ConsoleAuthError(Exception):
    """The request is not from someone allowed to use the console."""


@dataclass(frozen=True)
class ConsoleAuthConfig:
    """Read from the environment once, at startup."""

    access_team_domain: str | None = None  # e.g. "myteam.cloudflareaccess.com"
    access_audience: str | None = None  # the Access application's AUD tag
    allowed_emails: frozenset[str] = field(default_factory=frozenset)
    token: str | None = None

    @property
    def access_enabled(self) -> bool:
        return bool(self.access_team_domain and self.access_audience)

    def problems(self) -> list[str]:
        """Why this configuration must not be served. Empty when it is safe."""
        issues: list[str] = []
        if not self.access_enabled and not self.token:
            issues.append(
                "set CCDB_CONSOLE_ACCESS_TEAM_DOMAIN + CCDB_CONSOLE_ACCESS_AUD, "
                "or CCDB_CONSOLE_TOKEN — the console never runs unauthenticated"
            )
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

    @classmethod
    def from_env(cls) -> ConsoleAuthConfig:
        team = (os.getenv("CCDB_CONSOLE_ACCESS_TEAM_DOMAIN") or "").strip()
        team = team.removeprefix("https://").rstrip("/")
        emails = os.getenv("CCDB_CONSOLE_ALLOWED_EMAILS") or ""
        return cls(
            access_team_domain=team or None,
            access_audience=(os.getenv("CCDB_CONSOLE_ACCESS_AUD") or "").strip() or None,
            allowed_emails=frozenset(e.strip().lower() for e in emails.split(",") if e.strip()),
            token=(os.getenv("CCDB_CONSOLE_TOKEN") or "").strip() or None,
        )


class ConsoleAuthenticator:
    """Turns a request's headers into an identity, or refuses."""

    def __init__(self, config: ConsoleAuthConfig, jwk_client: Any | None = None) -> None:
        self._config = config
        self._jwk_client = jwk_client
        if config.access_enabled and jwk_client is None:
            import jwt

            self._jwk_client = jwt.PyJWKClient(
                f"https://{config.access_team_domain}/cdn-cgi/access/certs",
                cache_keys=True,
                lifespan=3600,
            )

    async def identify(self, headers: Any, cookies: Any) -> str:
        """Return who is calling (an email, or ``"token"``)."""
        auth = headers.get("Authorization", "")
        if self._config.token and auth.startswith("Bearer "):
            # Bytes, not str: compare_digest raises on non-ASCII str input.
            if hmac.compare_digest(auth[7:].encode(), self._config.token.encode()):
                return "token"
            raise ConsoleAuthError("invalid token")
        if self._config.access_enabled:
            assertion = headers.get(ACCESS_HEADER) or cookies.get(ACCESS_COOKIE)
            if assertion:
                return await self._verify_access(assertion)
        raise ConsoleAuthError("not authenticated")

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
