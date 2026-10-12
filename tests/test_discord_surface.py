"""DiscordSurface must pass the same contract every other frontend will.

This is the test that makes the abstraction real. Up to now the conformance
suite has only been run against ``MemorySurface``, which was written to pass
it. Running it against the Discord binding — over a faked discord.py, but with
the binding's own logic untouched — is what proves the contract describes
something a *real* surface can satisfy, rather than something only a purpose-
built double can.

When the Teams surface arrives it runs this identical suite. That is the
mechanism by which "Teams is missing a feature" stops being possible: it would
have to fail a check that Discord passes, in CI, before merge.

The Discord fakes below are deliberately thin. They record calls and return
plausible objects; they do not simulate Discord. Anything that needs real
Discord semantics is not a contract obligation and does not belong here.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from claude_code_core.conformance import check_surface
from claude_code_core.frontend import (
    ActivitySpec,
    Choice,
    ChoicePrompt,
    ConversationSurface,
    Notice,
    NoticeLevel,
    OutboundFile,
    StatusKind,
)
from claude_discord.surface import DiscordSurface


class FakeMessage(MagicMock):
    """A sent message that can be edited and knows its id."""

    def __init__(self, content: str = "", **kwargs: Any) -> None:
        super().__init__(spec=discord.Message, **kwargs)
        self.id = 555
        self.content = content
        self.jump_url = "https://discord.test/555"
        self.guild = None
        self.edit = AsyncMock()
        self.delete = AsyncMock()
        self.add_reaction = AsyncMock()
        self.remove_reaction = AsyncMock()


class RecordingSurface(DiscordSurface):
    """DiscordSurface plus the two hooks the contract reads.

    Recording lives in the test, not in ``DiscordSurface`` — production code
    should not carry test scaffolding just so a checker can see inside it.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.conformance_sent_text: list[str] = []
        self.conformance_delivered_files: list[str] = []

    async def send_text(self, text: str) -> str | None:
        from claude_code_core.rendering import render_for

        self.conformance_sent_text.extend(render_for(text, self.capabilities))
        return await super().send_text(text)

    async def deliver_files(self, files):  # type: ignore[no-untyped-def]
        self.conformance_delivered_files.extend(f.display_name for f in files)
        return await super().deliver_files(files)


def _fake_thread() -> MagicMock:
    thread = MagicMock(spec=discord.Thread)
    thread.id = 1234567890123456789  # a plausible snowflake
    thread.send = AsyncMock(side_effect=lambda *a, **k: FakeMessage(a[0] if a else ""))
    thread.edit = AsyncMock()
    return thread


def _make_surface(**kwargs: Any) -> RecordingSurface:
    return RecordingSurface(_fake_thread(), **kwargs)


class TestDiscordSurfaceIsConformant:
    async def test_passes_every_contract_check(self) -> None:
        report = await check_surface(lambda: _as_async(_make_surface()))
        assert report.ok, report.summary()

    async def test_passes_without_a_status_message(self) -> None:
        """Scheduled runs and webhook triggers have no user message to react
        to. Status must degrade to a no-op rather than fail the session."""
        report = await check_surface(lambda: _as_async(_make_surface(status_message=None)))
        assert report.ok, report.summary()

    async def test_satisfies_the_protocol_structurally(self) -> None:
        assert isinstance(_make_surface(), ConversationSurface)


class TestIdentityMatchesDiscord:
    def test_thread_key_is_the_snowflake_verbatim(self) -> None:
        """Discord ids are already ints in the ledger's key space, so there is
        no surrogate — and existing rows keep resolving after the protocol
        migration."""
        thread = _fake_thread()
        surface = RecordingSurface(thread)
        assert surface.thread_key == thread.id
        assert surface.external_id == str(thread.id)
        assert surface.frontend == "discord"

    def test_capabilities_report_discord_reality(self) -> None:
        caps = _make_surface().capabilities
        assert caps.max_message_chars == 2000
        assert caps.supports_reactions is True
        assert caps.supports_slash_commands is True
        # Discord's code-block font misreports CJK width, so CJK tables must
        # keep falling back to the vertical layout.
        assert caps.monospace_cjk_is_double_width is False


class TestOutputGoesThroughDiscord:
    async def test_long_text_is_sent_as_several_messages(self) -> None:
        surface = _make_surface()
        await surface.send_text("x" * 5000)
        assert surface.thread.send.await_count > 1

    async def test_empty_text_sends_nothing(self) -> None:
        surface = _make_surface()
        await surface.send_text("")
        surface.thread.send.assert_not_awaited()

    async def test_notice_level_picks_the_embed_colour(self) -> None:
        from claude_discord.discord_ui.embeds import COLOR_ERROR, COLOR_SUCCESS

        surface = _make_surface()
        await surface.send_notice(Notice(level=NoticeLevel.SUCCESS, title="done"))
        await surface.send_notice(Notice(level=NoticeLevel.ERROR, title="boom"))
        colours = [c.kwargs["embed"].color.value for c in surface.thread.send.await_args_list]
        assert colours == [COLOR_SUCCESS, COLOR_ERROR]

    async def test_a_deleted_thread_does_not_raise(self) -> None:
        """A user can delete a thread mid-session; that must not surface as a
        crash in the middle of a Claude run."""
        surface = _make_surface()
        surface.thread.send = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "gone"))
        assert await surface.send_text("hello") is None

    async def test_a_deleted_thread_is_only_contacted_once(self) -> None:
        """Later assistant events must not retry a thread already known missing."""
        surface = _make_surface()
        surface.thread.send = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "gone"))

        assert await surface.send_text("first") is None
        assert await surface.send_text("second") is None

        surface.thread.send.assert_awaited_once()

    async def test_known_deleted_thread_skips_completion_files(self) -> None:
        """Completion must not attempt attachments after text found the thread missing."""
        surface = _make_surface()
        surface.thread.send = AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "gone"))
        await surface.send_text("first")

        with patch("claude_discord.surface.send_files", new_callable=AsyncMock) as send:
            await surface.deliver_files(
                [OutboundFile(display_name="result.txt", path="/tmp/result.txt")]
            )

        send.assert_not_awaited()

    async def test_unexpected_send_failure_does_not_mark_thread_missing(self) -> None:
        """Only Discord NotFound changes the delivery state."""
        surface = _make_surface()
        surface.thread.send = AsyncMock(side_effect=RuntimeError("unexpected bug"))

        with pytest.raises(RuntimeError, match="unexpected bug"):
            await surface.send_text("first")

        surface.thread.send.side_effect = None
        surface.thread.send.return_value = FakeMessage("second")
        assert await surface.send_text("second") == "555"
        assert surface.thread.send.await_count == 2


class TestActivityMapsToAnEditedEmbed:
    async def test_completion_edits_the_original_message(self) -> None:
        surface = _make_surface()
        handle = await surface.open_activity(ActivitySpec(kind="tool", title="Read"))
        sent = surface.thread.send.await_args
        assert sent is not None
        await handle.complete("42 lines")
        # One send for the in-progress embed, then an edit rather than a
        # second message — the thread stays readable.
        assert surface.thread.send.await_count == 1

    async def test_completing_twice_is_a_no_op(self) -> None:
        surface = _make_surface()
        handle = await surface.open_activity(ActivitySpec(kind="tool", title="Bash"))
        await handle.complete("ok")
        await handle.complete("ok again")  # must not raise or re-edit


class TestStatusDegradesWithoutAMessage:
    async def test_no_status_message_means_no_reaction(self) -> None:
        surface = _make_surface(status_message=None)
        await surface.set_status(StatusKind.THINKING)
        await surface.clear_status()  # must not raise

    async def test_status_reacts_on_the_user_message(self) -> None:
        message = FakeMessage()
        surface = _make_surface(status_message=message)
        await surface.set_status(StatusKind.TOOL_EDIT)
        # StatusManager debounces, so the reaction is scheduled rather than
        # immediate; the contract only requires that this does not raise.
        assert surface._status is not None


class TestChoicePrompt:
    async def test_timeout_returns_the_declared_default(self) -> None:
        """An unattended permission request must deny, not hang."""
        surface = _make_surface()
        prompt = ChoicePrompt(
            question="Run rm -rf?",
            choices=(Choice(value="allow", label="Allow"), Choice(value="deny", label="Deny")),
            default_on_timeout="deny",
            timeout_seconds=0.05,
        )
        assert await surface.prompt_choice(prompt) == ("deny",)

    async def test_timeout_without_a_default_returns_none(self) -> None:
        surface = _make_surface()
        prompt = ChoicePrompt(
            question="Which?",
            choices=(Choice(value="a", label="A"),),
            timeout_seconds=0.05,
        )
        assert await surface.prompt_choice(prompt) is None

    async def test_a_failed_post_still_fails_closed(self) -> None:
        """If the prompt never reaches the user, a permission request must
        land on its deny default rather than silently allowing."""
        surface = _make_surface()
        surface.thread.send = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=500), "nope")
        )
        prompt = ChoicePrompt(
            question="Run it?",
            choices=(Choice(value="allow", label="A"), Choice(value="deny", label="D")),
            default_on_timeout="deny",
            timeout_seconds=5,
        )
        assert await surface.prompt_choice(prompt) == ("deny",)

    async def test_many_choices_render_as_a_select_menu(self) -> None:
        from claude_discord.discord_ui.prompt_views import ChoiceView, _ChoiceSelect

        prompt = ChoicePrompt(
            question="Pick",
            choices=tuple(Choice(value=str(i), label=f"Option {i}") for i in range(8)),
        )
        view = ChoiceView(prompt)
        assert any(isinstance(c, _ChoiceSelect) for c in view.children)

    async def test_few_choices_render_as_buttons(self) -> None:
        from claude_discord.discord_ui.prompt_views import ChoiceView, _ChoiceButton

        prompt = ChoicePrompt(
            question="Pick",
            choices=(Choice(value="y", label="Yes"), Choice(value="n", label="No")),
        )
        view = ChoiceView(prompt)
        assert all(isinstance(c, _ChoiceButton) for c in view.children)
        assert len(view.children) == 2

    async def test_choices_can_be_answered_from_the_console(self) -> None:
        import asyncio

        from claude_discord.message_actions import message_actions

        surface = _make_surface()
        sent = MagicMock(id=4242)
        sent.edit = AsyncMock()
        surface.thread.send = AsyncMock(return_value=sent)
        prompt = ChoicePrompt(
            question="Run it?",
            choices=(
                Choice(value="allow", label="Allow", style="positive"),
                Choice(value="deny", label="Deny", style="destructive"),
            ),
            timeout_seconds=5,
        )
        task = asyncio.create_task(surface.prompt_choice(prompt))
        for _ in range(5):
            await asyncio.sleep(0)
        offered = message_actions.for_message(surface.thread.id, 4242)
        assert [(a["label"], a["style"]) for a in offered] == [
            ("Allow", "success"),
            ("Deny", "danger"),
        ]
        assert await message_actions.invoke(surface.thread.id, 4242, "1", "ebi")
        assert await task == ("deny",)
        sent.edit.assert_awaited()
        assert message_actions.for_message(surface.thread.id, 4242) == []

    async def test_multi_select_is_not_offered_to_the_console(self) -> None:
        from claude_discord.discord_ui.prompt_views import ChoiceView

        prompt = ChoicePrompt(
            question="Pick",
            choices=(Choice(value="a", label="A"), Choice(value="b", label="B")),
            multi_select=True,
        )
        assert ChoiceView(prompt).console_actions(MagicMock()) == []


class TestFileDelivery:
    async def test_paths_and_blobs_both_reach_discord(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        real = tmp_path / "a.txt"
        real.write_text("hi", encoding="utf-8")
        surface = _make_surface()
        await surface.deliver_files(
            [
                OutboundFile(display_name="a.txt", path=str(real)),
                OutboundFile(display_name="b.txt", blob=b"data"),
            ]
        )
        # One batch of on-disk files, one of in-memory blobs.
        assert surface.thread.send.await_count == 2

    async def test_empty_list_sends_nothing(self) -> None:
        surface = _make_surface()
        await surface.deliver_files([])
        surface.thread.send.assert_not_awaited()


class TestRename:
    async def test_renames_a_thread(self) -> None:
        surface = _make_surface()
        await surface.rename("a much better title")
        surface.thread.edit.assert_awaited_once()

    async def test_a_channel_is_not_renamed(self) -> None:
        """Inline-reply mode posts into a channel, which is not ours to
        rename. It must be ignored rather than attempted."""
        channel = MagicMock(spec=discord.TextChannel)
        channel.id = 42
        channel.edit = AsyncMock()
        surface = RecordingSurface(channel)
        await surface.rename("nope")
        channel.edit.assert_not_awaited()


async def _as_async(value: RecordingSurface) -> RecordingSurface:
    return value


@pytest.fixture(autouse=True)
def _no_real_sleeping(monkeypatch: pytest.MonkeyPatch) -> None:
    """StatusManager debounces with a real sleep; keep the suite fast."""
    import asyncio

    original = asyncio.sleep

    async def fast_sleep(delay: float, *args: Any, **kwargs: Any) -> Any:
        return await original(0, *args, **kwargs)

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
