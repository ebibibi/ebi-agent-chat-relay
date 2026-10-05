"""bwrap: home allowlist, protected config, git, hides, layout refusals."""

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
from claude_code_core.execution.bwrap_fs import (
    create_git_hooks_dir,
    create_placeholders,
    git_dirs,
    layout_problem,
    protected_entries,
    toolchain_paths,
)
from claude_code_core.execution.config import BwrapSettings

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
    **kwargs: object,
) -> list[str]:
    dirs = dirs or set()
    return list(
        build_bwrap_argv(
            settings or BwrapSettings(),
            backend,
            lch,
            exists=lambda p: p in existing or p in dirs,
            isdir=lambda p: p in dirs,
            realpath=lambda p: p,
            **kwargs,  # type: ignore[arg-type]
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
        tmpfs = pairs(argv, "--tmpfs")
        assert tmpfs[:2] == ["/tmp", HOME]
        sep = argv.index("--")
        assert argv[sep - 2 : sep] == ["--chdir", "/work/repo"]
        assert tuple(argv[sep + 1 :]) == CLAUDE_ARGV

    def test_every_home_bind_comes_after_the_home_tmpfs(self) -> None:
        state = f"{HOME}/.claude"
        argv = argv_for(
            launch(cwd=f"{HOME}/work/repo"),
            {f"{HOME}/work/repo", f"{HOME}/.claude.json", f"{HOME}/.gitconfig"},
            {state},
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
        bound = set(pairs(argv, "--bind")) | set(pairs(argv, "--ro-bind"))
        home_bound = {p for p in bound if p.startswith(HOME)}
        assert f"{HOME}/.claude" in home_bound
        assert f"{HOME}/.claude.json" in home_bound
        assert f"{HOME}/.gitconfig" in home_bound
        assert f"{HOME}/.local/bin" in home_bound
        for secret in (".config/gh/hosts.yml", ".aws/credentials", "other-repo"):
            assert f"{HOME}/{secret}" not in " ".join(argv)

    def test_toolchain_bound_read_only(self) -> None:
        argv = argv_for(
            launch(),
            {"/work/repo", f"{HOME}/.npm-global/lib/node_modules"},
            toolchain=(f"{HOME}/.npm-global/lib/node_modules",),
        )
        assert f"{HOME}/.npm-global/lib/node_modules" in pairs(argv, "--ro-bind")

    def test_operator_paths(self) -> None:
        settings = BwrapSettings(rw_paths=(f"{HOME}/cache",), ro_paths=(f"{HOME}/tools",))
        argv = argv_for(
            launch(), {"/work/repo", f"{HOME}/cache", f"{HOME}/tools"}, settings=settings
        )
        assert f"{HOME}/cache" in pairs(argv, "--bind")
        assert f"{HOME}/tools" in pairs(argv, "--ro-bind")


# ---------------------------------------------------------------- protected config


class TestProtectedConfig:
    def test_claude_config_read_only_inside_writable_state(self) -> None:
        state = f"{HOME}/.claude"
        existing = {"/work/repo", f"{state}/settings.json", f"{HOME}/.claude.json"}
        argv = argv_for(launch(), existing, {state, f"{state}/plugins"})
        ro = pairs(argv, "--ro-bind")
        assert f"{state}/settings.json" in ro
        assert f"{state}/plugins" in ro
        assert f"{HOME}/.claude.json" in ro
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
        entries = tuple(
            (str(state / n), str(state)) for n in ("settings.json", "CLAUDE.md", "hooks")
        )
        entries += ((str(state / "settings.local.json"), str(state)),)
        create_placeholders(entries)
        settings = state / "settings.json"
        assert settings.read_text() == "{}\n"
        assert settings.stat().st_mode & 0o777 == 0o600
        assert (state / "CLAUDE.md").read_text() == ""
        assert (state / "hooks").is_dir()
        assert (state / "settings.local.json").read_text() == '{"keep": 1}'

    def test_placeholders_never_follow_a_symlinked_parent(self, tmp_path: Path) -> None:
        root = tmp_path / ".pi"
        root.mkdir()
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        (root / "agent").symlink_to(elsewhere)
        create_placeholders(((str(root / "agent" / "settings.json"), str(root)),))
        assert not (elsewhere / "settings.json").exists()

    def test_placeholders_never_follow_a_symlink_at_the_name(self, tmp_path: Path) -> None:
        state = tmp_path / ".claude"
        state.mkdir()
        target = tmp_path / "target"
        (state / "settings.json").symlink_to(target)
        create_placeholders(((str(state / "settings.json"), str(state)),))
        assert not target.exists()


# ---------------------------------------------------------------- refusals


class TestLayoutRefusals:
    def test_home_as_working_dir_refused(self) -> None:
        assert layout_problem(HOME, (), (), HOME) is not None
        assert layout_problem("/home", (), (), HOME) is not None
        assert layout_problem("/", (), (), HOME) is not None
        assert layout_problem(f"{HOME}/work", (), (), HOME) is None

    def test_state_or_operator_path_equal_to_home_refused(self) -> None:
        assert layout_problem("/w", (HOME,), (), HOME) is not None
        assert layout_problem("/w", (), ("/home",), HOME) is not None

    async def test_symlinked_protected_entry_refused(self, tmp_path: Path) -> None:
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
        # A refused layout leaves no placeholders behind.
        assert not (state / "settings.json").exists()
        with pytest.raises(ExecutionRefusedError, match="symbolic link"):
            env.transform("claude", lch)
        # The operator can accept the risk explicitly.
        relaxed = BwrapEnvironment(BwrapSettings(binary="/bin/true", protect_config=False))
        assert await relaxed.preflight("claude", lch) is None

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


def _worktree(tmp_path: Path) -> tuple[Path, Path]:
    common = tmp_path / "main" / ".git"
    (common / "objects").mkdir(parents=True)
    (common / "HEAD").write_text("ref: refs/heads/main\n")
    (common / "hooks").mkdir()
    gitdir = common / "worktrees" / "wt"
    gitdir.mkdir(parents=True)
    work = tmp_path / "wt"
    work.mkdir()
    (work / ".git").write_text(f"gitdir: {gitdir}\n")
    (gitdir / "gitdir").write_text(f"{work / '.git'}\n")
    (gitdir / "commondir").write_text("../..\n")
    return work, common


class TestGit:
    def test_plain_repository(self, tmp_path: Path) -> None:
        (tmp_path / ".git").mkdir()
        assert git_dirs(str(tmp_path)) == (None, str(tmp_path / ".git"))

    def test_linked_worktree_trusted_with_backlink(self, tmp_path: Path) -> None:
        work, common = _worktree(tmp_path)
        assert git_dirs(str(work)) == (str(common), str(common))

    def test_forged_gitdir_without_backlink_is_ignored(self, tmp_path: Path) -> None:
        work = tmp_path / "wt"
        work.mkdir()
        target = tmp_path / "victim" / "worktrees" / "x"
        target.mkdir(parents=True)
        (work / ".git").write_text(f"gitdir: {target}\n")
        assert git_dirs(str(work)) == (None, None)

    def test_gitdir_pointing_at_home_is_ignored(self, tmp_path: Path) -> None:
        work = tmp_path / "wt"
        work.mkdir()
        (work / ".git").write_text(f"gitdir: {tmp_path}\n")
        assert git_dirs(str(work)) == (None, None)

    def test_symlinked_dotgit_file_is_not_read(self, tmp_path: Path) -> None:
        work, _common = _worktree(tmp_path)
        (work / ".git").unlink()
        (work / ".git").symlink_to(tmp_path / "main" / ".git" / "HEAD")
        assert git_dirs(str(work)) == (None, None)

    def test_worktree_argv(self) -> None:
        argv = argv_for(
            launch(),
            {"/work/repo", "/src/main/.git/config"},
            {"/src/main/.git", "/src/main/.git/hooks"},
            git=("/src/main/.git", "/src/main/.git"),
        )
        assert "/src/main/.git" in pairs(argv, "--bind")
        assert {"/src/main/.git/hooks", "/src/main/.git/config"} <= set(pairs(argv, "--ro-bind"))


# ---------------------------------------------------------------- hides / env


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


class TestToolchain:
    def test_npm_script_and_interpreter(self, tmp_path: Path) -> None:
        home = tmp_path / "home"
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
        # A second hop: ~/.local/bin/cli2 -> ../../.npm-global/bin/cli -> script.
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

    def _run(self, tmp_path: Path, script: str) -> subprocess.CompletedProcess[str]:
        home = tmp_path / "home"
        secrets = {
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
        for rel, content in secrets.items():
            path = home / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        (home / ".gitconfig").write_text("[user]\n\tname = agent\n")
        work = home / "work"
        work.mkdir(exist_ok=True)
        env = {"HOME": str(home), "PATH": os.environ.get("PATH", "/usr/bin:/bin")}
        lch = Launch(argv=("bash", "-c", script), env=env, cwd=str(work))
        settings = BwrapSettings(binary=shutil.which("bwrap") or "bwrap")
        create_placeholders(protected_entries("claude", env))
        create_git_hooks_dir(git_dirs(str(work))[1])
        argv = build_bwrap_argv(
            settings,
            "claude",
            lch,
            relay_files=(str(home / "relay/.env"),),
            git=git_dirs(str(work)),
        )
        return subprocess.run(list(argv), capture_output=True, text=True, env=env, cwd=str(work))

    def test_home_shows_only_the_allowlist(self, tmp_path: Path) -> None:
        result = self._run(tmp_path, 'ls -A "$HOME"')
        assert result.returncode == 0, result.stderr
        assert set(result.stdout.split()) <= {".claude", ".claude.json", ".gitconfig", "work"}

    def test_credentials_and_other_repos_are_invisible(self, tmp_path: Path) -> None:
        result = self._run(
            tmp_path,
            "for f in .config/gh/hosts.yml .aws/credentials .azure/accessTokens.json .kube/config "
            ".docker/config.json .netrc .git-credentials .ssh/id_ed25519 other-repo/README "
            'relay/.env; do cat "$HOME/$f" 2>/dev/null && echo "LEAK $f"; done; echo done',
        )
        assert "LEAK" not in result.stdout
        assert "done" in result.stdout

    def test_dotgit_cannot_be_swapped_and_hooks_are_read_only(self, tmp_path: Path) -> None:
        work = tmp_path / "home" / "work"
        work.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(work)], check=True)
        result = self._run(
            tmp_path,
            "mv .git .git.x 2>/dev/null && echo MOVED; "
            "touch .git/hooks/pre-commit 2>/dev/null && echo HOOK; "
            "git config core.hooksPath /tmp 2>/dev/null && echo CONFIG; "
            "echo x > f && git add f && git -c user.email=a@b commit -qm t && echo COMMITTED",
        )
        assert "MOVED" not in result.stdout
        assert "HOOK" not in result.stdout
        assert "CONFIG" not in result.stdout
        assert "COMMITTED" in result.stdout, result.stderr

    def test_working_dir_writable_config_read_only(self, tmp_path: Path) -> None:
        result = self._run(
            tmp_path,
            'touch ok && echo WROTE; touch "$HOME/.claude/settings.json" 2>/dev/null || echo RO; '
            "git config --global user.name",
        )
        assert "WROTE" in result.stdout and "RO" in result.stdout
        assert "agent" in result.stdout
        assert (tmp_path / "home" / "work" / "ok").exists()


class TestReviewFindings:
    def test_toolchain_inside_a_writable_bind_is_read_only_again(self) -> None:
        state = f"{HOME}/.claude"
        tool = f"{state}/local/node_modules"
        argv = argv_for(launch(), {"/work/repo", tool}, {state}, toolchain=(tool,))
        ro_index = max(i for i, a in enumerate(argv) if a == "--ro-bind" and argv[i + 1] == tool)
        assert ro_index > argv.index(state)

    def test_dotgit_directory_becomes_a_mount_point(self) -> None:
        argv = argv_for(
            launch(),
            {"/work/repo"},
            {"/work/repo/.git", "/work/repo/.git/hooks"},
            git=(None, "/work/repo/.git"),
        )
        assert "/work/repo/.git" in pairs(argv, "--bind")
        # hooks re-bound read-only after the .git mount point
        assert argv.index("/work/repo/.git/hooks") > argv.index("/work/repo/.git")

    def test_worktree_dotgit_file_is_read_only(self) -> None:
        argv = argv_for(launch(), {"/work/repo", "/work/repo/.git"}, git=("/src/.git", "/src/.git"))
        assert "/work/repo/.git" in pairs(argv, "--ro-bind")

    def test_symlinked_parent_of_a_protected_entry_is_refused(self, tmp_path: Path) -> None:
        from claude_code_core.execution.bwrap_fs import symlinked_entries

        root = tmp_path / ".pi"
        root.mkdir()
        (tmp_path / "elsewhere").mkdir()
        (root / "agent").symlink_to(tmp_path / "elsewhere")
        entries = ((str(root / "agent" / "settings.json"), str(root)),)
        assert symlinked_entries(entries) == [str(root / "agent")]

    def test_pi_agent_dir_is_created_for_its_placeholders(self, tmp_path: Path) -> None:
        env = {"HOME": str(tmp_path)}
        (tmp_path / ".pi").mkdir()
        create_placeholders(protected_entries("pi", env))
        assert (tmp_path / ".pi" / "agent" / "settings.json").read_text() == "{}\n"
        assert (tmp_path / ".pi" / "agent" / "extensions").is_dir()

    def test_git_symlinks_refused_and_hooks_created(self, tmp_path: Path) -> None:
        from claude_code_core.execution.bwrap_fs import create_git_hooks_dir, git_link_problem

        git = tmp_path / ".git"
        git.mkdir()
        create_git_hooks_dir(str(git))
        assert (git / "hooks").is_dir()
        assert git_link_problem(str(tmp_path), str(git)) is None
        (git / "hooks").rmdir()
        (git / "hooks").symlink_to(tmp_path)
        assert git_link_problem(str(tmp_path), str(git)) == str(git / "hooks")

    async def test_preflight_refuses_symlinked_git_hooks(self, tmp_path: Path) -> None:
        work = tmp_path / "work"
        (work / ".git").mkdir(parents=True)
        (tmp_path / "shared").mkdir()
        (work / ".git" / "hooks").symlink_to(tmp_path / "shared")
        env = BwrapEnvironment(BwrapSettings(binary="/bin/true"))
        lch = Launch(argv=CLAUDE_ARGV, env={**ENV, "HOME": str(tmp_path / "h")}, cwd=str(work))
        (tmp_path / "h").mkdir()
        problem = await env.preflight("claude", lch)
        assert problem is not None and "hooks" in problem
