"""Local-model backend: the Codex CLI driven by a model on your own hardware.

The point of this backend is a claim that can be checked: while a thread runs
locally, nothing about it reaches a vendor. Pointing the CLI at a local model
is *not* enough to make that true — measured on codex-cli 0.145.0, a run
configured entirely against a local endpoint still opened a connection to
``chatgpt.com`` for the startup update check and analytics, and it did so even
with an empty ``CODEX_HOME`` and no credentials.

So ccdb does not reuse the user's ``~/.codex``. It generates and owns a
separate ``CODEX_HOME`` whose ``config.toml`` points at the local endpoint and
switches both callers-home off. Measured again with that config, the only
endpoint contacted was the local model.

Two consequences worth keeping in mind:

* The guarantee is **measured, not enforced**. A future CLI version could add a
  new call home. ``verify_quiet_settings()`` re-checks the settings we know
  about on every spawn, and the docs say to re-measure after a CLI upgrade.
* The check is structural, so it works the same on Linux, macOS and Windows.
  An OS-level egress rule would be stronger and is not portable; for a
  framework that ships to other people, the config ccdb controls is the right
  trade.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from .codex_runner import CodexRunner
from .ollama_client import (
    DEFAULT_PULL_TIMEOUT_SECONDS as DEFAULT_OLLAMA_PULL_TIMEOUT_SECONDS,
)
from .ollama_client import (
    ollama_api_url,
    validate_ollama_model_name,
)
from .ollama_client import pull_model as _pull_model

if TYPE_CHECKING:
    from .types import StreamEvent

logger = logging.getLogger(__name__)

__all__ = [
    "LOCAL_STREAM_TRUNCATED_HINT",
    "LocalModelConfig",
    "LocalCodexRunner",
    "build_local_config_toml",
    "ensure_codex_home",
    "ollama_pull_url",
    "pull_ollama_model",
    "validate_ollama_model_name",
    "verify_quiet_settings",
]

DEFAULT_BASE_URL = "http://127.0.0.1:11434/v1"
# Fallback only. The model is chosen at runtime with ``/ollama use`` (or
# ``/model``) and stored in ccdb's settings database; there is deliberately no
# environment variable for it. A second, invisible source of truth is worse
# than none: an env var that config.toml echoes but ``--model`` overrides makes
# ``/ollama list`` and the generated config disagree about what is running.
DEFAULT_MODEL = "gpt-oss:120b"
PROVIDER_ID = "ccdb_local"

# Settings that keep a local run local. Each maps to the TOML line that must be
# present in the generated config; verify_quiet_settings() reports any that are
# missing rather than assuming the file is still what we wrote.
REQUIRED_QUIET_SETTINGS: tuple[tuple[str, str], ...] = (
    ("check_for_update_on_startup", "check_for_update_on_startup = false"),
    ("analytics", "[analytics]\nenabled = false"),
)


# Ollama ends the ``/v1/responses`` stream without a ``response.completed``
# event when its tool-call parser rejects what the model emitted — measured on
# Ollama 0.32.9, where a malformed XML tool call logged
# ``qwen tool call parsing failed`` and the generation task was cancelled
# mid-flight. The Codex CLI reports that as a transport failure and retries,
# but a retry replays the same context to the same model, so it usually fails
# the same way until the turn is lost. Naming the cause turns an opaque
# "reconnecting 1/5" loop into something the operator can act on.
_STREAM_TRUNCATED_PATTERN = re.compile(r"stream disconnected before completion", re.IGNORECASE)
LOCAL_STREAM_TRUNCATED_HINT = (
    "The local model server ended the response stream early. This is usually the "
    "model emitting a tool call its runtime cannot parse — check "
    "`journalctl -u ollama` for `tool call parsing failed` around this time. "
    "Retrying replays the same context, so it tends to fail the same way; pick a "
    "model whose tool-call format the CLI handles cleanly with `/ollama use`."
)


def _env(name: str) -> str | None:
    """Read an env var, treating blank as unset."""
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


@dataclass(frozen=True)
class LocalModelConfig:
    """Where the local model lives and which model to ask for."""

    base_url: str = DEFAULT_BASE_URL
    model: str = DEFAULT_MODEL
    codex_home: Path | None = None

    @property
    def resolved_codex_home(self) -> Path:
        return self.codex_home or Path.home() / ".ccdb" / "local-codex-home"

    @property
    def endpoint_host(self) -> str:
        return urlparse(self.base_url).hostname or self.base_url

    @classmethod
    def from_env(cls) -> LocalModelConfig:
        home = _env("CCDB_LOCAL_CODEX_HOME")
        return cls(
            base_url=_env("CCDB_LOCAL_BASE_URL") or DEFAULT_BASE_URL,
            codex_home=Path(home) if home else None,
        )


def ollama_pull_url(base_url: str) -> str:
    """Derive Ollama's native ``/api/pull`` URL from the configured API URL.

    Kept as a named function because it is part of this module's public
    surface; the derivation itself now lives in :mod:`ollama_client`.
    """
    return ollama_api_url(base_url, "/api/pull")


async def pull_ollama_model(
    model: str,
    *,
    config: LocalModelConfig | None = None,
    timeout_seconds: float = DEFAULT_OLLAMA_PULL_TIMEOUT_SECONDS,
) -> None:
    """Pull an Ollama model without blocking the Discord event loop."""
    resolved = config or LocalModelConfig.from_env()
    await _pull_model(resolved.base_url, model, timeout_seconds=timeout_seconds)


def build_local_config_toml(config: LocalModelConfig) -> str:
    """Render the ``config.toml`` for the ccdb-owned CODEX_HOME.

    ``wire_api = "responses"`` is required: codex-cli dropped support for
    ``chat`` completions, and Ollama does serve ``/v1/responses``.
    """
    return f"""# Generated by ccdb — do not edit. Regenerated on every bot start.
# The ``model`` line below is only the fallback for a run that does not name a
# model. ccdb always passes ``--model`` explicitly, resolved from the selection
# stored by /ollama use (or /model) — that selection, not this file, is what a
# thread actually runs.
# Local-model backend: this CODEX_HOME is deliberately separate from ~/.codex
# so a local thread cannot pick up cloud credentials, and so the two settings
# below cannot be undone by a change to the user's own Codex config.
model = "{config.model}"
model_provider = "{PROVIDER_ID}"

# Keep local runs local. Measured on codex-cli 0.145.0: with these two enabled,
# a fully local run still contacts chatgpt.com. With them off, the only
# endpoint contacted is the local model. Re-measure after a CLI upgrade.
check_for_update_on_startup = false

[analytics]
enabled = false

[model_providers.{PROVIDER_ID}]
name = "ccdb local model"
base_url = "{config.base_url}"
wire_api = "responses"
"""


def ensure_codex_home(config: LocalModelConfig) -> Path:
    """Create/refresh the ccdb-owned CODEX_HOME and return its path.

    Rewritten whenever the content differs, so an edited or stale file cannot
    quietly survive — the generated config is the source of truth.
    """
    home = config.resolved_codex_home
    home.mkdir(parents=True, exist_ok=True)
    target = home / "config.toml"
    desired = build_local_config_toml(config)
    try:
        current = target.read_text(encoding="utf-8")
    except OSError:
        current = None
    if current != desired:
        target.write_text(desired, encoding="utf-8")
        logger.info("Wrote local-model Codex config: %s", target)
    return home


def verify_quiet_settings(codex_home: Path) -> list[str]:
    """Return the names of the local-only settings that are NOT in place.

    An empty list means every setting we know about is present. This is a
    structural check of a file ccdb wrote, not proof that the CLI stays
    offline — see the module docstring.
    """
    target = Path(codex_home) / "config.toml"
    try:
        content = target.read_text(encoding="utf-8")
    except OSError:
        return [name for name, _ in REQUIRED_QUIET_SETTINGS]
    return [name for name, snippet in REQUIRED_QUIET_SETTINGS if snippet not in content]


class LocalCodexRunner(CodexRunner):
    """Codex CLI pinned to a local model through a ccdb-owned CODEX_HOME."""

    _execution_backend = "local"

    def __init__(
        self,
        *args: object,
        local_config: LocalModelConfig | None = None,
        **kwargs: object,
    ) -> None:
        self.local_config = local_config or LocalModelConfig.from_env()
        # ``setdefault`` is not enough: create_backend() always passes
        # ``model=None`` when no selection is stored, which leaves the key
        # present and the default unapplied. The runner would then omit
        # ``--model`` and silently fall back to whatever config.toml happens to
        # say — a second source of truth for the one thing /ollama reports.
        if not kwargs.get("model"):
            kwargs["model"] = self.local_config.model
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]

    async def run(
        self,
        prompt: str,
        session_id: str | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Run Codex against the local model, naming local-only failure modes."""
        async for event in super().run(prompt, session_id):
            if event.error and _STREAM_TRUNCATED_PATTERN.search(event.error):
                logger.warning(
                    "Local model %s truncated the response stream: %s",
                    self.model,
                    event.error,
                )
                event.error = f"{event.error}\n\n{LOCAL_STREAM_TRUNCATED_HINT}"
            yield event

    def _build_env(self) -> dict[str, str]:
        env = super()._build_env()
        home = ensure_codex_home(self.local_config)
        missing = verify_quiet_settings(home)
        if missing:
            # Fail loudly rather than run a "local" thread that phones home.
            raise RuntimeError(
                "Refusing to start the local backend: "
                f"{', '.join(missing)} missing from {home / 'config.toml'}. "
                "Without these the CLI contacts its vendor even when the model "
                "is local."
            )
        env["CODEX_HOME"] = str(home)
        # Any cloud credentials in the ambient environment would defeat the
        # point of the separate CODEX_HOME.
        for key in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "CODEX_API_KEY"):
            env.pop(key, None)
        return env

    def clone(self, **kwargs: object) -> LocalCodexRunner:
        cloned = super().clone(**kwargs)  # type: ignore[arg-type]
        local = LocalCodexRunner(
            command=cloned.command,
            model=cloned.model,
            permission_mode=cloned.permission_mode,
            working_dir=cloned.working_dir,
            timeout_seconds=cloned.timeout_seconds,
            dangerously_skip_permissions=cloned.dangerously_skip_permissions,
            allowed_tools=cloned.allowed_tools,
            api_port=cloned.api_port,
            api_secret=cloned.api_secret,
            thread_id=cloned.thread_id,
            append_system_prompt=cloned.append_system_prompt,
            effort=cloned.effort,
            local_config=self.local_config,
        )
        local.execution_mode = cloned.execution_mode
        return local

    def describe_api(self) -> str:
        return f"Local model ({self.local_config.endpoint_host})"
