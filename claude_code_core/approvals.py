"""Approval and input requests, expressed in the frontend-neutral vocabulary.

A tool permission request, a plan approval and an MCP elicitation are the same
shape once the Discord embeds are peeled away: something asks a question, a
person answers, and the answer goes back to the CLI as a tool result. This
module owns that translation, so every frontend inherits it — a Teams surface
that implements ``prompt_choice`` gets permission approval without writing a
single line of approval logic.

Prompt and result are deliberately kept together
------------------------------------------------
Each request has a ``*_prompt`` builder and a ``*_result`` reader, defined side
by side. They are two halves of one contract: the choice *values* the prompt
offers are the same strings the reader matches on. Split across modules, a
renamed value would silently start meaning "denied" — the payload readers here
default to the safe answer for anything they do not recognise, and living next
to the builder is what makes that default a backstop rather than the norm.

Timeouts fail closed
--------------------
Every prompt carries ``default_on_timeout``, and every one of them points at
the refusing choice. An unattended session must deny a tool, cancel a plan and
abandon an elicitation rather than proceed unsupervised or hang forever.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Literal

from .frontend import Choice, ChoicePrompt, FormField, FormPrompt, Mention
from .types import ElicitationRequest, PermissionRequest

logger = logging.getLogger(__name__)

FieldKind = Literal["text", "multiline", "number", "choice", "toggle"]

__all__ = [
    "ALLOW",
    "APPROVE",
    "CANCEL",
    "DENY",
    "DONE",
    "ELICITATION_TIMEOUT_SECONDS",
    "PERMISSION_TIMEOUT_SECONDS",
    "PLAN_TIMEOUT_SECONDS",
    "elicitation_form_prompt",
    "elicitation_form_result",
    "elicitation_url_prompt",
    "elicitation_url_result",
    "permission_prompt",
    "permission_approvers",
    "permission_result",
    "permission_timeout_seconds",
    "plan_prompt",
    "plan_result",
]

# Choice values. These travel into the payload readers below, and into the
# conformance suite, so they are part of the contract rather than labels.
ALLOW = "allow"
DENY = "deny"
APPROVE = "approve"
CANCEL = "cancel"
DONE = "done"

PERMISSION_TIMEOUT_SECONDS = 120.0
PLAN_TIMEOUT_SECONDS = 300.0
ELICITATION_TIMEOUT_SECONDS = 300.0

#: Longest tool input we quote back in a permission question. Surfaces impose
#: their own limits, but a multi-megabyte Write payload should not be handed to
#: one at all — the decision rests on which tool and which target, not on
#: reading the whole body.
MAX_TOOL_INPUT_CHARS = 3000

#: Discord modals hold five inputs; other surfaces hold more. The cap is a
#: rendering concern, so it is not applied here — a surface truncates what it
#: cannot show. This only bounds pathological schemas.
MAX_FORM_FIELDS = 25

_TRUNCATION_NOTE = "\n... (truncated)"


def _format_tool_input(tool_input: dict[str, Any]) -> str:
    """Render tool arguments as readable JSON, bounded in length."""
    try:
        text = json.dumps(tool_input, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        text = str(tool_input)
    if len(text) > MAX_TOOL_INPUT_CHARS:
        text = text[:MAX_TOOL_INPUT_CHARS] + _TRUNCATION_NOTE
    return text


# ---------------------------------------------------------------------------
# Tool permission
# ---------------------------------------------------------------------------


def permission_timeout_seconds() -> float:
    """How long a permission prompt waits. ``CCDB_PERMISSION_TIMEOUT_SECONDS`` overrides."""
    raw = os.getenv("CCDB_PERMISSION_TIMEOUT_SECONDS", "").strip()
    try:
        value = float(raw) if raw else PERMISSION_TIMEOUT_SECONDS
    except ValueError:
        return PERMISSION_TIMEOUT_SECONDS
    return value if value > 0 else PERMISSION_TIMEOUT_SECONDS


def permission_approvers() -> frozenset[str] | None:
    """Who may answer a permission prompt, from ``CCDB_APPROVER_USER_IDS``.

    ``None`` means no restriction. A value that cannot be read gives an empty
    set, so a typo refuses everyone instead of letting everyone approve.
    """
    raw = os.getenv("CCDB_APPROVER_USER_IDS", "").strip()
    if not raw:
        return None
    ids = [part.strip() for part in raw.split(",") if part.strip()]
    if not all(part.isdigit() for part in ids):
        logger.warning("CCDB_APPROVER_USER_IDS must be integers; nobody can approve")
        return frozenset()
    return frozenset(ids)


def permission_prompt(request: PermissionRequest, *, notify: Mention | None = None) -> ChoicePrompt:
    """Ask whether a tool may run. Unanswered means denied."""
    question = f"Tool: {request.tool_name}\n\nInput:\n{_format_tool_input(request.tool_input)}"
    if request.reason:
        question = f"{request.reason}\n\n{question}"
    return ChoicePrompt(
        question=question,
        header="\U0001f510 Permission required",
        choices=(
            Choice(value=ALLOW, label="✅ Allow", style="positive"),
            Choice(value=DENY, label="❌ Deny", style="destructive"),
        ),
        timeout_seconds=permission_timeout_seconds(),
        default_on_timeout=DENY,
        notify=notify,
        allowed_user_ids=permission_approvers(),
    )


def permission_result(answer: tuple[str, ...] | None) -> dict[str, Any]:
    """Read a permission answer. Anything but an explicit allow denies."""
    return {"approved": answer is not None and ALLOW in answer}


# ---------------------------------------------------------------------------
# Plan approval (ExitPlanMode)
# ---------------------------------------------------------------------------


def plan_prompt(plan_text: str, *, notify: Mention | None = None) -> ChoicePrompt:
    """Ask whether a finished plan may be executed. Unanswered means cancelled."""
    body = plan_text.strip() or "(no plan text)"
    return ChoicePrompt(
        question=body,
        header="\U0001f4cb Plan ready — approve to execute",
        choices=(
            Choice(value=APPROVE, label="✅ Approve", style="positive"),
            Choice(value=CANCEL, label="❌ Cancel", style="destructive"),
        ),
        timeout_seconds=PLAN_TIMEOUT_SECONDS,
        default_on_timeout=CANCEL,
        notify=notify,
    )


def plan_result(answer: tuple[str, ...] | None) -> dict[str, Any]:
    """Read a plan answer. Anything but an explicit approval cancels."""
    return {"approved": answer is not None and APPROVE in answer}


# ---------------------------------------------------------------------------
# MCP elicitation
# ---------------------------------------------------------------------------


def _elicitation_header(request: ElicitationRequest) -> str:
    mode_label = "Form" if request.mode == "form-mode" else "URL"
    return f"\U0001f50c MCP input required ({mode_label}) — {request.server_name}"


def elicitation_url_prompt(
    request: ElicitationRequest, *, notify: Mention | None = None
) -> ChoicePrompt:
    """Confirm that the user finished an out-of-band URL flow.

    The link itself is delivered separately through ``prompt_url``: a surface
    that can render a link button uses one, and a surface that cannot still
    shows the URL as text. Either way this question is what unblocks the CLI.
    """
    return ChoicePrompt(
        question=request.message or "An MCP server needs your input to continue.",
        header=_elicitation_header(request),
        choices=(
            Choice(value=DONE, label="✅ Done", style="positive"),
            Choice(value=CANCEL, label="❌ Cancel"),
        ),
        timeout_seconds=ELICITATION_TIMEOUT_SECONDS,
        default_on_timeout=CANCEL,
        notify=notify,
    )


def elicitation_url_result(answer: tuple[str, ...] | None) -> dict[str, Any]:
    """Read a URL-mode answer. Anything but an explicit done abandons the flow."""
    return {"completed": answer is not None and DONE in answer}


def schema_to_fields(schema: dict[str, Any]) -> tuple[FormField, ...]:
    """Turn a flat JSON-schema object into form fields.

    Only simple schemas are understood, which is what MCP elicitation sends in
    practice. A schema with no usable properties still yields one free-text
    field, because a form the user cannot fill in is the same as no form at all.
    """
    properties = schema.get("properties") or {}
    required_keys = set(schema.get("required") or ())
    fields: list[FormField] = []
    for key, prop in list(properties.items())[:MAX_FORM_FIELDS]:
        spec = prop if isinstance(prop, dict) else {}
        description = spec.get("description") or spec.get("title") or ""
        fields.append(
            FormField(
                key=str(key),
                label=str(spec.get("title") or key),
                kind=_field_kind(spec),
                required=key in required_keys,
                placeholder=str(description) or None,
            )
        )
    if not fields:
        fields.append(FormField(key="response", label="Response", kind="multiline", required=True))
    return tuple(fields)


def _field_kind(spec: dict[str, Any]) -> FieldKind:
    """Map a JSON-schema type onto the protocol's field kinds."""
    json_type = spec.get("type")
    if json_type == "boolean":
        return "toggle"
    if json_type in ("integer", "number"):
        return "number"
    return "text"


def elicitation_form_prompt(
    request: ElicitationRequest, *, notify: Mention | None = None
) -> FormPrompt:
    """Collect structured input for an MCP server."""
    return FormPrompt(
        title=_elicitation_header(request),
        fields=schema_to_fields(request.schema),
        description=request.message or None,
        submit_label="\U0001f4dd Fill in form",
        timeout_seconds=ELICITATION_TIMEOUT_SECONDS,
        notify=notify,
    )


def elicitation_form_result(values: dict[str, str] | None) -> dict[str, Any]:
    """Read a form answer.

    An abandoned form reports ``completed: False`` rather than empty values, so
    the MCP server can tell "the user declined" from "the user submitted
    blanks" — the two mean different things to a server deciding what to do next.
    """
    if values is None:
        return {"completed": False}
    return {"values": values}
