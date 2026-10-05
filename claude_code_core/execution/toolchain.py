"""Which directories under ``$HOME`` a CLI needs to start (``bwrap`` binds them read-only).

Starting from the command as found on the child's PATH: the directory of every
symlink hop (``~/.local/bin/codex`` → ``~/.npm-global/bin/codex`` → …), the
install root of the real file (a versions directory, a global
``node_modules``, a toolchain prefix) and the same again for a script's
interpreter (``#!/usr/bin/env node``).

**Only trusted files are followed.** A symlink or script that sits in a
directory the agent can write (the working directory, its state directory,
operator read-write paths) may have been rewritten by a previous sandboxed run.
Following it would let the agent choose what is bound into the next sandbox —
``#!/home/me/.ssh/id_ed25519`` would bind ``~/.ssh``. Such a file's own
directory is still returned (so it is mounted read-only rather than left
writable), but nothing it points to is.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Mapping

from .base import home_dir
from .guarded_paths import inside, too_broad

# Read-only by default from the real home: git needs an identity to commit.
DEFAULT_HOME_READONLY: tuple[str, ...] = (".gitconfig", ".config/git")


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


def toolchain_paths(
    argv0: str,
    env: Mapping[str, str],
    untrusted: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Directories under ``$HOME`` to bind read-only so ``argv0`` can start.

    ``untrusted`` are real paths the agent can write; files inside them are not
    followed (see the module docstring).
    """
    home = os.path.realpath(home_dir(env))
    roots = tuple(os.path.realpath(u) for u in untrusted)
    found: list[str] = []

    def keep(path: str) -> None:
        path = os.path.realpath(path)
        if inside(path, home) and not too_broad(path, home) and path not in found:
            found.append(path)

    def tainted(path: str) -> bool:
        real_dir = os.path.realpath(os.path.dirname(path))
        return any(inside(real_dir, r) for r in roots)

    def add(command: str, depth: int) -> None:
        exe = command if os.sep in command else shutil.which(command, path=env.get("PATH"))
        if not exe:
            return
        path = os.path.abspath(exe)
        for _ in range(40):
            keep(os.path.dirname(path))
            if tainted(path):
                return  # rewritable by the agent: mount it, do not follow it
            if not os.path.islink(path):
                break
            path = os.path.normpath(os.path.join(os.path.dirname(path), os.readlink(path)))
        else:
            return
        keep(_install_root(os.path.realpath(path), home))
        if depth == 0:
            interpreter = _interpreter(os.path.realpath(path))
            if interpreter:
                add(interpreter, 1)

    add(argv0, 0)
    return tuple(found)
