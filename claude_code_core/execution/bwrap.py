"""``bwrap``: the relay wraps the CLI in bubblewrap.

The home directory is an **allowlist**, not a deny list. Everything outside
``$HOME`` is visible read-only (system directories, toolchains in ``/usr``);
``$HOME`` itself is an empty tmpfs, and only what the agent needs is bound back.
Credentials and data that live in the home directory — ``~/.config/gh``,
``~/.aws``, ``~/.azure``, ``~/.kube``, ``~/.docker``, ``~/.netrc``,
``~/.git-credentials``, ``~/.ssh``, other repositories, the relay's own clone
and ``.env`` — are absent by construction, without anyone having to list them.

Mount order (later mounts win, so the order is load-bearing):

1. ``--ro-bind / /``, ``--dev /dev``, ``--proc /proc``, ``--tmpfs /tmp``.
2. ``--tmpfs $HOME`` — empty and writable; whatever the CLI writes there that
   is not bound below (caches, logs) disappears with the sandbox.
3. Read-only binds into home: the CLI's own install (found from its PATH entry,
   its real path and its interpreter), ``~/.gitconfig`` / ``~/.config/git``, and
   operator-listed ``CCDB_BWRAP_RO_PATHS``.
4. Read-write binds: the working directory, the parts of a linked worktree's
   common git directory a commit writes (only when git's back-link confirms
   it), the agent's own state
   (``CLAUDE_CONFIG_DIR`` or ``~/.claude`` + ``~/.claude.json``; ``CODEX_HOME`` or
   ``~/.codex``; pi's dir) and ``CCDB_BWRAP_RW_PATHS``.
5. Read-only re-binds inside those: the agent configuration that can make a
   later, unsandboxed run execute something (Claude Code's ``settings.json``
   hooks, ``.claude.json`` MCP servers, plugins, skills; Codex's ``config.toml``,
   rules, skills; pi's settings and extensions), ``.git/hooks`` and
   ``.git/config``, and ``CCDB_BWRAP_RO_PATHS`` that fall inside writable binds.
6. Hidden paths that are still visible: the relay's ``.env*`` files, the Docker
   socket, the system D-Bus socket, the user runtime directory (systemd user
   bus — ``systemd-run --user`` would escape — and agent sockets), the
   ssh-agent socket, and ``CCDB_BWRAP_HIDE_PATHS``.

Namespaces: PID, IPC, UTS and (where available) cgroup are new; the network
namespace is shared because the agent has to reach its model API.
``--new-session`` stops ``TIOCSTI`` injection should a tty ever be attached.
bubblewrap sets ``PR_SET_NO_NEW_PRIVS``, so ``sudo`` cannot gain privileges.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import replace

from .base import ExecutionRefusedError, Launch, home_dir, resolve_binary, run_probe
from .config import BWRAP, BwrapSettings
from .guarded_paths import (
    GitLayout,
    GuardedMounts,
    git_layout,
    guard_refusal,
    guarded_mounts,
    inside,
    prepare_guarded,
)
from .toolchain import DEFAULT_HOME_READONLY, toolchain_paths

PROBE_TIMEOUT_SECONDS = 10.0

# Variables that point the child at host services the mounts hide.
HOST_SESSION_ENV = (
    "DBUS_SESSION_BUS_ADDRESS",
    "XDG_RUNTIME_DIR",
    "SSH_AUTH_SOCK",
    "GPG_AGENT_INFO",
)

OPT_OUT = ", or set CCDB_BWRAP_PROTECT_CONFIG=0 to accept that risk"

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


def relay_env_files(relay_dotenv: str | None) -> tuple[str, ...]:
    """The relay's ``.env`` and its siblings (``.env.bak-…``, ``.env.local``)."""
    if not relay_dotenv:
        return ()
    directory = os.path.dirname(relay_dotenv)
    try:
        names = os.listdir(directory)
    except OSError:
        return (relay_dotenv,)
    files = [
        os.path.join(directory, name)
        for name in sorted(names)
        if name.startswith(".env") and name != ".env.example"
    ]
    return tuple(files) or (relay_dotenv,)


def runtime_dir(env: Mapping[str, str]) -> str | None:
    configured = env.get("XDG_RUNTIME_DIR")
    if configured:
        return configured
    getuid = getattr(os, "getuid", None)
    return f"/run/user/{getuid()}" if getuid else None


def default_hide_paths(env: Mapping[str, str], relay_files: tuple[str, ...]) -> tuple[str, ...]:
    paths = [
        *relay_files,
        "/var/run/docker.sock",
        "/run/docker.sock",
        "/run/dbus/system_bus_socket",
    ]
    runtime = runtime_dir(env)
    if runtime:
        paths.append(runtime)
    if env.get("SSH_AUTH_SOCK"):
        paths.append(env["SSH_AUTH_SOCK"])
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
    launch: Launch,
    mounts: GuardedMounts,
    *,
    relay_files: tuple[str, ...] = (),
    toolchain: tuple[str, ...] = (),
    exists: Callable[[str], bool] = os.path.exists,
    isdir: Callable[[str], bool] = os.path.isdir,
    realpath: Callable[[str], str] = os.path.realpath,
) -> tuple[str, ...]:
    """Build the bubblewrap command line around ``launch.argv``.

    ``mounts`` comes from :func:`guarded_paths.guarded_mounts` (shared with the
    container environment) and ``toolchain`` from :func:`toolchain.toolchain_paths`.
    Filesystem lookups are injectable so the mount list can be unit-tested.
    """
    home = realpath(home_dir(launch.env))
    args: list[str] = [
        settings.binary,
        "--die-with-parent",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--unshare-cgroup-try",
        "--new-session",
    ]
    if settings.unshare_net:
        args.append("--unshare-net")
    args += ["--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp"]
    args += ["--tmpfs", home]

    bound: list[str] = []

    def bind(flag: str, path: str) -> None:
        args.extend([flag, path, path])
        bound.append(path)

    readable = _unique(
        [realpath(p) for p in toolchain]
        + [realpath(os.path.join(home, rel)) for rel in DEFAULT_HOME_READONLY]
    )
    for path in readable:
        if inside(path, home) and exists(path):
            bind("--ro-bind", path)

    writable = mounts.writable
    for path in writable:
        if exists(path):
            bind("--bind", path)

    # Read-only installs inside a writable bind (a CLI installed under its own
    # state directory or in the project's node_modules) would otherwise be
    # writable, and the next unsandboxed run would execute the change.
    for path in readable:
        if any(inside(path, w) for w in writable) and exists(path):
            bind("--ro-bind", path)

    if mounts.dotgit and exists(mounts.dotgit):
        bind("--ro-bind", mounts.dotgit)

    for path in mounts.readonly:
        if path in readable and not any(inside(path, w) for w in writable):
            continue  # bound read-only above
        if (inside(path, home) or any(inside(path, w) for w in writable)) and exists(path):
            bind("--ro-bind", path)

    hidden = list(settings.hide_paths)
    if settings.hide_defaults:
        hidden = [*default_hide_paths(launch.env, relay_files), *hidden]
    # Resolve symlinks: ``/var/run`` is usually a link to ``/run``, and bwrap
    # cannot create a mount point through a link it has not resolved itself.
    for path in _unique([realpath(p) for p in hidden]):
        if not exists(path):
            continue
        visible = not inside(path, home) or any(inside(path, b) for b in bound)
        if not visible:
            continue  # under the empty home already
        if any(inside(w, path) for w in writable):
            continue  # hiding it would hide the working directory itself
        if isdir(path):
            args += ["--tmpfs", path]
        else:
            args += ["--ro-bind", "/dev/null", path]

    cwd = realpath(os.path.abspath(launch.cwd))
    args += ["--chdir", cwd, "--", *launch.argv]
    return tuple(args)


def sandbox_env(settings: BwrapSettings, env: Mapping[str, str]) -> dict[str, str]:
    """Drop variables that point at host services the sandbox hides."""
    if not settings.hide_defaults:
        return dict(env)
    return {k: v for k, v in env.items() if k not in HOST_SESSION_ENV}


def _git(launch: Launch) -> GitLayout:
    return git_layout(os.path.realpath(launch.cwd), home_dir(launch.env))


def layout_refusal(settings: BwrapSettings, backend: str, launch: Launch) -> str | None:
    """Why this launch's binds would be unsafe, or None."""
    return guard_refusal(
        backend,
        launch,
        _git(launch),
        environment="bwrap",
        extra_paths=(*settings.rw_paths, *settings.ro_paths),
        protect=settings.protect_config,
        opt_out=OPT_OUT,
    )


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
        # Refuse before touching the state directory, so a refused layout
        # leaves no placeholders behind.
        problem = layout_refusal(self._settings, backend, launch)
        if problem:
            return problem
        try:
            prepare_guarded(backend, launch, _git(launch), protect=self._settings.protect_config)
        except OSError as exc:
            return f"Could not prepare the agent's state directory for bwrap: {exc}."
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
        # Re-checked here, immediately before the spawn, so a layout changed
        # since preflight (a symlink swapped in) is refused rather than bound.
        problem = layout_refusal(settings, backend, launch)
        if problem:
            raise ExecutionRefusedError(problem)
        mounts = guarded_mounts(
            backend,
            launch,
            _git(launch),
            rw_paths=settings.rw_paths,
            ro_paths=settings.ro_paths,
            protect=settings.protect_config,
        )
        binary = resolve_binary(settings.binary, launch.env) or settings.binary
        argv = build_bwrap_argv(
            replace(settings, binary=binary),
            launch,
            mounts,
            relay_files=relay_env_files(find_relay_dotenv()),
            toolchain=toolchain_paths(launch.argv[0], launch.env, untrusted=mounts.writable),
        )
        return replace(launch, argv=argv, env=sandbox_env(settings, launch.env))
