"""Execution environments: config, every mode's transform, preflight, allowlist."""

from __future__ import annotations

import json
import shlex
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest

from claude_code_core.execution import (
    ExecutionConfig,
    ExecutionRefusedError,
    Launch,
    agent_state_paths,
    prepare_launch,
    resolve_mode,
)
from claude_code_core.execution.bwrap import (
    BwrapEnvironment,
    build_bwrap_argv,
    default_hide_paths,
    find_relay_dotenv,
)
from claude_code_core.execution.config import (
    BwrapSettings,
    ContainerSettings,
    NativeSettings,
    SshSettings,
)
from claude_code_core.execution.container import (
    ContainerEnvironment,
    build_container_argv,
    forwarded_env_names,
)
from claude_code_core.execution.native import (
    PI_REFUSAL,
    NativeEnvironment,
    claude_sandbox_settings,
    rewrite_codex_argv,
)
from claude_code_core.execution.ssh import (
    SshEnvironment,
    build_ssh_argv,
    map_workdir,
    remote_command,
)

HOME = "/home/agent"
ENV = {"HOME": HOME, "PATH": "/usr/bin:/bin", "ANTHROPIC_API_KEY": "sk-secret"}
CLAUDE_ARGV = ("claude", "-p", "--output-format", "stream-json", "--input-format", "stream-json")
CODEX_ARGV = ("codex", "exec", "--json", "--skip-git-repo-check", "--cd", "/work/repo", "-")


def launch(argv: tuple[str, ...] = CLAUDE_ARGV, cwd: str = "/work/repo", **env: str) -> Launch:
    return Launch(argv=argv, env={**ENV, **env}, cwd=cwd)


# ---------------------------------------------------------------- config


class TestConfig:
    def test_default_is_host_only(self) -> None:
        config = ExecutionConfig.from_env({})
        assert config.default_mode == "host"
        assert config.allowed_modes == ("host",)
        assert config.error is None

    def test_default_always_allowed_and_first(self) -> None:
        config = ExecutionConfig.from_env(
            {"CCDB_EXECUTION_MODE": "bwrap", "CCDB_EXECUTION_ALLOWED_MODES": "host, native,bwrap"}
        )
        assert config.allowed_modes == ("bwrap", "host", "native")

    def test_unknown_default_mode_is_an_error_not_host(self) -> None:
        config = ExecutionConfig.from_env({"CCDB_EXECUTION_MODE": "bwarp"})
        assert config.error is not None and "bwarp" in config.error

    def test_unknown_allowed_mode_is_an_error(self) -> None:
        config = ExecutionConfig.from_env({"CCDB_EXECUTION_ALLOWED_MODES": "host,root"})
        assert config.error is not None and "root" in config.error

    def test_paths_must_be_absolute(self) -> None:
        config = ExecutionConfig.from_env({"CCDB_BWRAP_RW_PATHS": "relative/dir:/abs/dir"})
        assert config.error is not None and "relative/dir" in config.error
        assert config.bwrap.rw_paths == ("/abs/dir",)

    def test_bad_boolean_is_an_error(self) -> None:
        config = ExecutionConfig.from_env({"CCDB_BWRAP_UNSHARE_NET": "maybe"})
        assert config.error is not None

    def test_native_json_must_be_an_object(self) -> None:
        assert ExecutionConfig.from_env({"CCDB_NATIVE_CLAUDE_SANDBOX_JSON": "[1]"}).error
        assert ExecutionConfig.from_env({"CCDB_NATIVE_CLAUDE_SANDBOX_JSON": "{"}).error

    def test_ssh_settings(self) -> None:
        config = ExecutionConfig.from_env(
            {
                "CCDB_SSH_HOST": "agent@vm",
                "CCDB_SSH_OPTIONS": "-p 2222 -i '/keys/my key'",
                "CCDB_SSH_WORKDIR_MAP": "/work=/srv/work,/work/repo=/srv/repo",
                "CCDB_SSH_ENV": "FOO, BAR_2",
            }
        )
        assert config.error is None
        assert config.ssh.options == ("-p", "2222", "-i", "/keys/my key")
        # Longest prefix first.
        assert config.ssh.workdir_map[0] == ("/work/repo", "/srv/repo")
        assert config.ssh.forward_env == ("FOO", "BAR_2")

    def test_ssh_env_names_validated(self) -> None:
        assert ExecutionConfig.from_env({"CCDB_SSH_ENV": "A;rm -rf /"}).error

    def test_bad_workdir_map(self) -> None:
        assert ExecutionConfig.from_env({"CCDB_SSH_WORKDIR_MAP": "nope"}).error


# ---------------------------------------------------------------- allowlist


class TestResolveMode:
    def test_none_means_default(self) -> None:
        assert resolve_mode(None, ExecutionConfig.from_env({})) == "host"

    def test_requested_mode_outside_allowlist_is_refused(self) -> None:
        with pytest.raises(ExecutionRefusedError, match="not allowed"):
            resolve_mode("bwrap", ExecutionConfig.from_env({}))

    def test_config_error_refuses_every_mode(self) -> None:
        with pytest.raises(ExecutionRefusedError, match="misconfigured"):
            resolve_mode(None, ExecutionConfig.from_env({"CCDB_EXECUTION_MODE": "nope"}))


class TestPrepareLaunch:
    async def test_host_returns_inputs_unchanged_without_checks(self) -> None:
        result = await prepare_launch(
            backend="claude",
            requested_mode=None,
            argv=list(CLAUDE_ARGV),
            env=ENV,
            cwd="/work/repo",
            config=ExecutionConfig.from_env({}),
        )
        assert result.argv == CLAUDE_ARGV
        assert result.env == ENV
        assert result.cwd == "/work/repo"
        assert result.mode == "host"

    async def test_preflight_problem_raises_and_does_not_transform(self) -> None:
        config = ExecutionConfig.from_env({"CCDB_EXECUTION_MODE": "native"})
        with pytest.raises(ExecutionRefusedError, match="no native sandbox"):
            await prepare_launch(
                backend="pi",
                requested_mode=None,
                argv=["pi", "--mode", "json"],
                env=ENV,
                cwd="/work",
                config=config,
            )

    async def test_transformed_launch_reports_mode(self) -> None:
        config = ExecutionConfig.from_env({"CCDB_EXECUTION_MODE": "native"})
        result = await prepare_launch(
            backend="codex",
            requested_mode=None,
            argv=list(CODEX_ARGV),
            env=ENV,
            cwd="/work/repo",
            config=config,
        )
        assert result.mode == "native"
        assert result.argv[:4] == ("codex", "exec", "--sandbox", "workspace-write")


# ---------------------------------------------------------------- state dirs


class TestAgentStatePaths:
    def test_claude_default(self) -> None:
        assert agent_state_paths("claude", ENV) == (f"{HOME}/.claude", f"{HOME}/.claude.json")

    def test_claude_config_dir_wins(self) -> None:
        env = {**ENV, "CLAUDE_CONFIG_DIR": "/pool/profile-a"}
        assert agent_state_paths("claude", env) == ("/pool/profile-a",)

    def test_codex_and_local(self) -> None:
        assert agent_state_paths("codex", ENV) == (f"{HOME}/.codex",)
        assert agent_state_paths("local", {**ENV, "CODEX_HOME": "/x/home"}) == ("/x/home",)

    def test_pi(self) -> None:
        assert agent_state_paths("pi", ENV) == (f"{HOME}/.pi",)


# ---------------------------------------------------------------- native


class TestNative:
    def test_claude_gets_sandbox_settings(self) -> None:
        env = NativeEnvironment(NativeSettings())
        out = env.transform("claude", launch())
        assert out.argv[: len(CLAUDE_ARGV)] == CLAUDE_ARGV
        assert out.argv[-2] == "--settings"
        sandbox = json.loads(out.argv[-1])["sandbox"]
        assert sandbox["enabled"] is True
        assert sandbox["allowUnsandboxedCommands"] is False
        assert sandbox["failIfUnavailable"] is True

    def test_claude_operator_overrides_merge(self) -> None:
        merged = claude_sandbox_settings(
            NativeSettings(claude_sandbox_overrides={"network": {"allowedDomains": ["x.com"]}})
        )
        assert merged["sandbox"]["network"] == {"allowedDomains": ["x.com"]}
        assert merged["sandbox"]["enabled"] is True

    async def test_claude_preflight_needs_bwrap_and_socat_on_linux(self) -> None:
        env = NativeEnvironment(NativeSettings())
        with (
            patch("claude_code_core.execution.native.sys.platform", "linux"),
            patch("claude_code_core.execution.native.resolve_binary", return_value=None),
        ):
            problem = await env.preflight("claude", launch())
        assert problem is not None and "bwrap and socat" in problem

    async def test_claude_preflight_refuses_windows(self) -> None:
        env = NativeEnvironment(NativeSettings())
        with patch("claude_code_core.execution.native.sys.platform", "win32"):
            assert "Linux and macOS" in (await env.preflight("claude", launch()) or "")

    def test_codex_sandbox_inserted_after_exec(self) -> None:
        out = NativeEnvironment(NativeSettings()).transform("codex", launch(CODEX_ARGV))
        assert out.argv == ("codex", "exec", "--sandbox", "workspace-write", *CODEX_ARGV[2:])

    def test_codex_resume_keeps_sandbox_before_resume(self) -> None:
        argv = ("codex", "exec", "resume", "--json", "abc-123", "-")
        out = rewrite_codex_argv(argv, "workspace-write")
        assert out == (
            "codex",
            "exec",
            "--sandbox",
            "workspace-write",
            "resume",
            "--json",
            "abc-123",
            "-",
        )

    def test_codex_bypass_and_existing_sandbox_are_replaced(self) -> None:
        argv = (
            "codex",
            "exec",
            "--sandbox",
            "danger-full-access",
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            "-",
        )
        assert rewrite_codex_argv(argv, "workspace-write") == (
            "codex",
            "exec",
            "--sandbox",
            "workspace-write",
            "--json",
            "-",
        )

    def test_codex_read_only_override_is_kept(self) -> None:
        out = NativeEnvironment(NativeSettings()).transform(
            "codex", launch(CODEX_ARGV, CCDB_CODEX_SANDBOX_OVERRIDE="read-only")
        )
        assert out.argv[2:4] == ("--sandbox", "read-only")

    async def test_codex_danger_override_contradicts_native(self) -> None:
        problem = await NativeEnvironment(NativeSettings()).preflight(
            "codex", launch(CODEX_ARGV, CCDB_CODEX_SANDBOX_OVERRIDE="danger-full-access")
        )
        assert problem is not None and "contradicts" in problem

    async def test_local_backend_behaves_like_codex(self) -> None:
        env = NativeEnvironment(NativeSettings())
        assert await env.preflight("local", launch(CODEX_ARGV)) is None
        assert env.transform("local", launch(CODEX_ARGV)).argv[2:4] == (
            "--sandbox",
            "workspace-write",
        )

    async def test_pi_refuses_with_one_sentence(self) -> None:
        problem = await NativeEnvironment(NativeSettings()).preflight("pi", launch(("pi",)))
        assert problem == PI_REFUSAL
        assert problem.count(". ") == 0


# ---------------------------------------------------------------- bwrap


def _bwrap(settings: BwrapSettings, lch: Launch, existing: set[str], dirs: set[str]) -> list[str]:
    return list(
        build_bwrap_argv(
            settings,
            "claude",
            lch,
            relay_dotenv="/srv/relay/.env",
            exists=lambda p: p in existing,
            isdir=lambda p: p in dirs,
            realpath=lambda p: p,
        )
    )


class TestBwrapArgv:
    EXISTING = {
        "/work/repo",
        f"{HOME}/.claude",
        f"{HOME}/.claude.json",
        f"{HOME}/.ssh",
        "/run/docker.sock",
        "/srv/relay/.env",
    }
    DIRS = {"/work/repo", f"{HOME}/.claude", f"{HOME}/.ssh"}

    def test_structure(self) -> None:
        argv = _bwrap(BwrapSettings(), launch(), self.EXISTING, self.DIRS)
        assert argv[0] == "bwrap"
        for flag in ("--die-with-parent", "--unshare-pid", "--new-session"):
            assert flag in argv
        assert "--unshare-net" not in argv
        assert argv[argv.index("--ro-bind") : argv.index("--ro-bind") + 3] == [
            "--ro-bind",
            "/",
            "/",
        ]
        sep = argv.index("--")
        assert argv[sep - 2 : sep] == ["--chdir", "/work/repo"]
        assert tuple(argv[sep + 1 :]) == CLAUDE_ARGV

    def test_writable_paths_and_order(self) -> None:
        argv = _bwrap(BwrapSettings(), launch(), self.EXISTING, self.DIRS)
        joined = " ".join(argv)
        assert "--bind /work/repo /work/repo" in joined
        assert f"--bind {HOME}/.claude {HOME}/.claude" in joined
        assert f"--bind {HOME}/.claude.json {HOME}/.claude.json" in joined
        # /tmp tmpfs precedes binds; hides come after binds.
        assert argv.index("/tmp") < argv.index("--bind")
        last_bind = max(i for i, a in enumerate(argv) if a == "--bind")
        assert argv.index(f"{HOME}/.ssh") > last_bind

    def test_hides_dirs_with_tmpfs_and_files_with_dev_null(self) -> None:
        joined = " ".join(_bwrap(BwrapSettings(), launch(), self.EXISTING, self.DIRS))
        assert f"--tmpfs {HOME}/.ssh" in joined
        assert "--ro-bind /dev/null /run/docker.sock" in joined
        assert "--ro-bind /dev/null /srv/relay/.env" in joined
        # Missing paths are skipped rather than failing bwrap.
        assert "/var/run/docker.sock" not in joined

    def test_cwd_under_tmp_is_bound_after_tmpfs(self) -> None:
        lch = launch(cwd="/tmp/job")
        argv = _bwrap(BwrapSettings(), lch, {"/tmp/job"}, {"/tmp/job"})
        assert argv.index("--tmpfs") < argv.index("--bind")
        assert "--bind /tmp/job /tmp/job" in " ".join(argv)

    def test_unshare_net_and_extra_paths(self) -> None:
        settings = BwrapSettings(
            unshare_net=True, rw_paths=("/data/cache",), hide_paths=("/etc/secret",)
        )
        argv = _bwrap(settings, launch(), {"/data/cache", "/etc/secret", "/work/repo"}, set())
        joined = " ".join(argv)
        assert "--unshare-net" in argv
        assert "--bind /data/cache /data/cache" in joined
        assert "--ro-bind /dev/null /etc/secret" in joined

    def test_hide_defaults_can_be_disabled(self) -> None:
        argv = _bwrap(BwrapSettings(hide_defaults=False), launch(), self.EXISTING, self.DIRS)
        assert f"{HOME}/.ssh" not in argv

    def test_codex_state_dir(self) -> None:
        argv = build_bwrap_argv(
            BwrapSettings(),
            "codex",
            launch(CODEX_ARGV, CODEX_HOME="/pool/codex-b"),
            relay_dotenv=None,
            exists=lambda p: True,
            isdir=lambda p: True,
            realpath=lambda p: p,
        )
        assert "--bind /pool/codex-b /pool/codex-b" in " ".join(argv)

    def test_symlinked_hide_paths_are_resolved_and_deduplicated(self) -> None:
        argv = build_bwrap_argv(
            BwrapSettings(),
            "claude",
            launch(),
            relay_dotenv=None,
            exists=lambda p: p == "/run/docker.sock",
            isdir=lambda p: False,
            realpath=lambda p: p.replace("/var/run/", "/run/"),
        )
        assert argv.count("/run/docker.sock") == 1
        assert "/var/run/docker.sock" not in argv

    def test_default_hide_paths(self) -> None:
        assert default_hide_paths(ENV, "/r/.env") == (
            "/r/.env",
            f"{HOME}/.ssh",
            "/var/run/docker.sock",
            "/run/docker.sock",
        )

    def test_find_relay_dotenv(self, tmp_path) -> None:
        (tmp_path / ".env").write_text("X=1")
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        assert find_relay_dotenv(str(nested)) == str(tmp_path / ".env")


class TestBwrapPreflight:
    async def test_missing_binary(self, tmp_path) -> None:
        env = BwrapEnvironment(BwrapSettings(binary="definitely-not-bwrap"))
        problem = await env.preflight("claude", launch(cwd=str(tmp_path)))
        assert problem is not None and "bubblewrap" in problem

    async def test_missing_cwd(self, tmp_path) -> None:
        env = BwrapEnvironment(BwrapSettings(binary="/bin/true"))
        problem = await env.preflight("claude", launch(cwd=str(tmp_path / "gone")))
        assert problem is not None and "does not exist" in problem

    async def test_failing_probe_names_userns(self, tmp_path) -> None:
        env = BwrapEnvironment(BwrapSettings(binary="/bin/false"))
        lch = launch(cwd=str(tmp_path), HOME=str(tmp_path))
        problem = await env.preflight("claude", lch)
        assert problem is not None and "user namespaces" in problem


# ---------------------------------------------------------------- container


class TestContainer:
    SETTINGS = ContainerSettings(image="ccdb-agent:latest", extra_args=("--network", "host"))

    def test_argv(self) -> None:
        argv = build_container_argv(
            self.SETTINGS,
            "claude",
            launch(argv=("/usr/local/bin/claude", "-p")),
            uid=1000,
            gid=1000,
            exists=lambda p: True,
        )
        assert argv[:7] == ("docker", "run", "--rm", "-i", "--init", "--workdir", "/work/repo")
        joined = " ".join(argv)
        assert "--user 1000:1000" in joined
        assert "--volume /work/repo:/work/repo" in joined
        assert f"--volume {HOME}/.claude:{HOME}/.claude" in joined
        assert "--network host ccdb-agent:latest claude -p" in joined

    def test_env_is_passed_by_name_never_value(self) -> None:
        argv = build_container_argv(
            self.SETTINGS, "claude", launch(), uid=1, gid=1, exists=lambda p: True
        )
        assert "sk-secret" not in " ".join(argv)
        assert "ANTHROPIC_API_KEY" in argv
        assert "PATH" not in argv

    def test_forwarded_env_names_filters_host_only(self) -> None:
        names = forwarded_env_names(
            {
                "PATH": "x",
                "LD_PRELOAD": "y",
                "SSH_AUTH_SOCK": "z",
                "HOME": "h",
                "OPENAI_API_KEY": "k",
            }
        )
        assert names == ("HOME", "OPENAI_API_KEY")

    async def test_preflight_needs_image(self) -> None:
        problem = await ContainerEnvironment(ContainerSettings()).preflight("claude", launch())
        assert problem is not None and "CCDB_CONTAINER_IMAGE" in problem

    async def test_preflight_missing_runtime(self) -> None:
        env = ContainerEnvironment(ContainerSettings(image="x", runtime="no-such-runtime"))
        problem = await env.preflight("claude", launch())
        assert problem is not None and "no-such-runtime" in problem

    async def test_preflight_missing_image(self, tmp_path) -> None:
        env = ContainerEnvironment(ContainerSettings(image="x", runtime="/bin/false"))
        lch = launch(cwd=str(tmp_path), HOME=str(tmp_path))
        problem = await env.preflight("claude", lch)
        assert problem is not None and "not available" in problem

    async def test_preflight_rejects_colon_paths(self, tmp_path) -> None:
        bad = tmp_path / "a:b"
        bad.mkdir()
        env = ContainerEnvironment(ContainerSettings(image="x", runtime="/bin/true"))
        problem = await env.preflight("claude", launch(cwd=str(bad), HOME=str(tmp_path)))
        assert problem is not None and "cannot be mounted" in problem


# ---------------------------------------------------------------- ssh


class TestSsh:
    SETTINGS = SshSettings(host="agent@vm", options=("-p", "2222"))

    def test_argv_shape(self) -> None:
        argv = build_ssh_argv(self.SETTINGS, launch(), "/usr/bin/ssh")
        assert argv[:5] == ("/usr/bin/ssh", "-T", "-o", "BatchMode=yes", "-p")
        assert argv[argv.index("--") + 1] == "agent@vm"
        assert len(argv) == argv.index("--") + 3  # one remote command string

    def test_remote_command_round_trips_through_a_shell(self) -> None:
        cmd = remote_command(self.SETTINGS, launch())
        words = shlex.split(cmd)
        assert words[:3] == ["cd", "/work/repo", "&&"]
        assert words[3] == "exec"
        assert tuple(words[-len(CLAUDE_ARGV) :]) == CLAUDE_ARGV

    @pytest.mark.parametrize(
        "evil",
        [
            "$(touch /tmp/pwned)",
            "`id`",
            "a; rm -rf ~",
            "x' ; echo 'y",
            'x" && echo "y',
            "line\nbreak",
            "--option-looking",
        ],
    )
    def test_injection_in_args_cwd_and_env_stays_literal(self, evil: str) -> None:
        settings = replace(self.SETTINGS, forward_env=("EVIL",))
        lch = Launch(
            argv=("claude", "--append-system-prompt", evil),
            env={**ENV, "EVIL": evil, "DISCORD_THREAD_ID": "42"},
            cwd=f"/work/{evil.replace('/', '_')}",
        )
        cmd = remote_command(settings, lch)
        words = shlex.split(cmd)
        # Parsed by a POSIX shell, every value is exactly one literal word.
        assert words[0] == "cd" and words[1] == lch.cwd
        assert f"EVIL={evil}" in words
        assert words[-1] == evil
        assert words[-3:-1] == ["claude", "--append-system-prompt"]

    def test_secrets_not_forwarded_by_default(self) -> None:
        cmd = remote_command(self.SETTINGS, launch(DISCORD_THREAD_ID="7", CCDB_API_SECRET="s3"))
        assert "sk-secret" not in cmd
        assert "s3" not in cmd
        assert "DISCORD_THREAD_ID=7" in shlex.split(cmd)

    def test_remote_path_and_workdir_mapping(self) -> None:
        settings = replace(
            self.SETTINGS,
            remote_path="/opt/agent/bin:/usr/bin",
            workdir_map=(("/work", "/srv/work"),),
        )
        cmd = remote_command(settings, launch(CODEX_ARGV))
        words = shlex.split(cmd)
        assert words[1] == "/srv/work/repo"
        assert "PATH=/opt/agent/bin:/usr/bin" in words
        # Codex's --cd follows the mapping too.
        assert words[words.index("--cd") + 1] == "/srv/work/repo"

    def test_map_workdir(self) -> None:
        settings = SshSettings(workdir_map=(("/work/repo", "/r"), ("/work", "/w")))
        assert map_workdir(settings, "/work/repo/sub") == "/r/sub"
        assert map_workdir(settings, "/work/other") == "/w/other"
        assert map_workdir(settings, "/workshop") == "/workshop"
        assert map_workdir(settings, "/work") == "/w"

    def test_cli_path_becomes_basename(self) -> None:
        cmd = remote_command(self.SETTINGS, launch(("/home/me/.local/bin/claude", "-p")))
        assert shlex.split(cmd)[-2:] == ["claude", "-p"]

    async def test_preflight_requires_host(self) -> None:
        problem = await SshEnvironment(SshSettings()).preflight("claude", launch())
        assert problem is not None and "CCDB_SSH_HOST" in problem

    async def test_preflight_rejects_option_like_host(self) -> None:
        problem = await SshEnvironment(SshSettings(host="-oProxyCommand=x")).preflight(
            "claude", launch()
        )
        assert problem is not None and "not a host name" in problem

    async def test_preflight_unreachable_host(self) -> None:
        env = SshEnvironment(SshSettings(host="vm", binary="/bin/sh"))
        with patch(
            "claude_code_core.execution.ssh.run_probe",
            AsyncMock(return_value=(255, "ssh: connect to host vm port 22: Connection refused")),
        ):
            problem = await env.preflight("claude", launch())
        assert problem is not None and "Connection refused" in problem

    async def test_preflight_probe_success_is_cached(self) -> None:
        env = SshEnvironment(SshSettings(host="cached-vm", binary="/bin/sh"))
        probe = AsyncMock(return_value=(0, ""))
        with patch("claude_code_core.execution.ssh.run_probe", probe):
            assert await env.preflight("claude", launch()) is None
            assert await env.preflight("claude", launch()) is None
        assert probe.await_count == 1

    async def test_probe_can_be_disabled(self) -> None:
        env = SshEnvironment(SshSettings(host="vm", binary="/bin/sh", probe=False))
        with patch("claude_code_core.execution.ssh.run_probe", AsyncMock()) as probe:
            assert await env.preflight("claude", launch()) is None
        probe.assert_not_awaited()


class TestBwrapSshAgent:
    def test_agent_socket_hidden_and_variable_dropped(self) -> None:
        lch = launch(SSH_AUTH_SOCK="/run/user/1000/agent.sock")
        argv = build_bwrap_argv(
            BwrapSettings(),
            "claude",
            lch,
            relay_dotenv=None,
            exists=lambda p: True,
            isdir=lambda p: False,
            realpath=lambda p: p,
        )
        assert "--ro-bind /dev/null /run/user/1000/agent.sock" in " ".join(argv)
        out = BwrapEnvironment(BwrapSettings()).transform("claude", lch)
        assert "SSH_AUTH_SOCK" not in out.env

    def test_agent_kept_when_defaults_disabled(self) -> None:
        env = BwrapEnvironment(BwrapSettings(hide_defaults=False))
        out = env.transform("claude", launch(SSH_AUTH_SOCK="/run/a.sock"))
        assert out.env["SSH_AUTH_SOCK"] == "/run/a.sock"
