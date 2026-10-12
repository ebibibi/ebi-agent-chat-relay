"""Rendering driven by capabilities rather than by Discord's numbers.

The point of moving the chunker and table renderer into core is not tidiness —
it is that the *same* text should come out differently on a surface with a
2,000-character limit and a 55-column code block than on one that takes 100 KB
and is wider. These tests pin that: identical input, different capabilities,
correspondingly different output.

They also pin the two conservative defaults, because both are the difference
between "plain" and "visibly broken" for a user who never sees the config.
"""

from __future__ import annotations

import pytest

from claude_code_core.frontend import SurfaceCapabilities
from claude_code_core.rendering import render_for
from claude_code_core.rendering.tables import parse_gfm_table, render_table

DISCORDISH = SurfaceCapabilities(
    max_message_chars=2000,
    monospace_width=55,
    monospace_cjk_is_double_width=False,
)
TEAMSISH = SurfaceCapabilities(
    max_message_chars=80_000,
    monospace_width=100,
    monospace_cjk_is_double_width=False,
)

# Long enough to force several Discord messages, trivially one Teams message.
LONG_TEXT = "\n\n".join(f"Paragraph {i}. " + "word " * 60 for i in range(40))

ASCII_TABLE = "| Name | Role |\n|---|---|\n| Ada | Engineer |\n| Bob | Designer |"
CJK_TABLE = "| 名前 | 役割 |\n|---|---|\n| 胡田 | エンジニア |\n| 佐藤 | デザイナー |"


class TestMessageLimitComesFromCapabilities:
    def test_narrow_surface_splits(self) -> None:
        chunks = render_for(LONG_TEXT, DISCORDISH)
        assert len(chunks) > 1
        assert all(len(c) <= DISCORDISH.max_message_chars for c in chunks)

    def test_roomy_surface_sends_one_message(self) -> None:
        """The same answer that Discord chops into a stream arrives whole.

        This is the concrete payoff of reading the limit from the surface: no
        frontend inherits Discord's 2,000-character experience by default.
        """
        assert len(render_for(LONG_TEXT, TEAMSISH)) == 1

    def test_short_text_is_never_split(self) -> None:
        assert render_for("hello", DISCORDISH) == ["hello"]

    def test_empty_text_produces_nothing(self) -> None:
        assert render_for("", DISCORDISH) == []

    def test_every_chunk_fits_even_at_an_absurd_limit(self) -> None:
        tiny = SurfaceCapabilities(max_message_chars=80)
        for chunk in render_for(LONG_TEXT, tiny):
            assert len(chunk) <= tiny.max_message_chars


class TestTableWidthComesFromCapabilities:
    def test_wider_surface_renders_a_wider_table(self) -> None:
        narrow = SurfaceCapabilities(max_message_chars=100_000, monospace_width=40)
        wide = SurfaceCapabilities(max_message_chars=100_000, monospace_width=100)
        wide_text = (
            "| Column with a fairly long header | Another quite long header |\n"
            "|---|---|\n"
            "| a reasonably long cell value here | and another long cell value |"
        )
        narrow_out = render_for(wide_text, narrow)[0]
        wide_out = render_for(wide_text, wide)[0]

        def longest(s: str) -> int:
            return max(len(line) for line in s.splitlines())

        assert longest(narrow_out) <= 40
        assert longest(wide_out) > longest(narrow_out)

    def test_table_is_converted_to_monospace_on_every_surface(self) -> None:
        """Neither Discord nor Teams renders GFM pipe tables, so the raw pipe
        syntax must never survive to the wire."""
        for caps in (DISCORDISH, TEAMSISH):
            out = render_for(ASCII_TABLE, caps)[0]
            assert "```" in out
            assert "|" in out and "+--" in out  # rendered, not raw pipe syntax
            assert "|---|" not in out


class TestCjkDefaultIsSafe:
    def test_cjk_table_falls_back_to_vertical_by_default(self) -> None:
        """A surface that has not declared its font gets the layout that
        cannot look broken, rather than columns that drift apart."""
        rendered = render_table(parse_gfm_table(CJK_TABLE.splitlines()), 55)
        assert rendered is not None
        assert "+--" not in rendered  # vertical key:value layout, not a box
        assert "名前: 胡田" in rendered

    def test_declaring_double_width_allows_the_box_layout(self) -> None:
        rendered = render_table(
            parse_gfm_table(CJK_TABLE.splitlines()), 55, cjk_is_double_width=True
        )
        assert rendered is not None
        assert "+--" in rendered

    def test_ascii_table_uses_the_box_layout_regardless(self) -> None:
        rendered = render_table(parse_gfm_table(ASCII_TABLE.splitlines()), 55)
        assert rendered is not None
        assert "+--" in rendered


class TestCapabilityValidation:
    def test_monospace_width_must_be_positive(self) -> None:
        with pytest.raises(ValueError):
            SurfaceCapabilities(max_message_chars=2000, monospace_width=0)

    def test_monospace_defaults_are_the_narrow_and_cautious_ones(self) -> None:
        caps = SurfaceCapabilities(max_message_chars=2000)
        assert caps.monospace_width == 55
        assert caps.monospace_cjk_is_double_width is False


class TestDiscordShimIsUnchanged:
    """The move must be invisible to anything that only talks to Discord."""

    def test_shim_matches_rendering_through_discord_capabilities(self) -> None:
        from claude_discord.discord_ui.chunker import DISCORD_CAPABILITIES, chunk_message

        assert chunk_message(LONG_TEXT) == render_for(LONG_TEXT, DISCORD_CAPABILITIES)

    def test_public_api_still_exports_chunk_message(self) -> None:
        from claude_discord import chunk_message as exported

        assert exported("hi") == ["hi"]

    def test_table_renderer_module_still_importable(self) -> None:
        from claude_discord.discord_ui.table_renderer import render_table as shim

        assert shim is render_table
