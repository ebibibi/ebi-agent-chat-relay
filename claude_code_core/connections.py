"""Connections: *where* a backend's CLI sends its requests, and who pays.

"Claude" or "Codex" alone does not say what a turn costs. Claude Code can run
on the operator's Claude subscription or on Microsoft Foundry, Bedrock, Vertex
or an API key; Codex can run on a ChatGPT subscription or on any
``[model_providers.<id>]`` in its ``config.toml`` (an Azure / Foundry endpoint,
for example). A *connection* names one of those routes so it can be chosen and,
just as important, shown.

Connections are discovered, never declared (zero-config):

* Claude: ``subscription`` always exists. When the environment the CLI runs
  with selects a third-party provider (``CLAUDE_CODE_USE_FOUNDRY``, Bedrock,
  Vertex, ``ANTHROPIC_BASE_URL``, an API key), that route is offered too as
  ``provider``, labelled the way :func:`detect_api_provider` names it.
* Codex: ``subscription`` (the built-in ``openai`` provider, i.e. the ChatGPT
  sign-in) plus every ``[model_providers.<id>]`` table in
  ``$CODEX_HOME/config.toml``.

The *default* is whatever the deployment already does (the environment for
Claude, ``model_provider`` for Codex), so a relay that never chooses behaves
exactly as before. Choosing changes only the child process: Claude's
subscription route drops the provider variables from its environment; a Codex
route becomes ``-c model_provider="<id>"``. No credential is read or moved.
"""

from __future__ import annotations

import logging
import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .api_provider import detect_api_provider

logger = logging.getLogger(__name__)

SUBSCRIPTION = "subscription"
#: Claude's non-subscription route: whatever the deployment's environment selects.
PROVIDER = "provider"
CONNECTION_BACKENDS = ("claude", "codex")

#: The variables that move Claude Code off the subscription. Dropped for the
#: subscription route; ``CLAUDE_CODE_OAUTH_TOKEN`` *is* the subscription and stays.
CLAUDE_PROVIDER_KEYS = frozenset(
    {
        "CLAUDE_CODE_USE_FOUNDRY",
        "CLAUDE_CODE_USE_BEDROCK",
        "CLAUDE_CODE_USE_VERTEX",
        "ANTHROPIC_FOUNDRY_RESOURCE",
        "ANTHROPIC_FOUNDRY_BASE_URL",
        "ANTHROPIC_FOUNDRY_API_KEY",
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
    }
)
#: Codex's own name for the ChatGPT / OpenAI provider.
CODEX_OPENAI_PROVIDER = "openai"
_PROVIDER_ID = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
_AZURE_HOSTS = (".azure.com", ".azure.net", ".azure-api.net", ".microsoft.com")


@dataclass(frozen=True)
class Connection:
    """One route a backend can take. ``kind`` drives the badge the UI shows."""

    backend: str
    id: str
    #: ``subscription`` | ``foundry`` | ``bedrock`` | ``vertex`` | ``api``
    kind: str
    label: str
    detail: str | None = None
    default: bool = False
    #: The model ``config.toml`` names for this route (Codex default route only).
    model: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── discovery ──────────────────────────────────────────────


def _claude_kind(label: str) -> str:
    lowered = label.lower()
    for key in ("foundry", "bedrock", "vertex"):
        if key in lowered:
            return key
    return "api"


def claude_connections(env: Mapping[str, str]) -> list[Connection]:
    provider = detect_api_provider(env)
    keyed = bool((env.get("ANTHROPIC_API_KEY") or env.get("ANTHROPIC_AUTH_TOKEN") or "").strip())
    third_party = provider != "Anthropic API (direct)" or keyed
    subs = Connection(
        "claude", SUBSCRIPTION, SUBSCRIPTION, "Claude subscription", None, not third_party
    )
    if not third_party:
        return [subs]
    label = provider if provider != "Anthropic API (direct)" else "Anthropic API key"
    return [subs, Connection("claude", PROVIDER, _claude_kind(label), label, None, True)]


def codex_home(env: Mapping[str, str]) -> Path:
    home = (env.get("CODEX_HOME") or "").strip()
    return Path(home) if home else Path(env.get("HOME") or Path.home()) / ".codex"


def _read_codex_config(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, tomllib.TOMLDecodeError):
        logger.warning("connections: could not read %s", path, exc_info=True)
        return {}
    return data if isinstance(data, dict) else {}


def codex_connections(env: Mapping[str, str]) -> list[Connection]:
    config = _read_codex_config(codex_home(env) / "config.toml")
    current = config.get("model_provider")
    current = current if isinstance(current, str) and current else CODEX_OPENAI_PROVIDER
    found = [
        Connection(
            "codex",
            SUBSCRIPTION,
            SUBSCRIPTION,
            "ChatGPT subscription",
            None,
            current == CODEX_OPENAI_PROVIDER,
        )
    ]
    providers = config.get("model_providers")
    for pid, table in providers.items() if isinstance(providers, dict) else []:
        if (
            pid == CODEX_OPENAI_PROVIDER
            or not _PROVIDER_ID.match(pid)
            or not isinstance(table, dict)
        ):
            continue
        host = urlparse(str(table.get("base_url") or "")).hostname or None
        kind = "foundry" if host and host.endswith(_AZURE_HOSTS) else "api"
        name = table.get("name")
        label = name if isinstance(name, str) and name.strip() else pid
        model = config.get("model") if pid == current else None
        found.append(
            Connection(
                "codex",
                pid,
                kind,
                label.strip()[:80],
                host,
                pid == current,
                model if isinstance(model, str) and model else None,
            )
        )
    return found


def connections_for(backend: str, env: Mapping[str, str] | None = None) -> list[Connection]:
    """Every route *backend* can take here, the deployment's own first-class default marked."""
    source = os.environ if env is None else env
    if backend == "claude":
        return claude_connections(source)
    if backend == "codex":
        return codex_connections(source)
    return []


def resolve(
    backend: str, chosen: str | None, env: Mapping[str, str] | None = None
) -> Connection | None:
    """The connection a turn uses: *chosen* when it still exists, else the default."""
    options = connections_for(backend, env)
    if not options:
        return None
    for c in options:
        if chosen and c.id == chosen:
            return c
    return next((c for c in options if c.default), options[0])


# ── application to a child process ─────────────────────────


def apply_claude_env(connection: Connection | None, env: dict[str, str]) -> dict[str, str]:
    """*env* for Claude Code on *connection* (a new dict; the input is not changed)."""
    if connection is None or connection.backend != "claude" or connection.id != SUBSCRIPTION:
        return env
    return {k: v for k, v in env.items() if k not in CLAUDE_PROVIDER_KEYS}


def codex_args(connection: Connection | None) -> list[str]:
    """``-c model_provider=…`` for a Codex route that is not the configured default."""
    if connection is None or connection.backend != "codex" or connection.default:
        return []
    provider = CODEX_OPENAI_PROVIDER if connection.id == SUBSCRIPTION else connection.id
    if not _PROVIDER_ID.match(provider):
        raise ValueError(f"invalid Codex provider id {provider!r}")
    return ["-c", f'model_provider="{provider}"']


__all__ = [
    "CONNECTION_BACKENDS",
    "PROVIDER",
    "SUBSCRIPTION",
    "Connection",
    "apply_claude_env",
    "codex_args",
    "connections_for",
    "resolve",
]
