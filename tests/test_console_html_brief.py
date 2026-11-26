"""The 📄 button's text: built in per language, or the deployment's own file."""

from __future__ import annotations

from pathlib import Path

from claude_discord.console.html_brief import TEMPLATE_ENV, TEMPLATES, html_brief_template


def test_built_in_text_per_language() -> None:
    assert html_brief_template("ja", env={}) == TEMPLATES["ja"]
    assert html_brief_template("en", env={}) == TEMPLATES["en"]
    assert html_brief_template("fr", env={}) == TEMPLATES["en"]
    for text in TEMPLATES.values():
        assert "{{project}}" in text


def test_a_deployment_file_replaces_it_and_a_bad_one_falls_back(tmp_path: Path) -> None:
    own = tmp_path / "brief.md"
    own.write_text("my own brief {{project}}\n\n", encoding="utf-8")
    assert html_brief_template("en", env={TEMPLATE_ENV: str(own)}) == "my own brief {{project}}\n"
    (tmp_path / "empty.md").write_text("  \n", encoding="utf-8")
    for bad in ("absent.md", "empty.md"):
        assert html_brief_template("ja", env={TEMPLATE_ENV: str(tmp_path / bad)}) == TEMPLATES["ja"]
