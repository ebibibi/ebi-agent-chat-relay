"""Signing the agent CLIs in from the Console (device code / pasted code).

The fake CLIs print what the real ones printed when measured (claude 2.1.312,
codex-cli 0.165.0): an authorize URL and a ``Paste code here if prompted >``
prompt without a newline, and a coloured device-code block.
"""

from __future__ import annotations

import asyncio
import os
import stat
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from claude_discord.console import cli_login
from claude_discord.console.auth import ConsoleAuthConfig, ConsoleAuthenticator
from claude_discord.console.cli_login import CliLogin, LoginError, cli_env, parse_output
from claude_discord.console.server import ConsoleServer
from claude_discord.console.work_repo import WorkItemRepository

TOKEN = "t" * 40
AUTH = {"Authorization": f"Bearer {TOKEN}"}
WRITE = {**AUTH, "X-Console-Request": "1"}

CLAUDE_URL = (
    "https://claude.com/cai/oauth/authorize?code=true&client_id=x&response_type=code"
    "&redirect_uri=https%3A%2F%2Fplatform.claude.com%2Foauth%2Fcode%2Fcallback&state=s"
)

FAKE_CLAUDE = f"""
import json, os, sys
home = os.environ["FAKE_HOME"]
cred = os.path.join(home, "cred")
if sys.argv[1:3] == ["auth", "status"]:
    ok = os.path.exists(cred)
    print(json.dumps({{"loggedIn": ok, "authMethod": "claude.ai" if ok else "none"}}))
    sys.exit(0 if ok else 1)
if sys.argv[1:3] == ["auth", "login"]:
    print("Opening browser to sign in\\u2026")
    print("If the browser didn't open, visit: https://evil.example/phish")
    print("If the browser didn't open, visit: {CLAUDE_URL}")
    sys.stdout.write("Paste code here if prompted > ")
    sys.stdout.flush()
    code = sys.stdin.readline().strip()
    if code == "good#state":
        open(cred, "w").write("secret-token-never-shown")
        print("Login successful.")
        sys.exit(0)
    print("Login failed: That code wasn't accepted.")
    sys.exit(1)
sys.exit(2)
"""

FAKE_CODEX = """
import os, sys, time
home = os.environ["FAKE_HOME"]
cred = os.path.join(home, "codex-cred")
if sys.argv[1:3] == ["login", "status"]:
    if os.path.exists(cred):
        print("Logged in using ChatGPT"); sys.exit(0)
    print("Not logged in"); sys.exit(1)
if sys.argv[1:3] == ["login", "--device-auth"]:
    print("Follow these steps to sign in with ChatGPT using device code authorization:")
    print("1. Open this link in your browser and sign in to your account")
    print("   \\x1b[94mhttps://auth.openai.com/codex/device\\x1b[0m")
    print("2. Enter this one-time code \\x1b[90m(expires in 15 minutes)\\x1b[0m")
    print("   \\x1b[94mNL3K-S1TI9\\x1b[0m", flush=True)
    while not os.path.exists(os.path.join(home, "approved")):
        time.sleep(0.05)
    open(cred, "w").write("x")
    print("Successfully logged in")
    sys.exit(0)
sys.exit(2)
"""


def _script(path: Path, body: str) -> str:
    path.write_text(f"#!{sys.executable}\n{body}")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


@pytest.fixture
def fakes(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    commands = {
        "claude": _script(tmp_path / "claude", FAKE_CLAUDE),
        "codex": _script(tmp_path / "codex", FAKE_CODEX),
    }
    env = {**os.environ, "FAKE_HOME": str(home)}
    return commands, (lambda: dict(env)), home


async def _until(predicate, timeout: float = 10.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.02)


def _flow(login: CliLogin, backend: str):
    return login._flows[backend]


def test_parse_output_keeps_only_vendor_urls() -> None:
    text = f"visit: https://evil.example/x\nvisit: {CLAUDE_URL}\nPaste code here if prompted > "
    found = parse_output("claude", text)
    assert found == {"url": CLAUDE_URL, "needs_code": True}
    assert parse_output("codex", "https://evil.example/x ABCD-EFGH") == {
        "url": None,
        "user_code": None,
    }


def test_cli_env_strips_relay_secrets_and_applies_overlay(tmp_path) -> None:
    overlay = tmp_path / "overlay.env"
    overlay.write_text("# comment\nCLAUDE_CONFIG_DIR=/data/claude\n")
    env = cli_env(
        {
            "PATH": "/bin",
            "DISCORD_BOT_TOKEN": "bot",
            "CCDB_API_SECRET": "s",
            "CCDB_CLI_ENV_FILE": str(overlay),
        }
    )
    assert env["CLAUDE_CONFIG_DIR"] == "/data/claude"
    assert "DISCORD_BOT_TOKEN" not in env and "CCDB_API_SECRET" not in env
    assert env["BROWSER"] == "true"


def test_login_commands_can_be_turned_off(tmp_path) -> None:
    claude = _script(tmp_path / "claude", "")
    assert cli_login.login_commands(
        {"CCDB_CLAUDE_COMMAND": claude, "CCDB_CODEX_COMMAND": "/no"}
    ) == {"claude": claude}
    assert (
        cli_login.login_commands({"CCDB_CONSOLE_CLI_LOGIN": "0", "CCDB_CLAUDE_COMMAND": claude})
        == {}
    )


async def test_claude_pasted_code_signs_in(fakes) -> None:
    commands, env, home = fakes
    login = CliLogin(commands, env=env)
    assert (await login.status("claude"))["logged_in"] is False

    flow = await login.start("claude")
    assert flow.state == "starting"
    await _until(lambda: _flow(login, "claude").needs_code)
    flow = _flow(login, "claude")
    assert flow.state == "waiting" and flow.url == CLAUDE_URL

    with pytest.raises(LoginError):
        await login.submit("claude", "bad code\nwith newline")
    await login.submit("claude", " good#state ")
    await _until(lambda: not _flow(login, "claude").running)
    flow = _flow(login, "claude")
    assert flow.state == "succeeded" and flow.message == "Login successful."
    assert flow.url is None
    assert "secret-token" not in str(flow.as_dict())
    assert (await login.status("claude")) == {"logged_in": True, "method": "claude.ai"}


async def test_claude_wrong_code_fails(fakes) -> None:
    commands, env, _ = fakes
    login = CliLogin(commands, env=env)
    await login.start("claude")
    await _until(lambda: _flow(login, "claude").needs_code)
    await login.submit("claude", "wrong#state")
    await _until(lambda: not _flow(login, "claude").running)
    assert _flow(login, "claude").state == "failed"
    assert "wasn't accepted" in (_flow(login, "claude").message or "")


async def test_codex_device_code_and_cancel(fakes) -> None:
    commands, env, home = fakes
    login = CliLogin(commands, env=env)
    first = await login.start("codex")
    await _until(lambda: _flow(login, "codex").user_code is not None)
    flow = _flow(login, "codex")
    assert flow.url == "https://auth.openai.com/codex/device"
    assert flow.user_code == "NL3K-S1TI9" and flow.state == "waiting"
    # a second start while one is waiting shows the same attempt
    assert (await login.start("codex")).started_at == first.started_at
    with pytest.raises(LoginError):
        await login.submit("codex", "ABCD")

    cancelled = await login.cancel("codex")
    assert cancelled is not None and cancelled.state == "cancelled"

    await login.start("codex")
    await _until(lambda: _flow(login, "codex").user_code is not None)
    (home / "approved").write_text("")
    await _until(lambda: not _flow(login, "codex").running)
    assert _flow(login, "codex").state == "succeeded"
    assert (await login.status("codex")) == {"logged_in": True, "method": "ChatGPT"}


async def test_a_login_that_never_finishes_expires(fakes) -> None:
    commands, env, _ = fakes
    login = CliLogin(commands, env=env, timeout=0.5)
    await login.start("codex")
    await _until(lambda: not _flow(login, "codex").running)
    assert _flow(login, "codex").state == "expired"


@pytest.fixture
async def client(tmp_path, fakes):
    commands, env, _ = fakes
    db = str(tmp_path / "sessions.db")
    work = WorkItemRepository(db)
    await work.init_db()
    api = MagicMock()
    api.default_channel_id = None
    api.session_repo = None
    console = ConsoleServer(
        api,
        work,
        ConsoleAuthenticator(ConsoleAuthConfig(token=TOKEN)),
        port=0,
        cli_login=CliLogin(commands, env=env),
    )
    async with TestClient(TestServer(console.app)) as c:
        yield c
    await console.cli_login.close()


async def test_routes_need_auth_and_the_csrf_header(client) -> None:
    assert (await client.get("/console/api/cli-login")).status == 401
    res = await client.post("/console/api/cli-login/claude", json={"action": "start"}, headers=AUTH)
    assert res.status == 403


async def test_routes_start_and_read(client) -> None:
    data = await (await client.get("/console/api/cli-login", headers=AUTH)).json()
    assert [b["backend"] for b in data["backends"]] == ["claude", "codex"]
    assert data["backends"][0]["logged_in"] is False and data["backends"][0]["flow"] is None

    res = await client.post("/console/api/cli-login/codex", json={"action": "start"}, headers=WRITE)
    assert res.status == 200 and (await res.json())["flow"]["backend"] == "codex"

    async def code() -> str | None:
        rows = (await (await client.get("/console/api/cli-login", headers=AUTH)).json())["backends"]
        return rows[1]["flow"]["user_code"]

    async with asyncio.timeout(10):
        while await code() is None:
            await asyncio.sleep(0.05)
    res = await client.post(
        "/console/api/cli-login/codex", json={"action": "cancel"}, headers=WRITE
    )
    assert (await res.json())["flow"]["state"] == "cancelled"


async def test_routes_reject_bad_requests(client) -> None:
    post = client.post
    assert (
        await post("/console/api/cli-login/pi", json={"action": "start"}, headers=WRITE)
    ).status == 404
    assert (
        await post("/console/api/cli-login/claude", json={"action": "x"}, headers=WRITE)
    ).status == 400
    assert (await post("/console/api/cli-login/claude", json=[1], headers=WRITE)).status == 400
    res = await post(
        "/console/api/cli-login/claude", json={"action": "code", "code": "a"}, headers=WRITE
    )
    assert res.status == 409


async def test_routes_without_a_login_are_empty(tmp_path) -> None:
    db = str(tmp_path / "sessions.db")
    work = WorkItemRepository(db)
    await work.init_db()
    api = MagicMock()
    api.session_repo = None
    console = ConsoleServer(api, work, ConsoleAuthenticator(ConsoleAuthConfig(token=TOKEN)), port=0)
    async with TestClient(TestServer(console.app)) as c:
        assert (await (await c.get("/console/api/cli-login", headers=AUTH)).json()) == {
            "backends": []
        }
        res = await c.post("/console/api/cli-login/claude", json={"action": "start"}, headers=WRITE)
        assert res.status == 404
