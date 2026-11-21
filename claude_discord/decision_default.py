"""Which unanswered cards may proceed with their recommendation: the owner's scope list (#1478).

A decision card waits for the person. For some kinds of work that wait costs more than it
protects: the agent recommends a reversible choice ("write the draft, publish nothing") and the
card sits for days. The owner may list such kinds of work in advance, as *scopes*; a card that
names an approved scope in ``if_no_answer`` proceeds with its recommended option once its time
has passed.

Two rules hold regardless of configuration:

* **No file, no defaults.** Without ``CCDB_DECISION_DEFAULTS_FILE`` every card waits, as it
  always did. A file that cannot be read or fails validation turns the feature *off*, never on:
  the harm of a broken guard here is work proceeding without consent.
* **Silence never approves publishing, sending, payment, deletion or production changes.**
  A scope whose key names one of those is refused when the file is loaded, whatever the owner
  wrote, so a typo in the list cannot turn silence into consent for them.
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

ENV_FILE = "CCDB_DECISION_DEFAULTS_FILE"

_SCOPE_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,40}$")
_TOKEN = re.compile(r"[A-Za-z]+")
MAX_LABEL = 120

#: Words a scope key may not contain: silence is never consent for these.
FORBIDDEN_WORDS = frozenset(
    {
        "publish",
        "public",
        "send",
        "mail",
        "email",
        "post",
        "pay",
        "payment",
        "purchase",
        "billing",
        "charge",
        "delete",
        "remove",
        "destroy",
        "prod",
        "production",
        "release",
    }
)


class DefaultPolicyError(ValueError):
    """The scope file is not usable; the feature stays off."""


@dataclass(frozen=True)
class DefaultPolicy:
    """The approved scopes: key → the one-line description the owner approved."""

    scopes: dict[str, str] = field(default_factory=dict)


def forbidden_word(key: str) -> str | None:
    """The forbidden word *key* contains, if any (case-insensitive, per word)."""
    for token in _TOKEN.findall(key.lower()):
        if token in FORBIDDEN_WORDS:
            return token
    return None


def load_policy(path: str | os.PathLike[str]) -> DefaultPolicy:
    """Read and validate the scope file, or raise :class:`DefaultPolicyError`."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise DefaultPolicyError(f"cannot read {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("scopes"), dict):
        raise DefaultPolicyError('the file must be {"scopes": {"<key>": {"label": "..."}}}')
    scopes: dict[str, str] = {}
    for key, entry in data["scopes"].items():
        if not isinstance(key, str) or not _SCOPE_KEY.match(key):
            raise DefaultPolicyError(f"scope key {key!r} must be 1-40 letters, digits or _.:-")
        word = forbidden_word(key)
        if word is not None:
            raise DefaultPolicyError(
                f"scope {key!r} names {word!r}: silence never approves publishing, sending, "
                "payment, deletion or production changes"
            )
        label = entry.get("label") if isinstance(entry, dict) else None
        if not isinstance(label, str) or not label.strip() or len(label) > MAX_LABEL:
            raise DefaultPolicyError(f"scope {key!r} needs a label of 1-{MAX_LABEL} characters")
        scopes[key] = label.strip()
    return DefaultPolicy(scopes=scopes)


def policy_from_env() -> DefaultPolicy | None:
    """The configured policy, or ``None`` (feature off) when unset or unusable."""
    path = os.environ.get(ENV_FILE, "").strip()
    if not path:
        return None
    try:
        policy = load_policy(path)
    except DefaultPolicyError as exc:
        logger.error("decision defaults are OFF: %s", exc)
        return None
    return policy if policy.scopes else None
