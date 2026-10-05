"""Deployment-scoped execution-environment configuration (environment only).

Every value here is read from the relay's own environment, never from a thread
setting. A Discord or Teams user can choose a mode for their thread, but only
from :attr:`ExecutionConfig.allowed_modes` — the same stance as
``CCDB_CODEX_SANDBOX_OVERRIDE``: nobody escalates to a mode the operator did
not allow.

A malformed configuration does not degrade to ``host``. An operator who typed
``CCDB_EXECUTION_MODE=bwarp`` asked for a boundary; silently running without
one would be the worst possible reading of that typo, so the error is carried
on the config and every turn refuses with it until it is fixed.
"""

from __future__ import annotations

import json
import os
import shlex
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

HOST = "host"
NATIVE = "native"
BWRAP = "bwrap"
CONTAINER = "container"
SSH = "ssh"

MODES: tuple[str, ...] = (HOST, NATIVE, BWRAP, CONTAINER, SSH)
DEFAULT_MODE = HOST

MODE_ENV = "CCDB_EXECUTION_MODE"
ALLOWED_MODES_ENV = "CCDB_EXECUTION_ALLOWED_MODES"
NATIVE_CLAUDE_SANDBOX_ENV = "CCDB_NATIVE_CLAUDE_SANDBOX_JSON"
BWRAP_BIN_ENV = "CCDB_BWRAP_BIN"
BWRAP_RW_PATHS_ENV = "CCDB_BWRAP_RW_PATHS"
BWRAP_HIDE_PATHS_ENV = "CCDB_BWRAP_HIDE_PATHS"
BWRAP_HIDE_DEFAULTS_ENV = "CCDB_BWRAP_HIDE_DEFAULTS"
BWRAP_UNSHARE_NET_ENV = "CCDB_BWRAP_UNSHARE_NET"
BWRAP_RO_PATHS_ENV = "CCDB_BWRAP_RO_PATHS"
BWRAP_PROTECT_CONFIG_ENV = "CCDB_BWRAP_PROTECT_CONFIG"
CONTAINER_RUNTIME_ENV = "CCDB_CONTAINER_RUNTIME"
CONTAINER_IMAGE_ENV = "CCDB_CONTAINER_IMAGE"
CONTAINER_ARGS_ENV = "CCDB_CONTAINER_ARGS"
CONTAINER_RW_PATHS_ENV = "CCDB_CONTAINER_RW_PATHS"
SSH_BIN_ENV = "CCDB_SSH_BIN"
SSH_HOST_ENV = "CCDB_SSH_HOST"
SSH_OPTIONS_ENV = "CCDB_SSH_OPTIONS"
SSH_WORKDIR_MAP_ENV = "CCDB_SSH_WORKDIR_MAP"
SSH_ENV_ENV = "CCDB_SSH_ENV"
SSH_REMOTE_PATH_ENV = "CCDB_SSH_REMOTE_PATH"
SSH_PROBE_ENV = "CCDB_SSH_PROBE"

_TRUE = frozenset({"1", "true", "yes", "on"})
_FALSE = frozenset({"0", "false", "no", "off"})


def _get(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _flag(env: Mapping[str, str], name: str, default: bool, errors: list[str]) -> bool:
    raw = _get(env, name)
    if raw is None:
        return default
    lowered = raw.lower()
    if lowered in _TRUE:
        return True
    if lowered in _FALSE:
        return False
    errors.append(f"{name}={raw!r} is not a boolean (use 1 or 0).")
    return default


def _paths(env: Mapping[str, str], name: str, errors: list[str]) -> tuple[str, ...]:
    """Split an ``os.pathsep``-separated list of absolute paths (like ``PATH``)."""
    raw = _get(env, name)
    if raw is None:
        return ()
    paths: list[str] = []
    for part in raw.split(os.pathsep):
        part = part.strip()
        if not part:
            continue
        expanded = os.path.expanduser(part)
        if not os.path.isabs(expanded):
            errors.append(f"{name} entry {part!r} is not an absolute path.")
            continue
        paths.append(os.path.normpath(expanded))
    return tuple(paths)


def _words(env: Mapping[str, str], name: str, errors: list[str]) -> tuple[str, ...]:
    raw = _get(env, name)
    if raw is None:
        return ()
    try:
        return tuple(shlex.split(raw))
    except ValueError as exc:
        errors.append(f"{name} cannot be parsed as shell words: {exc}.")
        return ()


@dataclass(frozen=True)
class NativeSettings:
    """Extra Claude Code ``sandbox`` settings merged over ccdb's defaults."""

    claude_sandbox_overrides: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BwrapSettings:
    binary: str = "bwrap"
    rw_paths: tuple[str, ...] = ()
    hide_paths: tuple[str, ...] = ()
    hide_defaults: bool = True
    unshare_net: bool = False
    ro_paths: tuple[str, ...] = ()
    protect_config: bool = True


@dataclass(frozen=True)
class ContainerSettings:
    runtime: str = "docker"
    image: str | None = None
    extra_args: tuple[str, ...] = ()
    rw_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class SshSettings:
    binary: str = "ssh"
    host: str | None = None
    options: tuple[str, ...] = ()
    workdir_map: tuple[tuple[str, str], ...] = ()
    forward_env: tuple[str, ...] = ()
    remote_path: str | None = None
    probe: bool = True


def _parse_modes(raw: str | None, name: str, errors: list[str]) -> tuple[str, ...]:
    if raw is None:
        return ()
    modes: list[str] = []
    for part in raw.split(","):
        mode = part.strip().lower()
        if not mode:
            continue
        if mode not in MODES:
            errors.append(
                f"{name} names unknown execution mode {mode!r} (known: {', '.join(MODES)})."
            )
            continue
        if mode not in modes:
            modes.append(mode)
    return tuple(modes)


def _parse_workdir_map(raw: str | None, errors: list[str]) -> tuple[tuple[str, str], ...]:
    """``/local/a=/remote/a,/local/b=/remote/b`` → longest-prefix-first pairs."""
    if raw is None:
        return ()
    pairs: list[tuple[str, str]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        local, sep, remote = part.partition("=")
        local, remote = local.strip(), remote.strip()
        if not sep or not os.path.isabs(local) or not remote.startswith("/"):
            errors.append(f"{SSH_WORKDIR_MAP_ENV} entry {part!r} must be /local/path=/remote/path.")
            continue
        pairs.append((os.path.normpath(local), remote.rstrip("/") or "/"))
    pairs.sort(key=lambda pair: len(pair[0]), reverse=True)
    return tuple(pairs)


def _parse_native(env: Mapping[str, str], errors: list[str]) -> NativeSettings:
    raw = _get(env, NATIVE_CLAUDE_SANDBOX_ENV)
    if raw is None:
        return NativeSettings()
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        errors.append(f"{NATIVE_CLAUDE_SANDBOX_ENV} is not valid JSON: {exc.msg}.")
        return NativeSettings()
    if not isinstance(value, dict):
        errors.append(f"{NATIVE_CLAUDE_SANDBOX_ENV} must be a JSON object.")
        return NativeSettings()
    return NativeSettings(claude_sandbox_overrides=value)


@dataclass(frozen=True)
class ExecutionConfig:
    """Resolved execution configuration for one spawn."""

    default_mode: str = DEFAULT_MODE
    allowed_modes: tuple[str, ...] = (DEFAULT_MODE,)
    native: NativeSettings = field(default_factory=NativeSettings)
    bwrap: BwrapSettings = field(default_factory=BwrapSettings)
    container: ContainerSettings = field(default_factory=ContainerSettings)
    ssh: SshSettings = field(default_factory=SshSettings)
    # ``error`` concerns the mode selection itself and refuses every turn;
    # ``mode_errors`` refuse only turns that run in that mode.
    error: str | None = None
    mode_errors: Mapping[str, str] = field(default_factory=dict)

    def is_allowed(self, mode: str) -> bool:
        return mode in self.allowed_modes

    def problem_for(self, mode: str) -> str | None:
        return self.error or self.mode_errors.get(mode)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ExecutionConfig:
        source: Mapping[str, str] = os.environ if env is None else env
        errors: list[str] = []

        default_raw = _get(source, MODE_ENV)
        default_mode = DEFAULT_MODE
        if default_raw is not None:
            lowered = default_raw.lower()
            if lowered in MODES:
                default_mode = lowered
            else:
                errors.append(
                    f"{MODE_ENV}={default_raw!r} is not a known execution mode "
                    f"(known: {', '.join(MODES)})."
                )

        allowed = _parse_modes(_get(source, ALLOWED_MODES_ENV), ALLOWED_MODES_ENV, errors)
        # The default is always selectable: it is what a thread gets anyway.
        allowed = (default_mode, *(mode for mode in allowed if mode != default_mode))

        # Per-mode errors: a typo in a variable for a mode the turn does not use
        # must not stop that turn (a bad CCDB_SSH_ENV must not take down host).
        bwrap_errors: list[str] = []
        bwrap = BwrapSettings(
            binary=_get(source, BWRAP_BIN_ENV) or "bwrap",
            rw_paths=_paths(source, BWRAP_RW_PATHS_ENV, bwrap_errors),
            hide_paths=_paths(source, BWRAP_HIDE_PATHS_ENV, bwrap_errors),
            hide_defaults=_flag(source, BWRAP_HIDE_DEFAULTS_ENV, True, bwrap_errors),
            unshare_net=_flag(source, BWRAP_UNSHARE_NET_ENV, False, bwrap_errors),
            ro_paths=_paths(source, BWRAP_RO_PATHS_ENV, bwrap_errors),
            protect_config=_flag(source, BWRAP_PROTECT_CONFIG_ENV, True, bwrap_errors),
        )
        container_errors: list[str] = []
        container = ContainerSettings(
            runtime=_get(source, CONTAINER_RUNTIME_ENV) or "docker",
            image=_get(source, CONTAINER_IMAGE_ENV),
            extra_args=_words(source, CONTAINER_ARGS_ENV, container_errors),
            rw_paths=_paths(source, CONTAINER_RW_PATHS_ENV, container_errors),
        )
        ssh_errors: list[str] = []
        forward_env = tuple(
            name.strip() for name in (_get(source, SSH_ENV_ENV) or "").split(",") if name.strip()
        )
        for name in forward_env:
            if not name.replace("_", "").isalnum() or name[0].isdigit():
                ssh_errors.append(f"{SSH_ENV_ENV} entry {name!r} is not a valid variable name.")
        ssh = SshSettings(
            binary=_get(source, SSH_BIN_ENV) or "ssh",
            host=_get(source, SSH_HOST_ENV),
            options=_words(source, SSH_OPTIONS_ENV, ssh_errors),
            workdir_map=_parse_workdir_map(_get(source, SSH_WORKDIR_MAP_ENV), ssh_errors),
            forward_env=forward_env,
            remote_path=_get(source, SSH_REMOTE_PATH_ENV),
            probe=_flag(source, SSH_PROBE_ENV, True, ssh_errors),
        )
        native_errors: list[str] = []
        native = _parse_native(source, native_errors)

        def joined(found: list[str]) -> str | None:
            return "Execution environment misconfigured: " + " ".join(found) if found else None

        mode_errors = {
            mode: message
            for mode, found in (
                (NATIVE, native_errors),
                (BWRAP, bwrap_errors),
                (CONTAINER, container_errors),
                (SSH, ssh_errors),
            )
            if (message := joined(found))
        }
        return cls(
            default_mode=default_mode,
            allowed_modes=allowed,
            native=native,
            bwrap=bwrap,
            container=container,
            ssh=ssh,
            error=joined(errors),
            mode_errors=mode_errors,
        )
