"""Slash commands typed into the console's reply box.

The chat frontends have ``/backend``, ``/model`` and ``/effort`` as Discord
application commands; the console offers the same three, scoped to the one
conversation the human is looking at, plus the skills of whichever backend
that conversation runs on. The catalog is what the client autocompletes from:
backends, the models and effort levels valid for the *current* backend, and
its CLI built-ins (``/compact`` …, passed through as plain text) and its
skills — so switching backend changes what is offered next.

Settings go through ``BackendSettings`` with the thread key, exactly where the
Discord commands put a thread override, so a thread switched here is switched
in Discord too.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..backend_settings import ALL_BACKENDS
from ..cogs.backend_command import EFFORT_ORDER, SUGGESTED_MODELS, VALID_EFFORTS
from ..model_catalog import claude_model_choices, codex_model_choices, pi_model_choices

logger = logging.getLogger(__name__)

CONTROL_COMMANDS: tuple[tuple[str, str], ...] = (
    ("backend", "Switch the AI backend for this conversation"),
    ("model", "Choose the model for the current backend"),
    ("effort", "Set the reasoning effort for the current backend"),
)
#: CLI built-ins offered for completion. Claude Code runs them itself, so they go
#: out as plain text. ``codex exec`` reads them as words for the model, so the
#: Codex ones are *handled* here (``/compact`` through ``codex app-server``).
BUILTIN_COMMANDS: dict[str, tuple[tuple[str, str], ...]] = {
    "claude": (
        ("compact", "Summarize the conversation so far to free up context"),
        ("context", "Show how much of the context window is in use"),
        ("cost", "Show the token usage and cost of this session"),
    ),
    "codex": (("compact", "Summarize the conversation so far to free up context"),),
}
HANDLED_BUILTINS: dict[str, frozenset[str]] = {"codex": frozenset({"compact"})}


def builtins_for(backend: str) -> list[dict[str, Any]]:
    handled = HANDLED_BUILTINS.get(backend, frozenset())
    return [
        {"name": n, "description": d, "handled": n in handled}
        for n, d in BUILTIN_COMMANDS.get(backend, ())
    ]


MAX_SKILLS = 400

#: Loads ``[{name, description}]`` for a backend. Injected so tests need no disk.
SkillLoader = Callable[[str], Sequence[dict[str, str]]]


class CommandError(ValueError):
    """A command the console refuses, with a message fit to show the human."""


@dataclass(frozen=True)
class CommandResult:
    message: str


async def model_choices(backend: str) -> list[dict[str, str]]:
    """Model suggestions for *backend*: discovered where possible, the static list otherwise."""
    fallback = SUGGESTED_MODELS.get(backend, [])
    try:
        if backend == "claude":
            pairs = await claude_model_choices(fallback=fallback)
        elif backend == "codex":
            pairs = codex_model_choices(fallback=fallback)
        elif backend == "pi":
            pairs = pi_model_choices(fallback=fallback)
        else:
            pairs = fallback
    except Exception:
        # Discovery is a convenience; it must never take the command list down.
        logger.warning("console: model discovery failed for %s", backend, exc_info=True)
        pairs = fallback
    return [{"value": value, "label": label} for value, label in pairs]


def effort_levels(backend: str) -> list[str]:
    return list(EFFORT_ORDER.get(backend) or sorted(VALID_EFFORTS.get(backend, frozenset())))


async def catalog(
    settings: Any,
    thread_key: int,
    skills: SkillLoader,
    models: Callable[[str], Awaitable[list[dict[str, str]]]] = model_choices,
) -> dict[str, Any]:
    """What the reply box can autocomplete for this conversation, right now."""
    backend = await settings.current_backend(thread_key)
    return {
        "backend": backend,
        "model": await settings.current_model(backend, thread_key),
        "effort": await settings.current_effort(backend, thread_key),
        "commands": [{"name": n, "description": d} for n, d in CONTROL_COMMANDS],
        "builtins": builtins_for(backend),
        "backends": list(ALL_BACKENDS),
        "models": await models(backend),
        "efforts": effort_levels(backend),
        "skills": list(skills(backend))[:MAX_SKILLS],
    }


async def run_command(settings: Any, thread_key: int, name: str, arg: str) -> CommandResult:
    """Apply ``/backend``, ``/model`` or ``/effort`` to this conversation only."""
    arg = arg.strip()
    backend = await settings.current_backend(thread_key)
    if name == "backend":
        if arg not in ALL_BACKENDS:
            raise CommandError(f"Unknown backend `{arg}`. Choose: {', '.join(ALL_BACKENDS)}.")
        await settings.set_backend(arg, thread_id=thread_key)
        return CommandResult(f"Backend set to `{arg}` for this conversation.")
    if name == "model":
        if not arg or len(arg) > 200 or any(c.isspace() for c in arg):
            raise CommandError("Give one model name, e.g. `/model opus`.")
        await settings.set_model(backend, arg, thread_id=thread_key)
        return CommandResult(f"Model for `{backend}` set to `{arg}` for this conversation.")
    if name == "effort":
        level = arg.lower()
        valid = VALID_EFFORTS.get(backend, frozenset())
        if level not in valid:
            choices = ", ".join(effort_levels(backend))
            raise CommandError(f"Unknown effort `{arg}` for `{backend}`. Choose: {choices}.")
        await settings.set_effort(backend, level, thread_id=thread_key)
        return CommandResult(f"Effort for `{backend}` set to `{level}` for this conversation.")
    raise CommandError(f"Unknown command `/{name}`.")


def skill_prompt(backend: str, name: str, args: str) -> str:
    """How a skill is asked for. Claude Code runs ``/name`` itself; other CLIs get it in words."""
    args = args.strip()
    if backend == "claude":
        return f"/{name} {args}".rstrip()
    return f'Use the "{name}" skill.' + (f"\n\n{args}" if args else "")


def default_skill_loader(skill_cog: Any = None, home: Path | None = None) -> SkillLoader:
    """Skills as each backend sees them.

    Claude: the ``/skill`` command's own list (user skills plus plugin skills),
    reloaded the way that command reloads it. Codex and the others: Codex reads
    ``~/.codex/skills`` and ``~/.claude/skills`` both, so offer the union.
    """
    from ..cogs.skill_command import _collect_skills, _get_plugin_skill_dirs, _load_skills

    base = home or Path.home()

    def claude_skills() -> list[dict[str, str]]:
        if skill_cog is not None and hasattr(skill_cog, "_skills"):
            reload = getattr(skill_cog, "_maybe_reload_skills", None)
            if callable(reload):
                reload()
            return list(skill_cog._skills)
        return _collect_skills(base / ".claude" / "skills", _get_plugin_skill_dirs())

    def load(backend: str) -> list[dict[str, str]]:
        try:
            found = claude_skills()
            if backend != "claude":
                names = {s["name"] for s in found}
                found += [
                    s for s in _load_skills(base / ".codex" / "skills") if s["name"] not in names
                ]
            return sorted(found, key=lambda s: s["name"])
        except Exception:
            logger.warning("console: could not list skills", exc_info=True)
            return []

    return load
