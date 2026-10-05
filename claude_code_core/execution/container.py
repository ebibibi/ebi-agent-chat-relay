"""``container``: run the CLI inside an operator-provided image.

``docker run --rm -i`` streams stdin/stdout exactly like a local process. The
working directory and the agent's state are bind-mounted at the *same* paths
they have on the host, so every path already in the argv (``--cd``, the
attachment marker files, ``CLAUDE_CONFIG_DIR``) stays valid without rewriting.
The container runs as the relay's own uid/gid so files it writes are owned by
the relay, not root.

Environment values never appear on the command line: the runtime is given
``-e NAME`` and reads the value from its own environment, which is the child
environment the runner built. Host-specific variables (``PATH``, ``LD_*``, the
SSH agent socket ...) are not forwarded — the image has its own.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import replace

from .base import Launch, agent_state_paths, ensure_state_dirs, resolve_binary, run_probe
from .config import CONTAINER, ContainerSettings

PROBE_TIMEOUT_SECONDS = 20.0

# Not forwarded: they describe the host process, not the agent's configuration.
HOST_ONLY_ENV = frozenset(
    {
        "PATH",
        "HOSTNAME",
        "SHELL",
        "PWD",
        "OLDPWD",
        "SHLVL",
        "_",
        "TERM",
        "TMPDIR",
        "VIRTUAL_ENV",
        "PYTHONPATH",
        "PYTHONHOME",
        "XDG_RUNTIME_DIR",
        "DBUS_SESSION_BUS_ADDRESS",
        "SSH_AUTH_SOCK",
        "SSH_AGENT_PID",
        "SSH_CONNECTION",
        "SSH_CLIENT",
        "SSH_TTY",
        "INVOCATION_ID",
        "JOURNAL_STREAM",
        "SYSTEMD_EXEC_PID",
        "MANAGERPID",
        "NOTIFY_SOCKET",
        "MAIL",
        "LS_COLORS",
    }
)
HOST_ONLY_ENV_PREFIXES = ("LD_", "DYLD_", "UV_")


def forwarded_env_names(env: Mapping[str, str]) -> tuple[str, ...]:
    return tuple(
        sorted(
            name
            for name in env
            if name not in HOST_ONLY_ENV and not name.startswith(HOST_ONLY_ENV_PREFIXES)
        )
    )


def _mount_paths(settings: ContainerSettings, backend: str, launch: Launch) -> list[str]:
    paths: list[str] = []
    for path in (
        os.path.abspath(launch.cwd),
        *agent_state_paths(backend, launch.env),
        *settings.rw_paths,
    ):
        if path not in paths:
            paths.append(path)
    return paths


def build_container_argv(
    settings: ContainerSettings,
    backend: str,
    launch: Launch,
    *,
    uid: int | None,
    gid: int | None,
    exists: Callable[[str], bool] = os.path.exists,
) -> tuple[str, ...]:
    if not settings.image:
        raise ValueError("container image is not configured")
    cwd = os.path.abspath(launch.cwd)
    args: list[str] = [settings.runtime, "run", "--rm", "-i", "--init", "--workdir", cwd]
    if uid is not None and gid is not None:
        args += ["--user", f"{uid}:{gid}"]
    for path in _mount_paths(settings, backend, launch):
        if path == cwd or exists(path):
            args += ["--volume", f"{path}:{path}"]
    for name in forwarded_env_names(launch.env):
        args += ["--env", name]
    args += [*settings.extra_args, settings.image]
    # The host path of the CLI means nothing inside the image; its PATH decides.
    args += [os.path.basename(launch.argv[0]), *launch.argv[1:]]
    return tuple(args)


class ContainerEnvironment:
    name = CONTAINER

    def __init__(self, settings: ContainerSettings) -> None:
        self._settings = settings

    async def preflight(self, backend: str, launch: Launch) -> str | None:
        settings = self._settings
        if not settings.image:
            return "The container execution environment has no image; set CCDB_CONTAINER_IMAGE."
        runtime = resolve_binary(settings.runtime, launch.env)
        if runtime is None:
            return f"The container runtime {settings.runtime!r} was not found on PATH."
        if not os.path.isdir(launch.cwd):
            return f"The working directory {launch.cwd} does not exist."
        for path in _mount_paths(settings, backend, launch):
            if ":" in path or "," in path:
                return f"The path {path} cannot be mounted into a container (contains ':' or ',')."
        try:
            ensure_state_dirs(agent_state_paths(backend, launch.env))
        except OSError as exc:
            return f"Could not create the agent's state directory for the container: {exc}."
        code, stderr = await run_probe(
            [runtime, "image", "inspect", settings.image], launch.env, PROBE_TIMEOUT_SECONDS
        )
        if code != 0:
            return (
                f"The container image {settings.image!r} is not available "
                f"({stderr.splitlines()[0] if stderr else f'exit code {code}'}); "
                "build or pull it first."
            )
        return None

    def transform(self, backend: str, launch: Launch) -> Launch:
        getuid = getattr(os, "getuid", None)
        getgid = getattr(os, "getgid", None)
        argv = build_container_argv(
            self._settings,
            backend,
            launch,
            uid=getuid() if getuid else None,
            gid=getgid() if getgid else None,
        )
        return replace(launch, argv=argv)
