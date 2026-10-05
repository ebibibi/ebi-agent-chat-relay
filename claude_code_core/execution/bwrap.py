"""``bwrap``: the relay wraps the CLI in bubblewrap.

The boundary, in mount order (later mounts win, so the order is load-bearing):

1. ``--ro-bind / /`` — the whole host is visible and read-only.
2. ``--dev /dev``, ``--proc /proc`` (a new PID namespace's own), ``--tmpfs /tmp``.
3. Read-write binds: the working directory, its git directory when the working
   directory is a linked worktree, the agent's own state (``CLAUDE_CONFIG_DIR``
   or ``~/.claude`` + ``~/.claude.json``; ``CODEX_HOME`` or ``~/.codex``; pi's
   agent dir) and any operator-listed paths. They come after ``/tmp`` so a
   working directory under ``/tmp`` stays the real one.
4. Read-only re-binds inside those writable trees: the files that make an agent
   *run something later* — Claude Code's ``settings.json`` (hooks), plugins,
   skills, agents and commands, ``~/.claude.json`` (MCP servers), Codex's
   ``config.toml``, rules, skills and plugins, pi's settings and extensions, and
   ``.git/hooks`` + ``.git/config``. Without this a sandboxed agent could plant a
   hook that the next *unsandboxed* run (a ``host`` thread, a scheduled job)
   executes. The most dangerous missing ones (Claude's ``settings.json``,
   Codex's ``config.toml``, extension directories) are created empty by
   preflight so they cannot be created inside the sandbox either.
5. Hidden paths: a directory becomes an empty tmpfs, a file becomes
   ``/dev/null``. They come last so a secret inside the working directory (the
   relay's own ``.env`` when the agent works in the relay's checkout) is still
   hidden. The defaults include the user's runtime directory
   (``/run/user/<uid>``: the systemd user bus — ``systemd-run --user`` would run
   a command outside the sandbox — and the gpg/ssh agent sockets) and the
   system D-Bus socket.

Namespaces: PID, IPC, UTS and (where available) cgroup are new. bubblewrap
always sets ``PR_SET_NO_NEW_PRIVS``, so ``sudo`` and other setuid binaries
cannot gain privileges. ``--new-session`` (``setsid``) is kept: it costs nothing
with piped stdio and stops ``TIOCSTI`` keystroke injection should the relay
ever run with a controlling terminal. The network namespace is shared unless the
operator sets ``CCDB_BWRAP_UNSHARE_NET=1``, because the agent has to reach its
model API — see docs/execution-environments.md for what that leaves reachable.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import replace

from .base import (
    CODEX_BACKENDS,
    Launch,
    agent_state_paths,
    ensure_state_dirs,
    home_dir,
    resolve_binary,
    run_probe,
)
from .config import BWRAP, BwrapSettings

PROBE_TIMEOUT_SECONDS = 10.0

# Variables that point the child at host services the mounts just hid.
HOST_SESSION_ENV = ("DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR")

# Agent configuration that can make a *later* run execute something: hooks,
# MCP servers, plugins, skills/commands with scripts, instructions. Paths are
# relative to the agent's state directory. ``(name, create_if_missing)``: a
# missing entry marked True is created on the host by preflight — an empty file
# (mode 0600) or directory (0700), which every CLI we measured treats like an
# absent one — so it can be bound read-only and the agent cannot create it inside
# the sandbox. Unmarked ones are protected only when they exist, because
# creating them empty could change behaviour.
CLAUDE_PROTECTED: tuple[tuple[str, bool], ...] = (
    ("settings.json", True),
    ("settings.local.json", False),
    ("CLAUDE.md", False),
    ("AGENTS.md", False),
    ("keybindings.json", False),
    (".claude.json", False),
    ("hooks", True),
    ("plugins", True),
    ("skills", True),
    ("agents", True),
    ("commands", True),
    ("output-styles", False),
    ("rules", False),
    ("scripts", False),
)
CODEX_PROTECTED: tuple[tuple[str, bool], ...] = (
    ("config.toml", True),
    ("AGENTS.md", False),
    ("AGENTS.override.md", False),
    ("hooks.json", False),
    ("hooks", False),
    ("rules", True),
    ("skills", True),
    ("plugins", False),
    ("prompts", False),
    ("packages", False),
)
PI_PROTECTED: tuple[tuple[str, bool], ...] = (
    ("settings.json", False),
    ("models.json", False),
    ("AGENTS.md", False),
    ("extensions", False),
    ("skills", False),
    ("prompts", False),
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


def runtime_dir(env: Mapping[str, str]) -> str | None:
    configured = env.get("XDG_RUNTIME_DIR")
    if configured:
        return configured
    getuid = getattr(os, "getuid", None)
    return f"/run/user/{getuid()}" if getuid else None


def default_hide_paths(env: Mapping[str, str], relay_dotenv: str | None) -> tuple[str, ...]:
    home = home_dir(env)
    paths = [
        os.path.join(home, ".ssh"),
        os.path.join(home, ".gnupg"),
        "/var/run/docker.sock",
        "/run/docker.sock",
        "/run/dbus/system_bus_socket",
    ]
    runtime = runtime_dir(env)
    if runtime:
        paths.append(runtime)
    if env.get("SSH_AUTH_SOCK"):
        paths.append(env["SSH_AUTH_SOCK"])
    if relay_dotenv:
        paths.insert(0, relay_dotenv)
    return tuple(paths)


def protected_entries(backend: str, env: Mapping[str, str]) -> tuple[tuple[str, bool], ...]:
    """Absolute agent-config paths to bind read-only, with create-if-missing flags."""
    entries: list[tuple[str, bool]] = []
    if backend == "claude":
        configured = env.get("CLAUDE_CONFIG_DIR")
        base = configured or os.path.join(home_dir(env), ".claude")
        names = [(n, c) for n, c in CLAUDE_PROTECTED if configured or n != ".claude.json"]
        entries += [(os.path.join(base, n), c) for n, c in names]
        if not configured:
            entries.append((os.path.join(home_dir(env), ".claude.json"), False))
    elif backend in CODEX_BACKENDS:
        base = env.get("CODEX_HOME") or os.path.join(home_dir(env), ".codex")
        entries += [(os.path.join(base, n), c) for n, c in CODEX_PROTECTED]
    elif backend == "pi":
        configured = env.get("PI_CODING_AGENT_DIR")
        base = configured or os.path.join(home_dir(env), ".pi", "agent")
        entries += [(os.path.join(base, n), c) for n, c in PI_PROTECTED]
    return tuple(entries)


def git_paths(cwd: str, read_text: Callable[[str], str | None]) -> tuple[str | None, str | None]:
    """Return (writable git dir outside cwd, git common dir) for ``cwd``.

    A linked worktree's ``.git`` is a file naming a directory inside the main
    repository's ``.git``; commits write objects and refs to the *common* dir,
    which lives outside the working directory. That directory is made writable
    so the agent can commit; its hooks and config are re-bound read-only.
    """
    dotgit = os.path.join(cwd, ".git")
    content = read_text(dotgit)
    if content is None:
        return None, dotgit  # a plain repository (or none): .git is inside cwd
    line = content.strip()
    if not line.startswith("gitdir:"):
        return None, None
    gitdir = line[len("gitdir:") :].strip()
    if not os.path.isabs(gitdir):
        gitdir = os.path.normpath(os.path.join(cwd, gitdir))
    common = gitdir
    commondir = read_text(os.path.join(gitdir, "commondir"))
    if commondir is not None:
        rel = commondir.strip()
        common = os.path.normpath(rel if os.path.isabs(rel) else os.path.join(gitdir, rel))
    return common, common


def create_protected_placeholders(entries: tuple[tuple[str, bool], ...]) -> None:
    """Create missing create-if-missing entries so they can be bound read-only.

    Done on the host before the sandbox starts, with ordinary permissions, so the
    operator can still edit the file later. ``O_EXCL`` never truncates an
    existing file, and a symlink in place of the name is left alone.
    """
    for path, create in entries:
        if not create or os.path.lexists(path) or not os.path.isdir(os.path.dirname(path)):
            continue
        if "." in os.path.basename(path):
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
        else:
            os.mkdir(path, 0o700)


def _read_small_file(path: str) -> str | None:
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read(4096)
    except OSError:
        return None


def _unique(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            result.append(path)
    return result


def _inside(path: str, parent: str) -> bool:
    return path == parent or path.startswith(parent.rstrip("/") + "/")


def build_bwrap_argv(
    settings: BwrapSettings,
    backend: str,
    launch: Launch,
    *,
    relay_dotenv: str | None,
    exists: Callable[[str], bool] = os.path.exists,
    isdir: Callable[[str], bool] = os.path.isdir,
    realpath: Callable[[str], str] = os.path.realpath,
    read_text: Callable[[str], str | None] = _read_small_file,
) -> tuple[str, ...]:
    """Build the bubblewrap command line around ``launch.argv``.

    The filesystem callables are injectable so the mount list can be
    unit-tested without the paths existing on the test machine.
    """
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

    cwd = os.path.abspath(launch.cwd)
    git_writable, git_common = git_paths(cwd, read_text)
    writable = _unique(
        [
            realpath(p)
            for p in (
                cwd,
                *((git_writable,) if git_writable else ()),
                *agent_state_paths(backend, launch.env),
                *settings.rw_paths,
            )
        ],
    )
    for path in writable:
        if exists(path):
            args += ["--bind", path, path]

    readonly: list[tuple[str, bool]] = []
    if settings.protect_config:
        readonly += list(protected_entries(backend, launch.env))
        if git_common:
            readonly += [
                (os.path.join(git_common, "hooks"), False),
                (os.path.join(git_common, "config"), False),
            ]
    readonly += [(p, False) for p in settings.ro_paths]
    for raw, create in readonly:
        path = realpath(raw)
        if not any(_inside(path, w) for w in writable):
            continue  # already read-only via the root bind
        if exists(path):
            args += ["--ro-bind", path, path]
        elif create and exists(os.path.dirname(path)):
            if "." in os.path.basename(path):
                args += ["--ro-bind", "/dev/null", path]
            else:
                args += ["--tmpfs", path, "--remount-ro", path]

    hidden = list(settings.hide_paths)
    if settings.hide_defaults:
        hidden = [*default_hide_paths(launch.env, relay_dotenv), *hidden]
    # Resolve symlinks: ``/var/run`` is usually a link to ``/run``, and bwrap
    # cannot create a mount point through a link it has not resolved itself.
    for path in _unique([realpath(p) for p in hidden]):
        if not exists(path):
            continue
        if any(_inside(w, path) for w in writable):
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
    dropped = {*HOST_SESSION_ENV, "SSH_AUTH_SOCK", "GPG_AGENT_INFO"}
    return {k: v for k, v in env.items() if k not in dropped}


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
            if self._settings.protect_config:
                create_protected_placeholders(protected_entries(backend, launch.env))
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
        binary = resolve_binary(settings.binary, launch.env) or settings.binary
        argv = build_bwrap_argv(
            replace(settings, binary=binary),
            backend,
            launch,
            relay_dotenv=find_relay_dotenv(),
        )
        return replace(launch, argv=argv, env=sandbox_env(settings, launch.env))
