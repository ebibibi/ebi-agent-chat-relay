"""Entra ID access-token verification for the external ingest listener.

The external listener (``CCDB_INGEST_HOST`` / ``CCDB_INGEST_PORT``) has always
accepted one static bearer token. An organisation that already signs its people
in with Entra ID can instead let its clients send the user's own v2 access
token: it is tied to a person, expires on its own and is revoked in the tenant,
not by rotating a secret copied to every machine.

The verifier is deliberately narrow and fails closed:

- RS256 only, keys from the tenant's v2 JWKS endpoint;
- ``iss`` / ``tid`` must name the configured tenant;
- ``aud`` must be the configured app (client ID or ``api://<client ID>``);
- the delegated scope (``scp``) must contain the configured scope;
- the user's ``oid`` must be in an explicit allowlist — a valid token for some
  other member of the tenant is not enough.

Nothing about a rejected token is logged except the reason.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import jwt

logger = logging.getLogger(__name__)

DEFAULT_SCOPE = "access_as_user"
_LEEWAY_SECONDS = 60


def _jwks_url(tenant_id: str) -> str:
    return f"https://login.microsoftonline.com/{tenant_id}/discovery/v2.0/keys"


def looks_like_jwt(value: str) -> bool:
    """A compact JWS has three dot-separated segments."""
    return value.count(".") == 2


@dataclass(frozen=True)
class EntraTokenVerifier:
    """Verify Entra ID v2 access tokens issued for one app in one tenant."""

    tenant_id: str
    client_id: str
    allowed_oids: frozenset[str]
    scope: str = DEFAULT_SCOPE
    # Returns the signing key for a token. Injected in tests; defaults to a
    # cached PyJWKClient on the tenant's JWKS endpoint.
    key_resolver: Callable[[str], Any] | None = None

    @property
    def issuer(self) -> str:
        return f"https://login.microsoftonline.com/{self.tenant_id}/v2.0"

    @property
    def audiences(self) -> list[str]:
        return [self.client_id, f"api://{self.client_id}"]

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> EntraTokenVerifier | None:
        """Build a verifier from ``CCDB_INGEST_ENTRA_*``, or ``None`` when off.

        Tenant and client ID turn the feature on; the allowlist is required.
        Without it any member of the tenant could write to the vault, so a
        half-configured setup is reported and left off rather than widened.
        """
        tenant = (env.get("CCDB_INGEST_ENTRA_TENANT_ID") or "").strip()
        client = (env.get("CCDB_INGEST_ENTRA_CLIENT_ID") or "").strip()
        if not tenant and not client:
            return None
        oids = frozenset(
            o.strip().lower()
            for o in (env.get("CCDB_INGEST_ENTRA_ALLOWED_OIDS") or "").split(",")
            if o.strip()
        )
        if not tenant or not client or not oids:
            logger.error(
                "Entra ID ingest auth is half-configured (tenant=%s, client=%s, "
                "allowed oids=%d) — leaving it off. Set CCDB_INGEST_ENTRA_TENANT_ID, "
                "CCDB_INGEST_ENTRA_CLIENT_ID and CCDB_INGEST_ENTRA_ALLOWED_OIDS.",
                bool(tenant),
                bool(client),
                len(oids),
            )
            return None
        scope = (env.get("CCDB_INGEST_ENTRA_SCOPE") or "").strip() or DEFAULT_SCOPE
        return cls(tenant_id=tenant, client_id=client, allowed_oids=oids, scope=scope)

    def _resolver(self) -> Callable[[str], Any]:
        if self.key_resolver is not None:
            return self.key_resolver
        client = _jwk_clients.get(self.tenant_id)
        if client is None:
            client = jwt.PyJWKClient(_jwks_url(self.tenant_id), cache_keys=True, lifespan=3600)
            _jwk_clients[self.tenant_id] = client
        return lambda token: client.get_signing_key_from_jwt(token).key

    def verify_sync(self, token: str) -> tuple[bool, str]:
        """Return ``(ok, reason)``. Blocking: may fetch the JWKS."""
        try:
            key = self._resolver()(token)
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256"],
                audience=self.audiences,
                issuer=self.issuer,
                leeway=_LEEWAY_SECONDS,
                options={"require": ["exp", "iss", "aud", "tid", "oid"]},
            )
        except jwt.PyJWTError as e:
            return False, f"invalid token ({type(e).__name__})"
        except Exception as e:  # JWKS fetch / key errors: fail closed
            return False, f"verification error ({type(e).__name__})"
        if str(claims.get("tid", "")).lower() != self.tenant_id.lower():
            return False, "wrong tenant"
        scopes = str(claims.get("scp", "")).split()
        if self.scope not in scopes:
            return False, "missing scope"
        if str(claims.get("oid", "")).lower() not in self.allowed_oids:
            return False, "user not allowed"
        return True, "ok"

    async def verify(self, token: str) -> tuple[bool, str]:
        return await asyncio.to_thread(self.verify_sync, token)


# One JWKS client per tenant, shared across verifier instances (keys are cached
# inside the client).
_jwk_clients: dict[str, jwt.PyJWKClient] = {}
