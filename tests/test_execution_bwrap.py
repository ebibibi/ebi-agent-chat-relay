"""bwrap and the shared guarded mount plan: home allowlist, protected config, git, hides."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from claude_code_core.execution import ExecutionConfig, ExecutionRefusedError, Launch
from claude_code_core.execution.bwrap import (
    BwrapEnvironment,
    build_bwrap_argv,
    default_hide_paths,
    find_relay_dotenv,
    relay_env_files,
    sandbox_env,
)
from claude_code_core.execution.config import BwrapSettings
from claude_code_core.execution.guarded_paths import (
    GitLayout,
    create_placeholders,
    git_layout,
    guard_refusal,
    guarded_mounts,
    prepare_git,
    protected_entries,
    symlinked_entries,
)
from claude_code_core.execution.toolchain import toolchain_paths

HOME = "/home/agent"
ENV = {"HOME": HOME, "PATH": "/usr/bin:/bin", "ANTHROPIC_API_KEY": "sk-secret"}
CLAUDE_ARGV = ("claude", "-p", "--input-format", "stream-json")
CODEX_ARGV = ("codex", "exec", "--json", "-")


def launch(argv: tuple[str, ...] = CLAUDE_ARGV, cwd: str = "/work/repo", **env: str) -> Launch:
    return Launch(argv=argv, env={**ENV, **env}, cwd=cwd)


def argv_for(
    lch: Launch,
    existing: set[str],
    dirs: set[str] | None = None,
    *,
    settings: BwrapSettings | None = None,
    backend: str = "claude",
    git: GitLayout | None = None,
    relay_files: tuple[str, ...] = (),
    toolchain: tuple[str, ...] = (),
) -> list[str]:
    dirs = dirs or set()
    settings = settings or BwrapSettings()
    mounts = guarded_mounts(
        backend,
        lch,
        git or GitLayout(),
        rw_paths=settings.rw_paths,
        ro_paths=settings.ro_paths,
        protect=settings.protect_config,
        realpath=lambda p: p,
    )
    return list(
        build_bwrap_argv(
            settings,
            lch,
            mounts,
            relay_files=relay_files,
            toolchain=toolchain,
            exists=lambda p: p in existing or p in dirs,
            isdir=lambda p: p in dirs,
            realpath=lambda p: p,
        )
    )


def pairs(argv: list[str], flag: str) -> list[str]:
    return [argv[i + 1] for i, a in enumerate(argv) if a == flag]


# ---------------------------------------------------------------- argv shape


class TestStructure:
    def test_namespaces_and_session(self) -> None:
        argv = argv_for(launch(), {"/work/repo"})
        for flag in (
            "--die-with-parent",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--unshare-cgroup-try",
            "--new-session",
        ):
            assert flag in argv
        assert "--unshare-net" not in argv
        assert "--unshare-net" in argv_for(
            launch(), {"/work/repo"}, settings=BwrapSettings(unshare_net=True)
        )

    def test_home_is_an_empty_tmpfs_after_the_root_bind(self) -> None:
        argv = argv_for(launch(), {"/work/repo"})
        assert argv[argv.index("--ro-bind") + 1 : argv.index("--ro-bind") + 3] == ["/", "/"]
        assert pairs(argv, "--tmpfs")[:2] == ["/tmp", HOME]
        sep = argv.index("--")
        assert argv[sep - 2 : sep] == ["--chdir", "/work/repo"]
        assert tuple(argv[sep + 1 :]) == CLAUDE_ARGV

    def test_every_home_bind_comes_after_the_home_tmpfs(self) -> None:
        argv = argv_for(
            launch(cwd=f"{HOME}/work/repo"),
            {f"{HOME}/work/repo", f"{HOME}/.claude.json", f"{HOME}/.gitconfig"},
            {f"{HOME}/.claude"},
            toolchain=(f"{HOME}/.local/bin",),
        )
        home_tmpfs = argv.index(HOME)
        for flag in ("--bind", "--ro-bind"):
            for i, a in enumerate(argv):
                if a == flag and argv[i + 1].startswith(HOME + "/"):
                    assert i > home_tmpfs


class TestHomeAllowlist:
    def test_only_allowlisted_home_paths_are_bound(self) -> None:
        existing = {
            "/work/repo",
            f"{HOME}/.claude.json",
            f"{HOME}/.gitconfig",
            f"{HOME}/.local/bin",
            f"{HOME}/.config/gh/hosts.yml",
            f"{HOME}/.aws/credentials",
            f"{HOME}/other-repo",
        }
        argv = argv_for(launch(), existing, {f"{HOME}/.claude"}, toolchain=(f"{HOME}/.local/bin",))
        home_bound = {
            p for p in set(pairs(argv, "--bind")) | set(pairs(argv, "--ro-bind")) if HOME in p
        }
        assert {
            f"{HOME}/.claude",
            f"{HOME}/.claude.json",
            f"{HOME}/.gitconfig",
            f"{HOME}/.local/bin",
        } <= home_bound
        for secret in (".config/gh/hosts.yml", ".aws/credentials", "other-repo"):
            assert f"{HOME}/{secret}" not in " ".join(argv)

    def test_operator_paths(self) -> None:
        settings = BwrapSettings(rw_paths=(f"{HOME}/cache",), ro_paths=(f"{HOME}/tools",))
        argv = argv_for(
            launch(), {"/work/repo", f"{HOME}/cache", f"{HOME}/tools"}, settings=settings
        )
        assert f"{HOME}/cache" in pairs(argv, "--bind")
        assert f"{HOME}/tools" in pairs(argv, "--ro-bind")

    def test_toolchain_inside_a_writable_bind_is_read_only_again(self) -> None:
        state = f"{HOME}/.claude"
        tool = f"{state}/local/node_modules"
        argv = argv_for(launch(), {"/work/repo", tool}, {state}, toolchain=(tool,))
        ro_index = max(i for i, a in enumerate(argv) if a == "--ro-bind" and argv[i + 1] == tool)
        assert ro_index > argv.index(state)


# ---------------------------------------------------------------- protected config


class TestProtectedConfig:
    def test_claude_config_read_only_inside_writable_state(self) -> None:
        state = f"{HOME}/.claude"
        existing = {"/work/repo", f"{state}/settings.json", f"{HOME}/.claude.json"}
        argv = argv_for(launch(), existing, {state, f"{state}/plugins"})
        ro = pairs(argv, "--ro-bind")
        assert {f"{state}/settings.json", f"{state}/plugins", f"{HOME}/.claude.json"} <= set(ro)
        assert argv.index(f"{state}/settings.json") > argv.index(state)

    def test_claude_config_dir(self) -> None:
        lch = launch(CLAUDE_CONFIG_DIR="/pool/a")
        argv = argv_for(lch, {"/work/repo", "/pool/a/.claude.json"}, {"/pool/a"})
        assert "/pool/a/.claude.json" in pairs(argv, "--ro-bind")

    def test_codex(self) -> None:
        lch = launch(CODEX_ARGV, CODEX_HOME="/pool/codex")
        argv = argv_for(
            lch,
            {"/work/repo", "/pool/codex/config.toml", "/pool/codex/auth.json"},
            {"/pool/codex", "/pool/codex/rules"},
            backend="codex",
        )
        ro = pairs(argv, "--ro-bind")
        assert "/pool/codex/config.toml" in ro and "/pool/codex/rules" in ro
        assert "/pool/codex/auth.json" not in ro  # token refresh

    def test_pi_paths(self) -> None:
        entries = dict(protected_entries("pi", ENV))
        assert entries[f"{HOME}/.pi/agent/settings.json"] == f"{HOME}/.pi"

    def test_disabled(self) -> None:
        state = f"{HOME}/.claude"
        argv = argv_for(
            launch(),
            {"/work/repo", f"{state}/settings.json"},
            {state},
            settings=BwrapSettings(protect_config=False),
        )
        assert f"{state}/settings.json" not in argv

    def test_placeholders(self, tmp_path: Path) -> None:
        state = tmp_path / ".claude"
        state.mkdir()
        (state / "settings.local.json").write_text('{"keep": 1}')
        names = ("settings.json", "CLAUDE.md", "hooks", "settings.local.json")
        create_placeholders(tuple((str(state / n), str(state)) for n in names))
        settings = state / "settings.json"
        assert settings.read_text() == "{}\n"
        assert settings.stat().st_mode & 0o777 == 0o600
        assert (state / "hooks").stat().st_mode & 0o777 == 0o700
        assert (state / "CLAUDE.md").read_text() == ""
        assert (state / "settings.local.json").read_text() == '{"keep": 1}'

    def test_placeholders_never_follow_a_symlinked_parent(self, tmp_path: Path) -> None:
        root = tmp_path / ".pi"
        root.mkdir()
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (root / "agent").symlink_to(elsewhere)
        create_placeholders(((str(root / "agent" / "settings.json"), str(root)),))
        assert not (elsewhere / "settings.json").exists()
        assert symlinked_entries(((str(root / "agent" / "settings.json"), str(root)),)) == [
            str(root / "agent")
        ]

    def test_placeholders_never_follow_a_symlink_at_the_name(self, tmp_path: Path) -> None:
        state = tmp_path / ".claude"
        state.mkdir()
        target = tmp_path / "target"
        (state / "settings.json").symlink_to(target)
        create_placeholders(((str(state / "settings.json"), str(state)),))
        assert not target.exists()

    def test_pi_agent_dir_is_created_for_its_placeholders(self, tmp_path: Path) -> None:
        (tmp_path / ".pi").mkdir()
        create_placeholders(protected_entries("pi", {"HOME": str(tmp_path)}))
        assert (tmp_path / ".pi" / "agent" / "settings.json").read_text() == "{}\n"
        assert (tmp_path / ".pi" / "agent" / "extensions").is_dir()


# ---------------------------------------------------------------- refusals


class TestRefusals:
    def _refusal(self, cwd: str, **env: str) -> str | None:
        lch = Launch(argv=CLAUDE_ARGV, env={**ENV, **env}, cwd=cwd)
        return guard_refusal("claude", lch, GitLayout(), environment="bwrap")

    def test_home_or_wider_as_working_dir(self) -> None:
        for cwd in (HOME, "/home", "/"):
            assert self._refusal(cwd) is not None
        assert self._refusal(f"{HOME}/work") is None

    def test_state_dir_equal_to_home(self) -> None:
        assert self._refusal("/w", CLAUDE_CONFIG_DIR=HOME) is not None

    def test_operator_path_equal_to_home(self) -> None:
        lch = launch(cwd="/w")
        problem = guard_refusal(
            "claude", lch, GitLayout(), environment="bwrap", extra_paths=("/home",)
        )
        assert problem is not None

    async def test_symlinked_protected_entry_refused_without_side_effects(
        self, tmp_path: Path
    ) -> None:
        home = tmp_path / "home"
        state = home / ".claude"
        state.mkdir(parents=True)
        (tmp_path / "shared-hooks").mkdir()
        (state / "hooks").symlink_to(tmp_path / "shared-hooks")
        work = tmp_path / "work"
        work.mkdir()
        env = BwrapEnvironment(BwrapSettings(binary="/bin/true"))
        lch = Launch(argv=CLAUDE_ARGV, env={**ENV, "HOME": str(home)}, cwd=str(work))
        problem = await env.preflight("claude", lch)
        assert problem is not None and "symbolic link" in problem
        assert not (state / "settings.json").exists()
        with pytest.raises(ExecutionRefusedError, match="symbolic link"):
            env.transform("claude", lch)
        relaxed = BwrapEnvironment(BwrapSettings(binary="/bin/true", protect_config=False))
        assert await relaxed.preflight("claude", lch) is None

    async def test_symlinked_git_hooks_refused(self, tmp_path: Path) -> None:
        work, common, _gitdir = _worktree(tmp_path)
        shutil.rmtree(common / "hooks")
        (tmp_path / "shared").mkdir()
        (common / "hooks").symlink_to(tmp_path / "shared")
        (tmp_path / "h").mkdir()
        env = BwrapEnvironment(BwrapSettings(binary="/bin/true"))
        lch = Launch(argv=CLAUDE_ARGV, env={**ENV, "HOME": str(tmp_path / "h")}, cwd=str(work))
        problem = await env.preflight("claude", lch)
        assert problem is not None and "hooks" in problem

    async def test_preflight_refuses_home_as_cwd(self, tmp_path: Path) -> None:
        env = BwrapEnvironment(BwrapSettings(binary="/bin/true"))
        lch = Launch(argv=CLAUDE_ARGV, env={**ENV, "HOME": str(tmp_path)}, cwd=str(tmp_path))
        problem = await env.preflight("claude", lch)
        assert problem is not None and "home directory" in problem

    async def test_missing_binary(self, tmp_path: Path) -> None:
        env = BwrapEnvironment(BwrapSettings(binary="definitely-not-bwrap"))
        problem = await env.preflight("claude", launch(cwd=str(tmp_path)))
        assert problem is not None and "bubblewrap" in problem

    async def test_failing_probe_names_userns(self, tmp_path: Path) -> None:
        (tmp_path / "w").mkdir()
        env = BwrapEnvironment(BwrapSettings(binary="/bin/false"))
        lch = Launch(argv=CLAUDE_ARGV, env={**ENV, "HOME": str(tmp_path)}, cwd=str(tmp_path / "w"))
        problem = await env.preflight("claude", lch)
        assert problem is not None and "user namespaces" in problem


# ---------------------------------------------------------------- git


def _worktree(root: Path) -> tuple[Path, Path, Path]:
    common = root / "main" / ".git"
    for sub in ("objects", "refs", "hooks"):
        (common / sub).mkdir(parents=True)
    (common / "HEAD").write_text("ref: refs/heads/main\n")
    (common / "config").write_text("[core]\n")
    gitdir = common / "worktrees" / "wt"
    gitdir.mkdir(parents=True)
    work = root / "wt"
    work.mkdir()
    (work / ".git").write_text(f"gitdir: {gitdir}\n")
    (gitdir / "gitdir").write_text(f"{work / '.git'}\n")
    (gitdir / "commondir").write_text("../..\n")
    return work, common, gitdir


class TestGitLayout:
    def test_no_repository(self, tmp_path: Path) -> None:
        assert git_layout(str(tmp_path), "/nonexistent-home") == GitLayout()

    def test_plain_repository_is_read_only_whole(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        layout = git_layout(str(tmp_path), "/nonexistent-home")
        assert layout == GitLayout(dotgit=str(tmp_path / ".git"))

    def test_linked_worktree_mounts_only_what_a_commit_writes(self, tmp_path: Path) -> None:
        work, common, gitdir = _worktree(tmp_path)
        layout = git_layout(str(work), "/nonexistent-home")
        assert layout.writable == (
            str(common / "objects"),
            str(common / "refs"),
            str(common / "logs"),
            str(gitdir),
        )
        assert str(common) not in layout.writable
        assert str(common / "config") in layout.readonly
        assert str(common / "packed-refs") in layout.readonly
        assert layout.hooks_dir == str(common / "hooks")
        assert layout.dotgit == str(work / ".git")
        # The pointers that choose the common dir are read-only inside the
        # otherwise writable worktree git dir (the commondir-redirect finding).
        for name in ("commondir", "gitdir", "config.worktree"):
            assert str(gitdir / name) in layout.readonly
        assert str(common / "modules") in layout.readonly

    def _untrusted(self, layout: GitLayout) -> None:
        assert layout.writable == ()
        assert layout.hooks_dir is None

    def test_refused_without_backlink(self, tmp_path: Path) -> None:
        work, _common, gitdir = _worktree(tmp_path)
        (gitdir / "gitdir").unlink()
        self._untrusted(git_layout(str(work), "/nonexistent-home"))

    def test_refused_when_backlink_names_another_worktree(self, tmp_path: Path) -> None:
        work, _common, gitdir = _worktree(tmp_path)
        (gitdir / "gitdir").write_text(f"{tmp_path / 'elsewhere' / '.git'}\n")
        self._untrusted(git_layout(str(work), "/nonexistent-home"))

    def test_refused_when_not_under_worktrees(self, tmp_path: Path) -> None:
        work, common, _gitdir = _worktree(tmp_path)
        (work / ".git").write_text(f"gitdir: {common}\n")
        self._untrusted(git_layout(str(work), "/nonexistent-home"))

    @pytest.mark.parametrize("missing", ["HEAD", "objects", "refs"])
    def test_refused_without_core_entries(self, tmp_path: Path, missing: str) -> None:
        work, common, _gitdir = _worktree(tmp_path)
        target = common / missing
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
        self._untrusted(git_layout(str(work), "/nonexistent-home"))

    def test_refused_when_common_is_home_or_above(self, tmp_path: Path) -> None:
        work, common, _gitdir = _worktree(tmp_path)
        self._untrusted(git_layout(str(work), str(common)))
        self._untrusted(git_layout(str(work), str(common / "sub")))

    def test_refused_when_common_contains_the_working_dir(self, tmp_path: Path) -> None:
        common = tmp_path / "repo"
        for sub in ("objects", "refs"):
            (common / sub).mkdir(parents=True)
        (common / "HEAD").write_text("x")
        gitdir = common / "worktrees" / "wt"
        gitdir.mkdir(parents=True)
        work = common / "inner"
        work.mkdir()
        (work / ".git").write_text(f"gitdir: {gitdir}\n")
        (gitdir / "gitdir").write_text(f"{work / '.git'}\n")
        self._untrusted(git_layout(str(work), "/nonexistent-home"))

    def test_refused_when_dotgit_is_a_symlink(self, tmp_path: Path) -> None:
        work, common, _gitdir = _worktree(tmp_path)
        (work / ".git").unlink()
        (work / ".git").symlink_to(common / "HEAD")
        assert git_layout(str(work), "/nonexistent-home") == GitLayout()

    def test_prepare_creates_hooks_and_logs(self, tmp_path: Path) -> None:
        work, common, _gitdir = _worktree(tmp_path)
        (common / "hooks").rmdir()
        prepare_git(git_layout(str(work), "/nonexistent-home"))
        assert (common / "hooks").is_dir() and (common / "logs").is_dir()

    def test_worktree_argv(self) -> None:
        layout = GitLayout(
            dotgit="/work/repo/.git",
            writable=("/src/.git/objects", "/src/.git/refs", "/src/.git/worktrees/repo"),
            readonly=("/src/.git/config",),
            hooks_dir="/src/.git/hooks",
        )
        existing = {
            "/work/repo",
            "/work/repo/.git",
            "/src/.git/objects",
            "/src/.git/refs",
            "/src/.git/worktrees/repo",
            "/src/.git/config",
        }
        argv = argv_for(
            launch(cwd=f"{HOME}/w"),
            existing | {f"{HOME}/w"},
            {"/src/.git/hooks"},
            git=layout,
        )
        assert "/src/.git" not in pairs(argv, "--bind")
        assert {"/src/.git/objects", "/src/.git/refs", "/src/.git/worktrees/repo"} <= set(
            pairs(argv, "--bind")
        )
        # Outside $HOME, config and hooks are already read-only via the root bind.

    def test_dotgit_is_a_read_only_mount_point(self) -> None:
        layout = GitLayout(dotgit="/work/repo/.git")
        argv = argv_for(launch(), {"/work/repo"}, {"/work/repo/.git"}, git=layout)
        assert "/work/repo/.git" in pairs(argv, "--ro-bind")
        assert "/work/repo/.git" not in pairs(argv, "--bind")
        assert argv.index("/work/repo/.git") > argv.index("/work/repo")


class TestHides:
    def test_defaults(self) -> None:
        env = {**ENV, "XDG_RUNTIME_DIR": "/run/user/1000", "SSH_AUTH_SOCK": "/tmp/a.sock"}
        assert default_hide_paths(env, ("/srv/relay/.env",)) == (
            "/srv/relay/.env",
            "/var/run/docker.sock",
            "/run/docker.sock",
            "/run/dbus/system_bus_socket",
            "/run/user/1000",
            "/tmp/a.sock",
        )

    def test_visible_paths_are_hidden(self) -> None:
        lch = launch(XDG_RUNTIME_DIR="/run/user/1000")
        argv = argv_for(
            lch,
            {"/work/repo", "/run/dbus/system_bus_socket", "/run/docker.sock", "/work/repo/.env"},
            {"/run/user/1000"},
            relay_files=("/work/repo/.env",),
        )
        joined = " ".join(argv)
        assert "--tmpfs /run/user/1000" in joined
        assert "--ro-bind /dev/null /run/dbus/system_bus_socket" in joined
        assert "--ro-bind /dev/null /run/docker.sock" in joined
        assert "--ro-bind /dev/null /work/repo/.env" in joined
        assert argv.index("/work/repo/.env") > argv.index("--bind")

    def test_hides_under_the_empty_home_are_skipped(self) -> None:
        argv = argv_for(
            launch(), {"/work/repo", f"{HOME}/relay/.env"}, relay_files=(f"{HOME}/relay/.env",)
        )
        assert f"{HOME}/relay/.env" not in argv

    def test_hide_never_covers_a_writable_path(self) -> None:
        argv = argv_for(
            launch(), {"/work/repo"}, {"/work"}, settings=BwrapSettings(hide_paths=("/work",))
        )
        assert "/work" not in pairs(argv, "--tmpfs")

    def test_relay_env_siblings(self, tmp_path: Path) -> None:
        for name in (".env", ".env.bak-1", ".env.local", ".env.example", "other"):
            (tmp_path / name).write_text("x")
        files = relay_env_files(str(tmp_path / ".env"))
        assert [Path(f).name for f in files] == [".env", ".env.bak-1", ".env.local"]

    def test_find_relay_dotenv(self, tmp_path: Path) -> None:
        (tmp_path / ".env").write_text("X=1")
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        assert find_relay_dotenv(str(nested)) == str(tmp_path / ".env")

    def test_session_variables_dropped(self) -> None:
        env = {
            **ENV,
            "XDG_RUNTIME_DIR": "/run/user/1",
            "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1/bus",
            "SSH_AUTH_SOCK": "/run/user/1/ssh",
        }
        out = sandbox_env(BwrapSettings(), env)
        for name in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "SSH_AUTH_SOCK"):
            assert name not in out
        assert out["ANTHROPIC_API_KEY"] == "sk-secret"
        assert sandbox_env(BwrapSettings(hide_defaults=False), env) == env


# ---------------------------------------------------------------- toolchain


def _npm_layout(home: Path) -> tuple[Path, Path, Path, Path]:
    pkg = home / ".npm-global" / "lib" / "node_modules" / "@x" / "cli" / "bin"
    pkg.mkdir(parents=True)
    script = pkg / "cli.js"
    script.write_text("#!/usr/bin/env node\n")
    script.chmod(0o755)
    bindir = home / ".npm-global" / "bin"
    bindir.mkdir(parents=True)
    (bindir / "cli").symlink_to(script)
    node_prefix = home / ".local" / "share" / "nodejs" / "v22"
    (node_prefix / "bin").mkdir(parents=True)
    node = node_prefix / "bin" / "node"
    node.write_text("")
    node.chmod(0o755)
    localbin = home / ".local" / "bin"
    localbin.mkdir(parents=True)
    (localbin / "node").symlink_to(node)
    return bindir, localbin, node_prefix, script


class TestToolchain:
    def test_npm_script_and_interpreter(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        bindir, localbin, node_prefix, _script = _npm_layout(home)
        (localbin / "cli2").symlink_to("../../.npm-global/bin/cli")
        assert str(bindir) in toolchain_paths("cli2", {"HOME": str(home), "PATH": str(localbin)})
        env = {"HOME": str(home), "PATH": f"{bindir}:{localbin}:/usr/bin"}
        paths = toolchain_paths("cli", env)
        assert str(bindir) in paths
        assert str(home / ".npm-global" / "lib" / "node_modules") in paths
        assert str(localbin) in paths
        assert str(node_prefix) in paths
        assert str(home) not in paths and str(home / ".local") not in paths

    def test_binary_outside_home_needs_nothing(self) -> None:
        assert toolchain_paths("/bin/sh", {"HOME": "/nonexistent-home", "PATH": "/bin"}) == ()

    def test_a_shebang_in_a_writable_dir_is_not_followed(self, tmp_path: Path) -> None:
        """The credential-exposure finding: a rewritten script must not pick the bind."""
        home = tmp_path / "home"
        secret = home / ".ssh"
        secret.mkdir(parents=True)
        (secret / "id_ed25519").write_text("PRIVATE")
        (secret / "id_ed25519").chmod(0o755)
        state = home / ".claude" / "local"
        state.mkdir(parents=True)
        cli = state / "claude"
        cli.write_text(f"#!{secret / 'id_ed25519'}\n")
        cli.chmod(0o755)
        env = {"HOME": str(home), "PATH": str(state)}
        trusted = toolchain_paths("claude", env)
        assert str(secret) in trusted  # what an attacker would get without the rule
        paths = toolchain_paths("claude", env, untrusted=(str(home / ".claude"),))
        assert str(secret) not in paths
        assert str(state) in paths  # still mounted, read-only

    def test_a_symlink_in_a_writable_dir_is_not_followed(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        (home / ".aws").mkdir(parents=True)
        (home / ".aws" / "credentials").write_text("x")
        work = home / "work"
        (work / "bin").mkdir(parents=True)
        (work / "bin" / "tool").symlink_to(home / ".aws" / "credentials")
        env = {"HOME": str(home), "PATH": str(work / "bin")}
        paths = toolchain_paths("tool", env, untrusted=(str(work),))
        assert str(home / ".aws") not in paths


class TestConfig:
    def test_new_settings(self) -> None:
        config = ExecutionConfig.from_env(
            {"CCDB_BWRAP_RO_PATHS": "/a:/b", "CCDB_BWRAP_PROTECT_CONFIG": "0"}
        )
        assert config.bwrap.ro_paths == ("/a", "/b")
        assert config.bwrap.protect_config is False


# ---------------------------------------------------------------- real sandbox


def _bwrap_works() -> bool:
    binary = shutil.which("bwrap")
    if not binary:
        return False
    result = subprocess.run(
        [binary, "--ro-bind", "/", "/", "--tmpfs", "/tmp", "--", "true"], capture_output=True
    )
    return result.returncode == 0


@pytest.mark.skipif(not _bwrap_works(), reason="bubblewrap with user namespaces not available")
class TestRealSandbox:
    """Run the generated argv for real and look from inside."""

    SECRETS = {
        ".config/gh/hosts.yml": "oauth_token: gho_secret",
        ".aws/credentials": "aws_secret_access_key=x",
        ".azure/accessTokens.json": "[]",
        ".kube/config": "token: x",
        ".docker/config.json": "{}",
        ".netrc": "machine x password y",
        ".git-credentials": "https://u:p@x",
        ".ssh/id_ed25519": "PRIVATE",
        "other-repo/README": "private",
        "relay/.env": "DISCORD_BOT_TOKEN=x",
    }

    def _run(
        self, tmp_path: Path, script: str, work: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        home = tmp_path / "home"
        for rel, content in self.SECRETS.items():
            path = home / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        (home / ".gitconfig").write_text("[user]\n\tname = agent\n\temail = a@b\n")
        work = work or home / "work"
        work.mkdir(parents=True, exist_ok=True)
        env = {"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        lch = Launch(argv=("bash", "-c", script), env=env, cwd=str(work))
        settings = BwrapSettings(binary=shutil.which("bwrap") or "bwrap")
        layout = git_layout(str(work), str(home))
        assert guard_refusal("claude", lch, layout, environment="bwrap") is None
        from claude_code_core.execution.guarded_paths import prepare_guarded

        prepare_guarded("claude", lch, layout, protect=True)
        mounts = guarded_mounts("claude", lch, layout)
        argv = build_bwrap_argv(settings, lch, mounts, relay_files=(str(home / "relay/.env"),))
        return subprocess.run(list(argv), capture_output=True, text=True, env=env, cwd=str(work))

    def test_home_shows_only_the_allowlist(self, tmp_path: Path) -> None:
        result = self._run(tmp_path, 'ls -A "$HOME"')
        assert result.returncode == 0, result.stderr
        assert set(result.stdout.split()) <= {".claude", ".claude.json", ".gitconfig", "work"}

    def test_credentials_and_other_repos_are_invisible(self, tmp_path: Path) -> None:
        names = " ".join(self.SECRETS)
        result = self._run(
            tmp_path,
            f'for f in {names}; do cat "$HOME/$f" 2>/dev/null && echo "LEAK $f"; done; echo done',
        )
        assert "LEAK" not in result.stdout
        assert "done" in result.stdout

    def test_plain_repo_git_dir_is_read_only(self, tmp_path: Path) -> None:
        work = tmp_path / "home" / "work"
        work.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(work)], check=True)
        result = self._run(
            tmp_path,
            "mv .git .git.x 2>/dev/null && echo MOVED; "
            "touch .git/hooks/pre-commit 2>/dev/null && echo HOOK; "
            "git config core.hooksPath /tmp 2>/dev/null && echo CONFIG; "
            "echo . > .git/commondir 2>/dev/null && echo REDIRECTED; "
            "echo x > f && echo EDITED; git status --short >/dev/null && echo STATUS",
        )
        for marker in ("MOVED", "HOOK", "CONFIG", "REDIRECTED"):
            assert marker not in result.stdout
        assert "EDITED" in result.stdout and "STATUS" in result.stdout
        assert not (work / ".git" / "commondir").exists()

    def test_linked_worktree_commits_and_common_dir_is_hidden(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
        main = home / "main"
        main.mkdir(parents=True)
        git = ["git", "-c", "user.name=a", "-c", "user.email=a@b"]
        subprocess.run([*git, "init", "-q", str(main)], check=True)
        subprocess.run(
            [*git, "-C", str(main), "commit", "-q", "--allow-empty", "-m", "i"], check=True
        )
        work = home / "wt"
        subprocess.run([*git, "-C", str(main), "worktree", "add", "-q", str(work)], check=True)
        (main / ".git" / "secret-note").write_text("x")
        result = self._run(
            tmp_path,
            "echo x > f && git add f && git commit -qm t && echo COMMITTED; "
            f"cat {main / '.git' / 'secret-note'} 2>/dev/null && echo SEEN; "
            f"touch {main / '.git' / 'hooks' / 'pre-commit'} 2>/dev/null && echo HOOK; "
            "echo 'gitdir: /tmp' > .git 2>/dev/null && echo REPOINTED; "
            f"echo /tmp > {main / '.git' / 'worktrees' / 'wt' / 'commondir'} 2>/dev/null "
            "&& echo REDIRECTED",
            work=work,
        )
        assert "COMMITTED" in result.stdout, result.stderr
        for marker in ("SEEN", "HOOK", "REPOINTED", "REDIRECTED"):
            assert marker not in result.stdout
        log = subprocess.run(
            ["git", "-C", str(main), "log", "--all", "--oneline"], capture_output=True, text=True
        )
        assert " t" in log.stdout

    def test_working_dir_writable_config_read_only(self, tmp_path: Path) -> None:
        result = self._run(
            tmp_path,
            'touch ok && echo WROTE; touch "$HOME/.claude/settings.json" 2>/dev/null || echo RO; '
            "git config --global user.name",
        )
        assert "WROTE" in result.stdout and "RO" in result.stdout
        assert "agent" in result.stdout
        assert (tmp_path / "home" / "work" / "ok").exists()
