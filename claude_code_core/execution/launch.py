"""Choose the environment for a spawn, run its preflight, transform the launch."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence

from .base import ExecutionEnvironment, ExecutionRefusedError, Launch, PreparedLaunch
from .bwrap import BwrapEnvironment
from .config import BWRAP, CONTAINER, HOST, NATIVE, SSH, ExecutionConfig
from .container import ContainerEnvironment
from .host import HostEnvironment
from .native import NativeEnvironment
from .ssh import SshEnvironment

logger = logging.getLogger(__name__)


def environment_for(mode: str, config: ExecutionConfig) -> ExecutionEnvironment:
    if mode == HOST:
        return HostEnvironment()
    if mode == NATIVE:
        return NativeEnvironment(config.native)
    if mode == BWRAP:
        return BwrapEnvironment(config.bwrap)
    if mode == CONTAINER:
        return ContainerEnvironment(config.container)
    if mode == SSH:
        return SshEnvironment(config.ssh)
    raise ExecutionRefusedError(f"Unknown execution environment {mode!r}.")


def resolve_mode(requested: str | None, config: ExecutionConfig) -> str:
    """The mode a spawn uses: the requested one if allowed, else refuse.

    ``None`` means "the deployment default". A requested mode outside the
    allowlist is refused here as well as at selection time, so a custom Cog that
    sets ``runner.execution_mode`` directly cannot step around the operator.
    """
    if config.error:
        raise ExecutionRefusedError(config.error)
    mode = requested or config.default_mode
    if not config.is_allowed(mode):
        raise ExecutionRefusedError(
            f"The {mode!r} execution environment is not allowed on this deployment "
            f"(allowed: {', '.join(config.allowed_modes)})."
        )
    problem = config.problem_for(mode)
    if problem:
        raise ExecutionRefusedError(problem)
    return mode


async def prepare_launch(
    *,
    backend: str,
    requested_mode: str | None,
    argv: Sequence[str],
    env: Mapping[str, str],
    cwd: str,
    config: ExecutionConfig | None = None,
) -> PreparedLaunch:
    """Return the launch to make for ``backend``, or raise :class:`ExecutionRefusedError`.

    ``host`` returns the inputs unchanged without any check, so a deployment
    that never configures an execution environment spawns exactly as before.
    """
    resolved = config or ExecutionConfig.from_env()
    mode = resolve_mode(requested_mode, resolved)
    launch = Launch(argv=tuple(argv), env=dict(env), cwd=cwd)
    if mode == HOST:
        return PreparedLaunch(argv=launch.argv, env=dict(env), cwd=cwd, mode=HOST)

    environment = environment_for(mode, resolved)
    problem = await environment.preflight(backend, launch)
    if problem:
        logger.warning("Execution environment %s refused %s: %s", mode, backend, problem)
        raise ExecutionRefusedError(problem)
    transformed = environment.transform(backend, launch)
    logger.info("Execution environment %s wraps %s: %s", mode, backend, transformed.argv[0])
    return PreparedLaunch(
        argv=transformed.argv, env=dict(transformed.env), cwd=transformed.cwd, mode=mode
    )
