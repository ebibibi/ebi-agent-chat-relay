"""A demo instance that can be reset to a scripted scenario.

Showing the Ebi AI Console in a screencast with real work on screen is a
leak waiting to happen. A demo instance is a second deployment — its own OS
user, data root, vault and Console port — whose state is rebuilt from a
versioned *scenario*:

* ``vault/`` — an Ebi Workspace written as Markdown. ``{{today}}`` and
  ``{{date:+N}}`` / ``{{date:-N}}`` become dates relative to the reset day, so
  "due tomorrow" stays due tomorrow however old the scenario is.
* ``scenario.toml`` — Console places and work items, seeded through the
  Console API so they pass the same validation as anything a person types.

Both destructive steps refuse to touch a folder without the marker file this
module wrote there, so a wrong path cannot wipe a real vault or real data.

    python -m claude_discord.demo reset --scenario DIR --vault DIR --data-root DIR
    python -m claude_discord.demo seed --scenario DIR --url http://127.0.0.1:8120

``seed`` reads the Console bearer token from ``CCDB_CONSOLE_TOKEN``.
See docs/demo.md.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

VAULT_MARKER = ".ccdb-demo-vault"
DATA_MARKER = ".ccdb-demo-data"
# Sign-in survives a reset: re-enrolling a passkey before every take is friction
# that buys nothing, since these rows hold no scenario content.
KEEP_TABLES = frozenset({"console_passkeys", "console_sessions"})
TEXT_SUFFIXES = frozenset({".md", ".txt", ".csv", ".json", ".yaml", ".yml", ".canvas"})
ITEM_FIELDS = ("title", "note", "priority", "project", "place", "state", "snoozed_until")

_PLACEHOLDER = re.compile(r"\{\{\s*(?:today|date:([+-]?\d+))\s*\}\}")
_DUE = re.compile(r"^([+-]?\d+)(?:@(\d{1,2}):(\d{2}))?$")


class DemoError(ValueError):
    """A scenario or a target folder that a reset must not proceed with."""


@dataclass(frozen=True)
class Scenario:
    root: Path
    name: str
    timezone: str = "UTC"
    places: list[str] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)

    @property
    def vault(self) -> Path:
        return self.root / "vault"


def render_text(text: str, today: date) -> str:
    """Replace the date placeholders; leave any other ``{{...}}`` alone."""

    def replace(match: re.Match[str]) -> str:
        offset = int(match.group(1) or 0)
        return (today + timedelta(days=offset)).isoformat()

    return _PLACEHOLDER.sub(replace, text)


def resolve_due(value: str, today: date, timezone: str) -> str:
    """``"+2"`` → a date; ``"+2@18:00"`` → that local time as ISO 8601."""
    match = _DUE.match(str(value).strip())
    if not match:
        raise DemoError(f"due {value!r}: use +N or +N@HH:MM")
    day = today + timedelta(days=int(match.group(1)))
    if match.group(2) is None:
        return day.isoformat()
    moment = datetime(
        day.year,
        day.month,
        day.day,
        int(match.group(2)),
        int(match.group(3)),
        tzinfo=ZoneInfo(timezone),
    )
    return moment.isoformat()


def load_scenario(root: Path) -> Scenario:
    try:
        raw = tomllib.loads((root / "scenario.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise DemoError(f"cannot read {root / 'scenario.toml'}: {exc}") from exc
    items = raw.get("items", [])
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not str(item.get("title") or "").strip():
            raise DemoError(f"item {index + 1} needs a title")
    return Scenario(
        root=root,
        name=str(raw.get("name") or root.name),
        timezone=str(raw.get("timezone") or "UTC"),
        places=[str(p) for p in raw.get("places", [])],
        items=items,
    )


def seed_requests(scenario: Scenario, today: date) -> list[tuple[str, dict[str, Any]]]:
    """The Console API calls that recreate the scenario's places and items."""
    requests: list[tuple[str, dict[str, Any]]] = [
        ("/console/api/places", {"name": name}) for name in scenario.places
    ]
    for item in scenario.items:
        body = {
            key: render_text(item[key], today) if isinstance(item[key], str) else item[key]
            for key in ITEM_FIELDS
            if key in item
        }
        if "due" in item:
            body["due_at"] = resolve_due(item["due"], today, scenario.timezone)
        requests.append(("/console/api/items", body))
    return requests


def _claim(target: Path, marker: str) -> None:
    """Make *target* a demo folder, or refuse when it already holds something else."""
    if target.exists() and not (target / marker).is_file() and any(target.iterdir()):
        raise DemoError(f"{target} is not a demo folder (no {marker}); refusing to touch it")
    target.mkdir(parents=True, exist_ok=True)
    (target / marker).write_text("Managed by claude_discord.demo; reset wipes it.\n")


def build_vault(scenario: Scenario, target: Path, today: date) -> None:
    """Replace *target* with the scenario vault, dates rendered for *today*."""
    _claim(target, VAULT_MARKER)
    for child in target.iterdir():
        if child.name == VAULT_MARKER:
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    for source in sorted(scenario.vault.rglob("*")):
        # File names carry placeholders too: a daily note is named after its day.
        destination = target / render_text(str(source.relative_to(scenario.vault)), today)
        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif source.suffix.lower() in TEXT_SUFFIXES:
            destination.parent.mkdir(parents=True, exist_ok=True)
            text = source.read_text(encoding="utf-8")
            destination.write_text(render_text(text, today), encoding="utf-8")
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)


def prepare_data_root(root: Path) -> None:
    """Mark an empty (or new) data root as demo data, so later resets may wipe it."""
    _claim(root, DATA_MARKER)


def wipe_data(root: Path) -> None:
    """Empty every table in the demo data root except sign-in. Run with the relay stopped."""
    if not (root / DATA_MARKER).is_file():
        raise DemoError(f"{root} is not a demo data root (no {DATA_MARKER}); refusing to wipe it")
    for db in sorted(root.glob("*.db")):
        con = sqlite3.connect(db)
        try:
            tables = [
                row[0]
                for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
            ]
            for table in tables:
                if table not in KEEP_TABLES:
                    con.execute(f'DELETE FROM "{table}"')
            con.commit()
            con.execute("VACUUM")
        finally:
            con.close()
    for child in root.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)


def _post(url: str, token: str, body: dict[str, Any]) -> None:
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            # The Console's CSRF guard: every state-changing call must carry it.
            "X-Console-Request": "1",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        response.read()


def wait_for_console(base_url: str, token: str, timeout: float = 120.0) -> None:
    deadline = time.monotonic() + timeout
    request = urllib.request.Request(
        f"{base_url}/console/api/me", headers={"Authorization": f"Bearer {token}"}
    )
    while True:
        try:
            with urllib.request.urlopen(request, timeout=5):
                return
        except (urllib.error.URLError, OSError):
            if time.monotonic() > deadline:
                raise DemoError(f"the Console at {base_url} did not answer") from None
            time.sleep(2)


def seed(scenario: Scenario, base_url: str, token: str, today: date) -> int:
    wait_for_console(base_url, token)
    requests = seed_requests(scenario, today)
    for path, body in requests:
        try:
            _post(base_url + path, token, body)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:200]
            # A place that survived from an earlier seed is not an error.
            if path.endswith("/places") and exc.code in (400, 409):
                continue
            raise DemoError(
                f"POST {path} {body.get('title') or body}: {exc.code} {detail}"
            ) from exc
    return len(requests)


def _today(scenario: Scenario) -> date:
    return datetime.now(ZoneInfo(scenario.timezone)).date()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m claude_discord.demo")
    sub = parser.add_subparsers(dest="command", required=True)
    reset = sub.add_parser("reset", help="rebuild the vault and empty the data (relay stopped)")
    reset.add_argument("--scenario", type=Path, required=True)
    reset.add_argument("--vault", type=Path, required=True)
    reset.add_argument("--data-root", type=Path, required=True)
    seed_cmd = sub.add_parser("seed", help="create the scenario's Console items (relay running)")
    seed_cmd.add_argument("--scenario", type=Path, required=True)
    seed_cmd.add_argument("--url", default="http://127.0.0.1:8120")
    args = parser.parse_args(argv)
    try:
        scenario = load_scenario(args.scenario)
        today = _today(scenario)
        if args.command == "reset":
            build_vault(scenario, args.vault, today)
            if not (args.data_root / DATA_MARKER).is_file():
                prepare_data_root(args.data_root)
            wipe_data(args.data_root)
            print(f"reset {scenario.name!r} for {today}: vault {args.vault}, data {args.data_root}")
        else:
            token = (os.environ.get("CCDB_CONSOLE_TOKEN") or "").strip()
            if not token:
                raise DemoError("CCDB_CONSOLE_TOKEN is not set")
            count = seed(scenario, args.url.rstrip("/"), token, today)
            print(f"seeded {count} records from {scenario.name!r}")
    except DemoError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
