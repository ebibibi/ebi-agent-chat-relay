"""Shared types for execution environments.

An execution environment sits between a CLI runner and the subprocess it
spawns. It sees the launch the runner would have made — argv, environment and
working directory — and returns the launch to make instead. Before that it gets
one chance to refuse (:meth:`ExecutionEnvironment.preflight`): a missing
``bwrap``, an image that is not there, a host that does not answer. A refusal
becomes one sentence in the thread and no process starts.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

# The backends whose runners spawn a CLI. ``local`` is the Codex CLI with a
# ccdb-owned CODEX_HOME, so wherever Codex behaviour is meant it means both.
CODEX_BACKENDS = frozenset({"codex", "local"})


class ExecutionRefusedError(Exception):
    """Raised when an environment cannot (or must not) start the CLI."""


@dataclass(frozen=True)
class Launch:
    """One subprocess launch: what ``create_subprocess_exec`` receives."""

    argv: tuple[str, ...]
    env: Mapping[str, str]
    cwd: str


@dataclass(frozen=True)
class PreparedLaunch:
    """A launch after the environment transformed it, plus who served it."""

    argv: tuple[str, ...]
    env: dict[str, str]
    cwd: str
    mode: str


class ExecutionEnvironment(Protocol):
    """What every mode implements."""

    name: str

    async def preflight(self, backend: str, launch: Launch) -> str | None:
        """Return one sentence describing why the launch cannot run, or None."""
        ...

    def transform(self, backend: str, launch: Launch) -> Launch:
        """Return the launch to make instead of ``launch``. Pure."""
        ...


def home_dir(env: Mapping[str, str]) -> str:
    return env.get("HOME") or str(Path.home())


def agent_state_paths(backend: str, env: Mapping[str, str]) -> tuple[str, ...]:
    """The paths a backend's CLI must be able to write: its own state.

    Read from the *final* child environment, so a ``CLAUDE_CONFIG_DIR`` or
    ``CODEX_HOME`` injected by the runner (account pools, the local backend's
    generated home) is the one that gets mounted.
    """
    home = home_dir(env)
    if backend == "claude":
        configured = env.get("CLAUDE_CONFIG_DIR")
        if configured:
            return (os.path.normpath(configured),)
        return (os.path.join(home, ".claude"), os.path.join(home, ".claude.json"))
    if backend in CODEX_BACKENDS:
        return (os.path.normpath(env.get("CODEX_HOME") or os.path.join(home, ".codex")),)
    if backend == "pi":
        configured = env.get("PI_CODING_AGENT_DIR")
        return (os.path.normpath(configured) if configured else os.path.join(home, ".pi"),)
    return ()


def ensure_state_dirs(paths: tuple[str, ...]) -> None:
    """Create missing state *directories* so they can be bind-mounted.

    Files (``~/.claude.json``) are left alone: an absent one is created by the
    CLI inside the writable parent the first time it runs on the host.
    """
    for path in paths:
        if path.endswith(".json"):
            continue
        Path(path).mkdir(parents=True, exist_ok=True)


def resolve_binary(binary: str, env: Mapping[str, str]) -> str | None:
    """Resolve ``binary`` the way the spawn will: against the child's PATH."""
    if os.path.sep in binary:
        return binary if os.access(binary, os.X_OK) else None
    return shutil.which(binary, path=env.get("PATH"))


async def run_probe(argv: list[str], env: Mapping[str, str], timeout: float) -> tuple[int, str]:
    """Run a short check command. Returns (exit code, stderr); 124 on timeout."""
    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
            env=dict(env),
        )
    except OSError as exc:
        return 127, str(exc)
    try:
        _, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        process.kill()
        await process.wait()
        return 124, f"timed out after {timeout:.0f}s"
    return process.returncode or 0, stderr.decode("utf-8", errors="replace").strip()


def first_line(text: str, limit: int = 200) -> str:
    line = text.strip().splitlines()[0] if text.strip() else ""
    return line[:limit]
