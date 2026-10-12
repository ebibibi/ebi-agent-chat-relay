"""Tests for the in-memory registry of pressable message actions."""

from __future__ import annotations

from claude_discord.message_actions import MessageAction, MessageActions


def _recorder() -> tuple[list[str], MessageAction]:
    pressed: list[str] = []

    async def cb(who: str) -> None:
        pressed.append(who)

    return pressed, MessageAction("Yes", cb, "success")


async def test_registered_actions_are_listed_for_their_channel() -> None:
    reg = MessageActions()
    _, action = _recorder()
    reg.register(1, 10, [action])
    assert reg.for_message(1, 10) == [{"id": "0", "label": "Yes", "style": "success"}]
    assert reg.for_message(2, 10) == []
    assert reg.for_message(1, 11) == []


async def test_invoke_runs_callback_once() -> None:
    reg = MessageActions()
    pressed, action = _recorder()
    reg.register(1, 10, [action])
    assert await reg.invoke(1, 10, "0", "alice")
    assert pressed == ["alice"]
    # Withdrawn on press: a second click is refused.
    assert not await reg.invoke(1, 10, "0", "bob")
    assert pressed == ["alice"]


async def test_invoke_refuses_wrong_channel_and_bad_ids() -> None:
    reg = MessageActions()
    pressed, action = _recorder()
    reg.register(1, 10, [action])
    assert not await reg.invoke(2, 10, "0", "x")
    assert not await reg.invoke(1, 10, "1", "x")
    assert not await reg.invoke(1, 10, "-1", "x")
    assert not await reg.invoke(1, 10, "abc", "x")
    assert pressed == []
    assert reg.for_message(1, 10)


async def test_clear_and_unknown_style() -> None:
    reg = MessageActions()
    _, action = _recorder()
    reg.register(1, 10, [MessageAction("No", action.callback, "weird")])
    assert reg.for_message(1, 10)[0]["style"] == "secondary"
    reg.clear(10, 99)
    assert reg.for_message(1, 10) == []


async def test_empty_registration_is_ignored() -> None:
    reg = MessageActions()
    reg.register(1, 10, [])
    assert reg.for_message(1, 10) == []
