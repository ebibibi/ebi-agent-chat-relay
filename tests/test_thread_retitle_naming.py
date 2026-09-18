"""Tests for retag_thread_name — a re-title must not cost a thread its lineage.

The family tags (``🤖K2``, ``🌳P9``) are the only thing that tells the channel
list which agent started a thread and what it started.  Replacing the title is
exactly the moment they would silently disappear.
"""

from __future__ import annotations

import pytest

from claude_discord.thread_marker import (
    DEFAULT_PARENT_MARKER,
    DEFAULT_SPAWN_MARKER,
    MAX_THREAD_NAME_LENGTH,
    PARENT_MARKER_ENV_VAR,
    SPAWN_MARKER_ENV_VAR,
    family_code,
    retag_thread_name,
    split_marker_tags,
)


@pytest.fixture(autouse=True)
def _default_markers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(SPAWN_MARKER_ENV_VAR, raising=False)
    monkeypatch.delenv(PARENT_MARKER_ENV_VAR, raising=False)


class TestSplitMarkerTags:
    def test_plain_name_has_no_tags(self) -> None:
        assert split_marker_tags("Fix the build") == ("", "Fix the build")

    def test_spawn_tag_is_split_off(self) -> None:
        code = family_code(123)
        name = f"{DEFAULT_SPAWN_MARKER}{code} Fix the build"
        assert split_marker_tags(name) == (f"{DEFAULT_SPAWN_MARKER}{code}", "Fix the build")

    def test_bare_spawn_marker_is_split_off(self) -> None:
        name = f"{DEFAULT_SPAWN_MARKER} Fix the build"
        assert split_marker_tags(name) == (DEFAULT_SPAWN_MARKER, "Fix the build")

    def test_both_tags_are_split_off_in_order(self) -> None:
        child, parent = family_code(1), family_code(2)
        name = f"{DEFAULT_SPAWN_MARKER}{child} {DEFAULT_PARENT_MARKER}{parent} Fix the build"
        prefix, rest = split_marker_tags(name)
        assert prefix == f"{DEFAULT_SPAWN_MARKER}{child} {DEFAULT_PARENT_MARKER}{parent}"
        assert rest == "Fix the build"

    def test_parent_tag_alone_is_split_off(self) -> None:
        code = family_code(7)
        name = f"{DEFAULT_PARENT_MARKER}{code} Fix the build"
        assert split_marker_tags(name) == (f"{DEFAULT_PARENT_MARKER}{code}", "Fix the build")

    def test_an_unrelated_emoji_is_part_of_the_title(self) -> None:
        assert split_marker_tags("🔀 Fork of something") == ("", "🔀 Fork of something")


class TestRetagThreadName:
    def test_untagged_thread_takes_the_new_title_as_is(self) -> None:
        assert retag_thread_name("Fix the build", "Migrate the database") == (
            "Migrate the database"
        )

    def test_tags_survive_the_new_title(self) -> None:
        code = family_code(123)
        old = f"{DEFAULT_SPAWN_MARKER}{code} Fix the build"
        assert retag_thread_name(old, "Migrate the database") == (
            f"{DEFAULT_SPAWN_MARKER}{code} Migrate the database"
        )

    def test_result_fits_discord(self) -> None:
        code = family_code(123)
        old = f"{DEFAULT_SPAWN_MARKER}{code} Fix the build"
        assert len(retag_thread_name(old, "z" * 200)) == MAX_THREAD_NAME_LENGTH

    def test_a_title_that_repeats_the_tag_is_not_double_tagged(self) -> None:
        code = family_code(123)
        old = f"{DEFAULT_SPAWN_MARKER}{code} Fix the build"
        new = retag_thread_name(old, f"{DEFAULT_SPAWN_MARKER}{code} Migrate the database")
        assert new == f"{DEFAULT_SPAWN_MARKER}{code} Migrate the database"
