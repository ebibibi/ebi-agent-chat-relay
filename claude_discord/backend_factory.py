"""Factory for SessionBackend instances.

Holds the static configuration needed to construct ClaudeRunner or
CodexRunner instances on demand. Used by ClaudeChatCog (and friends)
to spawn a fresh runner per Discord thread whenever the user issues
a chat message.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from claude_code_core.backend import create_backend

if TYPE_CHECKING:
    from claude_code_core.backend import SessionBackend

logger = logging.getLogger(__name__)


# Sensible per-backend defaults (mirror the CLIs own defaults so users
# do not need to pick a model just to try a backend).
#
# ``codex`` is intentionally ``None``: when no model is configured we omit
# ``--model`` entirely so the Codex CLI uses its own default (the ``model``
# key in ~/.codex/config.toml, currently gpt-5.6-sol). Hard-coding a version
# here only goes stale as the Codex console default moves.
DEFAULT_MODEL: dict[str, str | None] = {
    "claude": "sonnet",
    "codex": None,
    "local": None,
    "agui": None,
    "pi": None,
}
DEFAULT_COMMAND = {
    "claude": "claude",
    "codex": "codex",
    "local": "codex",
    "agui": "ag-ui",
    "pi": "pi",
}


class BackendFactory:
    """Builds SessionBackend instances on demand from static configuration."""

    def __init__(
        self,
        *,
        claude_command: str,
        codex_command: str,
        permission_mode: str,
        working_dir: str | None,
        timeout_seconds: int,
        dangerously_skip_permissions: bool,
        allowed_tools: list[str] | None,
        append_system_prompt: str | None,
        effort: str | None,
        api_port: int | None = None,
        api_secret: str | None = None,
        agui_url: str | None = None,
        agui_token: str | None = None,
        pi_command: str | None = None,
    ) -> None:
        self.claude_command = claude_command or DEFAULT_COMMAND["claude"]
        self.codex_command = codex_command or DEFAULT_COMMAND["codex"]
        self.permission_mode = permission_mode
        self.working_dir = working_dir
        self.timeout_seconds = timeout_seconds
        self.dangerously_skip_permissions = dangerously_skip_permissions
        self.allowed_tools = allowed_tools
        self.append_system_prompt = append_system_prompt
        self.effort = effort
        self.api_port = api_port
        self.api_secret = api_secret
        self.agui_url = agui_url
        self.agui_token = agui_token
        self.pi_command = pi_command or DEFAULT_COMMAND["pi"]

    def command_for(self, backend: str) -> str:
        if backend == "claude":
            return self.claude_command
        if backend in ("codex", "local"):
            # The local backend is the same CLI, pointed at a ccdb-owned
            # CODEX_HOME that pins it to a model on your own hardware.
            return self.codex_command
        if backend == "pi":
            return self.pi_command
        if backend == "agui":
            return DEFAULT_COMMAND["agui"]
        raise ValueError(f"Unknown backend: {backend!r}")

    def default_model_for(self, backend: str) -> str | None:
        """Return the built-in default model, or ``None`` to defer to the CLI.

        ``None`` (codex) means "do not pass ``--model``" so the Codex CLI uses
        its own configured default.
        """
        return DEFAULT_MODEL.get(backend, DEFAULT_MODEL["claude"])

    def build(
        self,
        *,
        backend: str,
        model: str | None = None,
        thread_id: int | None = None,
    ) -> SessionBackend:
        """Construct a fresh SessionBackend for the given backend/model."""
        chosen_model = model or self.default_model_for(backend)
        command = self.command_for(backend)
        kwargs: dict[str, object] = {
            "command": command,
            "permission_mode": self.permission_mode,
            "working_dir": self.working_dir,
            "timeout_seconds": self.timeout_seconds,
            "dangerously_skip_permissions": self.dangerously_skip_permissions,
            "allowed_tools": self.allowed_tools,
        }
        if backend == "agui":
            if not self.agui_url:
                raise ValueError("CCDB_AGUI_URL is required for the AG-UI backend")
            kwargs["endpoint_url"] = self.agui_url
            if self.agui_token:
                kwargs["auth_token"] = self.agui_token
        if thread_id is not None:
            kwargs["thread_id"] = thread_id
        # ``append_system_prompt`` goes to every CLI-backed backend. Codex takes
        # it as `developer_instructions`, which lands as a `developer` message
        # ahead of the turn (measured on codex-cli 0.147.0). Withholding it made
        # the operator's standing instructions silently Claude-only, which
        # matters most on `local`: a small model needs a short, blunt directive
        # far more than a frontier one does.
        if backend in ("claude", "codex", "local", "pi") and self.append_system_prompt is not None:
            kwargs["append_system_prompt"] = self.append_system_prompt
        # The env-level ``effort`` stays Claude-only. Codex effort is resolved
        # per-backend from BackendSettings at spawn time, and the valid values
        # differ — Claude's "max" is not a Codex level.
        if backend == "claude" and self.effort is not None:
            kwargs["effort"] = self.effort
        if self.api_port is not None:
            kwargs["api_port"] = self.api_port
        if self.api_secret is not None:
            kwargs["api_secret"] = self.api_secret
        runner = create_backend(backend=backend, model=chosen_model, **kwargs)
        logger.debug("Built %s runner (model=%s, thread_id=%s)", backend, chosen_model, thread_id)
        return runner
