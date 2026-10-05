"""Backfill human activity from a guild's Discord history.

``ccdb attention-backfill`` walks a guild's text channels plus their active and
archived threads through the Discord REST API and records every human message
since a date, so the attention estimate does not start from zero. Rows are keyed
by message id, so running it twice — or over days the live hook already
recorded — adds nothing twice.

The backfill cannot know which past messages "reached a session": it records
human (non-bot, non-webhook) messages by the given authors, marked
``source = 'backfill'`` so reports can leave them out. Message ids that are
already recorded are skipped. Thread titles are the titles at backfill time.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from claude_code_core.attention import SOURCE_BACKFILL, SOURCE_LIVE, HumanActivity
from claude_code_core.attention_repo import HumanActivityRepository

logger = logging.getLogger(__name__)

__all__ = [
    "BackfillStats",
    "DiscordRest",
    "activity_from_payload",
    "backfill_guild",
    "snowflake_for",
]

API_BASE = "https://discord.com/api/v10"
DISCORD_EPOCH_MS = 1_420_070_400_000
PAGE_SIZE = 100

# Channel types (https://discord.com/developers/docs/resources/channel).
_TEXT_TYPES = frozenset({0, 5})  # GUILD_TEXT, GUILD_ANNOUNCEMENT
_THREAD_PARENT_TYPES = frozenset({0, 5, 15, 16})  # + GUILD_FORUM, GUILD_MEDIA
_PRIVATE_THREAD_PARENT_TYPES = frozenset({0})
# Message types a person types: DEFAULT and REPLY.
_HUMAN_MESSAGE_TYPES = frozenset({0, 19})

_MAX_ATTEMPTS = 6

# (method-less) GET: path, query params → (status, headers, parsed JSON or None)
RawGet = Callable[[str, Mapping[str, str]], Awaitable[tuple[int, Mapping[str, str], Any]]]
Sleep = Callable[[float], Awaitable[None]]


def snowflake_for(moment: datetime) -> int:
    """The smallest snowflake Discord could assign at *moment*."""
    ms = int(moment.timestamp() * 1000)
    return max(ms - DISCORD_EPOCH_MS, 0) << 22


class DiscordRest:
    """A minimal GET client that honours Discord's rate limits.

    A 429 waits for ``retry_after`` and retries; an exhausted bucket
    (``X-RateLimit-Remaining: 0``) waits for ``X-RateLimit-Reset-After``
    before returning, so the next call does not trip the limit. 5xx responses
    are retried with backoff. Other statuses are returned to the caller.
    """

    def __init__(self, raw_get: RawGet, *, sleep: Sleep = asyncio.sleep) -> None:
        self._raw_get = raw_get
        self._sleep = sleep

    async def get(self, path: str, params: Mapping[str, str] | None = None) -> tuple[int, Any]:
        params = dict(params or {})
        for attempt in range(_MAX_ATTEMPTS):
            status, headers, body = await self._raw_get(path, params)
            if status == 429:
                retry_after = _retry_after(headers, body)
                logger.info("Rate limited on %s; waiting %.2fs", path, retry_after)
                await self._sleep(retry_after)
                continue
            if status >= 500:
                await self._sleep(min(2.0**attempt, 30.0))
                continue
            if headers.get("X-RateLimit-Remaining") == "0":
                await self._sleep(_float(headers.get("X-RateLimit-Reset-After"), 1.0))
            return status, body
        raise RuntimeError(f"Discord kept refusing GET {path} after {_MAX_ATTEMPTS} attempts")


def _retry_after(headers: Mapping[str, str], body: Any) -> float:
    if isinstance(body, dict) and "retry_after" in body:
        return _float(body.get("retry_after"), 1.0)
    return _float(headers.get("Retry-After"), 1.0)


def _float(value: Any, default: float) -> float:
    try:
        return max(float(value), 0.0)
    except (TypeError, ValueError):
        return default


def aiohttp_raw_get(session: Any, token: str) -> RawGet:
    """Adapt an ``aiohttp.ClientSession`` to :data:`RawGet` with bot authentication."""
    headers = {"Authorization": token if token.startswith("Bot ") else f"Bot {token}"}

    async def raw_get(path: str, params: Mapping[str, str]) -> tuple[int, Mapping[str, str], Any]:
        async with session.get(f"{API_BASE}{path}", params=params, headers=headers) as resp:
            try:
                body = await resp.json(content_type=None)
            except ValueError:
                body = None
            return resp.status, dict(resp.headers), body

    return raw_get


def activity_from_payload(
    message: Mapping[str, Any],
    *,
    conversation_id: str,
    parent_id: str | None,
    title: str | None,
) -> HumanActivity | None:
    """A :class:`HumanActivity` for a raw message payload, or ``None`` if not human."""
    author = message.get("author")
    if not isinstance(author, dict) or author.get("bot") or author.get("system"):
        return None
    if message.get("webhook_id") or message.get("type", 0) not in _HUMAN_MESSAGE_TYPES:
        return None
    try:
        occurred_at = datetime.fromisoformat(str(message["timestamp"]))
        message_id = str(message["id"])
        author_id = str(author["id"])
    except (KeyError, ValueError):
        return None
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=UTC)
    attachments = message.get("attachments")
    return HumanActivity(
        frontend="discord",
        conversation_id=conversation_id,
        parent_id=parent_id,
        thread_title=title,
        author_id=author_id,
        occurred_at=occurred_at,
        char_count=len(message.get("content") or ""),
        attachment_count=len(attachments) if isinstance(attachments, list) else 0,
        message_id=message_id,
        source=SOURCE_BACKFILL,
    )


@dataclass
class BackfillStats:
    channels: int = 0
    threads: int = 0
    human_messages: int = 0
    inserted: int = 0
    #: Messages not written because their id is already recorded — by the live
    #: hook (possibly under a different conversation) or an earlier backfill.
    already_recorded: int = 0
    #: Live rows already inside the range: backfill only fills the gaps around them.
    live_rows_in_range: int = 0
    skipped_containers: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _Container:
    """Somewhere messages live: a text channel or a thread."""

    id: str
    title: str | None
    parent_id: str | None
    is_thread: bool


async def backfill_guild(
    rest: DiscordRest,
    repo: HumanActivityRepository,
    *,
    guild_id: str,
    since: datetime,
    until: datetime | None = None,
    author_ids: Iterable[str] | None = None,
    progress: Callable[[str], None] | None = None,
) -> BackfillStats:
    """Record human messages in *guild_id* sent in ``[since, until)``."""
    stats = BackfillStats()
    stats.live_rows_in_range = await repo.count_between(
        since, until or datetime.now(UTC), source=SOURCE_LIVE
    )
    authors = {str(a) for a in author_ids} if author_ids else None
    since_flake = snowflake_for(since)
    until_flake = snowflake_for(until) if until else None

    status, channels = await rest.get(f"/guilds/{guild_id}/channels")
    if status != 200 or not isinstance(channels, list):
        raise RuntimeError(f"Could not list channels of guild {guild_id} (HTTP {status})")

    threads = await _collect_threads(rest, guild_id, channels, since, stats)
    thread_names = {t.id: t.title for t in threads}
    texts = [
        _Container(str(c["id"]), c.get("name"), None, is_thread=False)
        for c in channels
        if c.get("type") in _TEXT_TYPES
    ]
    stats.channels = len(texts)
    stats.threads = len(threads)

    for index, container in enumerate([*texts, *threads], start=1):
        batch: list[HumanActivity] = []
        async for message in _messages_after(rest, container.id, since_flake, until_flake, stats):
            activity = _describe(message, container, thread_names)
            if activity is None or (authors is not None and activity.author_id not in authors):
                continue
            batch.append(activity)
        stats.human_messages += len(batch)
        # Skip ids already recorded anywhere: a live row for a thread that has
        # since been deleted sits under the thread, while history now shows the
        # same message under the channel — the key alone would count it twice.
        known = await repo.existing_message_ids("discord", (a.message_id for a in batch))
        fresh = [a for a in batch if a.message_id not in known]
        stats.already_recorded += len(batch) - len(fresh)
        stats.inserted += await repo.record_many(fresh)
        if progress is not None and index % 25 == 0:
            progress(f"... {index}/{len(texts) + len(threads)} channels and threads scanned")
    return stats


def _describe(
    message: Mapping[str, Any], container: _Container, thread_names: Mapping[str, str | None]
) -> HumanActivity | None:
    if container.is_thread:
        return activity_from_payload(
            message,
            conversation_id=container.id,
            parent_id=container.parent_id,
            title=container.title,
        )
    # A channel message that opened a thread belongs to that thread: Discord
    # gives the thread the message's id, exactly as the live hook records it.
    message_id = str(message.get("id"))
    started = message.get("thread")
    if message_id in thread_names or isinstance(started, dict):
        title = thread_names.get(message_id)
        if title is None and isinstance(started, dict):
            title = started.get("name")
        return activity_from_payload(
            message, conversation_id=message_id, parent_id=container.id, title=title
        )
    return activity_from_payload(
        message, conversation_id=container.id, parent_id=None, title=container.title
    )


async def _collect_threads(
    rest: DiscordRest,
    guild_id: str,
    channels: list[dict[str, Any]],
    since: datetime,
    stats: BackfillStats,
) -> list[_Container]:
    found: dict[str, dict[str, Any]] = {}
    status, active = await rest.get(f"/guilds/{guild_id}/threads/active")
    if status == 200 and isinstance(active, dict):
        for thread in active.get("threads") or []:
            found[str(thread["id"])] = thread
    else:
        stats.skipped_containers.append(f"guild {guild_id} active threads (HTTP {status})")

    for channel in channels:
        kind = channel.get("type")
        if kind not in _THREAD_PARENT_TYPES:
            continue
        scopes = ["public"] + (["private"] if kind in _PRIVATE_THREAD_PARENT_TYPES else [])
        for scope in scopes:
            async for thread in _archived_threads(rest, str(channel["id"]), scope, since, stats):
                found.setdefault(str(thread["id"]), thread)

    since_flake = snowflake_for(since)
    containers = []
    for thread_id, thread in found.items():
        last = thread.get("last_message_id")
        if last is not None and int(last) < since_flake:
            continue  # nothing was said in it since the start date
        parent = thread.get("parent_id")
        containers.append(
            _Container(thread_id, thread.get("name"), str(parent) if parent else None, True)
        )
    return containers


async def _archived_threads(
    rest: DiscordRest, channel_id: str, scope: str, since: datetime, stats: BackfillStats
):
    before: str | None = None
    while True:
        params = {"limit": str(PAGE_SIZE)}
        if before:
            params["before"] = before
        status, page = await rest.get(f"/channels/{channel_id}/threads/archived/{scope}", params)
        if status != 200 or not isinstance(page, dict):
            if status not in (403, 404):
                stats.skipped_containers.append(f"{scope} archive of {channel_id} (HTTP {status})")
            return
        threads = page.get("threads") or []
        oldest: datetime | None = None
        for thread in threads:
            yield thread
            stamp = (thread.get("thread_metadata") or {}).get("archive_timestamp")
            if stamp:
                oldest = datetime.fromisoformat(stamp)
        # Sorted newest archive first: once a page reaches before *since*, every
        # later thread was archived — and so went quiet — before the range.
        if not page.get("has_more") or not threads or oldest is None or oldest < since:
            return
        before = oldest.isoformat()


async def _messages_after(
    rest: DiscordRest,
    channel_id: str,
    since_flake: int,
    until_flake: int | None,
    stats: BackfillStats,
):
    after = since_flake
    while True:
        params = {"limit": str(PAGE_SIZE), "after": str(after)}
        status, page = await rest.get(f"/channels/{channel_id}/messages", params)
        if status != 200 or not isinstance(page, list):
            if status not in (403, 404):
                stats.skipped_containers.append(f"messages of {channel_id} (HTTP {status})")
            return
        if not page:
            return
        page_sorted = sorted(page, key=lambda m: int(m["id"]))
        for message in page_sorted:
            if until_flake is not None and int(message["id"]) >= until_flake:
                return
            yield message
        if len(page) < PAGE_SIZE:
            return
        after = int(page_sorted[-1]["id"])
