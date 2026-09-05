"""Discord's implementation of :class:`ConversationSurface`.

This is a *binding*, not a rewrite. Every behaviour it exposes already
existed — ``StreamingMessageManager`` for live text, ``StatusManager`` for the
emoji reaction, ``LiveToolTimer`` for the elapsed counter, ``send_files`` for
attachments, ``StopView`` for interruption. What is new is that they are now
reachable through vocabulary that names *intents*, so the session machinery
can drive Discord without importing it.

The mapping is the interesting part, because it is where Discord gets to be
Discord:

===================  ========================================================
Intent               How Discord does it
===================  ========================================================
``send_text``        chunked to 2,000 chars, tables rendered to monospace
``send_notice``      a coloured embed — the level picks the colour
``open_activity``    one embed per tool, edited in place on completion, with
                     a live elapsed-time counter while it runs
``set_status``       an emoji reaction on the user's own message, debounced
``prompt_choice``    buttons, or a select menu past four options
``prompt_form``      a modal
``offer_interrupt``  a Stop button that re-posts itself to stay in view
===================  ========================================================

A Teams surface will answer the same calls very differently — folding tool
activity into one card it keeps updating rather than posting an embed each
time — and both are correct. That freedom is the point.

Discord-specific degradations are handled here rather than pushed onto the
caller. ``set_status`` needs a message to react to; when the surface was
built without one (a scheduled run has no user message), it is a no-op rather
than an error.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable, Callable, Sequence

import discord

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
from claude_code_core.rendering import render_for, wrap_tables_in_fences
from claude_code_core.types import ToolCategory

from .discord_ui.chunker import DISCORD_CAPABILITIES
from .discord_ui.embeds import (
    CATEGORY_ICON,
    COLOR_ERROR,
    COLOR_INFO,
    COLOR_SUCCESS,
    COLOR_TODO,
    COLOR_TOOL,
    tool_result_embed,
    tool_result_preview_embed,
)
from .discord_ui.file_sender import send_file_blobs, send_files
from .discord_ui.prompt_views import ChoiceView, FormModal
from .discord_ui.status import StatusManager
from .discord_ui.streaming_manager import StreamingMessageManager
from .discord_ui.tool_timer import TOOL_TIMER_INTERVAL
from .discord_ui.views import StopView, ToolResultView

logger = logging.getLogger(__name__)

_NOTICE_COLOR: dict[NoticeLevel, int] = {
    NoticeLevel.INFO: COLOR_INFO,
    NoticeLevel.SUCCESS: COLOR_SUCCESS,
    NoticeLevel.WARNING: COLOR_TODO,  # orange
    NoticeLevel.ERROR: COLOR_ERROR,
    NoticeLevel.SUBTLE: COLOR_INFO,
}

# Tool categories map to the icons already used in embeds.py.
_ACTIVITY_ICON = "\U0001f527"  # 🔧


class DiscordActivity:
    """One tool call, shown as an embed that is edited when it finishes."""

    def __init__(self, message: discord.Message | None, spec: ActivitySpec) -> None:
        self._message = message
        self._spec = spec
        self._finished = False
        self._started_at = time.monotonic()
        self._timer = (
            asyncio.create_task(self._run_timer())
            if message is not None and spec.kind == "tool"
            else None
        )

    async def _run_timer(self) -> None:
        """Keep the elapsed counter owned by the Discord adapter."""
        try:
            await self.update("⏳ 0s elapsed...")
            while True:
                await asyncio.sleep(TOOL_TIMER_INTERVAL)
                elapsed = int(time.monotonic() - self._started_at)
                await self.update(f"⏳ {elapsed}s elapsed...")
        except asyncio.CancelledError:
            pass

    def _stop_timer(self) -> None:
        if self._timer is not None and not self._timer.done():
            self._timer.cancel()

    async def update(self, detail: str) -> None:
        if self._finished or self._message is None:
            return
        with contextlib.suppress(Exception):
            await self._message.edit(embed=_activity_embed(self._spec, detail))

    async def complete(self, result: str | None, *, ok: bool = True) -> None:
        # Idempotent by contract: a session that errors after a tool finished
        # must not take the surface down with it.
        if self._finished:
            return
        self._finished = True
        self._stop_timer()
        if self._message is None:
            return
        title = _activity_title(self._spec)
        try:
            content = result or ""
            if len(content.splitlines()) > 1:
                await self._message.edit(
                    embed=tool_result_preview_embed(title, content),
                    view=ToolResultView(title, content),
                )
            else:
                await self._message.edit(embed=tool_result_embed(title, content))
        except Exception:
            logger.warning("Failed to complete Discord activity", exc_info=True)

    async def cancel(self) -> None:
        if self._finished:
            return
        self._finished = True
        self._stop_timer()
        if self._message is None:
            return
        if self._spec.kind == "todo":
            with contextlib.suppress(Exception):
                await self._message.delete()
            return
        with contextlib.suppress(Exception):
            await self._message.edit(embed=_activity_embed(self._spec, "cancelled"))


class DiscordStream:
    """Live text, delegated to the existing debounced streaming manager."""

    def __init__(self, manager: StreamingMessageManager) -> None:
        self._manager = manager
        self._finalized = False
        self._result = ""

    @property
    def has_content(self) -> bool:
        return self._manager.has_content

    async def append(self, delta: str) -> None:
        if self._finalized:
            return
        await self._manager.append(delta)

    async def finalize(self, transform: Callable[[str], str] | None = None) -> str:
        if self._finalized:
            return self._result
        self._finalized = True
        if transform is None:
            transform = _prepare_stream_text
        self._result = await self._manager.finalize(transform=transform)
        return self._result


class DiscordInterrupt:
    """The Stop button, wrapped so the protocol does not name discord.ui."""

    def __init__(self, view: StopView, thread: discord.abc.Messageable) -> None:
        self._view = view
        self._thread = thread
        self._disabled = False

    async def bump(self) -> None:
        if self._disabled:
            return
        await self._view.bump(self._thread)  # type: ignore[arg-type]

    async def disable(self) -> None:
        if self._disabled:
            return
        self._disabled = True
        await self._view.disable()


class DiscordSurface:
    """A Discord thread, driven through the frontend protocol.

    Args:
        thread: The thread (or channel, for inline-reply mode) to post in.
        status_message: The user's own message, for the emoji status
            reaction. Omit for flows that have no user message — scheduled
            runs and webhook triggers — and status becomes a no-op.
        model: Used only to widen the stall thresholds for slower models.
        interrupt_runner: The backend the Stop button interrupts. Without it,
            ``offer_interrupt`` still returns a working handle so callers need
            no special case; it simply has no button behind it.
    """

    frontend = "discord"

    def __init__(
        self,
        thread: discord.Thread | discord.TextChannel,
        *,
        status_message: discord.Message | None = None,
        status_manager: StatusManager | None = None,
        model: str | None = None,
        working_dir: str | None = None,
        interrupt_runner: object | None = None,
        interrupt_view: StopView | None = None,
    ) -> None:
        self._thread = thread
        self._status_message = status_message
        self._model = model
        self.working_dir = working_dir
        self._interrupt_runner = interrupt_runner
        self._interrupt_view = interrupt_view
        self._status: StatusManager | None = status_manager
        self._thread_missing = False

    # -- identity ----------------------------------------------------------
    @property
    def thread_key(self) -> ThreadKey:
        """Discord snowflakes are used verbatim — no surrogate needed."""
        return self._thread.id

    @property
    def external_id(self) -> str:
        return str(self._thread.id)

    @property
    def native_thread(self) -> discord.Thread | discord.TextChannel:
        """The Discord object behind this surface.

        An escape hatch for the flows that are still Discord-only — chiefly
        AskUserQuestion, whose views are persisted and restored across a bot
        restart. Everything expressible in the protocol should use the protocol;
        this exists so the remainder does not have to smuggle a raw thread
        alongside the surface.
        """
        return self._thread

    @property
    def capabilities(self) -> SurfaceCapabilities:
        return DISCORD_CAPABILITIES

    @property
    def thread(self) -> discord.Thread | discord.TextChannel:
        """The underlying thread, for code that is still Discord-specific."""
        return self._thread

    # -- output ------------------------------------------------------------
    async def send_text(self, text: str) -> str | None:
        if self._thread_missing:
            return None
        last: discord.Message | None = None
        for chunk in render_for(text, DISCORD_CAPABILITIES):
            try:
                last = await self._thread.send(chunk)
            except discord.NotFound:
                self._remember_missing_thread()
                return None
            except discord.HTTPException:
                logger.warning("Failed to send message chunk", exc_info=True)
                return None
        return str(last.id) if last else None

    async def send_notice(self, notice: Notice) -> str | None:
        if self._thread_missing:
            return None
        embed = discord.Embed(color=_NOTICE_COLOR.get(notice.level, COLOR_INFO))
        if notice.title:
            embed.title = notice.title[:256]
        if notice.body:
            body = f"```\n{notice.body}\n```" if notice.monospace_body else notice.body
            embed.description = body[:4096]
        for name, value in notice.fields:
            embed.add_field(name=name[:256], value=value[:1024], inline=True)
        try:
            sent = await self._thread.send(embed=embed)
        except discord.NotFound:
            self._remember_missing_thread()
            return None
        except discord.HTTPException:
            logger.warning("Failed to send notice", exc_info=True)
            return None
        return str(sent.id)

    async def deliver_files(self, files: Sequence[OutboundFile]) -> None:
        if self._thread_missing or not files:
            return
        paths = [f.path for f in files if f.path]
        blobs = [(f.display_name, f.blob) for f in files if f.blob is not None]
        if paths:
            await send_files(self._thread, paths, self.working_dir, require_delivery=True)  # type: ignore[arg-type]
        if blobs:
            await send_file_blobs(self._thread, blobs)  # type: ignore[arg-type]

    def open_stream(self) -> DiscordStream:
        return DiscordStream(StreamingMessageManager(self._thread))

    async def open_activity(self, spec: ActivitySpec) -> DiscordActivity:
        if self._thread_missing:
            return DiscordActivity(None, spec)
        message: discord.Message | None = None
        try:
            message = await self._thread.send(embed=_activity_embed(spec, spec.detail))
        except discord.NotFound:
            self._remember_missing_thread()
        except discord.HTTPException:
            pass
        return DiscordActivity(message, spec)

    def _remember_missing_thread(self) -> None:
        if not self._thread_missing:
            logger.info("Thread %d disappeared; disabling further delivery", self._thread.id)
        self._thread_missing = True

    # -- state -------------------------------------------------------------
    async def set_status(self, status: StatusKind) -> None:
        manager = self._ensure_status()
        if manager is None:
            return
        if status is StatusKind.DONE:
            await manager.set_done()
        elif status is StatusKind.ERROR:
            await manager.set_error()
        elif status is StatusKind.COMPACTING:
            await manager.set_compact()
        elif status is StatusKind.HOOK:
            await manager.set_hook()
        elif status in _TOOL_STATUSES:
            await manager.set_tool(_TOOL_STATUSES[status])
        else:
            await manager.set_thinking()

    async def clear_status(self) -> None:
        if self._status is not None:
            await self._status.cleanup()

    def _ensure_status(self) -> StatusManager | None:
        if self._status is None and self._status_message is not None:
            self._status = StatusManager(self._status_message, model=self._model)
        return self._status

    # -- interaction -------------------------------------------------------
    async def prompt_choice(self, prompt: ChoicePrompt) -> tuple[str, ...] | None:
        view = ChoiceView(prompt)
        embed = discord.Embed(
            title=(prompt.header or "Question")[:256],
            description=prompt.question[:4096],
            color=COLOR_INFO,
        )
        try:
            await self._thread.send(content=_mention_text(prompt.notify), embed=embed, view=view)
        except discord.HTTPException:
            logger.warning("Failed to post choice prompt", exc_info=True)
            # The prompt never reached the user, so fall back the same way a
            # timeout would — a permission request must still fail closed.
            default = prompt.default_on_timeout
            return (default,) if default is not None else None
        return await view.wait_for_answer()

    async def prompt_form(self, prompt: FormPrompt) -> dict[str, str] | None:
        """Discord modals can only open from an interaction, so the form is
        offered behind a button rather than appearing unprompted."""
        view = FormLauncher(prompt)
        embed = discord.Embed(
            title=prompt.title[:256],
            description=(prompt.description or "")[:4096] or None,
            color=COLOR_INFO,
        )
        try:
            await self._thread.send(content=_mention_text(prompt.notify), embed=embed, view=view)
        except discord.HTTPException:
            logger.warning("Failed to post form prompt", exc_info=True)
            return None
        return await view.wait_for_answer()

    async def prompt_url(self, title: str, url: str, *, notify: Mention | None = None) -> bool:
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label=title[:80], url=url))
        try:
            await self._thread.send(content=_mention_text(notify), view=view)
        except discord.HTTPException:
            logger.warning("Failed to post URL prompt", exc_info=True)
            return False
        return True

    async def offer_interrupt(self, on_stop: Callable[[], Awaitable[None]]) -> DiscordInterrupt:
        view = self._interrupt_view or StopView(_StopAdapter(on_stop))  # type: ignore[arg-type]
        return DiscordInterrupt(view, self._thread)

    # -- management --------------------------------------------------------
    async def rename(self, title: str) -> None:
        if not isinstance(self._thread, discord.Thread):
            return  # a channel is not ours to rename
        with contextlib.suppress(discord.HTTPException):
            await self._thread.edit(name=title[:100])

    async def recent_transcript(self, days: int) -> str | None:
        from .discord_ui.thread_context import build_recent_transcript

        if not isinstance(self._thread, discord.Thread):
            return None
        return await build_recent_transcript(self._thread, days=days)


class _StopAdapter:
    """Lets StopView, which expects a backend, drive an arbitrary callback."""

    def __init__(self, on_stop: Callable[[], Awaitable[None]]) -> None:
        self._on_stop = on_stop

    async def interrupt(self) -> None:
        await self._on_stop()


class FormLauncher(discord.ui.View):
    """A button that opens the modal — Discord requires an interaction.

    Like :class:`ChoiceView`, the clock is ours rather than discord.py's, so a
    view that never gets dispatched cannot hang the session.
    """

    def __init__(self, prompt: FormPrompt) -> None:
        super().__init__(timeout=prompt.timeout_seconds)
        self._prompt = prompt
        self._answer: asyncio.Future[dict[str, str] | None] = (
            asyncio.get_running_loop().create_future()
        )
        button = discord.ui.Button(
            label=prompt.submit_label[:80], style=discord.ButtonStyle.primary
        )
        button.callback = self._open  # type: ignore[method-assign]
        self.add_item(button)

    async def _open(self, interaction: discord.Interaction) -> None:
        modal = FormModal(self._prompt)
        await interaction.response.send_modal(modal)
        answers = await modal.wait_for_answer(self._prompt.timeout_seconds)
        if not self._answer.done():
            self._answer.set_result(answers)
        self.stop()

    async def wait_for_answer(self) -> dict[str, str] | None:
        try:
            return await asyncio.wait_for(
                asyncio.shield(self._answer), self._prompt.timeout_seconds
            )
        except (TimeoutError, asyncio.CancelledError):
            return None


_TOOL_STATUSES: dict[StatusKind, ToolCategory] = {
    StatusKind.TOOL_READ: ToolCategory.READ,
    StatusKind.TOOL_EDIT: ToolCategory.EDIT,
    StatusKind.TOOL_COMMAND: ToolCategory.COMMAND,
    StatusKind.TOOL_WEB: ToolCategory.WEB,
    StatusKind.TOOL_OTHER: ToolCategory.OTHER,
}


def _activity_embed(spec: ActivitySpec, detail: str | None) -> discord.Embed:
    suffix = "..." if spec.kind == "tool" else ""
    embed = discord.Embed(title=f"{_activity_title(spec)}{suffix}"[:256], color=COLOR_TOOL)
    if detail:
        embed.description = detail[:4096]
    return embed


def _activity_title(spec: ActivitySpec) -> str:
    icon = CATEGORY_ICON.get(spec.category, _ACTIVITY_ICON) if spec.category else _ACTIVITY_ICON
    return f"{icon} {spec.title}"


def _prepare_stream_text(text: str) -> str:
    return wrap_tables_in_fences(
        text,
        max_width=DISCORD_CAPABILITIES.monospace_width,
        cjk_is_double_width=DISCORD_CAPABILITIES.monospace_cjk_is_double_width,
    )


def _mention_text(mention: Mention | None) -> str | None:
    return f"<@{mention.external_user_id}>" if mention else None
