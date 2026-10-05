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
4. Read-write binds: the working directory, a linked worktree's common git
   directory (only when git's back-link confirms it), the agent's own state
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

from .base import (
    ExecutionRefusedError,
    Launch,
    agent_state_paths,
    ensure_state_dirs,
    home_dir,
    resolve_binary,
    run_probe,
)
from .bwrap_fs import (
    DEFAULT_HOME_READONLY,
    create_git_hooks_dir,
    create_placeholders,
    git_dirs,
    git_link_problem,
    inside,
    layout_problem,
    protected_entries,
    symlinked_entries,
    toolchain_paths,
)
from .config import BWRAP, BwrapSettings

PROBE_TIMEOUT_SECONDS = 10.0

# Variables that point the child at host services the mounts hide.
HOST_SESSION_ENV = (
    "DBUS_SESSION_BUS_ADDRESS",
    "XDG_RUNTIME_DIR",
    "SSH_AUTH_SOCK",
    "GPG_AGENT_INFO",
)

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
    backend: str,
    launch: Launch,
    *,
    relay_files: tuple[str, ...] = (),
    toolchain: tuple[str, ...] = (),
    git: tuple[str | None, str | None] = (None, None),
    exists: Callable[[str], bool] = os.path.exists,
    isdir: Callable[[str], bool] = os.path.isdir,
    realpath: Callable[[str], str] = os.path.realpath,
) -> tuple[str, ...]:
    """Build the bubblewrap command line around ``launch.argv``.

    Filesystem lookups are injectable so the mount list can be unit-tested
    without the paths existing on the test machine. ``git`` is
    :func:`bwrap_fs.git_dirs` for the working directory and ``toolchain`` is
    :func:`bwrap_fs.toolchain_paths` for the CLI.
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

    git_writable, git_protect = git
    cwd = realpath(os.path.abspath(launch.cwd))
    writable = _unique(
        [
            realpath(p)
            for p in (
                cwd,
                *((git_writable,) if git_writable else ()),
                *agent_state_paths(backend, launch.env),
                *settings.rw_paths,
            )
        ]
    )
    for path in writable:
        if exists(path):
            bind("--bind", path)

    # Read-only installs that sit inside a writable bind (a CLI installed under
    # its own state directory or in the project's node_modules) would otherwise
    # be writable, and the next unsandboxed run would execute the change.
    for path in readable:
        if any(inside(path, w) for w in writable) and exists(path):
            bind("--ro-bind", path)

    # Make the working directory's ``.git`` a mount point so it cannot be
    # renamed away and replaced by a fresh repository with its own hooks. A
    # worktree's ``.git`` file is also made read-only so it cannot be pointed
    # at a forged git directory for the next unsandboxed run.
    dotgit = os.path.join(cwd, ".git")
    if settings.protect_config and exists(dotgit):
        bind("--bind" if isdir(dotgit) else "--ro-bind", dotgit)

    protected: list[str] = []
    if settings.protect_config:
        protected += [path for path, _root in protected_entries(backend, launch.env)]
        if git_protect:
            protected += [
                os.path.join(git_protect, name) for name in ("hooks", "config", "config.worktree")
            ]
    for raw in [*protected, *settings.ro_paths]:
        path = realpath(raw)
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

    args += ["--chdir", cwd, "--", *launch.argv]
    return tuple(args)


def sandbox_env(settings: BwrapSettings, env: Mapping[str, str]) -> dict[str, str]:
    """Drop variables that point at host services the sandbox hides."""
    if not settings.hide_defaults:
        return dict(env)
    return {k: v for k, v in env.items() if k not in HOST_SESSION_ENV}


def layout_refusal(settings: BwrapSettings, backend: str, launch: Launch) -> str | None:
    """Why this launch's binds would be unsafe, or None."""
    home = home_dir(launch.env)
    problem = layout_problem(
        launch.cwd,
        agent_state_paths(backend, launch.env),
        (*settings.rw_paths, *settings.ro_paths),
        home,
    )
    if problem:
        return problem
    if settings.protect_config:
        links = symlinked_entries(protected_entries(backend, launch.env))
        cwd = os.path.realpath(launch.cwd)
        git_link = git_link_problem(cwd, git_dirs(cwd)[1])
        if git_link:
            links.append(git_link)
        if links:
            return (
                f"The bwrap execution environment cannot protect {links[0]}: it is a symbolic "
                "link, which the sandbox could replace; use a dedicated CLAUDE_CONFIG_DIR / "
                "CODEX_HOME for sandboxed threads, or set CCDB_BWRAP_PROTECT_CONFIG=0 to accept "
                "that risk."
            )
    return None


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
            ensure_state_dirs(agent_state_paths(backend, launch.env))
            if self._settings.protect_config:
                create_placeholders(protected_entries(backend, launch.env))
                create_git_hooks_dir(git_dirs(os.path.realpath(launch.cwd))[1])
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
        binary = resolve_binary(settings.binary, launch.env) or settings.binary
        argv = build_bwrap_argv(
            replace(settings, binary=binary),
            backend,
            launch,
            relay_files=relay_env_files(find_relay_dotenv()),
            toolchain=toolchain_paths(launch.argv[0], launch.env),
            git=git_dirs(os.path.realpath(launch.cwd)),
        )
        return replace(launch, argv=argv, env=sandbox_env(settings, launch.env))
