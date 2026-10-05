"""``ssh``: run the CLI on another machine.

Locally this is still ``create_subprocess_exec`` — no local shell is ever
involved. The remote side necessarily is a shell (that is how ``ssh`` delivers a
command), so the remote command is assembled from :func:`shlex.quote`-d words
only: the working directory, every ``NAME=value`` and every argv element. The
host comes after ``--`` so it can never be read as an ssh option.

Which environment crosses the wire is an explicit list (``CCDB_SSH_ENV``):
values given to ``env`` on the remote command line are visible in process
listings on both ends, so nothing is forwarded by default. The remote host is
expected to be logged in to the agent's provider itself.
"""

from __future__ import annotations

import os
import shlex
import time
from dataclasses import replace

from .base import Launch, resolve_binary, run_probe
from .config import SSH, SshSettings

PROBE_TIMEOUT_SECONDS = 20.0
PROBE_CACHE_SECONDS = 300.0

# Variables the runner itself sets for the agent that are safe and useful on
# the remote end. Credentials (CCDB_API_SECRET) and host-local addresses
# (CCDB_API_URL points at 127.0.0.1 here) are deliberately absent.
DEFAULT_FORWARDED_ENV = ("DISCORD_THREAD_ID", "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS")

_probe_ok_until: dict[tuple[str, ...], float] = {}


def map_workdir(settings: SshSettings, cwd: str) -> str:
    """Translate a local working directory with the longest matching prefix."""
    local = os.path.normpath(cwd)
    for local_prefix, remote_prefix in settings.workdir_map:
        if local == local_prefix:
            return remote_prefix
        if local.startswith(local_prefix.rstrip("/") + "/"):
            suffix = local[len(local_prefix.rstrip("/")) :]
            return remote_prefix.rstrip("/") + suffix
    return local


def remote_command(settings: SshSettings, launch: Launch) -> str:
    """The single string the remote shell runs. Every word is quoted."""
    local_cwd = os.path.normpath(launch.cwd)
    remote_cwd = map_workdir(settings, local_cwd)
    argv = [os.path.basename(launch.argv[0])]
    for arg in launch.argv[1:]:
        argv.append(
            remote_cwd if os.path.normpath(arg) == local_cwd and arg.startswith("/") else arg
        )

    assignments: list[str] = []
    if settings.remote_path:
        assignments.append(f"PATH={settings.remote_path}")
    for name in (*DEFAULT_FORWARDED_ENV, *settings.forward_env):
        if name in launch.env and not any(a.startswith(f"{name}=") for a in assignments):
            assignments.append(f"{name}={launch.env[name]}")

    words = ["env", *assignments, *argv] if assignments else argv
    return f"cd {shlex.quote(remote_cwd)} && exec " + " ".join(shlex.quote(w) for w in words)


def build_ssh_argv(settings: SshSettings, launch: Launch, binary: str) -> tuple[str, ...]:
    if not settings.host:
        raise ValueError("ssh host is not configured")
    return (
        binary,
        "-T",
        "-o",
        "BatchMode=yes",
        *settings.options,
        "--",
        settings.host,
        remote_command(settings, launch),
    )


def _probe_command(settings: SshSettings, launch: Launch) -> str:
    """Check the remote working directory exists and the CLI starts there."""
    probe = replace(launch, argv=(launch.argv[0], "--version"))
    return remote_command(settings, probe) + " >/dev/null"


class SshEnvironment:
    name = SSH

    def __init__(self, settings: SshSettings) -> None:
        self._settings = settings

    async def preflight(self, backend: str, launch: Launch) -> str | None:
        settings = self._settings
        if not settings.host:
            return "The ssh execution environment has no host; set CCDB_SSH_HOST."
        if settings.host.startswith("-"):
            return f"CCDB_SSH_HOST={settings.host!r} is not a host name."
        binary = resolve_binary(settings.binary, launch.env)
        if binary is None:
            return f"The ssh client {settings.binary!r} was not found on PATH."
        if not settings.probe:
            return None
        key = (
            binary,
            settings.host,
            *settings.options,
            map_workdir(settings, launch.cwd),
            launch.argv[0],
        )
        if _probe_ok_until.get(key, 0.0) > time.monotonic():
            return None
        argv = [
            binary,
            "-T",
            "-o",
            "BatchMode=yes",
            "-o",
            "ConnectTimeout=10",
            *settings.options,
            "--",
            settings.host,
            _probe_command(settings, launch),
        ]
        code, stderr = await run_probe(argv, launch.env, PROBE_TIMEOUT_SECONDS)
        if code != 0:
            detail = stderr.splitlines()[-1] if stderr else f"exit code {code}"
            return (
                f"The ssh host {settings.host} could not start "
                f"{os.path.basename(launch.argv[0])} in {map_workdir(settings, launch.cwd)} "
                f"({detail})."
            )
        _probe_ok_until[key] = time.monotonic() + PROBE_CACHE_SECONDS
        return None

    def transform(self, backend: str, launch: Launch) -> Launch:
        binary = resolve_binary(self._settings.binary, launch.env) or self._settings.binary
        return replace(launch, argv=build_ssh_argv(self._settings, launch, binary))
