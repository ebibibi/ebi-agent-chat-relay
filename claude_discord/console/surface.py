"""The console as a frontend: a conversation surface that writes to its own tables.

The console does not open a chat thread to run an agent. It implements the
same ``ConversationSurface`` / ``SessionFrontend`` seam Discord and Teams do,
so a console conversation runs through the shared session runner with no chat
platform involved, and its transcript lives in ``console_messages``.

What a chat platform renders live — tool activity, the streaming answer, the
Stop button — goes to the in-memory ``LiveBoard`` while the turn runs. Only
what a human reads afterwards is stored: the model's answers, questions it
asks, warnings or errors, and links to delivered files.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from claude_code_core.frontend import (
    ActivitySpec,
    ChoicePrompt,
    FormPrompt,
    Mention,
    Notice,
    NoticeLevel,
    OutboundFile,
    StatusKind,
    SurfaceCapabilities,
    ThreadKey,
)

from ..thread_marker import set_outcome_thread_name
from .conversations import CONSOLE_FRONTEND, ConversationRepository
from .live import LiveActivity, LiveBoard, LiveState

if TYPE_CHECKING:
    from ..database.frontend_thread_repo import FrontendThreadRepository

logger = logging.getLogger(__name__)

#: How the agent's messages are attributed in the transcript.
AGENT_AUTHOR = "agent"

CONSOLE_CAPABILITIES = SurfaceCapabilities(
    # The web client renders Markdown and scrolls; one answer is one message.
    max_message_chars=100_000,
    supports_tables=True,
    supports_headings=True,
    monospace_width=100,
    # The outcome marker lives in the stored name, so it can always be set.
    supports_thread_rename=True,
    file_delivery="link",
    max_files_per_message=10,
    max_file_bytes=50 * 1024 * 1024,
)

#: Where a delivered file can be fetched. ``files.py`` serves it.
FILES_ROUTE = "/console/api/files"

#: Notices worth keeping in the transcript. Session start/finish cards and
#: "thinking" asides are live-progress chatter a reader does not need later.
_KEPT_NOTICES = {NoticeLevel.WARNING, NoticeLevel.ERROR}


async def apply_outcome(repo: ConversationRepository, thread_key: int, outcome: str | None) -> str:
    """Set (or clear, with ``None``) the outcome marker on a conversation's name."""
    conversation = await repo.get(thread_key)
    if conversation is None:
        raise LookupError(f"unknown console conversation {thread_key}")
    marked = set_outcome_thread_name(conversation.name, outcome)
    if marked != conversation.name:
        await repo.set_name(thread_key, marked)
    return marked


def _notice_text(notice: Notice) -> str:
    parts = []
    if notice.title:
        parts.append(f"**{notice.title}**")
    if notice.body:
        parts.append(f"```\n{notice.body}\n```" if notice.monospace_body else notice.body)
    parts.extend(f"- {name}: {value}" for name, value in notice.fields)
    return "\n".join(parts)


def _prompt_text(prompt: ChoicePrompt) -> str:
    lines = [f"**{prompt.header}**"] if prompt.header else []
    lines.append(prompt.question)
    for choice in prompt.choices:
        suffix = f" — {choice.description}" if choice.description else ""
        lines.append(f"- {choice.label}{suffix}")
    lines.append("\n_Reply with your answer._")
    return "\n".join(lines)


class _TextStream:
    def __init__(self, surface: ConsoleSurface, live: LiveState) -> None:
        self._surface = surface
        self._live = live
        self._buffer = ""
        self._done = False
        self._result = ""

    @property
    def has_content(self) -> bool:
        return bool(self._buffer)

    async def append(self, delta: str) -> None:
        if not self._done:
            self._buffer += delta
            self._live.draft = self._buffer
            self._live.touch()

    async def finalize(self, transform: Callable[[str], str] | None = None) -> str:
        if self._done:
            return self._result
        self._done = True
        self._live.draft = ""
        text = transform(self._buffer) if transform and self._buffer else self._buffer
        if text:
            await self._surface.send_text(text)
        self._result = text
        return text


class _Activity:
    """Tool activity is live progress: shown while the turn runs, never stored."""

    def __init__(self, entry: LiveActivity, live: LiveState) -> None:
        self._entry = entry
        self._live = live

    async def update(self, detail: str) -> None:
        if not self._entry.done:
            self._entry.detail = detail
            self._live.touch()

    async def complete(self, result: str | None, *, ok: bool = True) -> None:
        self._entry.done = True
        self._entry.ok = ok
        self._live.touch()

    async def cancel(self) -> None:
        await self.complete(None, ok=False)


class _Interrupt:
    """The Stop button: the console's live view calls ``on_stop`` through the board."""

    def __init__(self, on_stop: Callable[[], Awaitable[None]], live: LiveState) -> None:
        self.on_stop = on_stop
        self._live = live
        live.stop = on_stop

    async def bump(self) -> None:
        return None

    async def disable(self) -> None:
        if self._live.stop is self.on_stop:
            self._live.stop = None


class ConsoleSurface:
    """One console conversation."""

    def __init__(
        self,
        repo: ConversationRepository,
        thread_key: ThreadKey,
        external_id: str,
        *,
        working_dir: str | None = None,
        live: LiveBoard | None = None,
        files_dir: Path | None = None,
    ) -> None:
        self._repo = repo
        self._thread_key = thread_key
        self._external_id = external_id
        self.working_dir = working_dir
        self._live = live or LiveBoard()
        self._files_dir = files_dir

    @property
    def live(self) -> LiveState:
        return self._live.state(self._thread_key)

    @property
    def thread_key(self) -> ThreadKey:
        return self._thread_key

    @property
    def external_id(self) -> str:
        return self._external_id

    @property
    def frontend(self) -> str:
        return CONSOLE_FRONTEND

    @property
    def capabilities(self) -> SurfaceCapabilities:
        return CONSOLE_CAPABILITIES

    async def _store(self, text: str) -> str | None:
        if not text.strip():
            return None
        message_id = await self._repo.append(
            self._thread_key, author=AGENT_AUTHOR, is_bot=True, content=text
        )
        return str(message_id)

    async def send_text(self, text: str) -> str | None:
        return await self._store(text)

    async def send_notice(self, notice: Notice) -> str | None:
        if notice.level not in _KEPT_NOTICES:
            return None
        return await self._store(_notice_text(notice))

    async def deliver_files(self, files: Sequence[OutboundFile]) -> None:
        lines = []
        for f in files:
            link = await self._keep_file(f)
            if link is not None:
                lines.append(f"- [{f.display_name}]({link})")
            else:
                lines.append(f"- `{f.display_name}`" + (f" ({f.path})" if f.path else ""))
        if lines:
            await self._store("📎 Files:\n" + "\n".join(lines))

    async def _keep_file(self, file: OutboundFile) -> str | None:
        """Copy a delivered file where the console serves it; its link, or None."""
        if self._files_dir is None:
            return None
        token = uuid.uuid4().hex
        target = self._files_dir / str(self._thread_key) / token / file.display_name
        try:
            await asyncio.to_thread(_write_file, file, target, CONSOLE_CAPABILITIES.max_file_bytes)
        except (OSError, ValueError) as exc:
            logger.warning("console: could not keep %s: %s", file.display_name, exc)
            return None
        return f"{FILES_ROUTE}/{self._thread_key}/{token}/{file.display_name}"

    def open_stream(self) -> _TextStream:
        return _TextStream(self, self.live)

    async def open_activity(self, spec: ActivitySpec) -> _Activity:
        live = self.live
        entry = LiveActivity(title=spec.title, detail=spec.detail)
        live.activities.append(entry)
        live.touch()
        return _Activity(entry, live)

    async def set_status(self, status: StatusKind) -> None:
        live = self.live
        live.status = str(status)
        live.touch()

    async def clear_status(self) -> None:
        live = self.live
        live.status = None
        live.touch()

    async def prompt_choice(self, prompt: ChoicePrompt) -> tuple[str, ...] | None:
        # The console has no live buttons. The question goes into the
        # transcript; the human's reply resumes the session with the answer.
        await self._store(_prompt_text(prompt))
        return None

    async def prompt_form(self, prompt: FormPrompt) -> dict[str, str] | None:
        fields = "\n".join(f"- {f.label}" for f in prompt.fields)
        description = f"\n{prompt.description}" if prompt.description else ""
        await self._store(f"**{prompt.title}**{description}\n{fields}\n\n_Reply with the values._")
        return None

    async def prompt_url(self, title: str, url: str, *, notify: Mention | None = None) -> bool:
        await self._store(f"**{title}**\n{url}")
        return True

    async def offer_interrupt(self, on_stop: Callable[[], Awaitable[None]]) -> _Interrupt:
        return _Interrupt(on_stop, self.live)

    async def rename(self, title: str) -> None:
        await self._repo.set_name(self._thread_key, title)

    async def set_outcome(self, outcome: str | None) -> None:
        """Show how the turn ended, the way a chat thread's title would."""
        await apply_outcome(self._repo, self._thread_key, outcome)

    async def recent_transcript(self, days: int) -> str | None:
        return None


def _write_file(file: OutboundFile, target: Path, limit: int) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if file.blob is not None:
        if len(file.blob) > limit:
            raise ValueError("file too large")
        target.write_bytes(file.blob)
        return
    source = Path(file.path or "")
    if source.stat().st_size > limit:
        raise ValueError("file too large")
    shutil.copyfile(source, target)


class ConsoleFrontend:
    """Hands out console conversations, keyed through the shared ledger."""

    name = CONSOLE_FRONTEND

    def __init__(
        self,
        repo: ConversationRepository,
        ledger: FrontendThreadRepository,
        *,
        working_dir: str | None = None,
        files_dir: Path | None = None,
    ) -> None:
        self.repo = repo
        self._ledger = ledger
        self._working_dir = working_dir
        #: Delivered files, served by ``files.py``. None: files are only named.
        self.files_dir = files_dir
        self.live = LiveBoard()

    def _surface(self, thread_key: ThreadKey, external_id: str) -> ConsoleSurface:
        return ConsoleSurface(
            self.repo,
            thread_key,
            external_id,
            working_dir=self._working_dir,
            live=self.live,
            files_dir=self.files_dir,
        )

    async def start(self) -> None:
        await self.repo.init_db()

    async def close(self) -> None:
        return None

    async def owns(self, thread_key: ThreadKey) -> bool:
        return await self.repo.get(thread_key) is not None

    async def open(self, *, external_id: str, title: str) -> ConsoleSurface:
        """Create (or reopen) the conversation for *external_id*."""
        key = await self._ledger.register(CONSOLE_FRONTEND, external_id)
        await self.repo.create(key, title)
        return self._surface(key, external_id)

    async def resolve_surface(self, thread_key: ThreadKey) -> ConsoleSurface | None:
        record = await self._ledger.resolve(thread_key)
        if record is None or record.frontend != CONSOLE_FRONTEND:
            return None
        if not await self.owns(thread_key):
            return None
        return self._surface(thread_key, record.external_id)

    async def create_surface(self, *, parent_id: str, title: str) -> ConsoleSurface:
        return await self.open(external_id=f"{parent_id}:{uuid.uuid4().hex[:12]}", title=title)
