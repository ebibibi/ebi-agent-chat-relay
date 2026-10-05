"""``native``: ask the agent to use its own sandbox.

* Claude Code takes ``sandbox`` settings through ``--settings``. On Linux its
  sandbox runtime needs ``bwrap`` and ``socat`` on PATH; ``failIfUnavailable``
  makes the CLI refuse rather than quietly run the shell unconfined.
  ``allowUnsandboxedCommands: false`` removes the per-command escape hatch.
  The sandbox confines the Bash tool and its children; the file-edit tools stay
  governed by the permission mode (see docs/execution-environments.md).
* Codex gets ``--sandbox workspace-write``. A ``CCDB_CODEX_SANDBOX_OVERRIDE`` of
  ``read-only`` is stricter and is kept; ``danger-full-access`` contradicts the
  mode and is refused rather than shown as "native" while unsandboxed. The
  ``--dangerously-bypass-approvals-and-sandbox`` flag that
  ``dangerously_skip_permissions`` adds is removed for the same reason —
  ``codex exec`` has no approval loop to bypass, so only the sandbox was lost.
* pi has no sandbox of its own (ADR-0007), so it refuses.
"""

from __future__ import annotations

import copy
import json
import sys
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from .base import CODEX_BACKENDS, Launch, resolve_binary
from .config import NATIVE, NativeSettings

CODEX_SANDBOX_OVERRIDE_ENV = "CCDB_CODEX_SANDBOX_OVERRIDE"
CODEX_BYPASS_FLAG = "--dangerously-bypass-approvals-and-sandbox"

DEFAULT_CLAUDE_SANDBOX: dict[str, Any] = {
    "enabled": True,
    "autoAllowBashIfSandboxed": True,
    "allowUnsandboxedCommands": False,
    "failIfUnavailable": True,
}

PI_REFUSAL = (
    "The pi backend has no native sandbox (see ADR-0007); choose the bwrap, container "
    "or ssh execution environment for an OS boundary instead."
)


def _deep_merge(base: dict[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    merged = copy.deepcopy(base)
    for key, value in overrides.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def claude_sandbox_settings(settings: NativeSettings) -> dict[str, Any]:
    return {"sandbox": _deep_merge(DEFAULT_CLAUDE_SANDBOX, settings.claude_sandbox_overrides)}


def codex_sandbox_level(env: Mapping[str, str]) -> str | None:
    """The ``--sandbox`` level native mode uses, or None if the override forbids it."""
    override = (env.get(CODEX_SANDBOX_OVERRIDE_ENV) or "").strip()
    if override == "danger-full-access":
        return None
    if override == "read-only":
        return "read-only"
    return "workspace-write"


def rewrite_codex_argv(argv: tuple[str, ...], level: str) -> tuple[str, ...]:
    """Force ``--sandbox <level>`` on a ``codex exec`` argv.

    ``--sandbox`` is an ``exec`` option that ``exec resume`` rejects after
    ``resume``, so it goes directly after ``exec``. Any existing ``--sandbox``
    pair and the bypass flag are dropped.
    """
    exec_index = argv.index("exec")
    head = list(argv[: exec_index + 1])
    tail: list[str] = []
    skip_next = False
    for arg in argv[exec_index + 1 :]:
        if skip_next:
            skip_next = False
            continue
        if arg in ("--sandbox", "-s"):
            skip_next = True
            continue
        if arg.startswith("--sandbox=") or arg == CODEX_BYPASS_FLAG:
            continue
        tail.append(arg)
    return (*head, "--sandbox", level, *tail)


class NativeEnvironment:
    name = NATIVE

    def __init__(self, settings: NativeSettings) -> None:
        self._settings = settings

    async def preflight(self, backend: str, launch: Launch) -> str | None:
        if backend == "pi":
            return PI_REFUSAL
        if backend in CODEX_BACKENDS:
            if "exec" not in launch.argv:
                return "The native execution environment expected a `codex exec` command line."
            if codex_sandbox_level(launch.env) is None:
                return (
                    f"{CODEX_SANDBOX_OVERRIDE_ENV}=danger-full-access disables the Codex sandbox, "
                    "which contradicts the native execution environment; unset it or choose "
                    "another environment."
                )
            return None
        if backend == "claude":
            if sys.platform.startswith("linux"):
                missing = [b for b in ("bwrap", "socat") if resolve_binary(b, launch.env) is None]
                if missing:
                    return (
                        "Claude Code's native sandbox needs "
                        f"{' and '.join(missing)} on PATH, which this host does not have."
                    )
                return None
            if sys.platform == "darwin":
                return None
            return "Claude Code's native sandbox supports Linux and macOS only."
        return f"The native execution environment does not support the {backend} backend."

    def transform(self, backend: str, launch: Launch) -> Launch:
        if backend in CODEX_BACKENDS:
            level = codex_sandbox_level(launch.env) or "workspace-write"
            return replace(launch, argv=rewrite_codex_argv(launch.argv, level))
        if backend == "claude":
            settings = json.dumps(claude_sandbox_settings(self._settings), separators=(",", ":"))
            return replace(launch, argv=(*launch.argv, "--settings", settings))
        return launch
