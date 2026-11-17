"""The AI for one turn, chosen on a Console screen and never stored (canvas mode, ADR-0025).

The secretary canvas and the focus session let the person pick an AI for what they send from
that screen: backend, connection, model and effort. It applies to the turns those sends start
and to nothing else. The global default, each thread's saved choice, scheduled tasks, a wait's
automatic resume and child threads keep their own settings, because nothing here is written to
:class:`~.backend_settings.BackendSettings`. The choice travels with the turn instead.

Every field is checked against what the relay offers; a value that is not offered is refused,
so a browser cannot put an arbitrary string on a CLI command line.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_MODEL = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/\[\]-]{0,99}$")
_FIELDS = frozenset({"backend", "model", "effort", "connection"})


class TurnAIError(ValueError):
    """A screen AI the relay does not offer."""


@dataclass(frozen=True)
class TurnAI:
    """``None`` in a field: the thread's own setting for it."""

    backend: str
    model: str | None = None
    effort: str | None = None
    connection: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "backend": self.backend,
            "model": self.model,
            "effort": self.effort,
            "connection": self.connection,
        }


def parse_turn_ai(raw: Any) -> TurnAI | None:
    """Validate a screen AI from a request body; ``None`` when none was sent."""
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise TurnAIError("ai must be an object with backend, model, effort, connection")
    unknown = sorted(set(raw) - _FIELDS)
    if unknown:
        raise TurnAIError(f"unknown ai fields: {', '.join(unknown)}")
    from claude_code_core.child_env import cli_environment
    from claude_code_core.connections import connections_for

    from .backend_settings import ALL_BACKENDS
    from .console.commands import effort_levels

    backend = raw.get("backend")
    if backend not in ALL_BACKENDS:
        raise TurnAIError(f"backend must be one of {', '.join(ALL_BACKENDS)}")
    model = _optional(raw.get("model"))
    if model is not None and not _MODEL.match(model):
        raise TurnAIError("model must be a model id (letters, digits, . _ : / - [ ])")
    effort = _optional(raw.get("effort"))
    if effort is not None and effort not in effort_levels(str(backend)):
        raise TurnAIError(f"effort {effort!r} is not offered for {backend}")
    connection = _optional(raw.get("connection"))
    if connection is not None:
        offered = {c.id for c in connections_for(str(backend), cli_environment())}
        if connection not in offered:
            raise TurnAIError(f"connection {connection!r} is not offered for {backend}")
    return TurnAI(backend=str(backend), model=model, effort=effort, connection=connection)


def _optional(value: Any) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise TurnAIError("ai fields must be strings")
    return value.strip() or None
