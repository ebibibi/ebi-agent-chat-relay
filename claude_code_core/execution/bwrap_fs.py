"""What the ``bwrap`` sandbox may see of the host filesystem, and checks on it.

The sandbox shows ``$HOME`` as an empty tmpfs and binds back an allowlist. This
module computes that allowlist and refuses layouts whose binds would leak more
than intended:

* the working directory, the agent's state and operator paths must not be the
  home directory, ``/`` or an ancestor of home;
* a linked worktree's git directory is honoured only when git's own back-link
  confirms it (the ``.git`` file is in the writable working directory, so a
  previous sandboxed run could have rewritten it);
* agent configuration inside the writable state directory is bound read-only,
  and a symlink at one of those names is refused — a mount cannot be placed on
  a symlink, so the sandbox could replace it.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Mapping

from .base import CODEX_BACKENDS, home_dir

# Agent configuration that can make a *later* run execute something (hooks, MCP
# servers, plugins, skills/commands with scripts) or carry instructions into it.
# Relative to the agent's state directory. Missing entries are created by
# preflight with neutral content (``{}`` for JSON, empty otherwise, an empty
# directory when there is no extension); Claude Code 2.1.289, codex-cli 0.160.0
# and pi 0.85 were run against exactly those placeholders and treat them as
# absent.
CLAUDE_PROTECTED: tuple[str, ...] = (
    "settings.json",
    "settings.local.json",
    "CLAUDE.md",
    "AGENTS.md",
    "keybindings.json",
    "hooks",
    "plugins",
    "skills",
    "agents",
    "commands",
    "output-styles",
    "rules",
    "scripts",
)
CODEX_PROTECTED: tuple[str, ...] = (
    "config.toml",
    "AGENTS.md",
    "AGENTS.override.md",
    "hooks.json",
    "hooks",
    "rules",
    "skills",
    "plugins",
    "prompts",
    "packages",
)
PI_PROTECTED: tuple[str, ...] = (
    "settings.json",
    "models.json",
    "AGENTS.md",
    "extensions",
    "skills",
    "prompts",
)

# Read-only by default from the real home: git needs an identity to commit.
DEFAULT_HOME_READONLY: tuple[str, ...] = (".gitconfig", ".config/git")


def inside(path: str, parent: str) -> bool:
    return path == parent or path.startswith(parent.rstrip("/") + "/")


def too_broad(real: str, home: str) -> bool:
    """A bind of ``real`` would expose the whole home directory (or more)."""
    return real == "/" or inside(home, real)


def protected_entries(backend: str, env: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    """(absolute path, state root) for every config entry to keep read-only."""
    home = home_dir(env)
    entries: list[tuple[str, str]] = []
    if backend == "claude":
        configured = env.get("CLAUDE_CONFIG_DIR")
        base = os.path.normpath(configured) if configured else os.path.join(home, ".claude")
        entries += [(os.path.join(base, n), base) for n in CLAUDE_PROTECTED]
        if configured:
            entries.append((os.path.join(base, ".claude.json"), base))
        else:
            entries.append((os.path.join(home, ".claude.json"), home))
    elif backend in CODEX_BACKENDS:
        base = os.path.normpath(env.get("CODEX_HOME") or os.path.join(home, ".codex"))
        entries += [(os.path.join(base, n), base) for n in CODEX_PROTECTED]
    elif backend == "pi":
        configured = env.get("PI_CODING_AGENT_DIR")
        root = os.path.normpath(configured) if configured else os.path.join(home, ".pi")
        base = root if configured else os.path.join(root, "agent")
        entries += [(os.path.join(base, n), root) for n in PI_PROTECTED]
    return tuple(entries)


def placeholder_content(path: str) -> bytes | None:
    """Neutral content for a missing entry; ``None`` means create a directory."""
    name = os.path.basename(path)
    if name.endswith(".json"):
        return b"{}\n"
    if "." in name:
        return b""
    return None


def create_placeholders(entries: tuple[tuple[str, str], ...]) -> None:
    """Create missing protected entries on the host so they can be bound read-only.

    Only directly inside the real state root (a symlinked parent is skipped),
    never following a symlink at the name (``O_NOFOLLOW``/``mkdir``) and never
    truncating (``O_EXCL``). Ordinary permissions, so the operator can edit
    them later.
    """
    for path, root in entries:
        parent = os.path.dirname(path)
        if os.path.lexists(path) or not _ensure_parent(parent, root):
            continue
        if os.path.islink(parent) or not inside(os.path.realpath(parent), os.path.realpath(root)):
            continue
        content = placeholder_content(path)
        if content is None:
            os.mkdir(path, 0o700)
            continue
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            os.write(fd, content)
        finally:
            os.close(fd)


def _ensure_parent(parent: str, root: str) -> bool:
    """Create the directories between ``root`` and ``parent`` (pi's ``agent/``).

    One component at a time with ``mkdir``, which never follows a symlink at the
    name, and stopping at the first component that is a symlink.
    """
    if not os.path.isdir(root) or os.path.islink(root) or not inside(parent, root):
        return False
    current = root
    for part in os.path.relpath(parent, root).split(os.sep):
        if part in ("", "."):
            continue
        current = os.path.join(current, part)
        if os.path.islink(current):
            return False
        if not os.path.lexists(current):
            os.mkdir(current, 0o700)
    return os.path.isdir(parent)


def symlinked_entries(entries: tuple[tuple[str, str], ...]) -> list[str]:
    """Protected names, or directories between them and their root, that are symlinks.

    A mount cannot be placed on a symlink, and a symlinked parent moves the
    whole entry out of reach of the read-only re-bind, so either one leaves the
    entry replaceable from inside the sandbox.
    """
    found: list[str] = []
    for path, root in entries:
        current = path
        while inside(current, root) and current != root:
            if os.path.islink(current):
                found.append(current)
                break
            current = os.path.dirname(current)
    return found


def git_link_problem(cwd: str, protect_dir: str | None) -> str | None:
    """The git paths that must not be symlinks for their protection to hold."""
    candidates = [os.path.join(cwd, ".git")]
    if protect_dir:
        candidates += [os.path.join(protect_dir, "hooks"), os.path.join(protect_dir, "config")]
    for path in candidates:
        if os.path.islink(path):
            return path
    return None


def create_git_hooks_dir(protect_dir: str | None) -> None:
    """A missing ``.git/hooks`` would be creatable inside the sandbox; create it first."""
    if not protect_dir or os.path.islink(protect_dir) or not os.path.isdir(protect_dir):
        return
    hooks = os.path.join(protect_dir, "hooks")
    if not os.path.lexists(hooks):
        os.mkdir(hooks, 0o755)


def _read_regular_file(path: str, limit: int = 4096) -> str | None:
    if os.path.islink(path) or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read(limit)
    except (OSError, UnicodeDecodeError):
        return None


def git_dirs(
    cwd: str, read_text: Callable[[str], str | None] = _read_regular_file
) -> tuple[str | None, str | None]:
    """Return (extra writable git dir, git dir whose hooks/config to protect).

    A plain repository's ``.git`` is inside the working directory, so nothing
    extra is writable. A linked worktree's ``.git`` is a file naming
    ``<common>/worktrees/<name>``; commits write to ``<common>``, outside the
    working directory. That file is attacker-writable, so ``<common>`` is only
    trusted when the layout is the one git creates and git's back-link
    (``<common>/worktrees/<name>/gitdir``) names this working directory.
    """
    dotgit = os.path.join(cwd, ".git")
    if os.path.islink(dotgit):
        return None, None
    content = read_text(dotgit)
    if content is None:
        return None, dotgit
    line = content.strip().splitlines()[0] if content.strip() else ""
    if not line.startswith("gitdir:"):
        return None, None
    named = line[len("gitdir:") :].strip()
    gitdir = os.path.realpath(named if os.path.isabs(named) else os.path.join(cwd, named))
    worktrees = os.path.dirname(gitdir)
    common = os.path.dirname(worktrees)
    if os.path.basename(worktrees) != "worktrees":
        return None, None
    backlink = read_text(os.path.join(gitdir, "gitdir"))
    if backlink is None or os.path.realpath(backlink.strip()) != os.path.realpath(dotgit):
        return None, None
    if not (os.path.isdir(os.path.join(common, "objects")) and os.path.exists(f"{common}/HEAD")):
        return None, None
    return common, common


def _install_root(real: str, home: str) -> str:
    marker = "/node_modules/"
    if marker in real:
        return real[: real.rindex(marker) + len(marker) - 1]
    parent = os.path.dirname(real)
    if os.path.basename(parent) == "bin":
        prefix = os.path.dirname(parent)
        if inside(prefix, home) and prefix not in (home, os.path.join(home, ".local")):
            return prefix
    return parent


def _interpreter(real: str) -> str | None:
    try:
        with open(real, "rb") as handle:
            first = handle.readline(256)
    except OSError:
        return None
    if not first.startswith(b"#!"):
        return None
    words = first[2:].decode("utf-8", errors="replace").split()
    if not words:
        return None
    if os.path.basename(words[0]) == "env":
        rest = [w for w in words[1:] if not w.startswith("-")]
        return rest[0] if rest else None
    return words[0]


def _link_chain_dirs(path: str, limit: int = 40) -> list[str]:
    """Directories of every hop in a symlink chain.

    ``~/.local/bin/x`` → ``~/.npm-global/bin/x`` → … each lives in a different directory.

    Each hop has to be visible inside the sandbox or the chain breaks there.
    """
    dirs: list[str] = []
    for _ in range(limit):
        dirs.append(os.path.dirname(path))
        if not os.path.islink(path):
            break
        target = os.readlink(path)
        path = os.path.normpath(os.path.join(os.path.dirname(path), target))
    return dirs


def toolchain_paths(argv0: str, env: Mapping[str, str]) -> tuple[str, ...]:
    """Directories under ``$HOME`` the CLI needs to start, read-only.

    The directory of the command as found on PATH (so the symlink resolves),
    the install root of its real file (a versions directory, a global
    ``node_modules``, a toolchain prefix) and, for a script, the same for its
    interpreter. Paths outside home are already visible read-only.
    """
    home = os.path.realpath(home_dir(env))
    found: list[str] = []

    def add(command: str, depth: int) -> None:
        exe = command if os.sep in command else shutil.which(command, path=env.get("PATH"))
        if not exe:
            return
        exe = os.path.abspath(exe)
        real = os.path.realpath(exe)
        for path in (*_link_chain_dirs(exe), _install_root(real, home)):
            path = os.path.realpath(path)
            if inside(path, home) and not too_broad(path, home) and path not in found:
                found.append(path)
        if depth == 0:
            interpreter = _interpreter(real)
            if interpreter:
                add(interpreter, 1)

    add(argv0, 0)
    return tuple(found)


def layout_problem(
    cwd: str,
    state_paths: tuple[str, ...],
    extra_paths: tuple[str, ...],
    home: str,
) -> str | None:
    """One sentence if a bind would expose the whole home directory or more."""
    real_home = os.path.realpath(home)
    for label, path in (
        ("working directory", cwd),
        *(("agent state directory", p) for p in state_paths),
        *(("configured path", p) for p in extra_paths),
    ):
        if too_broad(os.path.realpath(path), real_home):
            return (
                f"The bwrap execution environment cannot use {path} as its {label}: it is the "
                "home directory or contains it, which would expose everything the sandbox hides."
            )
    return None
