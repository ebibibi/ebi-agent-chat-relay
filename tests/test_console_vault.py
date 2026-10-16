"""Whole-vault note reads for the Console, minus the owner's exclusion list."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_discord.console.vault import Vault, VaultError, excluded, parse_patterns
from claude_discord.console.workspace import Workspace
from tests.test_console_conversations import AUTH, WRITE
from tests.test_console_conversations import env as conversation_env

env = conversation_env

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16


@pytest.fixture
def vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    (root / "02_Contexts" / "Work").mkdir(parents=True)
    (root / "02_Contexts" / "Work" / "AGENTS.md").write_text("# Work\n", encoding="utf-8")
    (root / "04_Knowledge").mkdir()
    (root / "04_Knowledge" / "Idea.md").write_text("# Idea\nSee [[Other]]\n", encoding="utf-8")
    (root / "secret").mkdir()
    (root / "secret" / "Diary.md").write_text("private\n", encoding="utf-8")
    (root / "notes.private.md").write_text("private\n", encoding="utf-8")
    (root / ".obsidian").mkdir()
    (root / ".obsidian" / "app.md").write_text("x\n", encoding="utf-8")
    (root / "99_Attachments").mkdir()
    (root / "99_Attachments" / "pic.png").write_bytes(PNG)
    (root / "99_Attachments" / "draw.svg").write_text("<svg/>", encoding="utf-8")
    return root


@pytest.mark.parametrize(
    ("path", "patterns", "hidden"),
    [
        ("a/b/c.md", [], False),
        (".obsidian/x.md", [], True),
        ("a/.git/x.md", [], True),
        ("x/secret/y.md", ["secret/"], True),
        ("secret.md", ["secret/"], False),
        ("02_Contexts/Fam/x.md", ["02_Contexts/Fam/"], True),
        ("01/02_Contexts/Fam/x.md", ["02_Contexts/Fam/"], False),
        ("a/b.private.md", ["*.private.md"], True),
        ("01_Daily/2025-01-01.md", ["01_Daily/2025-*"], True),
        ("01_Daily/2026-01-01.md", ["01_Daily/2025-*"], False),
    ],
)
def test_exclusion_patterns(path, patterns, hidden):
    assert excluded(path, patterns) is hidden


def test_patterns_skip_comments_blank_lines_and_leading_slashes():
    assert parse_patterns("# c\n\n /secret/ \n*.x\n") == ["secret/", "*.x"]


def test_notes_and_attachments_read_unless_excluded_or_unsafe(vault, tmp_path):
    v = Vault(vault, tmp_path / "exclude.txt")
    note = v.note("04_Knowledge/Idea.md")
    assert note["text"].startswith("# Idea") and len(note["revision"]) == 64
    v.save_excludes("secret/\n*.private.md")
    for bad in (
        "secret/Diary.md",
        "notes.private.md",
        ".obsidian/app.md",
        "../outside.md",
        "/etc/passwd.md",
        "04_Knowledge/../secret/Diary.md",
        "04_Knowledge/Missing.md",
        "99_Attachments/pic.png",
        "a\\b.md",
        "",
        None,
    ):
        with pytest.raises(VaultError):
            v.note(bad)
    path, content_type = v.file("99_Attachments/pic.png")
    assert content_type == "image/png" and path.read_bytes() == PNG
    with pytest.raises(VaultError):
        v.file("99_Attachments/draw.svg")


def test_symlinks_never_lead_out(vault, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.md").write_text("secret\n", encoding="utf-8")
    (vault / "link").symlink_to(outside)
    (vault / "04_Knowledge" / "alias.md").symlink_to(vault / "04_Knowledge" / "Idea.md")
    v = Vault(vault, tmp_path / "exclude.txt")
    for bad in ("link/x.md", "04_Knowledge/alias.md"):
        with pytest.raises(VaultError):
            v.note(bad)


def test_exclusion_list_defaults_saves_and_reloads(vault, tmp_path):
    target = tmp_path / "cfg" / "exclude.txt"
    v = Vault(vault, target)
    assert v.patterns() == [] and v.exclude_text().startswith("# Ebi AI Console")
    assert v.save_excludes("secret/") == ["secret/"]
    assert target.read_text(encoding="utf-8") == "secret/\n"
    target.write_text("*.private.md\n", encoding="utf-8")
    import os

    os.utime(target, (1, 1))
    assert v.patterns() == ["*.private.md"]
    with pytest.raises(VaultError):
        v.save_excludes("x" * 30_000)


async def test_routes_are_authenticated_and_hide_excluded_notes(env, vault, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    (vault / "02_Contexts" / "Work" / "status.md").unlink(missing_ok=True)
    env.console.workspace = Workspace(vault)
    url = "/console/api/workspace/note"
    assert (await env.client.get(url, params={"path": "04_Knowledge/Idea.md"})).status == 401
    ok = await env.client.get(url, headers=AUTH, params={"path": "04_Knowledge/Idea.md"})
    assert (await ok.json())["path"] == "04_Knowledge/Idea.md"
    assert (
        await env.client.put("/console/api/workspace/exclude", headers=AUTH, json={"text": "x"})
    ).status == 403
    saved = await env.client.put(
        "/console/api/workspace/exclude", headers=WRITE, json={"text": "04_Knowledge/\n"}
    )
    assert (await saved.json())["patterns"] == ["04_Knowledge/"]
    hidden = await env.client.get(url, headers=AUTH, params={"path": "04_Knowledge/Idea.md"})
    assert hidden.status == 404
    image = await env.client.get(
        "/console/api/workspace/file", headers=AUTH, params={"path": "99_Attachments/pic.png"}
    )
    assert image.status == 200 and image.headers["Content-Type"] == "image/png"
    assert "sandbox" in image.headers["Content-Security-Policy"]


# -- links -------------------------------------------------------------------------------


def _write(root: Path, rel: str, text: str = "x\n") -> None:
    (root / rel).parent.mkdir(parents=True, exist_ok=True)
    (root / rel).write_text(text, encoding="utf-8")


def test_wikilinks_ignore_code_aliases_headings_and_duplicates():
    from claude_discord.console.vault import wikilinks

    text = "[[A|alias]] [[B#Heading]] ![[pic.png]] [[A]]\n```\n[[Code]]\n```\n[[C^block]]"
    assert wikilinks(text) == ["A", "B", "pic.png", "C"]


def test_links_resolve_like_obsidian(vault, tmp_path):
    from claude_discord.console.vault import NoteIndex

    _write(vault, "A/Same.md")
    _write(vault, "B/Same.md")
    _write(vault, "B/deeper/Same.md")
    _write(vault, "A/sub/Child.md")
    _write(vault, "secret/Hidden.md")
    v = Vault(vault, tmp_path / "exclude.txt")
    v.save_excludes("secret/\n")
    index = NoteIndex(v)
    assert index.resolve("Idea")["path"] == "04_Knowledge/Idea.md"
    assert index.resolve("04_Knowledge/Idea.md")["path"] == "04_Knowledge/Idea.md"
    assert index.resolve("sub/Child", "A/x.md")["path"] == "A/sub/Child.md"
    assert index.resolve("../A/sub/Child", "B/x.md")["path"] == "A/sub/Child.md"
    assert index.resolve("Same", "B/x.md") == {"path": "B/Same.md", "candidates": []}
    far = index.resolve("Same", "C/x.md")
    assert far["path"] == "A/Same.md" and len(far["candidates"]) == 3
    assert index.resolve("Same", "B/deeper/x.md")["path"] == "B/deeper/Same.md"
    assert index.resolve("pic.png")["path"] == "99_Attachments/pic.png"
    assert index.resolve("Hidden")["path"] is None
    assert index.resolve("Diary")["path"] is None
    assert index.resolve("Nothing")["path"] is None
    assert index.search_names("idea") == ["04_Knowledge/Idea.md"]
    assert "secret/Hidden.md" not in index.search_names("")


def test_backlinks_follow_resolution_and_hide_excluded_sources(vault, tmp_path):
    from claude_discord.console.vault import NoteIndex

    _write(vault, "04_Knowledge/Other.md", "[[Idea]] and [[Idea#Part|again]]\n")
    _write(vault, "secret/Leak.md", "[[Idea]]\n")
    _write(vault, "X/Idea.md", "a different Idea\n")
    _write(vault, "X/Near.md", "[[Idea]]\n")
    v = Vault(vault, tmp_path / "exclude.txt")
    v.save_excludes("secret/\n")
    index = NoteIndex(v)
    assert index.backlinks("04_Knowledge/Idea.md") == ["04_Knowledge/Other.md"]
    assert index.backlinks("X/Idea.md") == ["X/Near.md"]


async def test_note_route_resolves_links_and_backlinks_route(env, vault, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    _write(vault, "04_Knowledge/Other.md", "Back to [[Idea]]\n")
    env.console.workspace = Workspace(vault)
    note = await (
        await env.client.get(
            "/console/api/workspace/note", headers=AUTH, params={"path": "04_Knowledge/Idea.md"}
        )
    ).json()
    assert note["links"] == {"Other": "04_Knowledge/Other.md"}
    back = await env.client.get(
        "/console/api/workspace/backlinks", headers=AUTH, params={"path": "04_Knowledge/Idea.md"}
    )
    assert (await back.json())["backlinks"] == ["04_Knowledge/Other.md"]
    names = await env.client.get("/console/api/workspace/notes", headers=AUTH, params={"q": "oth"})
    assert (await names.json())["notes"] == ["04_Knowledge/Other.md"]
    missing = await env.client.get(
        "/console/api/workspace/backlinks", headers=AUTH, params={"path": "04_Knowledge/No.md"}
    )
    assert missing.status == 404


def test_a_new_exclusion_applies_to_links_at_once(vault, tmp_path):
    from claude_discord.console.vault import NoteIndex

    _write(vault, "04_Knowledge/Other.md", "[[Idea]]\n")
    v = Vault(vault, tmp_path / "exclude.txt")
    index = NoteIndex(v)
    assert index.resolve("Other")["path"] == "04_Knowledge/Other.md"
    assert index.backlinks("04_Knowledge/Idea.md") == ["04_Knowledge/Other.md"]
    v.save_excludes("04_Knowledge/Other.md\n")
    assert index.resolve("Other")["path"] is None
    assert index.backlinks("04_Knowledge/Idea.md") == []
    assert "04_Knowledge/Other.md" not in index.search_names("other")


def test_full_text_search_needs_every_term_and_skips_excluded(vault, tmp_path):
    from claude_discord.console.vault import NoteIndex

    _write(vault, "A/Plan.md", "# Plan\nfirst line\nThe Console viewer ships\n")
    _write(vault, "B/console.md", "nothing here\n")
    _write(vault, "secret/Console.md", "The Console viewer\n")
    v = Vault(vault, tmp_path / "exclude.txt")
    v.save_excludes("secret/\n")
    index = NoteIndex(v)
    hits = index.search_text("console viewer")
    assert [h["path"] for h in hits] == ["A/Plan.md"]
    assert hits[0]["line"] == 3 and hits[0]["snippet"] == "The Console viewer ships"
    assert [h["path"] for h in index.search_text("console")] == ["B/console.md", "A/Plan.md"]
    assert index.search_text("") == [] and index.search_text("x" * 300) == []


async def test_resolve_and_search_routes(env, vault, tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    env.console.workspace = Workspace(vault)
    found = await env.client.get(
        "/console/api/workspace/resolve", headers=AUTH, params={"link": "Idea#Part|x"}
    )
    assert (await found.json())["path"] == "04_Knowledge/Idea.md"
    missing = await env.client.get(
        "/console/api/workspace/resolve", headers=AUTH, params={"link": "Nope"}
    )
    assert missing.status == 404
    hits = await env.client.get(
        "/console/api/workspace/search", headers=AUTH, params={"q": "other"}
    )
    assert [h["path"] for h in (await hits.json())["hits"]] == ["04_Knowledge/Idea.md"]
    assert (await env.client.get("/console/api/workspace/search", params={"q": "x"})).status == 401
