"""Passkey sign-in, end to end over HTTP, with a software authenticator."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.console.auth import (
    SESSION_COOKIE,
    ConsoleAuthConfig,
    ConsoleAuthenticator,
    ConsoleAuthError,
)
from claude_discord.console.passkey_store import PasskeyStore
from claude_discord.console.passkeys import PasskeyError, PasskeyService
from claude_discord.console.server import CSRF_HEADER, ConsoleServer
from claude_discord.console.work_repo import WorkItemRepository

from .soft_authenticator import SoftAuthenticator

HOST = "localhost:8100"
ORIGIN = f"http://{HOST}"
HEADERS = {"Host": HOST, CSRF_HEADER: "1"}


class Console:
    """A running console plus the handles a test needs."""

    def __init__(self, client: TestClient, service: PasskeyService) -> None:
        self.client = client
        self.service = service

    async def post(self, path: str, body: dict | None = None, **kwargs):
        headers = {**HEADERS, **kwargs.pop("headers", {})}
        return await self.client.post(
            f"/console/api/{path}", json=body or {}, headers=headers, **kwargs
        )

    async def get(self, path: str):
        return await self.client.get(f"/console/api/{path}", headers={"Host": HOST})

    async def register(self, device: SoftAuthenticator, code: str | None, origin: str = ORIGIN):
        options = await self.post("auth/passkey/register/options", {"code": code})
        assert options.status == 200, await options.text()
        data = await options.json()
        credential = device.register(data["options"], origin)
        return await self.post(
            "auth/passkey/register/verify",
            {"ticket": data["ticket"], "credential": credential, "name": "laptop"},
        )

    async def login(self, device: SoftAuthenticator, origin: str = ORIGIN):
        data = await (await self.post("auth/passkey/login/options")).json()
        credential = device.login(data["options"], origin, rp_id="localhost")
        return await self.post(
            "auth/passkey/login/verify", {"ticket": data["ticket"], "credential": credential}
        )


@pytest.fixture
async def console(tmp_path):
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
    service = PasskeyService(store)
    authenticator = ConsoleAuthenticator(ConsoleAuthConfig(), sessions=store)
    server = ConsoleServer(api, repo, authenticator, port=0, passkeys=service)
    async with TestClient(TestServer(server.app)) as client:
        yield Console(client, service)


async def test_a_fresh_console_needs_setup_and_lets_nobody_in(console) -> None:
    status = await (await console.get("auth/status")).json()
    assert status == {
        "signed_in": False,
        "methods": {"passkey": True, "access": False, "token": False},
        "needs_setup": True,
    }
    assert (await console.get("board")).status == 401


async def test_the_setup_code_registers_the_first_passkey_and_signs_in(console) -> None:
    response = await console.register(SoftAuthenticator(), console.service.issue_code())
    assert response.status == 200, await response.text()
    assert SESSION_COOKIE in response.cookies
    cookie = response.cookies[SESSION_COOKIE]
    assert cookie["httponly"] and cookie["samesite"] == "Strict"
    assert (await console.get("board")).status == 200
    assert (await (await console.get("me")).json())["identity"] == "passkey:laptop"


@pytest.mark.parametrize("code", [None, "", "WRONG-CODE1"])
async def test_registration_without_a_valid_code_is_refused(console, code) -> None:
    console.service.issue_code()
    response = await console.post("auth/passkey/register/options", {"code": code})
    assert response.status == 400


async def test_a_setup_code_works_once(console) -> None:
    code = console.service.issue_code()
    assert (await console.register(SoftAuthenticator(), code)).status == 200
    console.client.session.cookie_jar.clear()
    response = await console.post("auth/passkey/register/options", {"code": code})
    assert response.status == 400


async def test_the_code_is_typed_forgivingly(console) -> None:
    code = console.service.issue_code().lower().replace("-", " ")
    assert (await console.register(SoftAuthenticator(), code)).status == 200


async def test_a_registered_passkey_signs_in(console) -> None:
    device = SoftAuthenticator()
    await console.register(device, console.service.issue_code())
    console.client.session.cookie_jar.clear()
    assert (await console.get("board")).status == 401
    response = await console.login(device)
    assert response.status == 200, await response.text()
    assert (await console.get("board")).status == 200


async def test_an_unknown_passkey_is_refused(console) -> None:
    await console.register(SoftAuthenticator(), console.service.issue_code())
    console.client.session.cookie_jar.clear()
    assert (await console.login(SoftAuthenticator())).status == 400


async def test_a_passkey_without_user_verification_is_refused(console) -> None:
    device = SoftAuthenticator(user_verified=False)
    response = await console.register(device, console.service.issue_code())
    assert response.status == 400


async def test_a_signature_made_for_another_site_is_refused(console) -> None:
    device = SoftAuthenticator()
    await console.register(device, console.service.issue_code())
    console.client.session.cookie_jar.clear()
    assert (await console.login(device, origin="https://evil.example")).status == 400


async def test_a_sign_in_ticket_cannot_be_replayed(console) -> None:
    device = SoftAuthenticator()
    await console.register(device, console.service.issue_code())
    data = await (await console.post("auth/passkey/login/options")).json()
    credential = device.login(data["options"], ORIGIN, rp_id="localhost")
    body = {"ticket": data["ticket"], "credential": credential}
    assert (await console.post("auth/passkey/login/verify", body)).status == 200
    assert (await console.post("auth/passkey/login/verify", body)).status == 400


async def test_sign_in_routes_need_the_csrf_header(console) -> None:
    response = await console.client.post(
        "/console/api/auth/passkey/login/options", json={}, headers={"Host": HOST}
    )
    assert response.status == 403


async def test_a_signed_in_device_invites_another(console) -> None:
    await console.register(SoftAuthenticator(), console.service.issue_code())
    code = (await (await console.post("passkeys/invite")).json())["code"]
    console.client.session.cookie_jar.clear()
    phone = SoftAuthenticator()
    assert (await console.register(phone, code)).status == 200
    listed = (await (await console.get("passkeys")).json())["passkeys"]
    assert len(listed) == 2
    assert all("public_key" not in p for p in listed)


async def test_inviting_needs_a_session(console) -> None:
    assert (await console.post("passkeys/invite")).status == 401


async def test_removing_a_passkey_signs_out_its_sessions(console) -> None:
    await console.register(SoftAuthenticator(), console.service.issue_code())
    laptop_jar = list(console.client.session.cookie_jar)
    code = (await (await console.post("passkeys/invite")).json())["code"]
    console.client.session.cookie_jar.clear()
    await console.register(SoftAuthenticator(), code)
    listed = (await (await console.get("passkeys")).json())["passkeys"]
    laptop = listed[0]["id"]
    response = await console.client.delete(f"/console/api/passkeys/{laptop}", headers=HEADERS)
    assert response.status == 200
    console.client.session.cookie_jar.clear()
    for cookie in laptop_jar:
        console.client.session.cookie_jar.update_cookies({cookie.key: cookie.value})
    assert (await console.get("board")).status == 401


async def test_the_last_passkey_cannot_be_removed(console) -> None:
    await console.register(SoftAuthenticator(), console.service.issue_code())
    only = (await (await console.get("passkeys")).json())["passkeys"][0]["id"]
    response = await console.client.delete(f"/console/api/passkeys/{only}", headers=HEADERS)
    assert response.status == 409


async def test_sign_out_ends_the_session(console) -> None:
    await console.register(SoftAuthenticator(), console.service.issue_code())
    jar = list(console.client.session.cookie_jar)
    assert (await console.post("auth/logout")).status == 200
    for cookie in jar:
        console.client.session.cookie_jar.update_cookies({cookie.key: cookie.value})
    assert (await console.get("board")).status == 401


async def test_a_new_setup_code_is_refused_once_a_passkey_exists(console) -> None:
    assert (await console.post("auth/setup-code")).status == 200
    await console.register(SoftAuthenticator(), console.service.issue_code())
    assert (await console.post("auth/setup-code")).status == 409


async def test_wrong_guesses_do_not_burn_the_owners_code(tmp_path) -> None:
    service = PasskeyService(PasskeyStore(str(tmp_path / "db")))
    await service.store.init_db()
    code = service.issue_code()
    for _ in range(50):
        with pytest.raises(PasskeyError):
            await service.registration_options(ORIGIN, signed_in=False, code="AAAAA-AAAAA")
    await service.registration_options(ORIGIN, signed_in=False, code=code)


async def test_codes_expire(tmp_path) -> None:
    now = [0.0]
    service = PasskeyService(PasskeyStore(str(tmp_path / "db")), clock=lambda: now[0])
    await service.store.init_db()
    code = service.issue_code()
    now[0] += 16 * 60
    with pytest.raises(PasskeyError):
        await service.registration_options(ORIGIN, signed_in=False, code=code)


async def test_enrollment_is_logged_only_while_no_passkey_exists(tmp_path, caplog) -> None:
    store = PasskeyStore(str(tmp_path / "db"))
    await store.init_db()
    service = PasskeyService(store)
    assert await service.open_enrollment() is not None
    await store.add(credential_id=b"x", public_key=b"k", sign_count=0, rp_id="h", name="n")
    assert await service.open_enrollment() is None
    assert await service.open_enrollment(force=True) is not None


async def test_sign_in_cannot_be_locked_out_by_flooding(console) -> None:
    device = SoftAuthenticator()
    await console.register(device, console.service.issue_code())
    console.client.session.cookie_jar.clear()
    for _ in range(300):  # more than the rate limit and the ceremony table
        assert (await console.post("auth/passkey/login/options")).status == 200
    assert (await console.login(device)).status == 200


async def test_setup_code_guessing_is_rate_limited(console) -> None:
    statuses = [
        (await console.post("auth/passkey/register/options", {"code": "AAAAA-AAAAA"})).status
        for _ in range(130)
    ]
    assert 429 in statuses


async def test_only_one_logged_setup_code_is_live(tmp_path) -> None:
    service = PasskeyService(PasskeyStore(str(tmp_path / "db")))
    await service.store.init_db()
    first = await service.open_enrollment()
    second = await service.open_enrollment()
    assert first and second and first != second
    with pytest.raises(PasskeyError):
        await service.registration_options(ORIGIN, signed_in=False, code=first)
    await service.registration_options(ORIGIN, signed_in=False, code=second)


async def test_a_stale_token_does_not_hide_a_passkey_session(tmp_path) -> None:
    store = PasskeyStore(str(tmp_path / "db"))
    await store.init_db()
    auth = ConsoleAuthenticator(ConsoleAuthConfig(token="t" * 40), sessions=store)
    session = await store.create_session("passkey:laptop", ConsoleAuthConfig().session_lifetime)
    headers = {"Authorization": "Bearer stale-token"}
    assert await auth.identify(headers, {SESSION_COOKIE: session}) == "passkey:laptop"


async def test_the_last_passkey_can_go_when_another_way_in_exists(tmp_path) -> None:
    store = PasskeyStore(str(tmp_path / "db"))
    await store.init_db()
    only = await store.add(credential_id=b"x", public_key=b"k", sign_count=0, rp_id="h", name="n")
    assert await store.delete(only.id, keep_one=True) == "last"
    assert await store.delete("nope", keep_one=False) == "missing"
    assert await store.delete(only.id, keep_one=False) == "deleted"
    assert await store.count() == 0


async def test_turning_passkeys_off_ends_their_sessions(tmp_path, monkeypatch) -> None:
    from claude_discord.console.server import maybe_start_console

    db = str(tmp_path / "sessions.db")
    store = PasskeyStore(db)
    await store.init_db()
    session = await store.create_session("passkey:laptop", ConsoleAuthConfig().session_lifetime)
    api = MagicMock()
    api.session_repo.db_path = db
    monkeypatch.setenv("CCDB_CONSOLE_PORT", "0")
    monkeypatch.setenv("CCDB_CONSOLE_PASSKEYS", "0")
    monkeypatch.setenv("CCDB_CONSOLE_TOKEN", "t" * 40)
    console = await maybe_start_console(api)
    assert console is not None
    try:
        with pytest.raises(ConsoleAuthError):
            await console.auth.identify({}, {SESSION_COOKIE: session})
    finally:
        await console.stop()
