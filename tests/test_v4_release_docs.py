import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_v4_public_story_names_both_frontends_and_all_backends() -> None:
    readme = read("README.md")

    assert "Microsoft Teams (in progress)" not in readme
    assert "Frontend × backend" in readme
    for name in ("Discord", "Microsoft Teams", "Claude Code", "OpenAI Codex", "Local", "AG-UI"):
        assert name in readme
    assert "docs/teams-setup.md" in readme
    assert "docs/backends.md" in readme


def test_teams_setup_guide_covers_the_complete_relay_path() -> None:
    guide = read("docs/teams-setup.md")

    for concept in (
        "Entra",
        "Azure Bot",
        "Azure Storage Queue",
        "ActivityPuller",
        "CCDB_FRONTENDS=discord,teams",
        "CCDB_TEAMS_APP_ID",
        "CCDB_TEAMS_TENANT_ID",
        "CCDB_TEAMS_APP_PASSWORD",
        "CCDB_TEAMS_PUBLIC_HOST",
        "CCDB_TEAMS_QUEUE_URL",
        "python -m claude_teams manifest",
        "python -m claude_teams relay",
        "does not yet dispatch those text commands",
        "Troubleshooting",
    ):
        assert concept in guide


def test_v4_release_artifacts_and_versions_are_in_sync() -> None:
    project = tomllib.loads(read("pyproject.toml"))["project"]
    current_version = project["version"]
    lock = read("uv.lock")
    changelog = read("CHANGELOG.md")
    release_notes = read("docs/releases/v4.0.0.md")

    # v4 set the multi-frontend boundary; later majors keep its artifacts.
    assert int(current_version.split(".")[0]) >= 4
    assert f'name = "claude-code-discord-bridge"\nversion = "{current_version}"' in lock
    assert "## [4.0.0] - 2026-08-11" in changelog
    assert "# Ebi Agent Chat Relay 4.0.0" in release_notes
    assert "Compatibility" in release_notes


def test_v4_adr_supersedes_the_previous_release_classification() -> None:
    old = read("docs/adr/0005-classify-feature-backends-as-minor-releases.md")
    new = read("docs/adr/0006-publish-the-multi-frontend-platform-as-v4.md")
    index = read("docs/adr/_index.md")

    assert "status: superseded" in old
    assert "superseded_by: ADR-0006" in old
    assert "supersedes: ADR-0005" in new
    assert "ADR-0006" in index


def test_v5_release_notes_and_adr_describe_the_gui_boundary() -> None:
    changelog = read("CHANGELOG.md")
    notes = read("docs/releases/v5.0.0.md")
    adr = read("docs/adr/0014-publish-the-ebi-ai-console-as-v5.md")

    assert "## [5.0.0] - 2026-10-13" in changelog
    assert "# Ebi Agent Chat Relay 5.0.0" in notes
    for text in ("Ebi AI Console", "Compatibility", "CCDB_CONSOLE_PORT", "CCDB_CONSOLE_TRIAGE=0"):
        assert text in notes
    assert "id: ADR-0014" in adr
    assert "ADR-0014" in read("docs/adr/_index.md")
