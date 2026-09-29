"""Tests for claude_code_core.claude_plugins — plugin skill discovery."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claude_code_core.claude_plugins import claude_config_dir, plugin_skill_dirs


def _write_index(claude_dir: Path, plugins: dict) -> None:
    index = claude_dir / "plugins" / "installed_plugins.json"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text(json.dumps({"version": 2, "plugins": plugins}))


def _plugin(claude_dir: Path, name: str, *, with_skills: bool = True) -> Path:
    root = claude_dir / "plugins" / "cache" / name / "1.0.0"
    (root / "skills" if with_skills else root).mkdir(parents=True)
    return root


class TestPluginSkillDirs:
    def test_missing_index_returns_empty(self, tmp_path: Path) -> None:
        assert plugin_skill_dirs(tmp_path) == []

    @pytest.mark.parametrize("content", ["{invalid", "[]", '{"plugins": []}'])
    def test_malformed_index_returns_empty(self, tmp_path: Path, content: str) -> None:
        index = tmp_path / "plugins" / "installed_plugins.json"
        index.parent.mkdir(parents=True)
        index.write_text(content)
        assert plugin_skill_dirs(tmp_path) == []

    def test_malformed_entries_are_skipped(self, tmp_path: Path) -> None:
        good = _plugin(tmp_path, "good")
        _write_index(
            tmp_path,
            {
                "bad-list": "nope",
                "bad-entry": ["nope", {"installPath": 42}, {"scope": "user"}],
                "good@m": [{"scope": "user", "installPath": str(good)}],
            },
        )
        assert plugin_skill_dirs(tmp_path) == [good / "skills"]

    def test_plugins_without_skills_are_skipped(self, tmp_path: Path) -> None:
        bare = _plugin(tmp_path, "bare", with_skills=False)
        _write_index(tmp_path, {"bare@m": [{"scope": "user", "installPath": str(bare)}]})
        assert plugin_skill_dirs(tmp_path) == []

    def test_scope_filter(self, tmp_path: Path) -> None:
        user = _plugin(tmp_path, "user")
        project = _plugin(tmp_path, "project")
        _write_index(
            tmp_path,
            {
                "user@m": [{"scope": "user", "installPath": str(user)}],
                "project@m": [{"scope": "project", "installPath": str(project)}],
            },
        )
        assert len(plugin_skill_dirs(tmp_path)) == 2
        assert plugin_skill_dirs(tmp_path, user_scope_only=True) == [user / "skills"]

    def test_enabled_filter(self, tmp_path: Path) -> None:
        on = _plugin(tmp_path, "on")
        off = _plugin(tmp_path, "off")
        unlisted = _plugin(tmp_path, "unlisted")
        _write_index(
            tmp_path,
            {
                key: [{"scope": "user", "installPath": str(path)}]
                for key, path in (("on@m", on), ("off@m", off), ("unlisted@m", unlisted))
            },
        )
        (tmp_path / "settings.json").write_text(
            json.dumps({"enabledPlugins": {"on@m": True, "off@m": False}})
        )
        assert len(plugin_skill_dirs(tmp_path)) == 3
        assert plugin_skill_dirs(tmp_path, enabled_only=True) == [
            on / "skills",
            unlisted / "skills",
        ]

    def test_duplicate_install_paths_are_collapsed(self, tmp_path: Path) -> None:
        root = _plugin(tmp_path, "dup")
        entry = {"scope": "user", "installPath": str(root)}
        _write_index(tmp_path, {"dup@m": [entry, entry]})
        assert plugin_skill_dirs(tmp_path) == [root / "skills"]


class TestClaudeConfigDir:
    def test_defaults_to_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
        assert claude_config_dir() == Path.home() / ".claude"

    def test_honours_claude_config_dir(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path))
        assert claude_config_dir() == tmp_path
        _write_index(tmp_path, {})
        assert plugin_skill_dirs() == []
