"""Where one deployment keeps its state.

A deployment is ccdb's isolation boundary, and the boundary matters more here
than in most systems. The AI Lounge, the session ledger, claims and collision
detection all work *because* every session in a deployment can see the others —
that is the product. Which means it is also the thing that must never reach
across a customer boundary: one customer's session titles appearing in another
customer's prompt would be a leak produced by the feature working correctly.

The cheapest guarantee is structural: everything a deployment owns hangs off a
single root. Running a second customer on the same server becomes one setting
rather than thirty, and "are they actually isolated?" becomes a question with a
mechanical answer instead of a careful reading of the config.

Which deployment shape a customer gets — our infrastructure, a dedicated Azure
environment we manage, or entirely inside their own tenant — is a commercial
decision that changes *where* this root points and nothing else. That is the
whole reason to have the knob: the shape stops being an architecture question
and becomes a deployment question.

Individual paths can still be overridden, because an existing deployment has to
be able to migrate one file at a time. An override is the only remaining way to
break isolation, so :meth:`DataLayout.paths_outside_root` reports them and
``setup_bridge`` logs them at startup — visible rather than silent.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

__all__ = ["DEFAULT_DATA_ROOT", "DataLayout"]

#: Where ccdb has always kept its state, relative to the process's working
#: directory. Kept as the default so an upgrade never moves a live database.
DEFAULT_DATA_ROOT = "data"

_ENV_ROOT = "CCDB_DATA_ROOT"

# Per-path environment overrides that predate the root, kept working.
_ENV_OVERRIDES = {
    "worktrees_dir": "WORKTREE_BASE_DIR",
    "teams_vault_dir": "CCDB_TEAMS_VAULT_ROOT",
    "log_file": "CCDB_LOG_FILE",
}


@dataclass(frozen=True)
class DataLayout:
    """Every file and directory a single deployment owns.

    Build with :meth:`for_root` or :meth:`from_env` rather than directly, so
    the root is normalised and the defaults are applied consistently.
    """

    root: str
    sessions_db: str
    tasks_db: str
    notifications_db: str
    worktrees_dir: str
    teams_vault_dir: str
    log_file: str

    @classmethod
    def for_root(
        cls,
        root: str,
        *,
        sessions_db: str | None = None,
        tasks_db: str | None = None,
        notifications_db: str | None = None,
        worktrees_dir: str | None = None,
        teams_vault_dir: str | None = None,
        log_file: str | None = None,
    ) -> DataLayout:
        """Derive the layout from *root*, honouring any explicit override."""
        base = root.rstrip("/") or "/"

        def under(name: str) -> str:
            return f"{base}/{name}"

        return cls(
            root=base,
            sessions_db=sessions_db or under("sessions.db"),
            tasks_db=tasks_db or under("tasks.db"),
            notifications_db=notifications_db or under("notifications.db"),
            worktrees_dir=worktrees_dir or under("worktrees"),
            teams_vault_dir=teams_vault_dir or under("teams"),
            log_file=log_file or under("ccdb.log"),
        )

    @classmethod
    def from_env(cls) -> DataLayout:
        """Read ``CCDB_DATA_ROOT``, then the pre-existing per-path variables."""
        overrides = {field: os.getenv(env) or None for field, env in _ENV_OVERRIDES.items()}
        return cls.for_root(os.getenv(_ENV_ROOT) or DEFAULT_DATA_ROOT, **overrides)  # type: ignore[arg-type]

    def all_paths(self) -> dict[str, str]:
        """Every path this deployment owns, keyed by field name."""
        return {
            "sessions_db": self.sessions_db,
            "tasks_db": self.tasks_db,
            "notifications_db": self.notifications_db,
            "worktrees_dir": self.worktrees_dir,
            "teams_vault_dir": self.teams_vault_dir,
            "log_file": self.log_file,
        }

    def paths_outside_root(self) -> dict[str, str]:
        """Paths that escape the root — the only way isolation can still break.

        Overriding is legitimate (a running deployment migrates one file at a
        time) but it should never be accidental, so callers surface this.
        """
        base = Path(self.root).resolve()
        return {
            name: path
            for name, path in self.all_paths().items()
            if not Path(path).resolve().is_relative_to(base)
        }

    def ensure_db_dirs(self) -> None:
        """Create only the parent directories of the SQLite databases. Idempotent.

        SQLite creates a missing database file but not its directory, so these
        must exist before the first open. Optional paths (worktrees, the Teams
        vault, the log file) are left to the features that use them.
        """
        for path in (self.sessions_db, self.tasks_db, self.notifications_db):
            Path(path).parent.mkdir(parents=True, exist_ok=True)

    def ensure_dirs(self) -> None:
        """Create the directories the paths imply. Idempotent."""
        for path in self.all_paths().values():
            parent = Path(path).parent
            parent.mkdir(parents=True, exist_ok=True)
        Path(self.worktrees_dir).mkdir(parents=True, exist_ok=True)
