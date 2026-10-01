"""Pure logic for the Teams thread sync API (/api/teams/sync).

A browser extension mirrors upstream Teams threads into a folder tree, one file
per message, so an agent can be pointed at the folder and read the raw
conversation instead of a summary. This module holds every decision that does
not touch the filesystem: validation, identity, hashing comparison, and
rendering. The I/O lives in :mod:`claude_discord.ext.teams_store`.

Identity
--------
Teams gives each message a ``mid`` — a 13-digit Unix-ms string exposed in the
DOM as ``id="content-{mid}"`` and, in Microsoft Graph, as ``chatMessage.id``.
The thread is identified by its root message's mid (``data-reply-chain-id``),
scoped by the team GUID so two teams can never collide. So the primary key is::

    {team}/{root_mid}/{mid}

Because every mid is the same width, sorting filenames as strings sorts them
chronologically — ``ls messages/`` is already a timeline.

Why the hash is opaque
----------------------
The client sends a ``hash`` per message; the server stores it verbatim and
compares the incoming hash against the stored one. The server deliberately does
NOT recompute it. Recomputing would mean reimplementing the client's
normalisation in Python and keeping the two in lockstep forever — and any drift
would show up as a permanent re-sync of every message, silently. Comparing
client hash to stored client hash is always apples-to-apples. If the client ever
changes its algorithm, every message re-syncs exactly once, which is harmless
because the whole protocol is idempotent.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field

# A team GUID as it appears in the avatar src (/teams/{groupId}).
_TEAM_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)
# A Teams message id: Unix-ms, so 13 digits today. Accept a range rather than
# pinning 13 so a future widening does not lock the client out, but keep it
# digits-only — this value becomes a path segment.
_MID_RE = re.compile(r"^\d{6,20}$")
# Characters that are hostile in a path, in Obsidian wikilinks, or on Windows.
_UNSAFE_DIR_RE = re.compile(r'[\\/:*?"<>|#^\[\]\x00-\x1f]+')
_HASH_RE = re.compile(r"^[\w:.\-]{1,128}$")

MAX_TITLE_SLUG_CHARS = 60
MAX_MESSAGES_PER_REQUEST = 2000
MAX_TEXT_CHARS = 200_000


@dataclass(frozen=True)
class ThreadRef:
    """The identity of one Teams thread, plus its human-facing labels.

    ``org`` is the company the conversation belongs to. It is a *label*, not part
    of the identity: two threads of the same team are the same team whatever the
    folder says, so relabelling never re-uploads anything.

    ``identity_scope`` says which part of the pair the client considers
    authoritative:

    - ``"oldest-mid"`` (default) — the pair is the key. A real channel team holds
      many threads, so both halves are needed to tell them apart.
    - ``"conversation"`` — the team alone is the key, and ``root_mid`` is allowed
      to move. Only a chat may claim this: it has no real team GUID, so the
      client derives one from Teams' conversation id, which makes the team
      already unique per conversation. This exists so a client can upload the
      newest part of a very long chat before it has scrolled to the beginning —
      under the strict key a partial scan reports a different root and the server
      opens a second folder for the same conversation.
    """

    team: str
    root_mid: str
    title: str
    url: str = ""
    org: str = ""
    identity_scope: str = "oldest-mid"

    @property
    def key(self) -> str:
        return f"{self.team}/{self.root_mid}"

    @property
    def conversation_scoped(self) -> bool:
        return self.identity_scope == "conversation"


@dataclass(frozen=True)
class AttachmentRef:
    """One attachment as the client declared it.

    ``status`` mirrors the existing ingest manifest vocabulary: ``embedded``
    (bytes included), ``linked`` (URL only), ``skipped``, ``failed``. Anything
    that is not ``embedded`` is recorded as pending rather than dropped — a
    missing file must stay visible, never be reported as complete.
    """

    name: str
    status: str = "embedded"
    url: str = ""
    reason: str = ""
    data_b64: str = ""


@dataclass(frozen=True)
class IncomingMessage:
    """One message as sent by the client (plan: metadata only; push: full)."""

    mid: str
    hash: str
    attachments: tuple[AttachmentRef, ...] = ()
    text: str = ""
    author: str = ""
    timestamp: str = ""
    reply_to: str | None = None
    deleted: bool = False


@dataclass(frozen=True)
class StoredMessage:
    """What the vault already holds for one mid."""

    mid: str
    hash: str
    attachments_present: frozenset[str] = frozenset()


@dataclass
class SyncPlan:
    """The server's answer to "what don't you have?"."""

    want_messages: list[str] = field(default_factory=list)
    want_attachments: list[dict] = field(default_factory=list)
    have: int = 0
    newest_have_mid: str | None = None


class ValidationError(ValueError):
    """Raised when a client payload is malformed. The message is user-facing."""


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def parse_thread_ref(raw: object) -> ThreadRef:
    """Validate the ``thread`` object of a sync request.

    ``team`` and ``root_mid`` become path segments, so they are checked against
    strict patterns here — before anything downstream joins them onto a root.
    """
    if not isinstance(raw, dict):
        raise ValidationError("thread must be an object")
    team = str(raw.get("team") or "").strip().lower()
    if not _TEAM_RE.match(team):
        raise ValidationError("thread.team must be a GUID")
    root_mid = str(raw.get("root_mid") or "").strip()
    if not _MID_RE.match(root_mid):
        raise ValidationError("thread.root_mid must be a numeric message id")
    title = _clean_line(raw.get("title"), limit=300) or f"thread-{root_mid}"
    url = _clean_line(raw.get("url"), limit=2000)
    org = _clean_line(raw.get("org"), limit=200)
    # Anything this version does not recognise falls back to the strict key.
    # Widening the match on an unknown value would merge conversations that a
    # newer client meant to keep apart, and a merge cannot be undone by syncing
    # again — the messages are already interleaved in one folder.
    scope = str(raw.get("identity_scope") or "").strip().lower()
    if scope != "conversation":
        scope = "oldest-mid"
    return ThreadRef(
        team=team, root_mid=root_mid, title=title, url=url, org=org, identity_scope=scope
    )


def parse_messages(raw: object, *, require_body: bool) -> list[IncomingMessage]:
    """Validate the ``messages`` array.

    ``require_body`` is False for /plan (metadata only, keeps the request small)
    and True for /push, where the text is the point.
    """
    if not isinstance(raw, list):
        raise ValidationError("messages must be a list")
    if len(raw) > MAX_MESSAGES_PER_REQUEST:
        raise ValidationError(f"too many messages (max {MAX_MESSAGES_PER_REQUEST})")
    out: list[IncomingMessage] = []
    seen: set[str] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ValidationError(f"messages[{i}] must be an object")
        mid = str(item.get("mid") or "").strip()
        if not _MID_RE.match(mid):
            raise ValidationError(f"messages[{i}].mid must be a numeric message id")
        if mid in seen:
            raise ValidationError(f"messages[{i}].mid {mid} appears twice")
        seen.add(mid)
        digest = str(item.get("hash") or "").strip()
        if not _HASH_RE.match(digest):
            raise ValidationError(f"messages[{i}].hash is required")
        text = str(item.get("text") or "")
        # An empty body is legitimate for an attachment-only post, so only
        # refuse when there is nothing at all to store.
        empty = not text.strip() and not item.get("deleted") and not item.get("attachments")
        if require_body and empty:
            raise ValidationError(f"messages[{i}] has neither text nor attachments")
        reply_to = str(item.get("reply_to") or "").strip() or None
        if reply_to is not None and not _MID_RE.match(reply_to):
            raise ValidationError(f"messages[{i}].reply_to must be a numeric message id")
        out.append(
            IncomingMessage(
                mid=mid,
                hash=digest,
                attachments=_parse_attachments(item.get("attachments"), i),
                text=text[:MAX_TEXT_CHARS],
                author=_clean_line(item.get("author"), limit=200),
                timestamp=_clean_line(item.get("timestamp"), limit=64),
                reply_to=reply_to,
                deleted=bool(item.get("deleted")),
            )
        )
    return out


def _parse_attachments(raw: object, index: int) -> tuple[AttachmentRef, ...]:
    if raw in (None, ""):
        return ()
    if not isinstance(raw, list):
        raise ValidationError(f"messages[{index}].attachments must be a list")
    out: list[AttachmentRef] = []
    for att in raw:
        # /plan sends bare names; /push sends objects. Accept both shapes so the
        # client does not have to build two different message representations.
        if isinstance(att, str):
            name = _clean_line(att, limit=300)
            if name:
                out.append(AttachmentRef(name=name, status="declared"))
            continue
        if not isinstance(att, dict):
            raise ValidationError(f"messages[{index}].attachments entries must be objects")
        name = _clean_line(att.get("name"), limit=300)
        if not name:
            raise ValidationError(f"messages[{index}] has an attachment without a name")
        out.append(
            AttachmentRef(
                name=name,
                status=_clean_line(att.get("status"), limit=32) or "declared",
                url=_clean_line(att.get("url"), limit=2000),
                reason=_clean_line(att.get("reason"), limit=500),
                data_b64=str(att.get("data") or ""),
            )
        )
    return tuple(out)


def _clean_line(raw: object, *, limit: int) -> str:
    """Collapse a client-supplied label to a single safe line."""
    text = str(raw or "")
    text = "".join(ch for ch in text if ch == "\t" or ord(ch) >= 0x20)
    return re.sub(r"\s+", " ", text).strip()[:limit]


# ---------------------------------------------------------------------------
# Identity → folder name
# ---------------------------------------------------------------------------


def thread_dirname(title: str, root_mid: str) -> str:
    """Human-readable folder name that still carries the identity.

    The vault is read by a person as well as an agent, so a bare GUID folder is
    not acceptable; but the title alone is not stable (Teams threads get
    renamed) and not unique. Appending the root mid gives both. The folder is
    named once, at first sync, and never renamed afterwards — the thread's real
    identity is recorded inside ``thread.json``, so a folder the user renames by
    hand keeps working.
    """
    slug = unicodedata.normalize("NFC", title)
    slug = _UNSAFE_DIR_RE.sub(" ", slug)
    slug = re.sub(r"\s+", "_", slug).strip("._ ")
    slug = slug[:MAX_TITLE_SLUG_CHARS].strip("._ ")
    return f"{slug or 'thread'}--{root_mid}"


def org_dirname(org: str) -> str:
    """Folder name for one company, or ``""`` when there is nothing to file under.

    The label is free text a human typed, so it gets the same treatment as a
    thread title — minus the mid, because the company folder is meant to be
    recognised at a glance. Anything that survives sanitising to nothing (``..``,
    a run of slashes, whitespace) yields an empty string, which the caller reads
    as "leave it at the root" rather than as a directory called ``.``.
    """
    slug = unicodedata.normalize("NFC", str(org or ""))
    slug = _UNSAFE_DIR_RE.sub(" ", slug)
    slug = re.sub(r"\s+", "_", slug).strip("._ ")
    return slug[:MAX_TITLE_SLUG_CHARS].strip("._ ")


def safe_attachment_name(raw: str, index: int = 0) -> str:
    """Reduce an attachment filename to a safe basename (no directories)."""
    name = _UNSAFE_DIR_RE.sub("_", str(raw or "").replace("\\", "/").split("/")[-1])
    name = name.strip().lstrip(".")
    return name[:200] or f"attachment_{index}"


# ---------------------------------------------------------------------------
# have / want negotiation
# ---------------------------------------------------------------------------


def build_plan(
    incoming: list[IncomingMessage],
    stored: dict[str, StoredMessage],
    pending: list[dict] | None = None,
    unavailable: list[dict] | None = None,
) -> SyncPlan:
    """Decide what the client still needs to send.

    A message is wanted when it is unknown OR its hash differs from the stored
    one. That single comparison covers both "never sent" and "edited upstream" —
    they are not two features, they are the same one. A message is also wanted
    when old pending metadata names an attachment that its current authoritative
    inventory no longer declares. Re-sending it lets the push endpoint remove
    stale warnings created by an older client. An attachment is wanted when its
    message is wanted or its bytes are not on disk yet, which is what makes a
    failed attachment reappear on the next sync instead of vanishing.
    """
    plan = SyncPlan(have=len(stored))
    pending_by_mid: dict[str, set[str]] = {}
    for item in pending or []:
        mid = str(item.get("mid") or "")
        name = str(item.get("name") or "")
        if mid and name:
            pending_by_mid.setdefault(mid, set()).add(name)
    unavailable_keys = {
        (str(item.get("mid") or ""), str(item.get("name") or ""))
        for item in unavailable or []
        if item.get("mid") and item.get("name")
    }
    if stored:
        plan.newest_have_mid = max(stored, key=_mid_sort_key)
    for msg in incoming:
        current = stored.get(msg.mid)
        declared = {safe_attachment_name(att.name) for att in msg.attachments}
        obsolete_pending = pending_by_mid.get(msg.mid, set()) - declared
        wanted = current is None or current.hash != msg.hash or bool(obsolete_pending)
        if wanted:
            plan.want_messages.append(msg.mid)
        for att in msg.attachments:
            name = safe_attachment_name(att.name)
            present = current is not None and name in current.attachments_present
            if not present and (msg.mid, name) not in unavailable_keys:
                plan.want_attachments.append({"mid": msg.mid, "name": name})
    plan.want_messages.sort(key=_mid_sort_key)
    return plan


def hash_scheme(digest: str) -> str:
    """The ``<scheme>`` of a ``<scheme>:<digest>`` client hash ("" when absent)."""
    return digest.split(":", 1)[0] if ":" in digest else ""


def is_edit(previous_hash: str, new_hash: str) -> bool:
    """Whether a hash change means the message itself changed upstream.

    Hashes are only comparable within one scheme. When a client changes its
    formula (``fnv1a:`` → ``fnv1a2:``) every stored hash differs at once; that
    is a re-keying, not thousands of edits, so it must not fill ``_history``.
    """
    return previous_hash != new_hash and hash_scheme(previous_hash) == hash_scheme(new_hash)


def _mid_sort_key(mid: str) -> tuple[int, str]:
    """Sort mids numerically, tolerating an unexpected non-numeric one."""
    return (len(mid), mid)


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def render_message(
    msg: IncomingMessage,
    thread: ThreadRef,
    *,
    prev_mid: str | None,
    edited: bool,
    first_synced_at: str,
    last_synced_at: str,
    pending: list[dict],
    unavailable: list[dict] | None = None,
    kept_attachments: tuple[str, ...] = (),
) -> str:
    """Render one message as an Obsidian note with YAML frontmatter.

    ``next`` is deliberately absent. Writing it would mean rewriting an existing
    file every time a reply arrives — a mutation of already-stored data, and a
    chain that breaks in the middle if the write is interrupted. Order lives in
    the append-only ``chain.jsonl`` instead, and ``prev`` is enough to walk
    backwards from any single file.

    ``kept_attachments`` are files already stored for this message that the
    current push did not declare. Teams lazy-loads images, so one pass sees a
    screenshot and the next does not; the file stays on disk either way, and so
    does its link. Attachment names are de-duplicated — the same name declared
    twice is one file.
    """
    entries = _unique_attachments(msg.attachments)
    names = [name for name, _ in entries]
    names += [name for name in kept_attachments if name not in names]
    front = {
        "mid": msg.mid,
        "thread_root": thread.root_mid,
        "team": thread.team,
        "prev": prev_mid,
        "reply_to": msg.reply_to,
        "author": msg.author,
        "timestamp": msg.timestamp,
        "hash": msg.hash,
        "edited": edited,
        "deleted": msg.deleted,
        "attachments": names,
        "attachments_pending": [p["name"] for p in pending],
        "attachments_unavailable": [p["name"] for p in unavailable or []],
        "first_synced_at": first_synced_at,
        "last_synced_at": last_synced_at,
    }
    lines = ["---"]
    for key, value in front.items():
        lines.append(f"{key}: {_yaml_scalar(value)}")
    lines.append("---")
    lines.append("")
    if msg.deleted:
        lines.append("> [!warning] このメッセージは Teams 上で削除されました")
        lines.append("")
    body = msg.text.strip()
    if body:
        lines.append(body)
        lines.append("")
    for name, att in entries:
        if att.status == "embedded":
            lines.append(f"- 📎 [[{msg.mid}/{name}]]")
        else:
            note = att.reason or att.status
            lines.append(f"- ⚠️ 添付未取得: {name}（{note}）")
    for name in names[len(entries) :]:
        lines.append(f"- 📎 [[{msg.mid}/{name}]]")
    if names:
        lines.append("")
    return "\n".join(lines)


def _unique_attachments(
    attachments: tuple[AttachmentRef, ...],
) -> list[tuple[str, AttachmentRef]]:
    """Safe name per attachment, first occurrence wins; an embedded copy beats a gap."""
    out: dict[str, AttachmentRef] = {}
    for att in attachments:
        name = safe_attachment_name(att.name)
        if name not in out or (att.status == "embedded" and out[name].status != "embedded"):
            out[name] = att
    return list(out.items())


def _yaml_scalar(value: object) -> str:
    """Emit a YAML scalar that survives a round trip.

    Every string is quoted with :func:`json.dumps`. A bare Japanese title
    containing ``: `` silently breaks a YAML parse, and mids are digit strings
    that would otherwise be read back as integers and stop matching filenames.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(json.dumps(str(v), ensure_ascii=False) for v in value) + "]"
    return json.dumps(str(value), ensure_ascii=False)


def chain_entry(msg: IncomingMessage, *, prev_mid: str | None, rev: int, synced_at: str) -> dict:
    """One append-only line of ``chain.jsonl``.

    The same mid may appear more than once: a later line with a higher ``rev``
    is an edit. Readers take the last line per mid and sort by mid.
    """
    return {
        "mid": msg.mid,
        "prev": prev_mid,
        "author": msg.author,
        "ts": msg.timestamp,
        "hash": msg.hash,
        "rev": rev,
        "deleted": msg.deleted,
        "attachments": [safe_attachment_name(a.name) for a in msg.attachments],
        "synced_at": synced_at,
    }


def render_readme(meta: dict, chain: list[dict]) -> str:
    """The note an agent reads first. States coverage and gaps up front.

    Anything missing is written here in plain language. A sync that quietly
    reports success while an attachment never arrived is worse than one that
    fails loudly, because it actively reassures — that is the failure mode this
    whole rewrite exists to remove.
    """
    pending = meta.get("pending_attachments") or []
    unavailable = meta.get("unavailable_attachments") or []
    coverage = meta.get("coverage") or {}
    lines = [
        f"# Teams スレッド: {meta.get('title', '')}",
        "",
        f"- 発言数: {len(chain)}",
        f"- 最終同期: {meta.get('last_synced_at', '')}",
        f"- スレッドID: `{meta.get('team', '')}/{meta.get('root_mid', '')}`",
    ]
    if meta.get("org"):
        lines.insert(3, f"- 会社: {meta['org']}")
    if meta.get("url"):
        lines.append(f"- Teams: {meta['url']}")
    lines += [
        "",
        "## 読み方",
        "",
        "1. `chain.jsonl` を mid の昇順で読むと時系列になる"
        "（同じ mid が複数行あれば最後の行が最新版）",
        "2. 本文は `messages/{mid}.md`。ファイル名の辞書順 = 時系列",
        "3. 添付は `messages/{mid}/` 配下",
        "4. 編集前の版は `_history/` にある",
        "",
    ]
    if pending:
        lines += ["## ⚠️ 未取得の添付", ""]
        for item in pending:
            lines.append(
                f"- `{item.get('name', '')}`（{item.get('mid', '')}）— {item.get('reason', '')}"
            )
        lines.append("")
    if unavailable:
        lines += ["## 取得元で利用できない添付", ""]
        for item in unavailable:
            lines.append(
                f"- `{item.get('name', '')}`（{item.get('mid', '')}）— {item.get('reason', '')}"
            )
        lines.append("")
    oldest = coverage.get("oldest_seen_mid")
    if oldest:
        lines += [
            "## ⚠️ カバレッジ",
            "",
            f"mid `{oldest}` より前は同期時に確認していない。"
            "それより古い発言が後から編集されても検出できない"
            "（拡張の「スレッド全体を再同期」で解消する）。",
            "",
        ]
    return "\n".join(lines)
