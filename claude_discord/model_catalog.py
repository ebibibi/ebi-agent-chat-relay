"""Discover which models this installation can actually select.

``/model``'s autocomplete used to be a hardcoded list, so it went stale on every
model launch — it was still offering "Opus 4.8" the week Opus 5 shipped. This
module asks Anthropic's ``GET /v1/models`` which models the local credentials
can see, and derives the alias suggestions (``opus``, ``sonnet``, ...) from that
answer instead of from a constant.

This is the one place ccdb talks to the Anthropic API instead of the Claude Code
CLI, because the CLI exposes no way to enumerate models. It is deliberately
non-essential: no credentials, no network, a third-party provider or a disabled
lookup all degrade to the caller's static fallback list. Nothing here ever
raises into the Discord command path, and the token is only ever read to build a
request header — never logged.

Codex models are discovered too, but from the catalog the Codex CLI already
fetched for itself on disk rather than from a second vendor API — see the
"Codex" section at the bottom of this module.

Opt out with ``CCDB_MODEL_DISCOVERY=0``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://api.anthropic.com"
MODELS_PATH = "/v1/models?limit=100"
ANTHROPIC_VERSION = "2023-06-01"
OAUTH_BETA = "oauth-2025-04-20"

#: Autocomplete fires on every keystroke, so a lookup is reused for hours.
CACHE_TTL_SECONDS = 6 * 60 * 60
#: A failed lookup is remembered briefly so typing doesn't retry the network.
FAILURE_TTL_SECONDS = 5 * 60
REQUEST_TIMEOUT_SECONDS = 5.0

#: CLI aliases that mean "the newest model of this family" (``claude --model``).
ALIAS_FAMILIES = ("opus", "sonnet", "haiku", "fable")

#: Env vars that mean the CLI authenticates against a third-party gateway, where
#: api.anthropic.com knows nothing about the available models.
THIRD_PARTY_PROVIDER_VARS = (
    "CLAUDE_CODE_USE_BEDROCK",
    "CLAUDE_CODE_USE_VERTEX",
    "CLAUDE_CODE_USE_FOUNDRY",
)


@dataclass(frozen=True)
class ModelInfo:
    """One model as reported by the API."""

    id: str
    display_name: str
    created_at: str


# (choices, expires_at) — ``None`` choices means "the last lookup failed".
_cache: tuple[list[tuple[str, str]] | None, float] | None = None
_cache_lock: asyncio.Lock | None = None


def reset_cache() -> None:
    """Drop the memoised lookup (tests, and anyone re-authenticating)."""
    global _cache
    _cache = None


def _lock() -> asyncio.Lock:
    """Lazily create the lock so importing this module needs no event loop."""
    global _cache_lock
    if _cache_lock is None:
        _cache_lock = asyncio.Lock()
    return _cache_lock


def parse_models(payload: Mapping[str, Any]) -> list[ModelInfo]:
    """Turn a ``/v1/models`` payload into models, newest first."""
    models: list[ModelInfo] = []
    for entry in payload.get("data", []):
        if not isinstance(entry, dict):
            continue
        model_id = entry.get("id")
        if not isinstance(model_id, str) or not model_id:
            continue
        models.append(
            ModelInfo(
                id=model_id,
                display_name=str(entry.get("display_name") or model_id),
                created_at=str(entry.get("created_at") or ""),
            )
        )
    # Explicit sort: the API happens to return newest first, but the alias
    # mapping ("opus" → newest opus) must not depend on that.
    models.sort(key=lambda m: m.created_at, reverse=True)
    return models


def _family_of(model_id: str) -> str | None:
    """The alias family a model id belongs to, if any."""
    for family in ALIAS_FAMILIES:
        if family in model_id:
            return family
    return None


def build_choices(models: list[ModelInfo]) -> list[tuple[str, str]]:
    """Build ``(value, description)`` suggestions: aliases first, then ids.

    Aliases are ordered by how recently their family shipped, so the newest
    generation sits at the top of the dropdown without anyone editing a list.
    """
    newest_per_family: dict[str, ModelInfo] = {}
    known: list[ModelInfo] = []
    for model in models:  # already newest-first
        family = _family_of(model.id)
        if family is None:
            continue
        known.append(model)
        newest_per_family.setdefault(family, model)

    aliases = [
        (family, f"{model.display_name} (alias → {model.id})")
        for family, model in newest_per_family.items()
    ]
    return aliases + [(model.id, model.display_name) for model in known]


def _credentials_path(env: Mapping[str, str]) -> Path:
    config_dir = env.get("CLAUDE_CONFIG_DIR") or os.path.join(Path.home(), ".claude")
    return Path(config_dir) / ".credentials.json"


def _oauth_token(env: Mapping[str, str]) -> str | None:
    """The Claude Code CLI's own OAuth token, if present and unexpired."""
    try:
        raw = _credentials_path(env).read_text(encoding="utf-8")
        oauth = json.loads(raw).get("claudeAiOauth") or {}
    except (OSError, ValueError):
        return None

    token = oauth.get("accessToken")
    if not isinstance(token, str) or not token:
        return None
    expires_at = oauth.get("expiresAt")
    if isinstance(expires_at, (int, float)) and expires_at / 1000 <= time.time():
        return None
    return token


def auth_headers(env: Mapping[str, str] | None = None) -> dict[str, str] | None:
    """Request headers for the models endpoint, or ``None`` if we can't ask.

    Mirrors how the CLI itself authenticates: explicit API key, explicit auth
    token, then the OAuth credentials written by ``claude auth``.
    """
    env = os.environ if env is None else env
    if any(env.get(var) for var in THIRD_PARTY_PROVIDER_VARS):
        return None

    base = {"anthropic-version": ANTHROPIC_VERSION, "accept": "application/json"}

    api_key = env.get("ANTHROPIC_API_KEY")
    if api_key:
        return {**base, "x-api-key": api_key}

    token = env.get("ANTHROPIC_AUTH_TOKEN") or _oauth_token(env)
    if token:
        return {**base, "Authorization": f"Bearer {token}", "anthropic-beta": OAUTH_BETA}
    return None


def _get_json(url: str, headers: Mapping[str, str], timeout: float) -> dict[str, Any]:
    """Blocking GET returning parsed JSON. Raises on any transport error."""
    request = urllib.request.Request(url, headers=dict(headers), method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https URL
        return json.loads(response.read().decode("utf-8"))


def _fetch_choices(env: Mapping[str, str]) -> list[tuple[str, str]]:
    """Blocking model lookup. Raises if the API can't be reached or parsed."""
    headers = auth_headers(env)
    if headers is None:
        raise LookupError("no Anthropic credentials available for model discovery")
    base_url = (env.get("ANTHROPIC_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    payload = _get_json(base_url + MODELS_PATH, headers, REQUEST_TIMEOUT_SECONDS)
    return build_choices(parse_models(payload))


async def claude_model_choices(
    *,
    fallback: list[tuple[str, str]],
    env: Mapping[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Suggestions for the Claude ``/model`` autocomplete, cached and safe.

    Returns ``fallback`` unchanged whenever discovery is disabled, impossible or
    fails — the command must keep working offline.
    """
    global _cache
    env = os.environ if env is None else env
    if env.get("CCDB_MODEL_DISCOVERY", "1").strip().lower() in {"0", "false", "no", "off"}:
        return fallback

    async with _lock():
        now = time.monotonic()
        if _cache is not None and now < _cache[1]:
            cached = _cache[0]
            return cached if cached is not None else fallback

        try:
            choices = await asyncio.to_thread(_fetch_choices, env)
        except Exception as exc:  # network, auth, malformed payload — all non-fatal
            logger.warning("Model discovery failed, using static suggestions: %s", exc)
            _cache = (None, now + FAILURE_TTL_SECONDS)
            return fallback

        if not choices:
            logger.warning("Model discovery returned no usable models, using static suggestions")
            _cache = (None, now + FAILURE_TTL_SECONDS)
            return fallback

        _cache = (choices, now + CACHE_TTL_SECONDS)
        return choices


# ── Codex ──────────────────────────────────────────────────────────────
#
# The Codex CLI has no "list models" subcommand, but it already solves this
# problem for itself: it fetches its model catalog from OpenAI and writes the
# answer to ``$CODEX_HOME/models_cache.json``. Reading that file gives ccdb the
# same list the Codex console shows — including generation changes like GPT-6 —
# without ccdb making a vendor call of its own (see CLAUDE.md decision 13) and
# without a constant that goes stale on every launch.

#: Written by the Codex CLI itself; ccdb only ever reads it.
CODEX_MODELS_CACHE = "models_cache.json"

#: Models the CLI marks ``hide`` are internal (auto-review, reserve capacity)
#: and must not be offered as a chat model.
CODEX_LISTED_VISIBILITY = "list"


def _codex_home(env: Mapping[str, str]) -> Path:
    return Path(env.get("CODEX_HOME") or os.path.join(Path.home(), ".codex"))


def parse_codex_models(payload: Mapping[str, Any]) -> list[tuple[str, str]]:
    """Turn a ``models_cache.json`` payload into ``(slug, description)`` pairs.

    Ordered by the catalog's own ``priority`` so the newest generation leads the
    dropdown, exactly as it does in the Codex console.
    """
    models = payload.get("models")
    if not isinstance(models, list):
        return []
    ranked: list[tuple[int, int, str, str]] = []
    for index, entry in enumerate(models):
        if not isinstance(entry, dict):
            continue
        slug = entry.get("slug")
        if not isinstance(slug, str) or not slug:
            continue
        if str(entry.get("visibility") or CODEX_LISTED_VISIBILITY) != CODEX_LISTED_VISIBILITY:
            continue
        display = str(entry.get("display_name") or slug)
        description = str(entry.get("description") or "").strip()
        priority = entry.get("priority")
        # Missing priority sorts last but keeps file order among its peers.
        rank = priority if isinstance(priority, int) else len(models)
        # The slug is already the autocomplete's prefix and ``display_name`` is
        # only its prettified form ("gpt-6-astra" / "GPT-6-Astra"), so prefer
        # the one-line description that actually distinguishes the models.
        label = description or display
        ranked.append((rank, index, slug, label))
    ranked.sort(key=lambda item: (item[0], item[1]))
    return [(slug, label) for _, _, slug, label in ranked]


def codex_model_choices(
    *,
    fallback: list[tuple[str, str]],
    env: Mapping[str, str] | None = None,
) -> list[tuple[str, str]]:
    """Suggestions for the Codex ``/model`` autocomplete, read from disk.

    Returns ``fallback`` unchanged when the cache is absent, unreadable or
    empty — a user who has never run the Codex CLI must still get suggestions.
    No caching: this is a local file read, and the CLI rewrites it whenever the
    catalog changes.
    """
    env = os.environ if env is None else env
    if env.get("CCDB_MODEL_DISCOVERY", "1").strip().lower() in {"0", "false", "no", "off"}:
        return fallback
    try:
        raw = (_codex_home(env) / CODEX_MODELS_CACHE).read_text(encoding="utf-8")
        choices = parse_codex_models(json.loads(raw))
    except (OSError, ValueError, AttributeError, TypeError) as exc:
        logger.warning("Codex model discovery failed, using static suggestions: %s", exc)
        return fallback
    return choices or fallback
