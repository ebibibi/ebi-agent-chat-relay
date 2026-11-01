"""One deployment owns one data root, and two deployments share nothing.

Why this matters more than it looks
-----------------------------------
ccdb's distinguishing feature is that concurrent sessions *see each other*:
the AI Lounge, the session ledger, claims and collision detection all work
because every session in a deployment reads the same tables. That is the
product within one boundary — and precisely what must never cross a customer
boundary.

So a deployment is an isolation boundary, and the cheapest way to guarantee it
is that all of a deployment's state hangs off a single root. Then "run a second
customer on the same server" is one setting, not thirty; and "did I actually
isolate them?" is a question with a mechanical answer rather than a careful
reading of the config.

These tests pin the mechanical answer.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import claude_discord.main as ccdb_main
from claude_discord.deployment import DEFAULT_DATA_ROOT, DataLayout
from claude_discord.ext.api_server import ApiServer


class TestEverythingHangsOffTheRoot:
    def test_every_path_is_inside_the_root(self) -> None:
        """A path that escapes the root is a path two deployments can collide
        on without either config looking wrong."""
        layout = DataLayout.for_root("/srv/ccdb/acme")
        root = Path("/srv/ccdb/acme").resolve()
        for name, path in layout.all_paths().items():
            assert Path(path).resolve().is_relative_to(root), (
                f"{name} ({path}) escapes the deployment root {root}"
            )

    def test_the_known_state_is_all_accounted_for(self) -> None:
        """If a new kind of state is added without a home in the layout, it
        will default to the process's cwd and silently be shared."""
        assert set(DataLayout.for_root("/tmp/x").all_paths()) == {
            "sessions_db",
            "tasks_db",
            "notifications_db",
            "worktrees_dir",
            "teams_vault_dir",
            "log_file",
        }


class TestTwoDeploymentsShareNothing:
    def test_no_path_is_common_to_two_roots(self) -> None:
        acme = DataLayout.for_root("/srv/ccdb/acme").all_paths()
        globex = DataLayout.for_root("/srv/ccdb/globex").all_paths()
        assert not (set(acme.values()) & set(globex.values()))

    def test_the_session_database_differs(self) -> None:
        """Ten repositories share this one file — the ledger, the Lounge,
        claims, pending asks. Sharing it across customers would put one
        customer's session list in another's prompt."""
        assert DataLayout.for_root("/a").sessions_db != DataLayout.for_root("/b").sessions_db

    def test_roots_are_normalised_before_comparison(self) -> None:
        """``/srv/ccdb/acme`` and ``/srv/ccdb/acme/`` are one deployment, not
        two — otherwise a trailing slash silently forks the state."""
        assert (
            DataLayout.for_root("/srv/x").all_paths() == DataLayout.for_root("/srv/x/").all_paths()
        )


class TestBackwardsCompatibility:
    def test_the_default_root_reproduces_the_historical_paths(self) -> None:
        """Existing deployments must not have their databases move underneath
        them when they upgrade."""
        layout = DataLayout.for_root(DEFAULT_DATA_ROOT)
        assert layout.sessions_db == "data/sessions.db"
        assert layout.tasks_db == "data/tasks.db"
        assert layout.notifications_db == "data/notifications.db"

    def test_an_explicit_override_still_wins(self) -> None:
        """Callers that already pass a path keep it; the root only supplies
        defaults."""
        layout = DataLayout.for_root("/srv/acme", sessions_db="/mnt/shared/legacy.db")
        assert layout.sessions_db == "/mnt/shared/legacy.db"
        assert layout.tasks_db.startswith("/srv/acme")

    def test_an_override_is_reported_as_outside_the_root(self) -> None:
        """Overriding is allowed — it is how an existing deployment migrates —
        but it must be visible, because it is the one way isolation can still
        be broken."""
        layout = DataLayout.for_root("/srv/acme", sessions_db="/mnt/shared/legacy.db")
        assert layout.paths_outside_root() == {"sessions_db": "/mnt/shared/legacy.db"}

    def test_nothing_is_outside_when_nothing_is_overridden(self) -> None:
        assert DataLayout.for_root("/srv/acme").paths_outside_root() == {}


class TestFromEnvironment:
    def test_reads_the_root_from_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CCDB_DATA_ROOT", "/srv/ccdb/acme")
        assert DataLayout.from_env().sessions_db == "/srv/ccdb/acme/sessions.db"

    def test_falls_back_to_the_historical_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CCDB_DATA_ROOT", raising=False)
        assert DataLayout.from_env().sessions_db == "data/sessions.db"

    def test_individual_overrides_still_come_from_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CCDB_DATA_ROOT", "/srv/acme")
        monkeypatch.setenv("WORKTREE_BASE_DIR", "/fast-disk/worktrees")
        layout = DataLayout.from_env()
        assert layout.worktrees_dir == "/fast-disk/worktrees"
        assert "worktrees_dir" in layout.paths_outside_root()


class TestCreation:
    def test_creates_the_directories_it_needs(self, tmp_path: Path) -> None:
        layout = DataLayout.for_root(str(tmp_path / "acme"))
        layout.ensure_dirs()
        assert (tmp_path / "acme").is_dir()
        assert Path(layout.worktrees_dir).is_dir()

    def test_creating_twice_is_harmless(self, tmp_path: Path) -> None:
        layout = DataLayout.for_root(str(tmp_path / "acme"))
        layout.ensure_dirs()
        layout.ensure_dirs()


class TestFreshInstall:
    """Issue #1126: ``ccdb start`` on a fresh install must not crash on a missing data root."""

    async def test_startup_creates_only_database_dirs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Drive real startup (console frontend, API enabled) with no data root on disk."""
        for env in (
            "WORKTREE_BASE_DIR",
            "CCDB_TEAMS_VAULT_ROOT",
            "CCDB_LOG_FILE",
            "DISCORD_BOT_TOKEN",
            "DISCORD_CHANNEL_ID",
        ):
            monkeypatch.delenv(env, raising=False)
        monkeypatch.chdir(tmp_path)
        root = tmp_path / "fresh" / "data"
        monkeypatch.setenv("CCDB_DATA_ROOT", str(root))
        monkeypatch.setenv("CCDB_FRONTENDS", "console")
        monkeypatch.setenv("API_PORT", "1")
        monkeypatch.setenv("CCDB_CONSOLE_PORT", "2")

        async def no_op(*_args: object, **_kwargs: object) -> None:
            return None

        monkeypatch.setattr(ApiServer, "start", no_op)
        monkeypatch.setattr(ccdb_main, "_run_headless", no_op)
        assert not root.exists()

        await ccdb_main.main()

        layout = DataLayout.from_env()
        assert Path(layout.notifications_db).is_file()
        assert Path(layout.sessions_db).is_file()
        assert not Path(layout.worktrees_dir).exists()
        assert not Path(layout.teams_vault_dir).exists()
