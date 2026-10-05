"""Backend resolution for non-chat automated runs."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ..claude.runner import _UNSET
from ..execution_settings import apply_thread_execution_mode

if TYPE_CHECKING:
    from claude_code_core.backend import SessionBackend

    from ..backend_factory import BackendFactory
    from ..backend_settings import BackendSettings

logger = logging.getLogger(__name__)


async def build_headless_runner(
    base_runner: SessionBackend,
    *,
    factory: BackendFactory | None = None,
    settings: BackendSettings | None = None,
    thread_id: int | None = None,
    working_dir: str | None = None,
    timeout_seconds: int | None = None,
    allowed_tools: list[str] | None | object = _UNSET,
    permission_mode: str | None = None,
    dangerously_skip_permissions: bool | None = None,
    backend_override: str | None = None,
    model_override: str | None = None,
) -> SessionBackend:
    """Build a runner for scheduler/webhook/custom-cog automation.

    Chat sessions already resolve backend/model in ``ClaudeChatCog``.  Headless
    flows used to clone the startup runner, so a global ``/backend codex`` switch
    did not affect scheduled tasks or failure triage.  When a factory/settings
    pair is available, resolve the current backend at spawn time; otherwise keep
    the legacy clone behaviour.

    ``backend_override`` lets a caller — a scheduled task pinned to one backend,
    say — decide which backend gets built instead of asking
    ``settings.current_backend()``. ``model_override`` does the same for the
    model. Effort still comes from ``settings`` for the chosen backend, so an
    overridden task follows whatever ``/effort`` is configured there.

    The two overrides are independent at this layer, but a model override alone
    is meaningless: the model would land on whichever backend the settings
    resolve to, and model ids are backend-specific. Callers are expected to pin
    both or neither; ``/api/tasks`` enforces that.
    """
    if factory is not None and settings is not None:
        backend = (
            backend_override
            if backend_override is not None
            else await settings.current_backend(thread_id)
        )
        model = (
            model_override
            if model_override is not None
            else await settings.current_model(backend, thread_id)
        )
        runner = factory.build(backend=backend, model=model, thread_id=thread_id)
        effort = await settings.current_effort(backend, thread_id)
        if effort is not None and hasattr(runner, "effort"):
            runner.effort = effort  # type: ignore[attr-defined]
        await apply_thread_execution_mode(runner, settings.repo, thread_id)
    else:
        if backend_override is not None or model_override is not None:
            # The clone path has no factory to build a different backend with,
            # so an override here cannot be honoured. Say so instead of running
            # the task on the wrong backend in silence.
            logger.warning(
                "backend_override=%r / model_override=%r requested but no "
                "backend_factory/backend_settings is configured — falling back "
                "to the cloned base runner",
                backend_override,
                model_override,
            )
        runner = base_runner.clone(thread_id=thread_id)

    if working_dir is not None:
        runner.working_dir = working_dir
    if timeout_seconds is not None:
        runner.timeout_seconds = timeout_seconds
    if allowed_tools is not _UNSET:
        runner.allowed_tools = allowed_tools  # type: ignore[assignment]
    if permission_mode is not None:
        runner.permission_mode = permission_mode
    if dangerously_skip_permissions is not None:
        runner.dangerously_skip_permissions = dangerously_skip_permissions
    return runner


def backend_factory_from_components(components: Any) -> BackendFactory | None:
    return vars(components).get("backend_factory")


def backend_settings_from_components(components: Any) -> BackendSettings | None:
    return vars(components).get("backend_settings")
