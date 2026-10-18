"""Tests for the resettable demo instance (claude_discord.demo)."""

from __future__ import annotations

import sqlite3
import urllib.request
from datetime import date
from pathlib import Path

import pytest

from claude_discord import demo

TODAY = date(2026, 10, 18)

SCENARIO_TOML = """
name = "test"
timezone = "Asia/Tokyo"
places = ["自宅", "事務所"]

[[items]]
title = "Rotate the secret"
note = "Expires {{date:+3}}"
priority = 1
due = "+3@18:00"
project = "ops"
place = "事務所"

[[items]]
title = "Someday"
"""


def _scenario(tmp_path: Path) -> Path:
    root = tmp_path / "scenario"
    (root / "vault" / "02_Contexts" / "work").mkdir(parents=True)
    (root / "vault" / "02_Contexts" / "work" / "single-actions.md").write_text(
        "- [ ] Renew 📅{{date:+2}}\n- [ ] Yesterday 📅{{date:-1}}\n最終更新: {{today}}\n",
        encoding="utf-8",
    )
    (root / "vault" / "logo.png").write_bytes(b"\x89PNG{{today}}")
    (root / "vault" / "01_Daily").mkdir()
    (root / "vault" / "01_Daily" / "{{date:-1}}.md").write_text("# {{date:-1}}\n", encoding="utf-8")
    (root / "scenario.toml").write_text(SCENARIO_TOML, encoding="utf-8")
    return root


class TestRenderText:
    def test_relative_dates(self) -> None:
        text = "a {{today}} b {{date:+2}} c {{date:-1}} d {{date:0}}"
        assert (
            demo.render_text(text, TODAY) == "a 2026-10-18 b 2026-10-20 c 2026-10-17 d 2026-10-18"
        )

    def test_unknown_placeholders_stay(self) -> None:
        assert demo.render_text("{{project_name}}", TODAY) == "{{project_name}}"


class TestLoadScenario:
    def test_items_and_places(self, tmp_path: Path) -> None:
        scenario = demo.load_scenario(_scenario(tmp_path))
        assert scenario.name == "test"
        assert scenario.places == ["自宅", "事務所"]
        assert [i["title"] for i in scenario.items] == ["Rotate the secret", "Someday"]

    def test_missing_title_is_rejected(self, tmp_path: Path) -> None:
        root = _scenario(tmp_path)
        (root / "scenario.toml").write_text("name='x'\n[[items]]\nnote='n'\n", encoding="utf-8")
        with pytest.raises(demo.DemoError, match="title"):
            demo.load_scenario(root)


class TestSeedRequests:
    def test_due_is_local_time_relative_to_today(self, tmp_path: Path) -> None:
        scenario = demo.load_scenario(_scenario(tmp_path))
        requests = demo.seed_requests(scenario, TODAY)
        assert requests[:2] == [
            ("/console/api/places", {"name": "自宅"}),
            ("/console/api/places", {"name": "事務所"}),
        ]
        path, body = requests[2]
        assert path == "/console/api/items"
        assert body == {
            "title": "Rotate the secret",
            "note": "Expires 2026-10-21",
            "priority": 1,
            "due_at": "2026-10-21T18:00:00+09:00",
            "project": "ops",
            "place": "事務所",
        }
        assert requests[3] == ("/console/api/items", {"title": "Someday"})

    def test_due_without_time_is_a_date(self) -> None:
        assert demo.resolve_due("+1", TODAY, "Asia/Tokyo") == "2026-10-19"

    def test_bad_due_is_rejected(self) -> None:
        with pytest.raises(demo.DemoError):
            demo.resolve_due("tomorrow", TODAY, "Asia/Tokyo")


class TestBuildVault:
    def test_renders_markdown_and_copies_other_files(self, tmp_path: Path) -> None:
        scenario = demo.load_scenario(_scenario(tmp_path))
        target = tmp_path / "vault"
        demo.build_vault(scenario, target, TODAY)
        text = (target / "02_Contexts" / "work" / "single-actions.md").read_text(encoding="utf-8")
        assert "📅2026-10-20" in text and "📅2026-10-17" in text and "2026-10-18" in text
        assert (target / "logo.png").read_bytes() == b"\x89PNG{{today}}"
        assert (target / demo.VAULT_MARKER).is_file()
        daily = target / "01_Daily" / "2026-10-17.md"
        assert daily.read_text(encoding="utf-8") == "# 2026-10-17\n"

    def test_rebuild_replaces_previous_demo_content(self, tmp_path: Path) -> None:
        scenario = demo.load_scenario(_scenario(tmp_path))
        target = tmp_path / "vault"
        demo.build_vault(scenario, target, TODAY)
        (target / "written-during-demo.md").write_text("x", encoding="utf-8")
        demo.build_vault(scenario, target, TODAY)
        assert not (target / "written-during-demo.md").exists()

    def test_refuses_a_folder_that_is_not_a_demo_vault(self, tmp_path: Path) -> None:
        scenario = demo.load_scenario(_scenario(tmp_path))
        real = tmp_path / "real-vault"
        real.mkdir()
        (real / "diary.md").write_text("private", encoding="utf-8")
        with pytest.raises(demo.DemoError, match="not a demo"):
            demo.build_vault(scenario, real, TODAY)
        assert (real / "diary.md").read_text(encoding="utf-8") == "private"


def _db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE console_passkeys (id TEXT)")
    con.execute("CREATE TABLE work_items (id TEXT)")
    con.execute("INSERT INTO console_passkeys VALUES ('key')")
    con.execute("INSERT INTO work_items VALUES ('item')")
    con.commit()
    con.close()


def _count(path: Path, table: str) -> int:
    con = sqlite3.connect(path)
    try:
        return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    finally:
        con.close()


class TestWipeData:
    def test_keeps_sign_in_and_empties_the_rest(self, tmp_path: Path) -> None:
        root = tmp_path / "data"
        demo.prepare_data_root(root)
        _db(root / "sessions.db")
        (root / "console_files" / "x").mkdir(parents=True)
        demo.wipe_data(root)
        assert _count(root / "sessions.db", "console_passkeys") == 1
        assert _count(root / "sessions.db", "work_items") == 0
        assert not (root / "console_files").exists()
        assert (root / demo.DATA_MARKER).is_file()

    def test_refuses_data_that_is_not_demo_data(self, tmp_path: Path) -> None:
        root = tmp_path / "data"
        root.mkdir()
        _db(root / "sessions.db")
        with pytest.raises(demo.DemoError, match="not a demo"):
            demo.wipe_data(root)
        assert _count(root / "sessions.db", "work_items") == 1

    def test_prepare_refuses_an_existing_non_demo_root(self, tmp_path: Path) -> None:
        root = tmp_path / "data"
        root.mkdir()
        _db(root / "sessions.db")
        with pytest.raises(demo.DemoError, match="not a demo"):
            demo.prepare_data_root(root)


class TestBundledScenarios:
    """Every scenario shipped in examples/demo must load and seed cleanly."""

    @pytest.mark.parametrize(
        "root",
        sorted((Path(__file__).parent.parent / "examples" / "demo").glob("*/scenario.toml")),
        ids=lambda p: p.parent.name,
    )
    def test_loads_and_renders(self, root: Path, tmp_path: Path) -> None:
        scenario = demo.load_scenario(root.parent)
        assert scenario.items
        requests = demo.seed_requests(scenario, TODAY)
        assert all("{{" not in str(body) for _, body in requests)
        demo.build_vault(scenario, tmp_path / "vault", TODAY)
        rendered = [p for p in (tmp_path / "vault").rglob("*") if "{{" in p.name]
        assert rendered == []


class TestPost:
    def test_sends_the_console_csrf_header_and_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[urllib.request.Request] = []

        class _Response:
            def __enter__(self) -> _Response:
                return self

            def __exit__(self, *exc: object) -> None:
                return None

            def read(self) -> bytes:
                return b"{}"

        def fake_urlopen(request: urllib.request.Request, timeout: float = 0) -> _Response:
            seen.append(request)
            return _Response()

        monkeypatch.setattr(demo.urllib.request, "urlopen", fake_urlopen)
        demo._post("http://127.0.0.1:1/console/api/items", "tok", {"title": "t"})
        headers = {k.lower(): v for k, v in seen[0].header_items()}
        assert headers["x-console-request"] == "1"
        assert headers["authorization"] == "Bearer tok"
