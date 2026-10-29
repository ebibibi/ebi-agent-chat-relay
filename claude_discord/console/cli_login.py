"""Signing the agent CLIs in from the Console.

A relay in a container has no terminal, so a new conversation there fails
with ``Not logged in``. Both CLIs already have a sign-in that needs only a
browser somewhere else, and both work with plain pipes (measured):

* Codex (codex-cli 0.165.0): ``codex login --device-auth`` prints a URL and a
  one-time code, then waits until the code has been entered on that page.
* Claude Code (2.1.312): ``claude auth login --claudeai`` prints an authorize
  URL, then reads the code that page shows from stdin
  (``Paste code here if prompted >``, no newline after it).

:class:`CliLogin` runs that command, passes the URL and code to the Console,
forwards the pasted code to the CLI's stdin, and reports how it ended. It never
reads, stores or relays a credential: the CLI writes its own home exactly as it
does when someone runs it in a terminal. Only URLs on the vendors' sign-in
hosts are passed on, so a CLI that printed anything else cannot make the
Console show an arbitrary link.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from claude_code_core.child_env import STRIPPED_ENV_KEYS, strip_transport_credentials

logger = logging.getLogger(__name__)

BACKENDS = ("claude", "codex")

#: The device code and the authorize URL both expire after 15 minutes.
LOGIN_TIMEOUT_SECONDS = 15 * 60
#: ``auth status`` spawns a process; the board polls far more often than that changes.
STATUS_CACHE_SECONDS = 60.0
STATUS_TIMEOUT_SECONDS = 20.0
#: What a pasted code may look like: the Claude page shows ``<code>#<state>``.
MAX_CODE_LENGTH = 512
_CODE_RE = re.compile(r"^[A-Za-z0-9._~#\-]+$")
#: Output kept per flow. Enough for the prompt; a CLI cannot fill memory.
MAX_OUTPUT = 16_384

_LOGIN_HOSTS = {
    "claude": frozenset(
        {"claude.com", "claude.ai", "platform.claude.com", "console.anthropic.com"}
    ),
    "codex": frozenset({"auth.openai.com"}),
}
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]|\x1b\][^\x07]*\x07")
_URL_RE = re.compile(r"https://[^\s\"'<>]+")
_DEVICE_CODE_RE = re.compile(r"\b[A-Z0-9]{4,5}-[A-Z0-9]{4,8}\b")
_PASTE_PROMPT = "paste code"
_PROMPT_RE = re.compile(r"^.*paste code[^>]*>", re.IGNORECASE)

#: Running, then one of the terminal states.
RUNNING_STATES = frozenset({"starting", "waiting", "verifying"})


class LoginError(ValueError):
    """A request the login flow cannot act on (shown to the user as-is)."""


@dataclass(frozen=True)
class LoginFlow:
    """What the Console shows about one sign-in attempt. Never a credential."""

    backend: str
    state: str = "starting"
    url: str | None = None
    user_code: str | None = None
    needs_code: bool = False
    message: str | None = None
    started_at: int = field(default_factory=lambda: int(time.time()))
    expires_at: int | None = None

    @property
    def running(self) -> bool:
        return self.state in RUNNING_STATES

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def clean(text: str) -> str:
    """CLI output without colour codes or carriage returns."""
    return _ANSI_RE.sub("", text).replace("\r", "")


def login_url(backend: str, text: str) -> str | None:
    """The first sign-in URL in *text* on *backend*'s own hosts, else ``None``."""
    hosts = _LOGIN_HOSTS[backend]
    for match in _URL_RE.finditer(text):
        url = match.group(0).rstrip(".,)")
        host = (urlparse(url).hostname or "").lower()
        if host in hosts:
            return url
    return None


def device_code(text: str) -> str | None:
    """The one-time code ``codex login --device-auth`` asks to enter."""
    for line in text.splitlines():
        if "://" in line:
            continue
        match = _DEVICE_CODE_RE.search(line)
        if match:
            return match.group(0)
    return None


def parse_output(backend: str, text: str) -> dict[str, Any]:
    """URL, code and prompt state read from the CLI's output so far."""
    text = clean(text)
    found: dict[str, Any] = {"url": login_url(backend, text)}
    if backend == "codex":
        found["user_code"] = device_code(text)
    else:
        found["needs_code"] = _PASTE_PROMPT in text.lower()
    return found


def last_line(text: str, limit: int = 300) -> str | None:
    """The CLI's closing words, for the result message. URLs are dropped."""
    for line in reversed(clean(text).splitlines()):
        # The prompt has no newline, so the result lands on the same line.
        line = _PROMPT_RE.sub("", _URL_RE.sub("", line)).strip(" >")
        if line:
            return line[:limit]
    return None


def check_code(code: Any) -> str:
    """*code* as the CLI should receive it, or :class:`LoginError`."""
    if not isinstance(code, str):
        raise LoginError("code must be a string")
    code = code.strip()
    if not code or len(code) > MAX_CODE_LENGTH or not _CODE_RE.match(code):
        raise LoginError("that does not look like the code the sign-in page shows")
    return code


def cli_env(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment the agent CLIs run with, so the login lands in the same home.

    Same policy as :meth:`ClaudeRunner._build_env`: relay credentials stripped,
    the ``CCDB_CLI_ENV_FILE`` overlay applied (it may move ``CLAUDE_CONFIG_DIR``).
    """
    base = dict(os.environ if source is None else source)
    env = {k: v for k, v in base.items() if k not in STRIPPED_ENV_KEYS}
    overlay = base.get("CCDB_CLI_ENV_FILE")
    if overlay:
        try:
            for line in Path(overlay).read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    env[key] = value
        except OSError:
            logger.debug("console: CLI env overlay not found: %s", overlay)
    env = strip_transport_credentials(env)
    env["NO_COLOR"] = "1"
    env["BROWSER"] = "true"  # there is no browser here; never try to open one
    return env


def login_argv(backend: str, command: str) -> list[str]:
    if backend == "codex":
        return [command, "login", "--device-auth"]
    return [command, "auth", "login", "--claudeai"]


def status_argv(backend: str, command: str) -> list[str]:
    if backend == "codex":
        return [command, "login", "status"]
    return [command, "auth", "status", "--json"]


def parse_status(backend: str, returncode: int, out: str) -> dict[str, Any]:
    """``logged_in`` and ``method`` from the CLI's status command."""
    out = clean(out).strip()
    if backend == "claude":
        try:
            data = json.loads(out)
        except ValueError:
            return {"logged_in": None, "method": None}
        method = data.get("authMethod")
        return {
            "logged_in": bool(data.get("loggedIn")),
            "method": method if isinstance(method, str) and method != "none" else None,
        }
    match = re.search(r"Logged in using (.+)", out)
    if returncode == 0 and match:
        return {"logged_in": True, "method": match.group(1).strip()[:60]}
    if "not logged in" in out.lower():
        return {"logged_in": False, "method": None}
    return {"logged_in": None, "method": None}


class CliLogin:
    """At most one sign-in per backend, driven from the Console."""

    def __init__(
        self,
        commands: Mapping[str, str],
        *,
        env: Callable[[], dict[str, str]] = cli_env,
        timeout: float = LOGIN_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._commands = {b: c for b, c in commands.items() if b in BACKENDS and c}
        self._env = env
        self._timeout = timeout
        self._clock = clock
        self._flows: dict[str, LoginFlow] = {}
        self._procs: dict[str, asyncio.subprocess.Process] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._status: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = asyncio.Lock()

    @property
    def backends(self) -> tuple[str, ...]:
        return tuple(b for b in BACKENDS if b in self._commands)

    def _command(self, backend: str) -> str:
        if backend not in self._commands:
            raise LoginError(f"{backend} cannot be signed in from here")
        return self._commands[backend]

    async def read(self) -> dict[str, Any]:
        rows = []
        for backend in self.backends:
            flow = self._flows.get(backend)
            rows.append(
                {
                    "backend": backend,
                    **await self.status(backend),
                    "flow": flow.as_dict() if flow is not None else None,
                }
            )
        return {"backends": rows}

    async def status(self, backend: str, *, fresh: bool = False) -> dict[str, Any]:
        command = self._command(backend)
        cached = self._status.get(backend)
        now = time.monotonic()
        if not fresh and cached is not None and now - cached[0] < STATUS_CACHE_SECONDS:
            return cached[1]
        result = await self._probe(backend, command)
        self._status[backend] = (now, result)
        return result

    async def _probe(self, backend: str, command: str) -> dict[str, Any]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *status_argv(backend, command),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=self._env(),
            )
        except OSError:
            logger.warning("console: %s status could not start", backend, exc_info=True)
            return {"logged_in": None, "method": None}
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), STATUS_TIMEOUT_SECONDS)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()
            await proc.wait()
            logger.warning("console: %s status timed out", backend)
            return {"logged_in": None, "method": None}
        return parse_status(backend, proc.returncode or 0, out.decode(errors="replace"))

    async def start(self, backend: str) -> LoginFlow:
        command = self._command(backend)
        async with self._lock:
            current = self._flows.get(backend)
            if current is not None and current.running:
                return current
            try:
                proc = await asyncio.create_subprocess_exec(
                    *login_argv(backend, command),
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    env=self._env(),
                    start_new_session=True,
                )
            except OSError as exc:
                logger.warning("console: %s login could not start", backend, exc_info=True)
                raise LoginError(f"{backend} could not be started: {exc.strerror}") from exc
            now = int(self._clock())
            flow = LoginFlow(backend, started_at=now, expires_at=now + int(self._timeout))
            self._flows[backend] = flow
            self._procs[backend] = proc
            self._status.pop(backend, None)
            self._tasks[backend] = asyncio.ensure_future(self._follow(backend, proc))
            logger.info("console: %s sign-in started", backend)
            return flow

    async def submit(self, backend: str, code: Any) -> LoginFlow:
        self._command(backend)
        code = check_code(code)
        flow = self._flows.get(backend)
        proc = self._procs.get(backend)
        if flow is None or not flow.running or proc is None or proc.stdin is None:
            raise LoginError("no sign-in is waiting for a code; start again")
        if backend != "claude":
            raise LoginError(f"{backend} does not take a pasted code")
        try:
            proc.stdin.write(code.encode() + b"\n")
            await proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise LoginError("the sign-in already ended; start again") from exc
        flow = replace(flow, state="verifying", needs_code=False)
        self._flows[backend] = flow
        return flow

    async def cancel(self, backend: str) -> LoginFlow | None:
        self._command(backend)
        proc = self._procs.get(backend)
        flow = self._flows.get(backend)
        if proc is None or flow is None or not flow.running:
            return flow
        self._flows[backend] = replace(flow, state="cancelled", message=None)
        _kill(proc)
        task = self._tasks.get(backend)
        if task is not None:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        return self._flows[backend]

    async def close(self) -> None:
        for backend in list(self._procs):
            await self.cancel(backend)

    async def _follow(self, backend: str, proc: asyncio.subprocess.Process) -> None:
        output = ""
        try:
            async with asyncio.timeout(self._timeout):
                assert proc.stdout is not None
                while chunk := await proc.stdout.read(4096):
                    output = (output + chunk.decode(errors="replace"))[-MAX_OUTPUT:]
                    self._update(backend, output)
                await proc.wait()
        except TimeoutError:
            _kill(proc)
            await proc.wait()
            self._finish(backend, "expired", "the sign-in expired; start again")
            return
        if proc.returncode == 0:
            self._finish(backend, "succeeded", last_line(output) or "signed in")
            logger.info("console: %s sign-in succeeded", backend)
        else:
            self._finish(backend, "failed", last_line(output) or f"exit {proc.returncode}")
            logger.info("console: %s sign-in ended with exit %s", backend, proc.returncode)

    def _update(self, backend: str, output: str) -> None:
        flow = self._flows.get(backend)
        if flow is None or not flow.running:
            return
        found = parse_output(backend, output)
        url = found.get("url") or flow.url
        code = found.get("user_code") or flow.user_code
        needs_code = bool(found.get("needs_code")) and flow.state != "verifying"
        ready = url is not None and (backend == "claude" or code is not None)
        state = flow.state if flow.state == "verifying" else ("waiting" if ready else "starting")
        self._flows[backend] = replace(
            flow, url=url, user_code=code, needs_code=needs_code, state=state
        )

    def _finish(self, backend: str, state: str, message: str) -> None:
        self._procs.pop(backend, None)
        self._status.pop(backend, None)
        flow = self._flows.get(backend)
        if flow is None or flow.state == "cancelled":
            return
        self._flows[backend] = replace(
            flow, state=state, message=message, needs_code=False, url=None, user_code=None
        )


def _kill(proc: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError):
        proc.kill()


def login_commands(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """The CLIs the Console may sign in: ``CCDB_CONSOLE_CLI_LOGIN=0`` turns it off."""
    import shutil

    env = os.environ if source is None else source
    if (env.get("CCDB_CONSOLE_CLI_LOGIN") or "1").strip().lower() in {"0", "false", "no", "off"}:
        return {}
    wanted = {
        "claude": (env.get("CCDB_CLAUDE_COMMAND") or env.get("CLAUDE_COMMAND") or "claude"),
        "codex": (env.get("CCDB_CODEX_COMMAND") or "codex"),
    }
    return {b: c.strip() for b, c in wanted.items() if c.strip() and shutil.which(c.strip())}


__all__ = ["CliLogin", "LoginError", "LoginFlow", "cli_env", "login_commands"]
