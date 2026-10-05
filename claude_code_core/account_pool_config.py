"""Load and validate the account-pool TOML file.

The file is named by ``CCDB_ACCOUNT_POOLS_FILE``. Without it the relay runs
exactly as before: one implicit profile per backend, using whatever
``CLAUDE_CONFIG_DIR`` / ``CODEX_HOME`` the process already has.

Validation is strict on purpose. A typo in a strategy name or a profile path
that does not exist is reported at startup with the offending key, instead of
silently routing every turn to the wrong login.
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .account_pool import ASSIGN_MODES, STRATEGIES, PoolConfig, ProfileSpec

ENV_VAR = "CCDB_ACCOUNT_POOLS_FILE"

#: Key that holds a profile's directory, per backend.
HOME_KEY: Mapping[str, str] = {"claude": "config_dir", "codex": "codex_home"}

_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,31}$")
_POOL_KEYS = frozenset(
    {
        "strategy",
        "switch_at",
        "windows",
        "assign",
        "retry_on_exhaustion",
        "cooldown_seconds",
        "profiles",
    }
)


class AccountPoolConfigError(ValueError):
    """The pool file is missing, unreadable, or invalid."""


def load_from_env(env: Mapping[str, str] | None = None) -> dict[str, PoolConfig]:
    """Load pools from ``$CCDB_ACCOUNT_POOLS_FILE``; ``{}`` when it is unset."""
    source = os.environ if env is None else env
    path = (source.get(ENV_VAR) or "").strip()
    if not path:
        return {}
    return load_pools(path)


def load_pools(path: str | os.PathLike[str]) -> dict[str, PoolConfig]:
    """Read and validate a pool file. Raises :class:`AccountPoolConfigError`."""
    file = Path(path).expanduser()
    try:
        raw = file.read_bytes()
    except OSError as exc:
        raise AccountPoolConfigError(f"{ENV_VAR}: cannot read {file}: {exc}") from exc
    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise AccountPoolConfigError(f"{file}: invalid TOML: {exc}") from exc
    return parse_pools(data, source=str(file))


def parse_pools(data: Mapping[str, Any], *, source: str = "<pools>") -> dict[str, PoolConfig]:
    """Validate an already-parsed TOML document."""
    unknown_top = set(data) - {"pools"}
    if unknown_top:
        raise AccountPoolConfigError(f"{source}: unknown top-level keys: {sorted(unknown_top)}")
    pools_raw = data.get("pools")
    if not isinstance(pools_raw, dict) or not pools_raw:
        raise AccountPoolConfigError(f"{source}: expected at least one [pools.<backend>] table")

    pools: dict[str, PoolConfig] = {}
    seen_names: dict[str, str] = {}
    for backend, table in pools_raw.items():
        where = f"{source}: [pools.{backend}]"
        if backend not in HOME_KEY:
            raise AccountPoolConfigError(
                f"{where}: unsupported backend (expected one of {sorted(HOME_KEY)})"
            )
        if not isinstance(table, dict):
            raise AccountPoolConfigError(f"{where}: must be a table")
        pool = _parse_pool(backend, table, where)
        for profile in pool.profiles:
            # Usage rows are keyed by profile name alone, so a name may appear
            # in only one pool.
            if profile.name in seen_names:
                raise AccountPoolConfigError(
                    f"{where}: profile name {profile.name!r} is already used in "
                    f"[pools.{seen_names[profile.name]}]"
                )
            seen_names[profile.name] = backend
        pools[backend] = pool
    return pools


def _parse_pool(backend: str, table: Mapping[str, Any], where: str) -> PoolConfig:
    unknown = set(table) - _POOL_KEYS
    if unknown:
        raise AccountPoolConfigError(f"{where}: unknown keys: {sorted(unknown)}")

    strategy = table.get("strategy", "priority")
    if strategy not in STRATEGIES:
        raise AccountPoolConfigError(
            f"{where}: strategy must be one of {list(STRATEGIES)}, got {strategy!r}"
        )

    switch_at = table.get("switch_at", 0.95)
    if isinstance(switch_at, bool) or not isinstance(switch_at, int | float):
        raise AccountPoolConfigError(f"{where}: switch_at must be a number")
    if not 0.0 < float(switch_at) <= 1.0:
        raise AccountPoolConfigError(f"{where}: switch_at must be in (0, 1], got {switch_at}")

    windows = table.get("windows", ["five_hour", "seven_day"])
    if (
        not isinstance(windows, list)
        or not windows
        or not all(isinstance(w, str) and w.strip() for w in windows)
    ):
        raise AccountPoolConfigError(f"{where}: windows must be a non-empty list of strings")

    assign = table.get("assign", "session")
    if assign not in ASSIGN_MODES:
        raise AccountPoolConfigError(
            f"{where}: assign must be one of {list(ASSIGN_MODES)}, got {assign!r}"
        )

    retry = table.get("retry_on_exhaustion", False)
    if not isinstance(retry, bool):
        raise AccountPoolConfigError(f"{where}: retry_on_exhaustion must be true or false")

    cooldown = table.get("cooldown_seconds", 3600)
    if isinstance(cooldown, bool) or not isinstance(cooldown, int) or cooldown <= 0:
        raise AccountPoolConfigError(f"{where}: cooldown_seconds must be a positive integer")

    profiles = _parse_profiles(backend, table.get("profiles"), where)
    return PoolConfig(
        backend=backend,
        profiles=profiles,
        strategy=strategy,
        switch_at=float(switch_at),
        windows=tuple(w.strip() for w in windows),
        assign=assign,
        retry_on_exhaustion=retry,
        cooldown_seconds=cooldown,
    )


def _parse_profiles(backend: str, raw: object, where: str) -> tuple[ProfileSpec, ...]:
    if not isinstance(raw, list) or not raw:
        raise AccountPoolConfigError(f"{where}: needs at least one [[pools.{backend}.profiles]]")
    home_key = HOME_KEY[backend]
    profiles: list[ProfileSpec] = []
    homes: set[str] = set()
    inherit_count = 0
    for index, entry in enumerate(raw):
        at = f"{where} profile #{index + 1}"
        if not isinstance(entry, dict):
            raise AccountPoolConfigError(f"{at}: must be a table")
        unknown = set(entry) - {"name", home_key}
        if unknown:
            raise AccountPoolConfigError(
                f"{at}: unknown keys {sorted(unknown)} (a {backend} profile takes "
                f"'name' and '{home_key}')"
            )
        name = entry.get("name")
        if not isinstance(name, str) or not _NAME.fullmatch(name):
            raise AccountPoolConfigError(
                f"{at}: name must be 1-32 characters of letters, digits, '_', '.', '-'"
            )
        if any(p.name == name for p in profiles):
            raise AccountPoolConfigError(f"{at}: duplicate profile name {name!r}")
        home = _parse_home(entry.get(home_key), at, home_key)
        if home is None:
            inherit_count += 1
        elif home in homes:
            raise AccountPoolConfigError(f"{at}: {home_key} {home} is listed twice")
        else:
            homes.add(home)
        profiles.append(ProfileSpec(name=name, home=home))
    if inherit_count > 1:
        raise AccountPoolConfigError(
            f"{where}: only one profile may omit '{home_key}' (it inherits the relay's own login)"
        )
    return tuple(profiles)


def _parse_home(value: object, at: str, key: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise AccountPoolConfigError(f"{at}: {key} must be a non-empty string")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise AccountPoolConfigError(f"{at}: {key} must be an absolute path, got {value!r}")
    if not path.is_dir():
        raise AccountPoolConfigError(
            f"{at}: {key} {path} does not exist — log in once with the vendor CLI "
            f"using that directory before adding it to a pool"
        )
    return str(path)
