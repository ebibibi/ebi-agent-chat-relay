"""Console authentication: refusal to run open, tokens, and Access JWTs."""

from __future__ import annotations

import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from claude_discord.console.auth import (
    ACCESS_HEADER,
    ConsoleAuthConfig,
    ConsoleAuthenticator,
    ConsoleAuthError,
)

TEAM = "team.cloudflareaccess.com"
AUD = "aud-tag"
TOKEN = "t" * 40


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeJwks:
    def __init__(self, public_key) -> None:
        self.public_key = public_key

    def get_signing_key_from_jwt(self, token: str):
        return SimpleNamespace(key=self.public_key)


def assertion(key, **overrides) -> str:
    now = int(time.time())
    claims = {
        "aud": [AUD],
        "iss": f"https://{TEAM}",
        "email": "owner@example.com",
        "iat": now,
        "exp": now + 600,
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256")


def access_auth(key) -> ConsoleAuthenticator:
    config = ConsoleAuthConfig(
        access_team_domain=TEAM,
        access_audience=AUD,
        allowed_emails=frozenset({"owner@example.com"}),
    )
    return ConsoleAuthenticator(config, jwk_client=FakeJwks(key.public_key()))


def test_an_unauthenticated_configuration_is_refused() -> None:
    assert ConsoleAuthConfig(passkeys=False).problems()


def test_passkeys_alone_are_enough() -> None:
    assert ConsoleAuthConfig().problems() == []


def test_a_plain_http_origin_is_refused() -> None:
    assert ConsoleAuthConfig(origins=("http://console.example.com",)).problems()
    assert ConsoleAuthConfig(origins=("http://localhost:8100",)).problems() == []


def test_access_without_an_email_allowlist_is_refused() -> None:
    problems = ConsoleAuthConfig(access_team_domain=TEAM, access_audience=AUD).problems()
    assert any("ALLOWED_EMAILS" in p for p in problems)


def test_a_short_token_is_refused() -> None:
    assert ConsoleAuthConfig(token="short").problems()


def test_half_an_access_configuration_is_refused() -> None:
    assert ConsoleAuthConfig(access_team_domain=TEAM, token=TOKEN).problems()


def test_from_env_normalises(monkeypatch) -> None:
    monkeypatch.setenv("CCDB_CONSOLE_ACCESS_TEAM_DOMAIN", f"https://{TEAM}/")
    monkeypatch.setenv("CCDB_CONSOLE_ACCESS_AUD", AUD)
    monkeypatch.setenv("CCDB_CONSOLE_ALLOWED_EMAILS", " Owner@Example.com , ")
    config = ConsoleAuthConfig.from_env()
    assert config.access_team_domain == TEAM
    assert config.allowed_emails == frozenset({"owner@example.com"})
    assert config.problems() == []


async def test_token_auth() -> None:
    auth = ConsoleAuthenticator(ConsoleAuthConfig(token=TOKEN))
    assert await auth.identify({"Authorization": f"Bearer {TOKEN}"}, {}) == "token"
    with pytest.raises(ConsoleAuthError):
        await auth.identify({"Authorization": "Bearer wrong"}, {})
    with pytest.raises(ConsoleAuthError):
        await auth.identify({}, {})


async def test_a_valid_access_assertion_identifies_the_email(key) -> None:
    auth = access_auth(key)
    assert await auth.identify({ACCESS_HEADER: assertion(key)}, {}) == "owner@example.com"
    assert await auth.identify({}, {"CF_Authorization": assertion(key)}) == "owner@example.com"


@pytest.mark.parametrize(
    "overrides",
    [
        {"aud": ["someone-else"]},
        {"iss": "https://evil.cloudflareaccess.com"},
        {"exp": int(time.time()) - 10},
        {"email": "stranger@example.com"},
        {"email": None},
    ],
)
async def test_bad_access_assertions_are_refused(key, overrides) -> None:
    with pytest.raises(ConsoleAuthError):
        await access_auth(key).identify({ACCESS_HEADER: assertion(key, **overrides)}, {})


async def test_an_assertion_signed_by_another_key_is_refused(key) -> None:
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(ConsoleAuthError):
        await access_auth(key).identify({ACCESS_HEADER: assertion(other)}, {})


async def test_a_non_ascii_token_is_a_refusal_not_a_crash() -> None:
    auth = ConsoleAuthenticator(ConsoleAuthConfig(token=TOKEN))
    with pytest.raises(ConsoleAuthError):
        await auth.identify({"Authorization": "Bearer ありがとう"}, {})
