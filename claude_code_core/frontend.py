"""Frontend-agnostic vocabulary for driving a chat surface.

A "frontend" is whatever carries the conversation: Discord today, Microsoft
Teams next. This module defines the *only* thing the session machinery
(``EventProcessor`` and friends) is allowed to know about it.

Why the vocabulary is semantic, not structural
----------------------------------------------
The tempting design is to lift Discord's own API::

    async def send(self, content=None, embed=None, view=None) -> Message: ...

That is a trap. It makes every other frontend's job "translate an embed into
our native thing", and a translator can only ever lose. The Teams
implementation would end up rendering a column of card-shaped embeds because
that is what Discord happened to do — and every gap would read as "Teams is
worse".

So the protocol names *intents* instead:

===============================  ==========================================
Intent                           Discord does                Teams does
===============================  ==========================================
``open_activity``                one embed per tool,         folds into the
                                 edited on completion        single session
                                                             card it already
                                                             keeps updating
``set_status``                   emoji reaction on the       the status row of
                                 user's message              that same card
``prompt_choice``                Buttons / Select menu       Adaptive Card
                                                             ``Action.Execute``
``offer_interrupt``              a re-posted Stop button     a Stop action
                                                             pinned in the card
===============================  ==========================================

Each frontend picks the best expression it has. Neither is a translation of
the other, so neither has to be a degraded copy of the other.

Capabilities, not feature flags
-------------------------------
Callers must not branch on ``if frontend == "teams"``. They ask
:class:`SurfaceCapabilities`, whose defaults are deliberately *conservative*:
an unset capability means "not supported". Adding a field to it is therefore
backwards compatible — an out-of-date frontend keeps the plain-text fallback
instead of silently claiming something it cannot do.

The same object carries the hard limits that are easy to blow past by
accident. Teams allows 1,800 updates per hour per conversation, so a naive
port of a once-a-second live timer would kill the conversation partway through
a long session. ``min_update_interval`` turns that from a bug you find in
production into a number you read before you loop.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable, Container, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import PurePosixPath, PureWindowsPath
from typing import Literal, Protocol, runtime_checkable

from .types import ToolCategory

__all__ = [
    "ActivityHandle",
    "ActivitySpec",
    "Choice",
    "ChoicePrompt",
    "ConversationSurface",
    "FormField",
    "FormPrompt",
    "InboundAttachment",
    "InboundMessage",
    "InterruptHandle",
    "Mention",
    "Notice",
    "NoticeLevel",
    "OutboundFile",
    "SessionFrontend",
    "StatusKind",
    "SurfaceCapabilities",
    "TextStream",
    "ThreadKey",
    "issue_thread_key",
    "derive_thread_key",
]

# ---------------------------------------------------------------------------
# Thread identity
# ---------------------------------------------------------------------------

#: The primary key a session is filed under, shared by every frontend.
#:
#: It is an ``int`` because that is what ``sessions.thread_id`` has always
#: been, and because the session ledger, AI Lounge, claims, rewind and
#: collision detection all key off it. Keeping the type means a Teams session
#: and a Discord session land in *one* ledger — which is the whole reason ccdb
#: can notice that two live sessions are editing the same file.
ThreadKey = int

#: The one frontend whose conversation ids are already integers.
DISCORD_FRONTEND = "discord"

# Derived keys are pushed above this floor so they can never be mistaken for a
# Discord snowflake, which is used verbatim as its own thread key. Snowflakes
# encode milliseconds since 2015 in their high bits and stay below 2**53 well
# past the year 2100, so the two spaces stay disjoint by construction.
_DERIVED_KEY_FLOOR = 2**53
_DERIVED_KEY_CEILING = 2**63 - 1
_DERIVED_KEY_SPAN = _DERIVED_KEY_CEILING - _DERIVED_KEY_FLOOR


def derive_thread_key(frontend: str, external_id: str) -> ThreadKey:
    """Derive a stable :data:`ThreadKey` from a frontend's own conversation id.

    Frontends whose conversation ids are not integers (Teams uses strings like
    ``19:...@thread.tacv2;messageid=1481567603816``) mint a surrogate here and
    record the pairing in the ``frontend_threads`` table, so the rest of ccdb
    keeps seeing plain integers.

    The result is deterministic, scoped by *frontend* so two platforms can
    never collide on the same id, positive, and inside SQLite's signed 64-bit
    INTEGER range.

    Raises:
        ValueError: if either argument is empty.
    """
    if not frontend:
        raise ValueError("frontend must not be empty")
    if not external_id:
        raise ValueError("external_id must not be empty")

    digest = hashlib.blake2b(
        f"{frontend}\x00{external_id}".encode(),
        digest_size=8,
    ).digest()
    return _DERIVED_KEY_FLOOR + (int.from_bytes(digest, "big") % _DERIVED_KEY_SPAN)


#: How many times a collision is probed before giving up. A blake2b collision
#: in a 2**63 space is already astronomically unlikely; fifty consecutive ones
#: mean something is wrong with the caller, not with luck.
_MAX_KEY_PROBES = 50


def issue_thread_key(frontend: str, external_id: str, *, taken: Container[int]) -> ThreadKey:
    """Mint the key this conversation will be known by, avoiding *taken* ones.

    ``derive_thread_key`` answers "what key does this id hash to". This answers
    the question a frontend actually has: "what key may I use for it". The
    difference is collisions. ``ThreadKey`` is the primary key of the sessions
    table, so two conversations sharing one does not raise — the second session
    quietly overwrites the first, and a thread ends up showing somebody else's
    history. Probing costs one extra row; sharing costs a conversation.

    Discord is special-cased rather than hashed: its snowflake *is* the id every
    Discord API call needs, so hashing it would produce a key nothing could be
    posted to.

    Args:
        frontend: The platform name, e.g. ``"discord"`` or ``"teams"``.
        external_id: The frontend's own conversation id.
        taken: Keys already in use. Anything supporting ``in`` will do, so a
            caller can pass a set, or a view backed by the database.

    Raises:
        ValueError: for an empty argument, a non-numeric Discord id, or if
            fifty consecutive probes are all taken.
    """
    if not frontend:
        raise ValueError("frontend must not be empty")
    if not external_id:
        raise ValueError("external_id must not be empty")

    if frontend == DISCORD_FRONTEND:
        try:
            return int(external_id)
        except ValueError as exc:
            raise ValueError(
                f"a discord conversation id must be a snowflake, got {external_id!r}"
            ) from exc

    candidate = derive_thread_key(frontend, external_id)
    for probe in range(_MAX_KEY_PROBES):
        if candidate not in taken:
            return candidate
        # Re-derive rather than increment: a linear walk would march a whole
        # cluster of collided keys through the same occupied stretch.
        candidate = derive_thread_key(frontend, f"{external_id}\x00{probe}")
    raise ValueError(
        f"could not find a free thread key for {frontend}:{external_id} in {_MAX_KEY_PROBES} probes"
    )


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class StatusKind(StrEnum):
    """What the session is doing right now, in frontend-neutral terms."""

    THINKING = "thinking"
    TOOL_READ = "tool_read"
    TOOL_EDIT = "tool_edit"
    TOOL_COMMAND = "tool_command"
    TOOL_WEB = "tool_web"
    TOOL_OTHER = "tool_other"
    HOOK = "hook"
    COMPACTING = "compacting"
    STALLED_SOFT = "stalled_soft"
    STALLED_HARD = "stalled_hard"
    DONE = "done"
    ERROR = "error"

    @classmethod
    def for_tool(cls, category: ToolCategory) -> StatusKind:
        """Map a tool category to a status.

        Falls back to :attr:`TOOL_OTHER` so that adding a ``ToolCategory``
        never leaves a session with a stale status indicator.
        """
        return _TOOL_STATUS.get(category, cls.TOOL_OTHER)


_TOOL_STATUS: dict[ToolCategory, StatusKind] = {
    ToolCategory.READ: StatusKind.TOOL_READ,
    ToolCategory.EDIT: StatusKind.TOOL_EDIT,
    ToolCategory.COMMAND: StatusKind.TOOL_COMMAND,
    ToolCategory.WEB: StatusKind.TOOL_WEB,
    ToolCategory.THINK: StatusKind.THINKING,
    ToolCategory.PLAN: StatusKind.THINKING,
    ToolCategory.ASK: StatusKind.TOOL_OTHER,
    ToolCategory.TASK: StatusKind.TOOL_OTHER,
    ToolCategory.OTHER: StatusKind.TOOL_OTHER,
}


class NoticeLevel(StrEnum):
    """Severity of an out-of-band message (not the assistant's own reply)."""

    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    SUBTLE = "subtle"


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SurfaceCapabilities:
    """What a surface can actually do. Defaults say "no".

    Only ``max_message_chars`` is required, because a surface that cannot say
    how long a message may be cannot be written to at all. Everything else
    defaults to the most restrictive answer, so forgetting to declare a
    capability degrades gracefully instead of failing at runtime.
    """

    max_message_chars: int

    # Rendering
    supports_tables: bool = False
    supports_headings: bool = False
    supports_inline_images: bool = False
    #: Usable display width inside a preformatted block, in monospace columns.
    #: Tables are rendered to fit it; too generous a value produces wrapped,
    #: unreadable rows, so the default is the narrow one.
    monospace_width: int = 55
    #: Whether the surface's monospace font renders CJK at exactly twice the
    #: width of ASCII. When False (the default), CJK tables fall back to a
    #: vertical layout: plain, but never visibly misaligned. Discord's
    #: code-block font is one that does not honour the 2x assumption.
    monospace_cjk_is_double_width: bool = False

    # Message lifecycle
    supports_message_edit: bool = False
    supports_message_delete: bool = False
    supports_reactions: bool = False

    # Live updates. ``live_update_budget_per_hour`` is a hard platform quota
    # (Teams: 1,800 per conversation); ``stream_min_interval`` is the pace the
    # frontend prefers. Callers should read ``min_update_interval``, which
    # respects whichever is stricter.
    live_update_budget_per_hour: int = 1800
    stream_min_interval: float = 1.5

    # Files
    max_files_per_message: int = 1
    max_file_bytes: int = 8 * 1024 * 1024
    file_delivery: Literal["inline", "consent", "link"] = "inline"

    # Affordances
    supports_slash_commands: bool = False
    supports_pinned_dashboard: bool = False
    supports_thread_rename: bool = False

    def __post_init__(self) -> None:
        if self.max_message_chars <= 0:
            raise ValueError("max_message_chars must be positive")
        if self.live_update_budget_per_hour <= 0:
            raise ValueError("live_update_budget_per_hour must be positive")
        if self.stream_min_interval < 0:
            raise ValueError("stream_min_interval must not be negative")
        if self.max_files_per_message <= 0:
            raise ValueError("max_files_per_message must be positive")
        if self.monospace_width <= 0:
            raise ValueError("monospace_width must be positive")

    @property
    def min_update_interval(self) -> float:
        """Seconds a caller must leave between edits of the same conversation.

        Whichever is stricter: the frontend's preferred streaming pace, or the
        pace implied by the platform's hourly update quota.
        """
        budget_floor = 3600.0 / self.live_update_budget_per_hour
        return max(self.stream_min_interval, budget_floor)


# ---------------------------------------------------------------------------
# Outbound value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Notice:
    """Out-of-band information: session start/complete, compaction, thinking.

    Deliberately *not* the assistant's reply — a surface is free to render
    these as subtle text, a card, or to drop them entirely in a "chat only"
    mode.
    """

    level: NoticeLevel
    title: str | None = None
    body: str | None = None
    fields: tuple[tuple[str, str], ...] = ()
    monospace_body: bool = False

    def __post_init__(self) -> None:
        if not (self.title or self.body or self.fields):
            raise ValueError("Notice must carry a title, a body, or fields")


@dataclass(frozen=True)
class ActivitySpec:
    """A unit of work that starts, optionally progresses, and finishes."""

    kind: Literal["tool", "todo"]
    title: str
    detail: str | None = None
    category: ToolCategory | None = None

    def __post_init__(self) -> None:
        if not self.title:
            raise ValueError("ActivitySpec.title must not be empty")


@dataclass(frozen=True)
class Choice:
    value: str
    label: str
    description: str | None = None
    style: Literal["default", "positive", "destructive"] = "default"

    def __post_init__(self) -> None:
        if not self.value:
            raise ValueError("Choice.value must not be empty")
        if not self.label:
            raise ValueError("Choice.label must not be empty")


@dataclass(frozen=True)
class Mention:
    """A user to notify. Rendering is entirely up to the surface."""

    external_user_id: str
    display_name: str | None = None

    def __post_init__(self) -> None:
        if not self.external_user_id:
            raise ValueError("Mention.external_user_id must not be empty")


@dataclass(frozen=True)
class ChoicePrompt:
    """Ask the user to pick something. Covers AskUserQuestion, tool permission
    requests and plan approval — they differ only in their choices."""

    question: str
    header: str | None = None
    choices: tuple[Choice, ...] = ()
    multi_select: bool = False
    allow_free_text: bool = False
    timeout_seconds: float | None = None
    #: Answer to assume when the prompt times out. Permission requests set
    #: this to the denying choice so an unattended session fails closed.
    default_on_timeout: str | None = None
    notify: Mention | None = None
    #: External user IDs who may answer. ``None`` lets anyone who can see it
    #: answer; an empty set lets nobody.
    allowed_user_ids: frozenset[str] | None = None

    def __post_init__(self) -> None:
        if not self.question:
            raise ValueError("ChoicePrompt.question must not be empty")
        if not self.choices and not self.allow_free_text:
            raise ValueError(
                "ChoicePrompt needs choices or allow_free_text — otherwise it is unanswerable"
            )
        values = [c.value for c in self.choices]
        if len(values) != len(set(values)):
            raise ValueError("ChoicePrompt.choices must have unique values")
        if self.default_on_timeout is not None and self.default_on_timeout not in values:
            raise ValueError("default_on_timeout must be one of the offered choice values")


@dataclass(frozen=True)
class FormField:
    key: str
    label: str
    kind: Literal["text", "multiline", "number", "choice", "toggle"]
    required: bool = False
    placeholder: str | None = None
    choices: tuple[Choice, ...] = ()

    def __post_init__(self) -> None:
        if not self.key:
            raise ValueError("FormField.key must not be empty")
        if not self.label:
            raise ValueError("FormField.label must not be empty")
        if self.kind == "choice" and not self.choices:
            raise ValueError(f"FormField {self.key!r} of kind 'choice' needs choices")


@dataclass(frozen=True)
class FormPrompt:
    """A multi-field form. Backs MCP elicitation."""

    title: str
    fields: tuple[FormField, ...]
    description: str | None = None
    submit_label: str = "Submit"
    timeout_seconds: float | None = None
    notify: Mention | None = None

    def __post_init__(self) -> None:
        if not self.title:
            raise ValueError("FormPrompt.title must not be empty")
        if not self.fields:
            raise ValueError("FormPrompt needs at least one field")
        keys = [f.key for f in self.fields]
        if len(keys) != len(set(keys)):
            raise ValueError("FormPrompt.fields must have unique keys")


@dataclass(frozen=True)
class OutboundFile:
    """A file to deliver, either from disk or already in memory.

    ``display_name`` is reduced to a bare filename at construction: a path a
    model wrote into the attachment marker must never turn into directory
    components in an upload request.
    """

    display_name: str
    path: str | None = None
    blob: bytes | None = None
    content_type: str | None = None

    def __post_init__(self) -> None:
        if (self.path is None) == (self.blob is None):
            raise ValueError("OutboundFile needs exactly one of path or blob")
        if not self.display_name:
            raise ValueError("OutboundFile.display_name must not be empty")
        # Strip directory components from both POSIX and Windows separators, so
        # "../../etc/passwd" and "..\\..\\secret" both reduce to a bare name.
        name = PureWindowsPath(PurePosixPath(self.display_name).name).name
        if not name or name in (".", ".."):
            raise ValueError(
                f"OutboundFile.display_name has no usable filename: {self.display_name!r}"
            )
        object.__setattr__(self, "display_name", name)


# ---------------------------------------------------------------------------
# Inbound value objects
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InboundAttachment:
    filename: str
    content_type: str | None = None
    url: str | None = None
    data: bytes | None = None


@dataclass(frozen=True)
class InboundMessage:
    """A user message, normalised across frontends.

    ``raw_text`` is what the platform delivered; ``text`` is what the model
    should see. They differ when the surface requires an @mention to be
    addressed at all — Teams channels do, unless the app is granted RSC.
    """

    surface: ConversationSurface
    author_external_id: str
    author_display: str
    raw_text: str
    text: str | None = None
    attachments: tuple[InboundAttachment, ...] = ()
    is_mention: bool = False
    is_new_conversation: bool = False
    mentions: tuple[Mention, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.text is None:
            object.__setattr__(self, "text", self.raw_text)


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


@runtime_checkable
class TextStream(Protocol):
    """An assistant reply being revealed as it is generated."""

    @property
    def has_content(self) -> bool: ...

    async def append(self, delta: str) -> None: ...

    async def finalize(self, transform: Callable[[str], str] | None = None) -> str:
        """Flush and stop. Returns the full text that was shown."""
        ...


@runtime_checkable
class ActivityHandle(Protocol):
    """A started :class:`ActivitySpec`. Always end with ``complete`` or ``cancel``."""

    async def update(self, detail: str) -> None: ...

    async def complete(self, result: str | None, *, ok: bool = True) -> None: ...

    async def cancel(self) -> None: ...


@runtime_checkable
class InterruptHandle(Protocol):
    """The user's way to stop a running session."""

    async def bump(self) -> None:
        """Move the control back into view. A no-op where it never leaves."""
        ...

    async def disable(self) -> None: ...


@runtime_checkable
class ConversationSurface(Protocol):
    """One conversation thread — the place a single session talks to a user."""

    @property
    def thread_key(self) -> ThreadKey: ...

    @property
    def external_id(self) -> str:
        """The frontend's own id for this conversation."""
        ...

    @property
    def frontend(self) -> str: ...

    @property
    def capabilities(self) -> SurfaceCapabilities: ...

    # -- output ------------------------------------------------------------
    async def send_text(self, text: str) -> str | None:
        """Post assistant-authored text. Returns a message id if the surface has one."""
        ...

    async def send_notice(self, notice: Notice) -> str | None: ...

    async def deliver_files(self, files: Sequence[OutboundFile]) -> None: ...

    def open_stream(self) -> TextStream: ...

    async def open_activity(self, spec: ActivitySpec) -> ActivityHandle: ...

    # -- state -------------------------------------------------------------
    async def set_status(self, status: StatusKind) -> None: ...

    async def clear_status(self) -> None: ...

    # -- interaction -------------------------------------------------------
    async def prompt_choice(self, prompt: ChoicePrompt) -> tuple[str, ...] | None:
        """Returns the chosen values, or None if unanswered."""
        ...

    async def prompt_form(self, prompt: FormPrompt) -> dict[str, str] | None: ...

    async def prompt_url(self, title: str, url: str, *, notify: Mention | None = None) -> bool: ...

    async def offer_interrupt(self, on_stop: Callable[[], Awaitable[None]]) -> InterruptHandle: ...

    # -- management --------------------------------------------------------
    async def rename(self, title: str) -> None:
        """No-op where threads have no name (Teams reply chains do not)."""
        ...

    async def recent_transcript(self, days: int) -> str | None: ...


@runtime_checkable
class SessionFrontend(Protocol):
    """A whole bot. The seam that scheduler, webhooks and the REST API use to
    reach a conversation without knowing which platform it lives on."""

    @property
    def name(self) -> str: ...

    async def start(self) -> None: ...

    async def close(self) -> None: ...

    async def resolve_surface(self, thread_key: ThreadKey) -> ConversationSurface | None:
        """Find an existing conversation, e.g. to resume it on a schedule."""
        ...

    async def create_surface(self, *, parent_id: str, title: str) -> ConversationSurface:
        """Start a new conversation under a channel/team."""
        ...
