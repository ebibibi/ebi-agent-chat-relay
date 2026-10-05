# Execution environments

An **execution environment** decides where and how a CLI backend's subprocess runs. The relay
spawns Claude Code, Codex, the `local` backend (Codex against your own model) and pi as
subprocesses. By default they run directly on the host as the relay's own OS user, which is what
every earlier version did. An execution environment sits between the runner and that subprocess:
it checks that it can run the launch (**preflight**), then rewrites the launch (argv, environment,
working directory) before the process starts.

| Mode | Where the agent runs | Boundary |
|---|---|---|
| `host` (default) | On the host, as the relay's user | None at the OS level. Identical to earlier versions |
| `native` | On the host, inside the agent's own sandbox | Claude Code: its sandbox, passed with `--settings`. Codex: `--sandbox workspace-write`. pi refuses (it has no sandbox) |
| `bwrap` | On the host, inside bubblewrap | Read-only host, writable working directory and agent state, private `/tmp`, no `sudo`, configurable hidden paths |
| `container` | In a container image you provide | Whatever the image and runtime give. Only the working directory and agent state are mounted |
| `ssh` | On a remote host you provide | A different machine. The relay only streams stdin and stdout |

The AG-UI backend talks to a remote agent over HTTP and spawns nothing, so execution environments
do not apply to it.

## Selection rules

- The **operator** sets the default mode and the allowlist in the deployment environment
  (`CCDB_EXECUTION_MODE`, `CCDB_EXECUTION_ALLOWED_MODES`). Nothing in Discord or Teams can change
  either value.
- A user can choose a mode for one thread with `/sandbox`, but **only from the allowlist**. If you
  ask for a mode that is not allowed, the command refuses and stores nothing. The runner checks the
  allowlist again at spawn time, so a custom Cog that sets `runner.execution_mode` directly cannot
  get around it either.
- If a thread chose a mode that the operator later removes from the allowlist, the thread goes
  back to the default. The relay does not keep honouring the old choice.
- When preflight fails (for example, `bwrap` is missing, the image is not there, or the host does
  not answer), the turn fails with one sentence in the thread and no process starts.
- A **malformed configuration fails closed.** If you set `CCDB_EXECUTION_MODE=bwarp` (a typo),
  every turn refuses with a message that names the bad value. The relay never falls back to
  `host`, because that is the opposite of what the operator asked for.
- The completion notice shows which environment served the turn as an `Environment` field. On a
  deployment that allows only `host`, nobody can choose a different mode, so the field is left out
  and existing deployments look exactly as before.

```text
/sandbox                 # show the default, this thread's mode, and the allowed modes
/sandbox mode:bwrap      # this thread only, from the next turn (refused if not allowed)
/sandbox mode:default    # drop the thread's choice
```

## Configuration

All variables are read from the relay's environment when each subprocess is spawned. Path lists
are separated with `:`, the same way as `PATH`.

| Variable | Default | Meaning |
|---|---|---|
| `CCDB_EXECUTION_MODE` | `host` | Default mode for every thread |
| `CCDB_EXECUTION_ALLOWED_MODES` | the default only | Comma-separated modes that `/sandbox` may choose. The default mode is always allowed |
| `CCDB_NATIVE_CLAUDE_SANDBOX_JSON` | unset | JSON object deep-merged over the relay's Claude Code `sandbox` settings |
| `CCDB_BWRAP_BIN` | `bwrap` | Path to bubblewrap, or a name to look up on `PATH` |
| `CCDB_BWRAP_RW_PATHS` | unset | Extra paths mounted read-write (caches, toolchains) |
| `CCDB_BWRAP_HIDE_PATHS` | unset | Extra paths to hide, added to the default list |
| `CCDB_BWRAP_HIDE_DEFAULTS` | `1` | Set to `0` to stop hiding the default list |
| `CCDB_BWRAP_UNSHARE_NET` | `0` | Set to `1` to give the agent no network. Only useful for a local model |
| `CCDB_CONTAINER_RUNTIME` | `docker` | Container CLI (`docker`, `podman`, ...) |
| `CCDB_CONTAINER_IMAGE` | unset | Image to run. Required for `container` |
| `CCDB_CONTAINER_ARGS` | unset | Extra `run` arguments, split like shell words (for example, `--network host`) |
| `CCDB_CONTAINER_RW_PATHS` | unset | Extra host paths mounted at the same path inside the container |
| `CCDB_SSH_HOST` | unset | `[user@]host` to run on. Required for `ssh` |
| `CCDB_SSH_BIN` | `ssh` | ssh client |
| `CCDB_SSH_OPTIONS` | unset | Extra ssh options, split like shell words (for example, `-p 2222 -i /keys/agent`) |
| `CCDB_SSH_WORKDIR_MAP` | unset | `/local/prefix=/remote/prefix,...`. The longest matching prefix wins. Without a match, the remote path is the same as the local one |
| `CCDB_SSH_ENV` | unset | Comma-separated names of extra variables to forward |
| `CCDB_SSH_REMOTE_PATH` | unset | `PATH` for the remote command (non-interactive ssh shells often leave out `~/.local/bin`) |
| `CCDB_SSH_PROBE` | `1` | Set to `0` to skip the reachability check |

## `host`

This is the default. Nothing is checked and nothing is rewritten: argv, environment and working
directory reach `create_subprocess_exec` exactly as they did before this layer existed.
`child_env.py` still strips the relay's own credentials from the environment. That is an
environment boundary, not an OS one. See [SECURITY.md](SECURITY.md#environment-isolation).

## `native`

**Claude Code.** The relay appends `--settings '{"sandbox":{...}}'` with these settings:

```json
{"enabled": true, "autoAllowBashIfSandboxed": true,
 "allowUnsandboxedCommands": false, "failIfUnavailable": true}
```

`failIfUnavailable` makes the CLI refuse to start rather than run its shell unconfined.
`allowUnsandboxedCommands: false` removes the per-command escape hatch that lets the model ask to
run outside the sandbox. To add network or filesystem rules (`network.allowedDomains`,
`filesystem.allowWrite`, and so on), put them in `CCDB_NATIVE_CLAUDE_SANDBOX_JSON`. That value is
deep-merged over the defaults.

On Linux, Claude Code's sandbox runtime needs `bwrap` and `socat` on the child's `PATH`. Preflight
checks for both. On macOS the sandbox is built in. Windows is refused.

Two limits to know about:

- Claude Code's sandbox confines the **Bash tool and everything it starts**. The file-edit tools
  are governed by the permission mode instead. With `--dangerously-skip-permissions`, the Edit and
  Write tools can still write outside the working directory. Use `bwrap` if you need one OS
  boundary around everything the agent does.
- Network is not restricted by these defaults. Measured with `--dangerously-skip-permissions`,
  `curl https://example.com` from the sandboxed shell returned `200`. To restrict it, add Claude
  Code's `sandbox.network` settings through `CCDB_NATIVE_CLAUDE_SANDBOX_JSON`.

**Codex** (and the `local` backend) gets `--sandbox workspace-write`, placed right after `exec` so
that `exec resume` accepts it. Precedence with `CCDB_CODEX_SANDBOX_OVERRIDE`:

| `CCDB_CODEX_SANDBOX_OVERRIDE` | Result in `native` |
|---|---|
| unset or `workspace-write` | `--sandbox workspace-write` |
| `read-only` | `--sandbox read-only` (stricter, so it is kept) |
| `danger-full-access` | refused: that value contradicts the mode, and the thread would show `native` while running unsandboxed |

`native` also removes `--dangerously-bypass-approvals-and-sandbox`, which the relay adds when
`dangerously_skip_permissions` is on. `codex exec` has no approval loop, so the only thing that flag
would turn off is the sandbox. Codex's sandbox turns off network access for commands by default.

**pi** refuses with one sentence. pi has no sandbox of its own (ADR-0007), so a `native` pi thread
would make a false claim. The `CCDB_PI_ALLOW_UNSANDBOXED` opt-in is still required in every mode.

## `bwrap`

The relay runs the CLI under [bubblewrap](https://github.com/containers/bubblewrap):

```text
bwrap --die-with-parent --unshare-pid --new-session [--unshare-net]
      --ro-bind / / --dev /dev --proc /proc --tmpfs /tmp
      --bind <working dir> <working dir>
      --bind <agent state> <agent state> ...      # see below
      --bind <CCDB_BWRAP_RW_PATHS> ...
      --tmpfs <hidden dir> | --ro-bind /dev/null <hidden file> ...
      --chdir <working dir> -- <the CLI argv>
```

- **Read-write paths:** the working directory, plus the agent's own state, taken from the final
  child environment. For Claude Code, that is `CLAUDE_CONFIG_DIR`, or `~/.claude` and
  `~/.claude.json`. For Codex and `local`, it is `CODEX_HOME` or `~/.codex`. For pi, it is
  `PI_CODING_AGENT_DIR` or `~/.pi`. Anything injected into the environment (an account pool's
  profile directory, the local backend's generated home) is what gets mounted. Missing state
  directories are created first.
- **Hidden by default:** the relay's own `.env` (found the way the relay loads it: the first `.env`
  at or above the relay's working directory), `~/.ssh`, `/var/run/docker.sock` and
  `/run/docker.sock`. An ssh-agent is hidden too: its socket is hidden and `SSH_AUTH_SOCK` is
  removed from the child environment. Hiding the Docker socket matters even though the filesystem is read-only,
  because connecting to a socket is not a write. Hidden paths are mounted last, so a secret inside
  the working directory is still hidden. Symlinks are resolved before mounting.
- **Mount order matters:** `/tmp` is a private tmpfs, and the binds come after it, so a working
  directory under `/tmp` stays the real one.
- **No privilege escalation:** bubblewrap always sets `no_new_privs`. `sudo` fails with
  *"The "no new privileges" flag is set"*, and other setuid binaries cannot gain privileges.
- **Network** is shared. The agent has to reach its model API, and the relay's REST API on
  `127.0.0.1`.
- **Preflight** checks for the binary, the working directory and the state directories, then
  makes one test sandbox. A host with unprivileged user namespaces disabled (for example, by
  AppArmor's `apparmor_restrict_unprivileged_userns`) is reported in one sentence. Success is
  cached for the life of the process.

**Codex inside bwrap** still applies its own `--sandbox` policy unless `dangerously_skip_permissions`
is on. The nested sandbox works on hosts that allow nested user namespaces. Codex's default
`read-only` policy then blocks writes even inside the working directory. Use
`dangerously_skip_permissions` (bwrap becomes the only boundary) or set
`CCDB_CODEX_SANDBOX_OVERRIDE=workspace-write`.

**What breaks when `$HOME` is read-only:** a tool or MCP server that writes under `$HOME` outside
the agent's state (`~/.cache`, `~/.npm`, `~/.config/gh`, `~/.azure`, ...) fails. Add those paths to
`CCDB_BWRAP_RW_PATHS`. Hiding `~/.ssh` also stops `git push` over SSH. If agents should push, use
HTTPS credentials, or set `CCDB_BWRAP_HIDE_DEFAULTS=0` and list only the paths you want hidden in
`CCDB_BWRAP_HIDE_PATHS`.

**Interrupts:** bubblewrap does not forward `SIGINT`. The Stop button ends the sandbox, and
`--die-with-parent` kills the CLI, instead of the CLI stopping gracefully. Both CLIs write their
session logs as they go, so the thread can still be resumed.

## `container`

```text
docker run --rm -i --init --workdir <dir> --user <uid>:<gid>
           --volume <dir>:<dir> --volume <agent state>:<agent state> ...
           --env NAME ... <CCDB_CONTAINER_ARGS> <image> claude <args>
```

- Paths are mounted at the **same path** inside the container, so every path in the argv
  (`--cd`, attachment marker files, `CLAUDE_CONFIG_DIR`) stays valid.
- The container runs as the relay's uid and gid, so files it writes belong to the relay.
- Environment variables are passed **by name** (`--env NAME`). Their values come from the runtime's
  own environment and never appear in the process list. Host-specific variables (`PATH`, `LD_*`,
  `SSH_AUTH_SOCK`, `XDG_RUNTIME_DIR`, ...) are not forwarded.
- The CLI is called by its base name, so the image's `PATH` decides which binary runs.
- `CCDB_API_URL` points at `127.0.0.1`. For the agent to call the relay's REST API, add
  `CCDB_CONTAINER_ARGS="--network host"`.
- Preflight checks the runtime, that the image exists locally (`image inspect`), and that the
  mounted paths contain no `:` or `,`.
- With rootless podman, add `--userns=keep-id` to `CCDB_CONTAINER_ARGS`.

A sample image lives at [`deploy/agent-container/Dockerfile`](../deploy/agent-container/Dockerfile).
It installs Claude Code and Codex on `node:22-bookworm-slim`:

```bash
docker build -t ccdb-agent:latest deploy/agent-container
CCDB_EXECUTION_MODE=container CCDB_CONTAINER_IMAGE=ccdb-agent:latest
```

## `ssh`

```text
ssh -T -o BatchMode=yes <CCDB_SSH_OPTIONS> -- <host> \
    'cd <remote dir> && exec env DISCORD_THREAD_ID=... <cli> <args>'
```

- Locally this is still `create_subprocess_exec`, with no local shell. The remote side is a shell,
  because that is how ssh delivers a command, so the remote command is built only from
  `shlex.quote`d words: the directory, every `NAME=value` and every argument. The host comes after
  `--`, so it cannot be read as an option, and a host that starts with `-` is refused.
- The working directory is translated with `CCDB_SSH_WORKDIR_MAP`, and an argv element equal to the
  local working directory (Codex's `--cd`) is translated with it. The remote directory has to exist
  already. Mount or sync the worktree there.
- **Environment:** values given to `env` on the remote command line show up in process listings on
  both machines, so only `DISCORD_THREAD_ID` and `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS` are
  forwarded, plus the names in `CCDB_SSH_ENV`. The remote host has to be logged in to the agent's
  provider itself (`claude login`, `codex login`). `CCDB_API_URL` and `CCDB_API_SECRET` are not
  forwarded, because the relay's API is on the relay's loopback.
- **Preflight** connects with `ConnectTimeout=10` and runs `<cli> --version` in the remote
  directory. That checks, in one round trip, that the host answers, that the directory exists and
  that the CLI is on `PATH`. Success is cached for five minutes.
- **Limitations:** session data (Claude's `~/.claude/projects`, Codex rollouts) lives on the remote
  host. Features that read it locally (Codex resume recovery, cross-backend handoff, transcript
  search) cannot see it. The Stop button ends the local ssh client. The remote CLI is not
  interrupted gracefully: with no terminal (`-T`), it stops only when it notices its input has
  closed or its output pipe is broken.

## Verifying a deployment

Run one turn in a scratch thread and ask the agent to run `touch ~/should_fail`, `sudo -n true`
and `touch ./inside_ok`. Under `native` and `bwrap`, the first two fail (`Read-only file system`,
`"no new privileges" flag is set`) and the third succeeds. The completion notice shows the
`Environment` field. The pull request that added this layer records those runs for Claude Code and
Codex on Linux with bubblewrap 0.6.1, Claude Code 2.1.289 and codex-cli 0.160.0.

## Design

See [ADR-0008](adr/0008-let-the-operator-choose-the-execution-environment.md).
