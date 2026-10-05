"""Strict validation of the account-pool TOML file."""

from __future__ import annotations

from pathlib import Path

import pytest

from claude_code_core.account_pool_config import (
    ENV_VAR,
    AccountPoolConfigError,
    load_from_env,
    load_pools,
)


@pytest.fixture()
def dirs(tmp_path: Path) -> dict[str, str]:
    out = {}
    for name in ("c1", "c2", "x1"):
        path = tmp_path / name
        path.mkdir()
        out[name] = str(path)
    return out


def _write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "pools.toml"
    path.write_text(text, encoding="utf-8")
    return path


def _claude_pool(dirs: dict[str, str], extra: str = "") -> str:
    return f"""
[pools.claude]
{extra}
[[pools.claude.profiles]]
name = "personal"
config_dir = "{dirs["c1"]}"
[[pools.claude.profiles]]
name = "work"
config_dir = "{dirs["c2"]}"
"""


class TestValidFiles:
    def test_defaults(self, tmp_path: Path, dirs: dict[str, str]) -> None:
        pools = load_pools(_write(tmp_path, _claude_pool(dirs)))
        pool = pools["claude"]
        assert pool.names == ("personal", "work")
        assert pool.strategy == "priority"
        assert pool.switch_at == 0.95
        assert pool.windows == ("five_hour", "seven_day")
        assert pool.assign == "session"
        assert pool.retry_on_exhaustion is False
        assert pool.cooldown_seconds == 3600
        assert pool.profiles[0].home == dirs["c1"]

    def test_all_knobs_and_a_codex_pool(self, tmp_path: Path, dirs: dict[str, str]) -> None:
        extra = (
            'strategy = "most_headroom"\nswitch_at = 0.8\nwindows = ["five_hour"]\n'
            'assign = "turn"\nretry_on_exhaustion = true\ncooldown_seconds = 600\n'
        )
        text = _claude_pool(dirs, extra) + (
            f'[pools.codex]\nstrategy = "round_robin"\n'
            f'[[pools.codex.profiles]]\nname = "cx"\ncodex_home = "{dirs["x1"]}"\n'
            f'[[pools.codex.profiles]]\nname = "cx-own"\n'
        )
        pools = load_pools(_write(tmp_path, text))
        claude = pools["claude"]
        assert (claude.strategy, claude.switch_at, claude.windows) == (
            "most_headroom",
            0.8,
            ("five_hour",),
        )
        assert (claude.assign, claude.retry_on_exhaustion, claude.cooldown_seconds) == (
            "turn",
            True,
            600,
        )
        codex = pools["codex"]
        assert codex.strategy == "round_robin"
        assert codex.profiles[1].home is None  # inherits the relay's own CODEX_HOME

    def test_tilde_is_expanded(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dirs: dict[str, str]
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        text = '[pools.claude]\n[[pools.claude.profiles]]\nname = "a"\nconfig_dir = "~/c1"\n'
        pools = load_pools(_write(tmp_path, text))
        assert pools["claude"].profiles[0].home == dirs["c1"]

    def test_env_unset_means_no_pools(self) -> None:
        assert load_from_env({}) == {}
        assert load_from_env({ENV_VAR: "  "}) == {}

    def test_env_points_at_the_file(self, tmp_path: Path, dirs: dict[str, str]) -> None:
        path = _write(tmp_path, _claude_pool(dirs))
        assert "claude" in load_from_env({ENV_VAR: str(path)})


class TestInvalidFiles:
    @pytest.mark.parametrize(
        ("extra", "message"),
        [
            ('strategy = "fastest"', "strategy must be one of"),
            ("switch_at = 0", "switch_at must be in (0, 1]"),
            ("switch_at = 1.5", "switch_at must be in (0, 1]"),
            ('switch_at = "high"', "switch_at must be a number"),
            ("switch_at = true", "switch_at must be a number"),
            ("windows = []", "windows must be a non-empty list"),
            ("windows = [1]", "windows must be a non-empty list"),
            ('assign = "thread"', "assign must be one of"),
            ('retry_on_exhaustion = "yes"', "retry_on_exhaustion must be true or false"),
            ("cooldown_seconds = 0", "cooldown_seconds must be a positive integer"),
            ("cooldown_seconds = 1.5", "cooldown_seconds must be a positive integer"),
            ("stratgy = 'priority'", "unknown keys"),
        ],
    )
    def test_bad_pool_knob(
        self, tmp_path: Path, dirs: dict[str, str], extra: str, message: str
    ) -> None:
        with pytest.raises(AccountPoolConfigError, match=message.replace("(", r"\(")):
            load_pools(_write(tmp_path, _claude_pool(dirs, extra)))

    def test_missing_file(self, tmp_path: Path) -> None:
        with pytest.raises(AccountPoolConfigError, match="cannot read"):
            load_pools(tmp_path / "nope.toml")

    def test_invalid_toml(self, tmp_path: Path) -> None:
        with pytest.raises(AccountPoolConfigError, match="invalid TOML"):
            load_pools(_write(tmp_path, "[pools.claude"))

    def test_no_pools(self, tmp_path: Path) -> None:
        with pytest.raises(AccountPoolConfigError, match="at least one"):
            load_pools(_write(tmp_path, "# empty\n"))
        with pytest.raises(AccountPoolConfigError, match="at least one"):
            load_pools(_write(tmp_path, "[pools]\n"))

    def test_unknown_top_level_key(self, tmp_path: Path, dirs: dict[str, str]) -> None:
        with pytest.raises(AccountPoolConfigError, match="unknown top-level"):
            load_pools(_write(tmp_path, "x = 1\n" + _claude_pool(dirs)))

    def test_unsupported_backend(self, tmp_path: Path) -> None:
        with pytest.raises(AccountPoolConfigError, match="unsupported backend"):
            load_pools(_write(tmp_path, '[pools.pi]\n[[pools.pi.profiles]]\nname = "a"\n'))

    def test_no_profiles(self, tmp_path: Path) -> None:
        with pytest.raises(AccountPoolConfigError, match="at least one"):
            load_pools(_write(tmp_path, '[pools.claude]\nstrategy = "sticky"\n'))

    @pytest.mark.parametrize("name", ["", "has space", "-lead", "x" * 33, "a/b"])
    def test_bad_profile_name(self, tmp_path: Path, name: str) -> None:
        text = f'[pools.claude]\n[[pools.claude.profiles]]\nname = "{name}"\n'
        with pytest.raises(AccountPoolConfigError, match="name must be"):
            load_pools(_write(tmp_path, text))

    def test_duplicate_profile_name(self, tmp_path: Path, dirs: dict[str, str]) -> None:
        text = (
            f'[pools.claude]\n[[pools.claude.profiles]]\nname = "a"\nconfig_dir = "{dirs["c1"]}"\n'
            f'[[pools.claude.profiles]]\nname = "a"\nconfig_dir = "{dirs["c2"]}"\n'
        )
        with pytest.raises(AccountPoolConfigError, match="duplicate profile name"):
            load_pools(_write(tmp_path, text))

    def test_name_reused_across_pools(self, tmp_path: Path, dirs: dict[str, str]) -> None:
        text = _claude_pool(dirs) + (
            f'[pools.codex]\n[[pools.codex.profiles]]\nname = "work"\ncodex_home = "{dirs["x1"]}"\n'
        )
        with pytest.raises(AccountPoolConfigError, match="already used"):
            load_pools(_write(tmp_path, text))

    def test_same_directory_twice(self, tmp_path: Path, dirs: dict[str, str]) -> None:
        text = (
            f'[pools.claude]\n[[pools.claude.profiles]]\nname = "a"\nconfig_dir = "{dirs["c1"]}"\n'
            f'[[pools.claude.profiles]]\nname = "b"\nconfig_dir = "{dirs["c1"]}"\n'
        )
        with pytest.raises(AccountPoolConfigError, match="listed twice"):
            load_pools(_write(tmp_path, text))

    def test_two_inheriting_profiles(self, tmp_path: Path) -> None:
        text = (
            '[pools.claude]\n[[pools.claude.profiles]]\nname = "a"\n'
            '[[pools.claude.profiles]]\nname = "b"\n'
        )
        with pytest.raises(AccountPoolConfigError, match="only one profile may omit"):
            load_pools(_write(tmp_path, text))

    def test_wrong_home_key_for_backend(self, tmp_path: Path, dirs: dict[str, str]) -> None:
        text = (
            f'[pools.claude]\n[[pools.claude.profiles]]\nname = "a"\ncodex_home = "{dirs["c1"]}"\n'
        )
        with pytest.raises(AccountPoolConfigError, match="config_dir"):
            load_pools(_write(tmp_path, text))

    def test_relative_path(self, tmp_path: Path) -> None:
        text = '[pools.claude]\n[[pools.claude.profiles]]\nname = "a"\nconfig_dir = "rel/dir"\n'
        with pytest.raises(AccountPoolConfigError, match="absolute path"):
            load_pools(_write(tmp_path, text))

    def test_missing_directory(self, tmp_path: Path) -> None:
        text = (
            f'[pools.claude]\n[[pools.claude.profiles]]\nname = "a"\n'
            f'config_dir = "{tmp_path / "absent"}"\n'
        )
        with pytest.raises(AccountPoolConfigError, match="does not exist"):
            load_pools(_write(tmp_path, text))


def test_shipped_example_is_valid(tmp_path: Path) -> None:
    """examples/account-pools.example.toml must parse once its directories exist."""
    example = Path(__file__).parent.parent / "examples" / "account-pools.example.toml"
    text = example.read_text(encoding="utf-8").replace("/home/me/", f"{tmp_path}/")
    for name in (".claude-work", ".codex-work"):
        (tmp_path / name).mkdir()
    pools = load_pools(_write(tmp_path, text))
    assert pools["claude"].names == ("personal", "work")
    assert pools["codex"].strategy == "most_headroom"
