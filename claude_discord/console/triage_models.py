"""Which model triages: the cheapest one of the backend ccdb is running on.

Triage follows the backend chosen with ``/backend``: Claude triages with
Haiku, Codex with the cheapest model in its own catalog (the "fast and
affordable" tier, newest generation first). A backend that is neither, or
whose CLI is missing, falls back to whichever of the two is installed.

Both are one-shot, no-tools calls: ``claude -p`` with tools, hooks and settings
off; ``codex exec`` ephemeral, read-only, without the user's config or rules,
in a scratch directory, the prompt on stdin.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import tempfile
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from claude_code_core.runner import resolve_cli_argv

from ..model_catalog import CODEX_LISTED_VISIBILITY, CODEX_MODELS_CACHE
from .triage import ASK_TIMEOUT_SECONDS, ask_model, triage_env

logger = logging.getLogger(__name__)

CLAUDE_TRIAGE_MODEL = "haiku"
#: Words a Codex catalog uses for its cheap tier.
_CHEAP = re.compile(r"\b(affordable|cheap|cheapest|efficient|fast|lite|mini|nano)\b", re.I)


def cheapest_codex_model(env: Mapping[str, str] | None = None) -> str | None:
    """The cheapest listed model in Codex's own catalog, or None when it cannot tell.

    The catalog has no prices, so "cheapest" is the newest model whose
    description or slug names the cheap tier ("Fast and affordable …").
    """
    env = os.environ if env is None else env
    home = env.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex")
    try:
        with open(os.path.join(home, CODEX_MODELS_CACHE), encoding="utf-8") as f:
            payload = json.load(f)
    except (OSError, ValueError):
        return None
    return pick_cheap_codex_model(payload)


def pick_cheap_codex_model(payload: Any) -> str | None:
    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return None
    best: tuple[int, int, str] | None = None
    for index, entry in enumerate(models):
        if not isinstance(entry, dict) or not isinstance(entry.get("slug"), str):
            continue
        if str(entry.get("visibility") or CODEX_LISTED_VISIBILITY) != CODEX_LISTED_VISIBILITY:
            continue
        text = f"{entry['slug']} {entry.get('description') or ''}"
        if not _CHEAP.search(text):
            continue
        priority = entry.get("priority")
        rank = (priority if isinstance(priority, int) else len(models), index, entry["slug"])
        best = rank if best is None or rank < best else best
    return best[2] if best else None


async def ask_codex(
    prompt: str,
    *,
    command: str = "codex",
    model: str | None = None,
    timeout: float = ASK_TIMEOUT_SECONDS,
) -> str | None:
    """One ephemeral, read-only ``codex exec``; its final message, or None."""
    argv = resolve_cli_argv(command)
    if argv is None:
        logger.warning("console triage: cannot start %s on this platform", command)
        return None
    args = [
        *argv,
        "exec",
        "--ephemeral",
        "--skip-git-repo-check",
        "--ignore-user-config",
        "--ignore-rules",
        "--sandbox",
        "read-only",
        "-c",
        'model_reasoning_effort="low"',
        "-C",
        tempfile.gettempdir(),
    ]
    if model:
        args += ["-m", model]
    args.append("-")  # the prompt comes on stdin
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=triage_env(),
            cwd=tempfile.gettempdir(),
        )
    except (OSError, ValueError) as exc:
        logger.warning("console triage: could not start %s: %s", command, exc)
        return None
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(prompt.encode()), timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.communicate()
        logger.warning("console triage: Codex did not answer in %ss", timeout)
        return None
    if proc.returncode != 0:
        logger.warning(
            "console triage: codex exit %s: %s",
            proc.returncode,
            stderr.decode(errors="replace")[-300:],
        )
        return None
    return stdout.decode(errors="replace")


class BackendAsk:
    """Ask the cheapest model of the current backend; ``model`` names the last one used."""

    def __init__(
        self,
        current_backend: Callable[[], Awaitable[str]] | None,
        *,
        claude_command: str | None,
        codex_command: str | None,
        claude_model: str = CLAUDE_TRIAGE_MODEL,
        codex_model: str | None = None,
    ) -> None:
        self._backend = current_backend
        self._claude = claude_command
        self._codex = codex_command
        self._claude_model = claude_model
        self._codex_model = codex_model
        self.model = claude_model if claude_command else (codex_model or "codex")

    async def _choice(self) -> str | None:
        backend = "claude"
        if self._backend is not None:
            try:
                backend = await self._backend()
            except Exception:
                logger.warning("console triage: could not read the backend", exc_info=True)
        if backend == "codex" and self._codex:
            return "codex"
        if backend == "claude" and self._claude:
            return "claude"
        return "claude" if self._claude else ("codex" if self._codex else None)

    async def __call__(self, prompt: str) -> str | None:
        choice = await self._choice()
        if choice == "codex" and self._codex:
            model = self._codex_model or cheapest_codex_model()
            self.model = model or "codex"
            return await ask_codex(prompt, command=self._codex, model=model)
        if choice == "claude" and self._claude:
            self.model = self._claude_model
            return await ask_model(prompt, command=self._claude, model=self._claude_model)
        return None


def installed(command: str) -> str | None:
    """*command* if it is on PATH (or a path that exists), else None."""
    return command if shutil.which(command) else None
