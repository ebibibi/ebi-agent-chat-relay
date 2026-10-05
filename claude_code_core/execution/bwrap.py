"""``bwrap``: the relay wraps the CLI in bubblewrap.

The boundary, in mount order (later mounts win, so the order is load-bearing):

1. ``--ro-bind / /`` — the whole host is visible and read-only.
2. ``--dev /dev``, ``--proc /proc`` (a new PID namespace's own), ``--tmpfs /tmp``.
3. Read-write binds: the working directory, the agent's own state
   (``CLAUDE_CONFIG_DIR`` or ``~/.claude`` + ``~/.claude.json``; ``CODEX_HOME`` or
   ``~/.codex``; pi's agent dir) and any operator-listed paths. They come after
   ``/tmp`` so a working directory under ``/tmp`` stays the real one.
4. Hidden paths: a directory becomes an empty tmpfs, a file becomes
   ``/dev/null``. They come last so a secret inside the working directory (the
   relay's own ``.env`` when the agent works in the relay's checkout) is still
   hidden.

bubblewrap always sets ``PR_SET_NO_NEW_PRIVS``, so ``sudo`` and other setuid
binaries cannot gain privileges. Network is shared unless the operator sets
``CCDB_BWRAP_UNSHARE_NET=1``: the agent has to reach its model API.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import replace

from .base import Launch, agent_state_paths, ensure_state_dirs, home_dir, resolve_binary, run_probe
from .config import BWRAP, BwrapSettings

PROBE_TIMEOUT_SECONDS = 10.0

# Probed once per binary per process. Only success is cached: a host that is
# fixed (userns enabled, bwrap installed) should not need a restart to notice.
_verified_binaries: set[str] = set()


def find_relay_dotenv(start: str | None = None) -> str | None:
    """The ``.env`` the relay loaded, found the way ``find_dotenv(usecwd=True)`` does."""
    current = os.path.abspath(start or os.getcwd())
    while True:
        candidate = os.path.join(current, ".env")
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def default_hide_paths(env: Mapping[str, str], relay_dotenv: str | None) -> tuple[str, ...]:
    home = home_dir(env)
    paths = [
        os.path.join(home, ".ssh"),
        "/var/run/docker.sock",
        "/run/docker.sock",
    ]
    if relay_dotenv:
        paths.insert(0, relay_dotenv)
    return tuple(paths)


def _unique(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            result.append(path)
    return result


def build_bwrap_argv(
    settings: BwrapSettings,
    backend: str,
    launch: Launch,
    *,
    relay_dotenv: str | None,
    exists: Callable[[str], bool] = os.path.exists,
    isdir: Callable[[str], bool] = os.path.isdir,
    realpath: Callable[[str], str] = os.path.realpath,
) -> tuple[str, ...]:
    """Build the bubblewrap command line around ``launch.argv``.

    ``exists``/``isdir``/``realpath`` are injectable so the mount list can be
    unit-tested without the paths existing on the test machine.
    """
    args: list[str] = [
        settings.binary,
        "--die-with-parent",
        "--unshare-pid",
        "--new-session",
    ]
    if settings.unshare_net:
        args.append("--unshare-net")
    args += ["--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]

    cwd = os.path.abspath(launch.cwd)
    writable = _unique(
        [realpath(p) for p in (cwd, *agent_state_paths(backend, launch.env), *settings.rw_paths)],
    )
    for path in writable:
        if exists(path):
            args += ["--bind", path, path]

    hidden = list(settings.hide_paths)
    if settings.hide_defaults:
        hidden = [*default_hide_paths(launch.env, relay_dotenv), *hidden]
        if launch.env.get("SSH_AUTH_SOCK"):
            hidden.append(launch.env["SSH_AUTH_SOCK"])
    # Resolve symlinks: ``/var/run`` is usually a link to ``/run``, and bwrap
    # cannot create a mount point through a link it has not resolved itself.
    for path in _unique([realpath(p) for p in hidden]):
        if not exists(path):
            continue
        if isdir(path):
            args += ["--tmpfs", path]
        else:
            args += ["--ro-bind", "/dev/null", path]

    args += ["--chdir", cwd, "--", *launch.argv]
    return tuple(args)


class BwrapEnvironment:
    name = BWRAP

    def __init__(self, settings: BwrapSettings) -> None:
        self._settings = settings

    async def preflight(self, backend: str, launch: Launch) -> str | None:
        binary = resolve_binary(self._settings.binary, launch.env)
        if binary is None:
            return (
                "The bwrap execution environment needs bubblewrap, and "
                f"{self._settings.binary!r} was not found on PATH."
            )
        if not os.path.isdir(launch.cwd):
            return f"The working directory {launch.cwd} does not exist."
        try:
            ensure_state_dirs(agent_state_paths(backend, launch.env))
        except OSError as exc:
            return f"Could not create the agent's state directory for bwrap: {exc}."
        if binary not in _verified_binaries:
            code, stderr = await run_probe(
                [binary, "--die-with-parent", "--ro-bind", "/", "/", "--", "true"],
                launch.env,
                PROBE_TIMEOUT_SECONDS,
            )
            if code != 0:
                detail = stderr.splitlines()[0] if stderr else f"exit code {code}"
                return (
                    "bubblewrap cannot create a sandbox on this host "
                    f"({detail}); unprivileged user namespaces may be disabled."
                )
            _verified_binaries.add(binary)
        return None

    def transform(self, backend: str, launch: Launch) -> Launch:
        settings = self._settings
        binary = resolve_binary(settings.binary, launch.env) or settings.binary
        argv = build_bwrap_argv(
            replace(settings, binary=binary),
            backend,
            launch,
            relay_dotenv=find_relay_dotenv(),
        )
        return replace(without_ssh_agent(settings, launch), argv=argv)


def without_ssh_agent(settings: BwrapSettings, launch: Launch) -> Launch:
    """Hiding ``~/.ssh`` means little if the agent can still use the ssh-agent.

    With the default hide list on, ``SSH_AUTH_SOCK`` is dropped from the child
    environment and the socket itself is hidden.
    """
    sock = launch.env.get("SSH_AUTH_SOCK")
    if not settings.hide_defaults or not sock:
        return launch
    env = {k: v for k, v in launch.env.items() if k != "SSH_AUTH_SOCK"}
    return replace(launch, env=env)
