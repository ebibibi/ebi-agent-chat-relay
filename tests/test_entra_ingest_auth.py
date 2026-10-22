"""Entra ID access tokens on the external ingest listener (#1195).

The static ``ingest_token`` keeps working; an Entra ID v2 access token for the
configured tenant, app, scope and allowlisted user is accepted as well. Every
other token is a 401, and with neither method configured the surface is off.
"""

from __future__ import annotations

import socket
import time
from unittest.mock import MagicMock

import jwt
import pytest
from aiohttp.test_utils import TestClient, TestServer
from cryptography.hazmat.primitives.asymmetric import rsa

from claude_discord.database.notification_repo import NotificationRepository
from claude_discord.ext.api_server import ApiServer
from claude_discord.ext.entra_auth import EntraTokenVerifier, UnknownSigningKeyError, _JwksCache

TENANT = "aaaaaaaa-0000-4000-8000-000000000001"
CLIENT = "bbbbbbbb-0000-4000-8000-000000000002"
OID = "cccccccc-0000-4000-8000-000000000003"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _verifier(**kw) -> EntraTokenVerifier:
    return EntraTokenVerifier(
        tenant_id=TENANT,
        client_id=CLIENT,
        allowed_oids=frozenset({OID}),
        key_resolver=lambda _token: _KEY.public_key(),
        **kw,
    )


def _token(key=_KEY, **overrides) -> str:
    now = int(time.time())
    claims = {
        "iss": f"https://login.microsoftonline.com/{TENANT}/v2.0",
        "aud": CLIENT,
        "tid": TENANT,
        "oid": OID,
        "scp": "access_as_user",
        "iat": now,
        "nbf": now,
        "exp": now + 3600,
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "test-kid"})


# --- verifier ---------------------------------------------------------------


def test_valid_token_is_accepted() -> None:
    assert _verifier().verify_sync(_token()) == (True, "ok")


def test_api_uri_audience_is_accepted() -> None:
    ok, _ = _verifier().verify_sync(_token(aud=f"api://{CLIENT}"))
    assert ok


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": "someone-else"},
        {"iss": "https://login.microsoftonline.com/other/v2.0"},
        {"tid": "00000000-0000-0000-0000-000000000000"},
        {"scp": "User.Read"},
        {"scp": None},
        {"oid": "11111111-1111-1111-1111-111111111111"},
        {"oid": None},
        {"exp": int(time.time()) - 3600},
    ],
)
def test_bad_claims_are_rejected(overrides: dict) -> None:
    ok, reason = _verifier().verify_sync(_token(**overrides))
    assert not ok
    assert reason


def test_wrong_signing_key_is_rejected() -> None:
    ok, _ = _verifier().verify_sync(_token(key=_OTHER_KEY))
    assert not ok


def test_hs256_token_is_rejected() -> None:
    forged = jwt.encode({"aud": CLIENT, "oid": OID}, "x" * 32, algorithm="HS256")
    ok, _ = _verifier().verify_sync(forged)
    assert not ok


def test_key_lookup_failure_fails_closed() -> None:
    def boom(_token: str):
        raise OSError("network down")

    v = EntraTokenVerifier(
        tenant_id=TENANT, client_id=CLIENT, allowed_oids=frozenset({OID}), key_resolver=boom
    )
    ok, reason = v.verify_sync(_token())
    assert not ok
    assert "OSError" in reason


# --- configuration ----------------------------------------------------------


def test_from_env_off_when_unset() -> None:
    assert EntraTokenVerifier.from_env({}) is None


def test_from_env_requires_allowlist() -> None:
    env = {"CCDB_INGEST_ENTRA_TENANT_ID": TENANT, "CCDB_INGEST_ENTRA_CLIENT_ID": CLIENT}
    assert EntraTokenVerifier.from_env(env) is None


def test_from_env_builds_verifier() -> None:
    v = EntraTokenVerifier.from_env(
        {
            "CCDB_INGEST_ENTRA_TENANT_ID": TENANT,
            "CCDB_INGEST_ENTRA_CLIENT_ID": CLIENT,
            "CCDB_INGEST_ENTRA_ALLOWED_OIDS": f" {OID.upper()} , ",
        }
    )
    assert v is not None
    assert v.allowed_oids == frozenset({OID})
    assert v.scope == "access_as_user"


# --- HTTP surface -----------------------------------------------------------


@pytest.fixture
async def repo() -> NotificationRepository:
    r = NotificationRepository(":memory:")
    await r.init_db()
    return r


async def _plan(api: ApiServer, auth: str | None) -> int:
    headers = {"Authorization": auth} if auth else {}
    async with TestClient(TestServer(api.external_app)) as client:
        resp = await client.post("/api/teams/sync/plan", json={}, headers=headers)
        return resp.status


async def test_entra_token_passes_auth(repo: NotificationRepository, tmp_path) -> None:
    api = ApiServer(
        repo=repo, bot=MagicMock(), ingest_entra=_verifier(), teams_vault_root=str(tmp_path)
    )
    # Past auth, the empty body is rejected by validation (400), not 401.
    assert await _plan(api, "Bearer " + _token()) == 400


async def test_static_token_still_works_with_entra(repo: NotificationRepository, tmp_path) -> None:
    api = ApiServer(
        repo=repo,
        bot=MagicMock(),
        ingest_token="secret",
        ingest_entra=_verifier(),
        teams_vault_root=str(tmp_path),
    )
    assert await _plan(api, "Bearer secret") == 400


async def test_static_token_only(repo: NotificationRepository, tmp_path) -> None:
    api = ApiServer(
        repo=repo, bot=MagicMock(), ingest_token="secret", teams_vault_root=str(tmp_path)
    )
    assert await _plan(api, "Bearer secret") == 400
    assert await _plan(api, "Bearer " + _token()) == 401


@pytest.mark.parametrize("auth", ["Bearer garbage", "Bearer a.b.c", None])
async def test_bad_bearer_is_401(repo: NotificationRepository, auth) -> None:
    api = ApiServer(repo=repo, bot=MagicMock(), ingest_entra=_verifier())
    assert await _plan(api, auth) == 401


async def test_rejected_entra_token_is_401(repo: NotificationRepository) -> None:
    api = ApiServer(repo=repo, bot=MagicMock(), ingest_entra=_verifier())
    assert await _plan(api, "Bearer " + _token(oid="11111111-1111-1111-1111-111111111111")) == 401


async def test_neither_configured_is_503(repo: NotificationRepository) -> None:
    api = ApiServer(repo=repo, bot=MagicMock())
    assert await _plan(api, "Bearer " + _token()) == 503


async def test_ingest_route_accepts_entra(repo: NotificationRepository) -> None:
    api = ApiServer(repo=repo, bot=MagicMock(), ingest_entra=_verifier())
    async with TestClient(TestServer(api.external_app)) as client:
        resp = await client.post(
            "/api/ingest",
            data="not json",
            headers={"Authorization": "Bearer " + _token()},
        )
        # Authenticated, then the body is rejected.
        assert resp.status == 400


async def test_listener_starts_with_entra_only(repo: NotificationRepository) -> None:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    api = ApiServer(
        repo=repo,
        bot=MagicMock(),
        ingest_entra=_verifier(),
        ingest_host="127.0.0.1",
        ingest_port=port,
    )
    await api._start_external_listener()
    try:
        assert api._ext_runner is not None
    finally:
        await api.stop()


# --- JWKS refetch limit (#1197) ----------------------------------------------


def test_token_without_kid_is_rejected_before_key_lookup() -> None:
    calls = []
    v = EntraTokenVerifier(
        tenant_id=TENANT,
        client_id=CLIENT,
        allowed_oids=frozenset({OID}),
        key_resolver=lambda tok: calls.append(tok) or _KEY.public_key(),
    )
    no_kid = jwt.encode({"aud": CLIENT}, _KEY, algorithm="RS256")
    ok, reason = v.verify_sync(no_kid)
    assert not ok and "kid" in reason
    assert calls == []


def test_non_rs256_header_is_rejected_before_key_lookup() -> None:
    calls = []
    v = EntraTokenVerifier(
        tenant_id=TENANT,
        client_id=CLIENT,
        allowed_oids=frozenset({OID}),
        key_resolver=lambda tok: calls.append(tok) or _KEY.public_key(),
    )
    forged = jwt.encode({"aud": CLIENT}, "x" * 32, algorithm="HS256", headers={"kid": "test-kid"})
    ok, reason = v.verify_sync(forged)
    assert not ok and reason == "unsupported alg"
    assert calls == []


def _cache(clock):
    fetches = []

    def fetch():
        fetches.append(clock[0])
        return {"known": "KEY"}

    return _JwksCache(
        "https://example.invalid/keys", fetch=fetch, clock=lambda: clock[0], cooldown=300
    ), fetches


def test_unknown_kids_refetch_at_most_once_per_cooldown() -> None:
    clock = [1000.0]
    cache, fetches = _cache(clock)
    assert cache.key_for_kid("known") == "KEY"
    for _ in range(50):
        with pytest.raises(UnknownSigningKeyError):
            cache.key_for_kid("forged")
    assert len(fetches) == 1
    clock[0] += 301
    with pytest.raises(UnknownSigningKeyError):
        cache.key_for_kid("forged")
    assert len(fetches) == 2


def test_known_kid_is_served_from_cache() -> None:
    clock = [0.0]
    cache, fetches = _cache(clock)
    for _ in range(10):
        assert cache.key_for_kid("known") == "KEY"
    assert len(fetches) == 1
