"""What an isolating environment (``bwrap``, ``container``) mounts, and what it refuses.

Both environments give the agent a writable working directory and its own
writable state directory. Both therefore face the same two problems, and this
module is the one place they are solved:

* **Persistence.** Parts of the state directory make an agent *run something
  later* — Claude Code's ``settings.json`` hooks, ``.claude.json`` MCP servers,
  plugins and skills; Codex's ``config.toml``; pi's settings and extensions —
  and so do ``.git/hooks`` and ``.git/config``. A sandboxed agent that could
  edit them would arm the next *unsandboxed* run. They are mounted read-only
  inside the writable directories, missing ones are created first, and a
  symlink at one of those names (which a mount cannot cover) is refused.
* **Attacker-controlled paths.** Anything the agent could write in a previous
  sandboxed run may have been rewritten: the ``.git`` file of a linked worktree,
  symlinks in the state directory. Such paths are trusted only after
  validation, and binds that would expose the whole home directory are refused.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .base import CODEX_BACKENDS, Launch, agent_state_paths, ensure_state_dirs, home_dir

# Agent configuration that can make a later run execute something or carry
# instructions into it, relative to the state directory. Missing entries are
# created with neutral content (``{}`` for JSON, empty otherwise, an empty
# directory when there is no extension); Claude Code 2.1.289, codex-cli 0.160.0
# and pi 0.85 were run against exactly those placeholders and treat them as
# absent. An *empty* ``.claude.json`` is reported as corrupted, hence ``{}``.
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


def inside(path: str, parent: str) -> bool:
    return path == parent or path.startswith(parent.rstrip("/") + "/")


def too_broad(real: str, home: str) -> bool:
    """A mount of ``real`` would expose the whole home directory (or more)."""
    return real == "/" or inside(home, real)


# ---------------------------------------------------------------- agent config


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


def _ensure_parent(parent: str, root: str) -> bool:
    """Create the directories between ``root`` and ``parent`` (pi's ``agent/``).

    One component at a time with ``mkdir``, which never follows a symlink at the
    name, stopping at the first component that is a symlink.
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


def create_placeholders(entries: tuple[tuple[str, str], ...]) -> None:
    """Create missing protected entries on the host so they can be mounted read-only.

    Never through a symlinked parent, never following a symlink at the name
    (``O_NOFOLLOW``/``mkdir``), never truncating (``O_EXCL``). Files are 0600 and
    directories 0700, the modes the CLIs use for their own state.
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


def symlinked_entries(entries: tuple[tuple[str, str], ...]) -> list[str]:
    """Protected names, or directories between them and their root, that are symlinks."""
    found: list[str] = []
    for path, root in entries:
        current = path
        while inside(current, root) and current != root:
            if os.path.islink(current):
                found.append(current)
                break
            current = os.path.dirname(current)
    return found


# ---------------------------------------------------------------- git


def _read_regular_file(path: str, limit: int = 4096) -> str | None:
    if os.path.islink(path) or not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            return handle.read(limit)
    except (OSError, UnicodeDecodeError):
        return None


@dataclass(frozen=True)
class GitLayout:
    """How the working directory's repository is mounted.

    ``dotgit`` is mounted **read-only** as a mount point, so it can be neither
    edited nor renamed away. For a plain repository that is the whole ``.git``
    directory: git keeps far more than ``hooks`` and ``config`` there that a
    later run would act on (``commondir`` redirects git to another directory
    entirely, ``modules/*`` holds submodule hooks), and its lock-file writes
    need the directory itself writable, so no finer split is safe. The agent
    can edit files but not commit; commits happen in linked worktrees, which is
    how the relay runs sessions anyway. For a linked worktree ``dotgit`` is the
    one-line ``.git`` file; ``writable`` are the parts of the common directory
    and the worktree's own git dir a commit writes, and ``readonly`` the parts
    git reads or that would redirect or execute something later. The rest of a
    common directory under ``$HOME`` is never mounted.
    """

    dotgit: str | None = None
    writable: tuple[str, ...] = ()
    readonly: tuple[str, ...] = ()
    hooks_dir: str | None = None
    create_dirs: tuple[str, ...] = ()
    create_files: tuple[str, ...] = ()

    def guarded_paths(self) -> tuple[str, ...]:
        """Paths that must not be symlinks for the mounts to mean what they say."""
        paths = [p for p in (self.dotgit, self.hooks_dir) if p]
        return (*paths, *self.writable, *self.readonly)


WORKTREE_WRITABLE = ("objects", "refs", "logs")
WORKTREE_READONLY = (
    "hooks",
    "config",
    "config.worktree",
    "packed-refs",
    "HEAD",
    "info",
    "shallow",
    "modules",
)
GITDIR_READONLY = ("commondir", "gitdir", "config.worktree")


def git_layout(
    cwd: str,
    home: str,
    read_text: Callable[[str], str | None] = _read_regular_file,
) -> GitLayout:
    """Validate and describe the repository of ``cwd`` (already ``realpath``-ed).

    A linked worktree's ``.git`` file is in the writable working directory, so
    a previous sandboxed run could have rewritten it. Its common directory is
    trusted only when all of these hold: the git dir it names resolves to
    ``<common>/worktrees/<name>``; that directory's ``gitdir`` back-link names
    this ``.git``; ``<common>`` has ``HEAD``, ``objects`` and ``refs``; and
    ``<common>`` is not ``/``, ``$HOME``, or an ancestor of ``$HOME`` or of the
    working directory. Otherwise nothing outside the working directory is
    mounted for git.
    """
    dotgit = os.path.join(cwd, ".git")
    if os.path.islink(dotgit) or not os.path.lexists(dotgit):
        return GitLayout()
    if os.path.isdir(dotgit):
        return GitLayout(dotgit=dotgit)
    plain_file = GitLayout(dotgit=dotgit)
    content = read_text(dotgit)
    if content is None or not content.strip():
        return plain_file
    line = content.strip().splitlines()[0]
    if not line.startswith("gitdir:"):
        return plain_file
    named = line[len("gitdir:") :].strip()
    gitdir = os.path.realpath(named if os.path.isabs(named) else os.path.join(cwd, named))
    worktrees = os.path.dirname(gitdir)
    common = os.path.dirname(worktrees)
    real_home = os.path.realpath(home)
    if (
        os.path.basename(worktrees) != "worktrees"
        or too_broad(common, real_home)
        or inside(cwd, common)
        or not os.path.isfile(os.path.join(common, "HEAD"))
        or not os.path.isdir(os.path.join(common, "objects"))
        or not os.path.isdir(os.path.join(common, "refs"))
    ):
        return plain_file
    backlink = read_text(os.path.join(gitdir, "gitdir"))
    if backlink is None or os.path.realpath(backlink.strip()) != os.path.realpath(dotgit):
        return plain_file
    return GitLayout(
        dotgit=dotgit,
        writable=(*(os.path.join(common, n) for n in WORKTREE_WRITABLE), gitdir),
        readonly=(
            *(os.path.join(common, n) for n in WORKTREE_READONLY if n != "hooks"),
            # Inside the writable worktree git dir: the pointers that decide
            # which common directory (and so which hooks and config) git uses.
            *(os.path.join(gitdir, n) for n in GITDIR_READONLY),
        ),
        hooks_dir=os.path.join(common, "hooks"),
        create_dirs=(os.path.join(common, "logs"),),
        # An empty per-worktree config is what git itself would create; having
        # it lets it be read-only, so the agent cannot add one.
        create_files=(os.path.join(gitdir, "config.worktree"),),
    )


def prepare_git(layout: GitLayout) -> None:
    """Create ``hooks`` and ``config.worktree`` (so they can be read-only) and ``logs``."""
    for path in (*((layout.hooks_dir,) if layout.hooks_dir else ()), *layout.create_dirs):
        parent = os.path.dirname(path)
        if os.path.islink(parent) or not os.path.isdir(parent) or os.path.lexists(path):
            continue
        os.mkdir(path, 0o755)
    for path in layout.create_files:
        parent = os.path.dirname(path)
        if os.path.islink(parent) or not os.path.isdir(parent) or os.path.lexists(path):
            continue
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
        os.close(fd)


# ---------------------------------------------------------------- plan


@dataclass(frozen=True)
class GuardedMounts:
    """The mounts both environments make, in order, as real paths.

    1. ``writable`` — read-write: working directory, git's writable parts,
       agent state, operator read-write paths.
    2. ``dotgit`` — the working directory's ``.git``, read-only, as a mount point.
    3. ``readonly`` — read-only, over the writable ones: protected agent config,
       git's read-only parts, operator read-only paths.
    """

    writable: tuple[str, ...]
    dotgit: str | None
    readonly: tuple[str, ...]


def _unique(paths: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for path in paths:
        if path not in seen:
            seen.add(path)
            out.append(path)
    return tuple(out)


def guarded_mounts(
    backend: str,
    launch: Launch,
    git: GitLayout,
    *,
    rw_paths: tuple[str, ...] = (),
    ro_paths: tuple[str, ...] = (),
    protect: bool = True,
    realpath: Callable[[str], str] = os.path.realpath,
) -> GuardedMounts:
    cwd = realpath(os.path.abspath(launch.cwd))
    writable = _unique(
        [
            realpath(p)
            for p in (
                cwd,
                *git.writable,
                *agent_state_paths(backend, launch.env),
                *rw_paths,
            )
        ]
    )
    readonly: list[str] = []
    dotgit = None
    if protect:
        readonly += [realpath(p) for p, _root in protected_entries(backend, launch.env)]
        if git.hooks_dir:
            readonly.append(realpath(git.hooks_dir))
        readonly += [realpath(p) for p in git.readonly]
        if git.dotgit:
            dotgit = realpath(git.dotgit)
    readonly += [realpath(p) for p in ro_paths]
    return GuardedMounts(writable=writable, dotgit=dotgit, readonly=_unique(readonly))


def guard_refusal(
    backend: str,
    launch: Launch,
    git: GitLayout,
    *,
    environment: str,
    extra_paths: tuple[str, ...] = (),
    protect: bool = True,
    opt_out: str = "",
) -> str | None:
    """One sentence if mounting this launch would leak or could be subverted."""
    home = home_dir(launch.env)
    real_home = os.path.realpath(home)
    for label, path in (
        ("working directory", launch.cwd),
        *(("agent state directory", p) for p in agent_state_paths(backend, launch.env)),
        *(("configured path", p) for p in extra_paths),
    ):
        if too_broad(os.path.realpath(path), real_home):
            return (
                f"The {environment} execution environment cannot use {path} as its {label}: it "
                "is the home directory or contains it, which would expose everything it hides."
            )
    if not protect:
        return None
    links = symlinked_entries(protected_entries(backend, launch.env))
    links += [p for p in git.guarded_paths() if os.path.islink(p)]
    if links:
        return (
            f"The {environment} execution environment cannot protect {links[0]}: it is a "
            "symbolic link, which the agent could replace; use a dedicated CLAUDE_CONFIG_DIR / "
            f"CODEX_HOME for sandboxed threads{opt_out}."
        )
    return None


def prepare_guarded(backend: str, launch: Launch, git: GitLayout, *, protect: bool) -> None:
    """Create state directories, placeholders and git directories before mounting."""
    ensure_state_dirs(agent_state_paths(backend, launch.env))
    if protect:
        create_placeholders(protected_entries(backend, launch.env))
        prepare_git(git)
