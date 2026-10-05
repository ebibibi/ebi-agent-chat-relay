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
| `bwrap` (preview) | On the host, inside bubblewrap | Read-only system, empty `$HOME` with only the working directory, agent state and CLI install bound back, protected config, private `/tmp`, no `sudo`, hidden host sockets |
| `container` (preview) | In a container image you provide | Only the working directory and agent state are mounted, with the same config and git protection as `bwrap` |
| `ssh` (preview) | On a remote host you provide | A different machine. The relay only streams stdin and stdout |

The AG-UI backend talks to a remote agent over HTTP and spawns nothing, so execution environments
do not apply to it.

> **Status: `bwrap`, `container` and `ssh` are preview.** `host` stays the default, and nothing
> changes unless an operator opts in. The isolating modes have been hardened through several
> security reviews and verified end to end, but they carry the residual risks listed in each
> section. **Recommended setup for any sandboxed deployment:** a dedicated `CLAUDE_CONFIG_DIR` /
> `CODEX_HOME` per sandboxed deployment (for example one account-pool profile per sandbox),
> never the operator's own `~/.claude` / `~/.codex`, and agents working in linked worktrees.

## Selection rules

- The **operator** sets the default mode and the allowlist in the deployment environment
  (`CCDB_EXECUTION_MODE`, `CCDB_EXECUTION_ALLOWED_MODES`). Nothing in Discord or Teams can change
  either value.
- `/sandbox` is restricted to the same users as `/skill` (`allowed_user_ids`, wired from
  `DISCORD_OWNER_ID`). Anyone else is refused, including for showing the current mode. `/backend`,
  `/model`, `/effort`, `/engine-status` and `/ollama pull|rm|use` follow the same rule.
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
  `host`, because that is the opposite of what the operator asked for. A bad value in a variable
  that belongs to one mode (`CCDB_BWRAP_*`, `CCDB_CONTAINER_*`, `CCDB_SSH_*`,
  `CCDB_NATIVE_CLAUDE_SANDBOX_JSON`) refuses only turns in that mode, and `/sandbox` refuses to
  select it, so a typo for an unused mode does not stop `host` threads.
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
| `CCDB_BWRAP_UNSHARE_NET` | `0` | Set to `1` to give the agent no network. Breaks every API-backed agent (Claude Code, Codex against a cloud model); only for a model reachable without the network |
| `CCDB_BWRAP_RO_PATHS` | unset | Extra paths bound read-only: toolchains under `$HOME` the agent's work needs, or a hook script inside the state directory |
| `CCDB_BWRAP_PROTECT_CONFIG` | `1` | Set to `0` to stop re-binding agent configuration and `.git/hooks` / `.git/config` read-only |
| `CCDB_CONTAINER_RUNTIME` | `docker` | Container CLI (`docker`, `podman`, ...) |
| `CCDB_CONTAINER_IMAGE` | unset | Image to run. Required for `container` |
| `CCDB_CONTAINER_ARGS` | unset | Extra `run` arguments, split like shell words (for example, `--network host`) |
| `CCDB_CONTAINER_RW_PATHS` | unset | Extra host paths mounted at the same path inside the container |
| `CCDB_SSH_HOST` | unset | `[user@]host` to run on. Required for `ssh` |
| `CCDB_SSH_BIN` | `ssh` | ssh client |
| `CCDB_SSH_OPTIONS` | unset | Extra ssh options, split like shell words (for example, `-p 2222 -i /keys/agent`) |
| `CCDB_SSH_WORKDIR_MAP` | unset | `/local/prefix=/remote/prefix,...`. The longest matching prefix wins. Without a match, the remote path is the same as the local one |
| `CCDB_SSH_ENV` | unset | Comma-separated names of extra variables to send with ssh `SendEnv` (the server needs a matching `AcceptEnv`) |
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

## `bwrap` (preview)

The relay runs the CLI under [bubblewrap](https://github.com/containers/bubblewrap). The home
directory is an **allowlist**: it starts empty, and only what the agent needs is bound back.

```text
bwrap --die-with-parent --unshare-pid --unshare-ipc --unshare-uts --unshare-cgroup-try
      --new-session [--unshare-net]
      --ro-bind / / --dev /dev --proc /proc --tmpfs /tmp
      --tmpfs $HOME                                  # empty; writes here are thrown away
      --ro-bind <CLI install dirs under $HOME> ...   # found from the CLI's PATH entry
      --ro-bind ~/.gitconfig ~/.config/git ...       # commit identity
      --bind <working dir> <working dir>
      --bind <worktree's common .git dir> ...        # only when git's back-link confirms it
      --bind <agent state> ...                       # ~/.claude + ~/.claude.json, ~/.codex, ...
      --bind <CCDB_BWRAP_RW_PATHS> ...
      --ro-bind <agent config inside the state> ...  # see "Persistence"
      --ro-bind <.git/hooks> <.git/config> <CCDB_BWRAP_RO_PATHS> ...
      --tmpfs <hidden dir> | --ro-bind /dev/null <hidden file> ...
      --chdir <working dir> -- <the CLI argv>
```

### What the agent can see

- **Outside `$HOME`:** everything, read-only (`/usr`, `/etc`, `/opt`, `/var`, ...), except the
  hidden sockets below. `/tmp` is private.
- **Inside `$HOME`:** only the allowlist. `~/.config/gh` (GitHub token), `~/.aws`, `~/.azure`,
  `~/.kube`, `~/.docker/config.json`, `~/.netrc`, `~/.git-credentials`, `~/.ssh`, `~/.gnupg`, other
  repositories, the relay's own clone and its `.env`, notes and documents are **absent by
  construction**. Nobody has to remember to list them. `$HOME` itself is a writable tmpfs, so a
  tool that writes a cache there still works; the writes disappear with the sandbox and never
  reach the host.
- **The allowlist:**
  - the working directory, read-write;
  - for a linked git worktree, only the parts of the repository's common directory a commit
    writes: `objects/`, `refs/` and `logs/` read-write, plus the worktree's own git dir
    (`<common>/worktrees/<name>`). Inside that git dir, `commondir`, `gitdir` and
    `config.worktree` are read-only, so the agent cannot redirect git to another common directory.
    `hooks/`, `config`, `config.worktree`, `packed-refs`, `HEAD`, `info/`, `shallow` and `modules/`
    of the common dir are read-only, and nothing else of it is mounted. The worktree's `.git` file
    lives in the writable working directory, so a previous sandboxed run could have rewritten it.
    It is trusted only when **all** of these hold, and otherwise nothing outside the working
    directory is mounted for git:
    - the git dir it names resolves to `<common>/worktrees/<name>`;
    - git's back-link `<common>/worktrees/<name>/gitdir` names this working directory's `.git`;
    - `<common>` contains `HEAD`, `objects/` and `refs/`;
    - `<common>` is not `/`, `$HOME`, or an ancestor of `$HOME` or of the working directory;
  - the agent's own state: `CLAUDE_CONFIG_DIR`, or `~/.claude` and `~/.claude.json` for Claude
    Code; `CODEX_HOME` or `~/.codex` for Codex and `local`; `PI_CODING_AGENT_DIR` or `~/.pi` for
    pi. The state comes from the final child environment, so an account pool's profile directory is
    the one that gets mounted;
  - the CLI's own installation, read-only. Starting from the command as found on the child's
    `PATH`, the relay binds the directory of every symlink hop (`~/.local/bin/codex` →
    `~/.npm-global/bin/codex` → ...), the install root of the real file (a versions directory,
    a global `node_modules`, a toolchain prefix such as `~/.local/share/nodejs/node-v22`), and the
    same again for a script's interpreter (`#!/usr/bin/env node`). Measured for this host's Claude
    Code (native binary under `~/.local/share/claude/versions`) and Codex (npm package plus a node
    under `~/.local/share/nodejs`);
  - `~/.gitconfig` and `~/.config/git`, read-only, so commits have an author;
  - `CCDB_BWRAP_RO_PATHS` and `CCDB_BWRAP_RW_PATHS`, for anything else the agent's work needs (a
    language toolchain in `~/.cargo` or `~/.local/share/uv`, a cache).
- **Refused layouts:** the working directory, the state directory, or an operator path that is
  `$HOME`, `/` or an ancestor of `$HOME` is refused in one sentence, because binding it would expose
  everything the allowlist keeps out. A relay whose working directory is the home directory needs a
  narrower one (a worktree) for `bwrap` threads.
- **Hidden** (they are outside `$HOME`, or inside a bound path): the relay's `.env` and its siblings
  (`.env.bak-*`, `.env.local`; `.env.example` stays), the Docker socket, the system D-Bus socket,
  the user runtime directory (`$XDG_RUNTIME_DIR`, normally `/run/user/<uid>`) and the ssh-agent
  socket. The runtime directory matters most. It holds the systemd user bus, and
  `systemd-run --user` or `busctl --user` would run a command **outside** the sandbox through it.
  It also holds the gpg and ssh agent sockets. This closes the user bus on hosts where it is a
  path socket in the runtime directory (systemd's default). A session bus that listens on an
  **abstract** address (`unix:abstract=...`, common for a `dbus-launch` X11 session) cannot be
  hidden by a mount; see the shared network namespace below. `DBUS_SESSION_BUS_ADDRESS`, `XDG_RUNTIME_DIR`,
  `SSH_AUTH_SOCK` and `GPG_AGENT_INFO` are removed from the child environment. Hiding a socket
  matters even on a read-only filesystem, because connecting to a socket is not a write.
- **Namespaces:** PID, IPC (no System V or POSIX shared memory with host processes), UTS and,
  where the kernel allows it, cgroup are new. `--new-session` (`setsid`) is kept. With piped stdio
  it costs nothing (stdin streaming was verified end to end), and it stops `TIOCSTI` keystroke
  injection should the relay ever run with a controlling terminal.
- **No privilege escalation:** bubblewrap always sets `no_new_privs`. `sudo` fails with
  *"The "no new privileges" flag is set"*, and other setuid binaries cannot gain privileges.
- **Preflight** checks for the binary and the working directory, refuses unsafe layouts, creates
  the protected placeholders (see below), then makes one test sandbox. A host with unprivileged
  user namespaces disabled (for example, by AppArmor's `apparmor_restrict_unprivileged_userns`) is
  reported in one sentence. The layout is checked again immediately before the spawn.

### Persistence across modes

The agent's state directory has to be writable: sessions, credential refresh and caches live
there. But some of what lives there makes an agent **run something later**: Claude Code's
`settings.json` (hooks, `statusLine`, `apiKeyHelper`), `.claude.json` (user MCP servers), plugins,
skills, agents and commands, and Codex's `config.toml` (MCP servers, hooks), rules and skills. A
sandboxed agent that could edit them would plant a hook that the next **unsandboxed** run executes:
a `host` thread, a scheduled job, or the operator's own terminal. So inside the writable state
directory, these are re-bound **read-only**:

| Backend | Read-only (relative to the state directory) |
|---|---|
| Claude Code | `settings.json`, `settings.local.json`, `.claude.json` (or `~/.claude.json`), `CLAUDE.md`, `AGENTS.md`, `keybindings.json`, `hooks/`, `plugins/`, `skills/`, `agents/`, `commands/`, `output-styles/`, `rules/`, `scripts/` |
| Codex / `local` | `config.toml`, `AGENTS.md`, `AGENTS.override.md`, `hooks.json`, `hooks/`, `rules/`, `skills/`, `plugins/`, `prompts/`, `packages/` |
| pi | `agent/settings.json`, `agent/models.json`, `agent/AGENTS.md`, `agent/extensions/`, `agent/skills/`, `agent/prompts/` |
| git | a plain repository's whole `.git`; for a linked worktree its `.git` file, the git dir's `commondir`/`gitdir`/`config.worktree`, and the common dir's `hooks/`, `config`, `config.worktree`, `packed-refs`, `HEAD`, `info/`, `shallow`, `modules/` |

- **Missing entries are created first, on the host,** so they can be bound read-only and cannot be
  created inside the sandbox: `{}` for a JSON file, an empty file otherwise (mode 0600), an empty
  directory (0700) for a name without an extension. Claude Code 2.1.289, codex-cli 0.160.0 and pi
  were each run against exactly these placeholders and treat them as absent. (An *empty*
  `.claude.json` is reported by Claude Code as corrupted, which is why JSON gets `{}`.) Creation
  never follows a symlink at the name (`O_NOFOLLOW`), never truncates (`O_EXCL`), and is skipped
  when the parent is a symlink or resolves outside the state root.
- **A symlink at a protected name is refused.** A mount cannot be placed on a symlink, so the
  sandbox could delete the link and create a real `hooks/` in its place. Preflight refuses with one
  sentence. Use a dedicated state directory (below), or set `CCDB_BWRAP_PROTECT_CONFIG=0` to accept
  the risk explicitly. The check runs before placeholders are created, so a refused layout leaves
  nothing behind.
- **Git, plain repository:** the whole `.git` is read-only (and a mount point, so it cannot be
  renamed away and replaced with `git init`). Protecting only `hooks` and `config` is not enough:
  a `commondir` file written into `.git` redirects git to a directory of the agent's choosing, with
  its own hooks and config, and `modules/` holds submodule hooks. Git's lock files need the
  directory itself writable, so no finer split is safe. The agent can edit files and run read-only
  git commands, but **cannot commit in a plain repository**. Commits happen in linked worktrees,
  which is how the relay runs sessions.
- **Git, linked worktree:** `git commit` works. The worktree's `.git` file is read-only, so it
  cannot be pointed at a forged git directory, and the pointers inside the worktree's git dir are
  read-only too. A missing `hooks/` is created first. A symlinked `.git`, `hooks/`, `config` or any
  other mounted git path is refused. Verified inside a real sandbox: writing the common dir's
  hooks, rewriting `.git`, rewriting the worktree's `commondir` and reading other files in the
  common dir all fail, and `git commit` succeeds and is visible from the main checkout.
- **The CLI's installation** is read-only even when it lives inside a writable bind (a CLI
  installed under its own state directory or in the project's `node_modules`), so the binary
  cannot be swapped for the next unsandboxed run.
- **A symlink anywhere between the state root and a protected name** (for example pi's `agent/`)
  is refused, and missing intermediate directories are created.
- Credentials (`.credentials.json`, `auth.json`), sessions and caches stay writable.
- Read-only git configuration means `git config`, `git push -u` (which records the upstream),
  deleting refs (which rewrites `packed-refs`) and `git gc` fail inside the sandbox. In a worktree,
  `git commit` and `git push origin HEAD` work, given credentials the sandbox can see.

**Recommended (and required with this host's layout):** give sandboxed agents their **own** `CLAUDE_CONFIG_DIR` / `CODEX_HOME`, separate
from the ones that `host` threads, scheduled jobs and your own terminal use. Account pools (#821)
make that natural: one profile directory per pool member. The state directory is bound
read-write, so anything else you keep in it (this host's `~/.claude` holds unrelated `.env` files
and token caches) is visible to the agent. A dedicated directory contains only what the CLI wrote,
and a missed persistence path only affects other sandboxed runs. The end-to-end runs below used
dedicated directories, because this host's `~/.claude` and `~/.codex` contain symlinked
`hooks`/`skills`/`AGENTS.md` and are refused.

### Residual risks

These are **not** covered by `bwrap`. Choose `container` or `ssh` if they matter to you.

- **Shared network namespace.** The agent reaches everything the relay's user can reach over the
  network. That includes every service on `127.0.0.1` (the relay's own REST API, which the agent is
  meant to use, but also databases, dev servers and admin UIs), the LAN and the cloud metadata
  endpoint. It also includes **abstract Unix sockets**, which belong to the network namespace
  rather than the filesystem, so a mount cannot hide them. That includes a D-Bus session bus or
  an X11 display listening on an abstract address. On the test host these were root services only
  (`multipathd`, `iscsiadm`). `CCDB_BWRAP_UNSHARE_NET=1` removes all of it, but it
  also removes the model API, so it only suits an agent whose model needs no network. bubblewrap
  has no destination allow list; a per-destination policy needs a firewall (`nftables` by uid or
  cgroup) or a container network.
- **A repository the sandbox creates.** In a working directory without `.git`, the agent can
  `git init` one with its own hooks, and a later unsandboxed `git` command there runs them.
- **The agent's own credential.** The state directory holds the CLI's OAuth credential
  (`.credentials.json`, `auth.json`). It stays readable and writable, because the CLI refreshes
  it, and with refresh-token rotation a read-only copy would leave the host's credential
  invalidated. So a sandboxed agent can read, and could exfiltrate, the credential of the account
  it runs as. This is inherent, and it is why a dedicated profile per sandboxed deployment is
  recommended: the blast radius is that one account, not the operator's own login. Placeholders
  are created 0600/0700, and no credential is copied or logged by the relay.
- **Shared refs.** A worktree's writable `refs/` is the whole repository's, so a sandboxed agent
  can move any branch (as any process that can commit there can). That affects history, not what
  runs later; protect important branches on the remote.
- **Objects from elsewhere.** A worktree's writable `objects/` can gain an `info/alternates` file
  that points object lookup at another repository. That changes which objects git can read, not
  what it executes.
- **Project configuration in the working directory** is writable by design:
  `.claude/settings*.json`, `.mcp.json`, `.codex/`, `AGENTS.md`/`CLAUDE.md`, and any
  `core.hooksPath` directory checked into the repository (husky, `.githooks/`). Whatever later
  runs **in the same working directory** without a sandbox loads them. Do not reuse a sandboxed
  thread's worktree from a `host` thread without reviewing the diff.
- **Unprotected names in the state directory.** Only the entries in the table are read-only.
  Claude's per-project memory and transcripts under `projects/` stay writable, so they can carry
  instructions (not code) into a later session. A hook that runs a script stored under some other
  name in the state directory can have that script rewritten; add it to `CCDB_BWRAP_RO_PATHS`.
- **Environment variables.** The child environment is the relay's minus the stripped credentials
  (see [SECURITY.md](SECURITY.md#environment-isolation)). A token you export to the relay's
  process (`GH_TOKEN`, `AWS_*`) reaches the agent in every mode, `bwrap` included.
- **Time of check to time of use.** Paths are resolved (`realpath`) and checked immediately before
  the spawn, and the resolved paths are what bwrap binds. A concurrent **unsandboxed** process that
  swaps a path for a symlink in the milliseconds between that check and bwrap's mount could still
  redirect a bind. A sandboxed process cannot do this to the protected names, because they are
  read-only mount points in every running sandbox. Binding by file descriptor (`--ro-bind-fd`)
  would close the window, and needs the runners to pass descriptors to the child.

**Codex inside bwrap** still applies its own `--sandbox` policy unless `dangerously_skip_permissions`
is on. The nested sandbox works on hosts that allow nested user namespaces. Codex's default
`read-only` policy then blocks writes even inside the working directory. Use
`dangerously_skip_permissions` (bwrap becomes the only boundary) or set
`CCDB_CODEX_SANDBOX_OVERRIDE=workspace-write`.

**Tools the agent's work needs** that live in `$HOME` (`uv`, `cargo`, `gh`, a language toolchain)
are not visible unless they are the CLI's own installation or listed in `CCDB_BWRAP_RO_PATHS`. A
symlink in a bound directory such as `~/.local/bin` whose target is not bound dangles. Note that a
bound directory is bound whole: every file in `~/.local/bin` is readable, not only the CLI. Hooks and
MCP servers configured in the state directory that run code from elsewhere in `$HOME` fail inside
the sandbox for the same reason. That is intended; bind what they need, read-only.

**Interrupts:** bubblewrap does not forward `SIGINT`. The Stop button ends the sandbox, and
`--die-with-parent` kills the CLI, instead of the CLI stopping gracefully. Both CLIs write their
session logs as they go, so the thread can still be resumed.

## `container` (preview)

```text
docker run --rm -i --init --workdir <dir> --user <uid>:<gid>
           --volume <dir>:<dir> --volume <agent state>:<agent state> ...
           --env NAME ... <CCDB_CONTAINER_ARGS> <image> claude <args>
```

- Paths are mounted at the **same path** inside the container, so every path in the argv
  (`--cd`, attachment marker files, `CLAUDE_CONFIG_DIR`) stays valid.
- **The mount plan is `bwrap`'s** (`claude_code_core/execution/guarded_paths.py`, shared code). The
  working directory and agent state are writable. The protected agent config is mounted `:ro` on
  top, with placeholders created first and symlinks refused. A plain repository's `.git` is `:ro`,
  and a linked worktree gets only its validated commit paths. A working directory or state
  directory that is `$HOME` or above is refused. Only sources that exist are mounted, because the
  runtime would otherwise create a missing one as root.
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

## `ssh` (preview)

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
- **Environment:** values on a command line show up in process listings, so no forwarded value is
  ever put on the local `ssh` command line except the two non-secret ones the runner sets
  (`DISCORD_THREAD_ID`, `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS`, given to `env` on the remote side).
  Names listed in `CCDB_SSH_ENV` are sent with ssh's own `-o SendEnv=NAME`: the command line holds
  only the name, and the value travels inside the encrypted session. The server must allow each
  name with `AcceptEnv NAME` in `sshd_config`, or the variable is silently dropped. The remote host
  has to be logged in to the agent's provider itself (`claude login`, `codex login`).
  `CCDB_API_URL` and `CCDB_API_SECRET` are not forwarded, because the relay's API is on the relay's
  loopback.
- **Preflight** connects with `ConnectTimeout=10` and runs `<cli> --version` in the remote
  directory. That checks, in one round trip, that the host answers, that the directory exists and
  that the CLI is on `PATH`. Success is cached for five minutes.
- **The `local` backend refuses `ssh`.** Its promise that nothing leaves the machine depends on a
  `CODEX_HOME` the relay owns locally; the remote end would use its own `~/.codex`.
- **Limitations:** session data (Claude's `~/.claude/projects`, Codex rollouts) lives on the remote
  host. Features that read it locally (Codex resume recovery, cross-backend handoff, transcript
  search) cannot see it. The Stop button ends the local ssh client. The remote CLI is not
  interrupted gracefully: with no terminal (`-T`), it stops only when it notices its input has
  closed or its output pipe is broken.

## Verifying a deployment

Run one turn in a scratch thread and ask the agent to run `touch ~/should_fail`, `sudo -n true`
and `touch ./inside_ok`. Under `native` and `bwrap`, the first two fail (`Read-only file system`,
`"no new privileges" flag is set`) and the third succeeds. Under `bwrap`, also check
`systemd-run --user true`, `busctl --user list`, `touch ~/.claude/settings.json` (or
`~/.codex/config.toml`), `curl --unix-socket /var/run/docker.sock http://localhost/version`,
`cat ~/.config/gh/hosts.yml` and reading another repository under `$HOME`. All of them must fail,
and `ls -A ~` must show only the allowlist. The completion notice shows the
`Environment` field. The pull request that added this layer records those runs for Claude Code and
Codex on Linux with bubblewrap 0.6.1, Claude Code 2.1.289 and codex-cli 0.160.0.

## Design

See [ADR-0008](adr/0008-let-the-operator-choose-the-execution-environment.md).
