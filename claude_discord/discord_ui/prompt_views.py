"""Generic Discord UI for the frontend protocol's prompts.

``ChoicePrompt`` and ``FormPrompt`` are the protocol's two ways of asking the
user something. Discord used to have a view per *caller* — AskView for
AskUserQuestion, PermissionView for tool approval, PlanApprovalView for plan
mode, two more for elicitation — each with its own answer plumbing and its own
copy of "what happens when nobody replies". All but AskView now route through
here, so a new caller does not have to invent a sixth view, and a non-Discord
frontend inherits every one of those flows by implementing two methods.

Why buttons up to four choices and a select menu beyond
-------------------------------------------------------
Discord allows five components per action row. Four leaves room for the free
text option without spilling into a second row, and matching the existing
AskView threshold keeps the two visually consistent while both exist.

Timeouts fail closed, and they are ours
---------------------------------------
``ChoicePrompt.default_on_timeout`` exists so an unattended permission request
denies rather than hangs. This module honours it; a prompt without one simply
returns ``None`` and the caller treats the turn as unanswered.

The waiting is done with our own ``asyncio.wait_for`` rather than
``discord.ui.View``'s built-in timeout, because the built-in one only starts
once the view has been registered with the client. If the message is posted
but the view never gets dispatched, discord.py's timer never fires and the
caller waits forever — a session hung on a permission request that nobody can
see. Owning the clock means the timeout holds regardless of what Discord did
with the view.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import discord

from claude_code_core.frontend import ChoicePrompt, FormField, FormPrompt

from ..message_actions import (
    STYLE_DANGER,
    STYLE_SECONDARY,
    STYLE_SUCCESS,
    MessageAction,
)

logger = logging.getLogger(__name__)

# Discord allows five components per action row; four buttons leaves a slot
# for the free-text option.
MAX_BUTTONS = 4


class ChoiceView(discord.ui.View):
    """Buttons or a select menu for a :class:`ChoicePrompt`.

    Resolve with :meth:`wait_for_answer`, which returns the chosen *values*
    (not labels — the caller matches on values), or ``None`` when the prompt
    times out with no default.
    """

    def __init__(self, prompt: ChoicePrompt) -> None:
        super().__init__(timeout=prompt.timeout_seconds)
        self._prompt = prompt
        self._future: asyncio.Future[tuple[str, ...] | None] = (
            asyncio.get_running_loop().create_future()
        )

        choices = prompt.choices
        if choices and (prompt.multi_select or len(choices) > MAX_BUTTONS):
            self.add_item(_ChoiceSelect(self, prompt))
        else:
            for choice in choices:
                self.add_item(_ChoiceButton(self, choice))

    async def wait_for_answer(self) -> tuple[str, ...] | None:
        """Await the user's choice, or fall back when the clock runs out."""
        timeout = self._prompt.timeout_seconds
        try:
            return await asyncio.wait_for(asyncio.shield(self._future), timeout)
        except (TimeoutError, asyncio.CancelledError):
            default = self._prompt.default_on_timeout
            self._resolve((default,) if default is not None else None)
            return (default,) if default is not None else None

    def console_actions(self, message: discord.Message) -> list[MessageAction]:
        """The choices as actions the Relay Console can press.

        Single choice only: a multi-select needs a form, so the console
        answers those by replying in text as before.
        """
        if self._prompt.multi_select:
            return []
        return [
            MessageAction(
                c.label,
                self._console_answer(message, c.value),
                _ACTION_STYLES.get(c.style, STYLE_SECONDARY),
            )
            for c in self._prompt.choices[:25]
        ]

    def _console_answer(self, message: discord.Message, value: str):  # noqa: ANN202
        async def answer(who: str) -> None:
            self._resolve((value,))
            await _disable_message(message, self)

        return answer

    def _resolve(self, values: tuple[str, ...] | None) -> None:
        if not self._future.done():
            self._future.set_result(values)
        self.stop()

    async def on_timeout(self) -> None:
        default = self._prompt.default_on_timeout
        self._resolve((default,) if default is not None else None)


_ACTION_STYLES = {"positive": STYLE_SUCCESS, "destructive": STYLE_DANGER}


class _ChoiceButton(discord.ui.Button):
    _STYLES = {
        "default": discord.ButtonStyle.secondary,
        "positive": discord.ButtonStyle.success,
        "destructive": discord.ButtonStyle.danger,
    }

    def __init__(self, view: ChoiceView, choice) -> None:  # noqa: ANN001 — Choice, avoids cycle
        super().__init__(
            label=choice.label[:80],
            style=self._STYLES.get(choice.style, discord.ButtonStyle.secondary),
        )
        self._owner = view
        self._value = choice.value

    async def callback(self, interaction: discord.Interaction) -> None:
        with contextlib.suppress(discord.HTTPException):
            await interaction.response.defer()
        self._owner._resolve((self._value,))
        await _disable(interaction, self._owner)


class _ChoiceSelect(discord.ui.Select):
    def __init__(self, view: ChoiceView, prompt: ChoicePrompt) -> None:
        options = [
            discord.SelectOption(
                label=c.label[:100],
                value=c.value[:100],
                description=(c.description or "")[:100] or None,
            )
            for c in prompt.choices[:25]  # Discord's per-menu cap
        ]
        super().__init__(
            placeholder=prompt.header or "Choose…",
            min_values=1,
            max_values=len(options) if prompt.multi_select else 1,
            options=options,
        )
        self._owner = view

    async def callback(self, interaction: discord.Interaction) -> None:
        with contextlib.suppress(discord.HTTPException):
            await interaction.response.defer()
        self._owner._resolve(tuple(self.values))
        await _disable(interaction, self._owner)


class FormModal(discord.ui.Modal):
    """A :class:`FormPrompt` rendered as a Discord modal.

    Discord modals hold five inputs and only text, so richer field kinds
    degrade to text rather than disappearing — a truncated form the user can
    still complete beats a missing one.
    """

    MAX_INPUTS = 5

    def __init__(self, prompt: FormPrompt) -> None:
        super().__init__(title=prompt.title[:45], timeout=prompt.timeout_seconds)
        self._keys: list[str] = []
        self._future: asyncio.Future[dict[str, str] | None] = (
            asyncio.get_running_loop().create_future()
        )

        for field in prompt.fields[: self.MAX_INPUTS]:
            self._keys.append(field.key)
            self.add_item(
                discord.ui.TextInput(
                    label=field.label[:45],
                    placeholder=(field.placeholder or _placeholder_for(field))[:100],
                    required=field.required,
                    style=(
                        discord.TextStyle.paragraph
                        if field.kind == "multiline"
                        else discord.TextStyle.short
                    ),
                )
            )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        with contextlib.suppress(discord.HTTPException):
            await interaction.response.defer()
        answers = {
            key: child.value
            for key, child in zip(self._keys, self.children, strict=False)
            if isinstance(child, discord.ui.TextInput)
        }
        if not self._future.done():
            self._future.set_result(answers)

    async def on_timeout(self) -> None:
        if not self._future.done():
            self._future.set_result(None)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        logger.warning("Form modal failed", exc_info=error)
        if not self._future.done():
            self._future.set_result(None)

    async def wait_for_answer(self, timeout: float | None = None) -> dict[str, str] | None:
        try:
            return await asyncio.wait_for(asyncio.shield(self._future), timeout)
        except (TimeoutError, asyncio.CancelledError):
            if not self._future.done():
                self._future.set_result(None)
            return None


def _placeholder_for(field: FormField) -> str:
    """Hint at a kind Discord cannot render natively."""
    if field.kind == "choice":
        return " / ".join(c.label for c in field.choices)[:100]
    if field.kind == "toggle":
        return "yes / no"
    if field.kind == "number":
        return "a number"
    return ""


async def _disable(interaction: discord.Interaction, view: discord.ui.View) -> None:
    """Grey the controls out so a second click cannot race the first."""
    await _disable_message(interaction.message, view)


async def _disable_message(message: discord.Message | None, view: discord.ui.View) -> None:
    for child in view.children:
        if isinstance(child, discord.ui.Button | discord.ui.Select):
            child.disabled = True
    if message:
        with contextlib.suppress(discord.HTTPException):
            await message.edit(view=view)
