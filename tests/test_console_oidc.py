"""OIDC sign-in against a fake provider that signs real ID tokens."""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import MagicMock
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from aiohttp.test_utils import TestClient, TestServer
from cryptography.hazmat.primitives.asymmetric import rsa

from claude_discord.console.auth import (
    SESSION_COOKIE,
    ConsoleAuthConfig,
    ConsoleAuthenticator,
    ConsoleAuthError,
    oidc_session_identity,
)
from claude_discord.console.oidc import (
    STATE_COOKIE,
    OidcClient,
    OidcConfig,
    OidcError,
    provider_name,
)
from claude_discord.console.passkey_store import PasskeyStore
from claude_discord.console.server import ConsoleServer
from claude_discord.console.work_repo import WorkItemRepository

ISSUER = "https://accounts.google.com"
CLIENT = "client-123.apps.googleusercontent.com"
ORIGIN = "https://console.example.com"
OWNER = "owner@example.com"


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeProvider:
    """Discovery, a token endpoint, and the keys that sign its ID tokens."""

    def __init__(self, key) -> None:
        self.key = key
        self.claims: dict = {}
        self.last_form: dict = {}
        self.issuer = ISSUER
        self.nonce = ""

    async def get_json(self, url: str) -> dict:
        assert url == f"{ISSUER}/.well-known/openid-configuration"
        return {
            "issuer": self.issuer,
            "authorization_endpoint": "https://accounts.google.com/o/oauth2/v2/auth",
            "token_endpoint": "https://oauth2.googleapis.com/token",
            "jwks_uri": "https://www.googleapis.com/oauth2/v3/certs",
        }

    async def post_form(self, url: str, form: dict) -> dict:
        self.last_form = form
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": CLIENT,
            "sub": "1234",
            "email": OWNER,
            "email_verified": True,
            "nonce": self.nonce,
            "iat": now,
            "exp": now + 600,
            **self.claims,
        }
        claims = {k: v for k, v in claims.items() if v is not None}
        return {"id_token": jwt.encode(claims, self.key, algorithm="RS256")}

    def jwks(self):
        published = self.key.public_key()  # the key at setup, not whatever signs later
        return SimpleNamespace(
            get_signing_key_from_jwt=lambda token: SimpleNamespace(key=published)
        )


def make_client(provider: FakeProvider, origin: str = ORIGIN, **overrides) -> OidcClient:
    config = OidcConfig(
        issuer=ISSUER,
        client_id=CLIENT,
        client_secret="secret",
        redirect_uri=f"{origin}/console/api/auth/oidc/callback",
        allowed_emails=frozenset({OWNER}),
        name="Google",
        **overrides,
    )
    return OidcClient(
        config,
        get_json=provider.get_json,
        post_form=provider.post_form,
        jwk_client=provider.jwks(),
    )


async def begin(client: OidcClient, provider: FakeProvider) -> tuple[str, str]:
    url, browser = await client.start()
    query = parse_qs(urlsplit(url).query)
    provider.nonce = query["nonce"][0]
    assert query["code_challenge_method"] == ["S256"]
    assert query["redirect_uri"] == [client.config.redirect_uri]
    return query["state"][0], browser


async def test_a_verified_allowed_email_signs_in(key) -> None:
    provider = FakeProvider(key)
    client = make_client(provider)
    state, browser = await begin(client, provider)
    assert await client.finish(state=state, code="c", browser=browser) == OWNER
    assert provider.last_form["code_verifier"]
    assert provider.last_form["client_secret"] == "secret"


@pytest.mark.parametrize(
    "claims",
    [
        {"email": "stranger@example.com"},
        {"email_verified": False},
        {"email_verified": None},  # absent: an email claim alone proves nothing
        {"aud": "another-client"},
        {"aud": [CLIENT, "another-client"], "azp": "another-client"},
        {"iss": "https://evil.example.com"},
        {"exp": int(time.time()) - 60},
        {"nonce": "a-different-sign-in"},
        {"email": None},
    ],
)
async def test_bad_id_tokens_are_refused(key, claims) -> None:
    provider = FakeProvider(key)
    provider.claims = claims
    client = make_client(provider)
    state, browser = await begin(client, provider)
    with pytest.raises(OidcError):
        await client.finish(state=state, code="c", browser=browser)


async def test_an_absent_email_verified_can_be_waived(key) -> None:
    provider = FakeProvider(key)
    provider.claims = {"email_verified": None}
    client = make_client(provider, trust_unverified_email=True)
    state, browser = await begin(client, provider)
    assert await client.finish(state=state, code="c", browser=browser) == OWNER


async def test_the_waiver_never_overrides_an_explicit_false(key) -> None:
    provider = FakeProvider(key)
    provider.claims = {"email_verified": False}
    client = make_client(provider, trust_unverified_email=True)
    state, browser = await begin(client, provider)
    with pytest.raises(OidcError):
        await client.finish(state=state, code="c", browser=browser)


async def test_a_token_signed_by_another_key_is_refused(key) -> None:
    provider = FakeProvider(key)
    client = make_client(provider)
    state, browser = await begin(client, provider)
    provider.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(OidcError):
        await client.finish(state=state, code="c", browser=browser)


async def test_a_callback_from_another_browser_is_refused(key) -> None:
    provider = FakeProvider(key)
    client = make_client(provider)
    state, _ = await begin(client, provider)
    with pytest.raises(OidcError):
        await client.finish(state=state, code="c", browser="someone-elses-cookie")
    with pytest.raises(OidcError):
        await client.finish(state=state, code="c", browser=None)


async def test_state_cannot_be_replayed_or_invented(key) -> None:
    provider = FakeProvider(key)
    client = make_client(provider)
    state, browser = await begin(client, provider)
    assert await client.finish(state=state, code="c", browser=browser) == OWNER
    with pytest.raises(OidcError):
        await client.finish(state=state, code="c", browser=browser)
    with pytest.raises(OidcError):
        await client.finish(state="invented", code="c", browser=browser)


async def test_a_discovery_document_for_another_issuer_is_refused(key) -> None:
    provider = FakeProvider(key)
    provider.issuer = "https://evil.example.com"
    with pytest.raises(OidcError):
        await make_client(provider).start()


def test_provider_names() -> None:
    assert provider_name("https://accounts.google.com") == "Google"
    assert provider_name("https://login.microsoftonline.com/tid/v2.0") == "Microsoft"
    assert provider_name("https://id.example.com") == "SSO"


def test_oidc_configuration_problems() -> None:
    base = {"oidc_issuer": ISSUER, "oidc_client_id": CLIENT}
    assert ConsoleAuthConfig(**base, origins=(ORIGIN,)).problems()  # no allowlist
    assert ConsoleAuthConfig(**base, allowed_emails=frozenset({OWNER})).problems()  # no origin
    insecure = ConsoleAuthConfig(
        oidc_issuer="http://id.example.com",
        oidc_client_id=CLIENT,
        allowed_emails=frozenset({OWNER}),
        origins=(ORIGIN,),
    )
    assert insecure.problems()
    good = ConsoleAuthConfig(**base, allowed_emails=frozenset({OWNER}), origins=(ORIGIN,))
    assert good.problems() == []
    assert good.oidc_redirect_uri == f"{ORIGIN}/console/api/auth/oidc/callback"


async def test_sessions_end_when_the_email_or_the_issuer_changes(tmp_path) -> None:
    store = PasskeyStore(str(tmp_path / "db"))
    await store.init_db()
    session = await store.create_session(
        oidc_session_identity(ISSUER, OWNER), ConsoleAuthConfig().session_lifetime
    )
    cookies = {SESSION_COOKIE: session}

    def auth(issuer: str = ISSUER, emails: frozenset[str] = frozenset({OWNER})):
        config = ConsoleAuthConfig(oidc_issuer=issuer, oidc_client_id=CLIENT, allowed_emails=emails)
        return ConsoleAuthenticator(config, sessions=store)

    assert await auth().identify({}, cookies) == f"oidc:{OWNER}"
    with pytest.raises(ConsoleAuthError):
        await auth(emails=frozenset({"other@example.com"})).identify({}, cookies)
    with pytest.raises(ConsoleAuthError):
        await auth(issuer="https://login.microsoftonline.com/tid/v2.0").identify({}, cookies)


async def test_a_flooded_start_does_not_evict_a_real_sign_in(key) -> None:
    provider = FakeProvider(key)
    client = make_client(provider)
    state, browser = await begin(client, provider)
    for _ in range(1000):
        await client.start()
    provider.nonce = client._open(browser)["n"]  # the provider answers the real sign-in
    assert await client.finish(state=state, code="c", browser=browser) == OWNER


async def test_a_tampered_flow_cookie_is_refused(key) -> None:
    provider = FakeProvider(key)
    client = make_client(provider)
    state, browser = await begin(client, provider)
    body, _, mac = browser.partition(".")
    for forged in (f"{body}x.{mac}", f"{body}.{mac[:-2]}AA", "garbage", ""):
        with pytest.raises(OidcError):
            await client.finish(state=state, code="c", browser=forged)


async def test_a_flow_cookie_expires(key) -> None:
    provider = FakeProvider(key)
    now = [1_000_000.0]
    client = OidcClient(
        make_client(provider).config,
        get_json=provider.get_json,
        post_form=provider.post_form,
        jwk_client=provider.jwks(),
        clock=lambda: now[0],
    )
    state, browser = await begin(client, provider)
    now[0] += 11 * 60
    with pytest.raises(OidcError):
        await client.finish(state=state, code="c", browser=browser)


async def test_a_failed_discovery_is_not_retried_on_every_request(key) -> None:
    calls = []

    async def down(url: str) -> dict:
        calls.append(url)
        raise OSError("unreachable")

    provider = FakeProvider(key)
    client = OidcClient(make_client(provider).config, get_json=down, post_form=provider.post_form)
    for _ in range(20):
        with pytest.raises(OidcError):
            await client.start()
    assert len(calls) == 1


async def test_the_http_flow_ends_in_a_session(tmp_path, key) -> None:
    db = str(tmp_path / "sessions.db")
    repo = WorkItemRepository(db)
    await repo.init_db()
    store = PasskeyStore(db)
    await store.init_db()
    api = MagicMock()
    api.default_channel_id = None
    api.lineage_repo = None
    api.session_repo = None
    api.bot.get_channel.return_value = None
    api.bot.cogs = {}
    api._running_thread_ids.return_value = set()
    config = ConsoleAuthConfig(
        passkeys=False,
        oidc_issuer=ISSUER,
        oidc_client_id=CLIENT,
        allowed_emails=frozenset({OWNER}),
        origins=("http://localhost:8100",),
    )
    provider = FakeProvider(key)
    # http, because the test client is: a Secure cookie would not come back.
    oidc = make_client(provider, origin="http://localhost:8100")
    server = ConsoleServer(
        api, repo, ConsoleAuthenticator(config, sessions=store), port=0, oidc=oidc, store=store
    )
    async with TestClient(TestServer(server.app)) as client:
        status = await (await client.get("/console/api/auth/status")).json()
        assert status["methods"]["oidc"] == "Google"
        start = await client.get("/console/api/auth/oidc/start", allow_redirects=False)
        assert start.status == 302
        assert STATE_COOKIE in start.cookies
        assert start.cookies[STATE_COOKIE]["samesite"] == "Lax"
        query = parse_qs(urlsplit(start.headers["Location"]).query)
        provider.nonce = query["nonce"][0]
        done = await client.get(
            "/console/api/auth/oidc/callback",
            params={"state": query["state"][0], "code": "c"},
            allow_redirects=False,
        )
        assert done.status == 302 and done.headers["Location"] == "/"
        assert SESSION_COOKIE in done.cookies
        assert (await client.get("/console/api/board")).status == 200

        client.session.cookie_jar.clear()
        refused = await client.get(
            "/console/api/auth/oidc/callback",
            params={"state": "x", "code": "c"},
            allow_redirects=False,
        )
        assert refused.status == 302
        assert refused.headers["Location"].startswith("/?signin_error=")
        assert (await client.get("/console/api/board")).status == 401
