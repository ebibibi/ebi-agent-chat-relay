---
type: adr
id: ADR-0008
title: Let the operator choose the execution environment
decision: Put an execution-environment layer (host, native, bwrap, container, ssh) between every CLI runner and the subprocess it spawns, chosen by the operator in the deployment environment and selectable per thread only from an operator allowlist, with host as the unchanged default.
status: accepted
date: 2026-10-05
deciders: [Masahiko Ebi, Claude]
scope: repository
supersedes:
superseded_by:
---

# ADR-0008: Let the operator choose the execution environment

## Context

Every CLI backend ran as the relay's own OS user, directly on the host, and the boundaries around
it were uneven. Claude Code had a permission mode, and many deployments add
`--dangerously-skip-permissions`. Codex had its own sandbox, which the operator could override.
pi had none (ADR-0007). `child_env.py` stripped the relay's credentials from the child environment,
but that is an environment boundary, not an OS one. An agent could still read the relay's `.env`,
`~/.ssh`, or the Docker socket whenever the relay's user could.

That is acceptable for one person's own bot. It is not acceptable for a relay shipped into another
organisation, whose operator has to decide where agents execute and has to be able to see that
decision on every thread.

## Options considered

### Document a systemd hardening recipe and stop there

Rejected. A unit-level sandbox applies to the relay itself as well as the agents, so it has to
leave the relay's own secrets readable, and those are the secrets that matter most. It also cannot
differ per thread, and the thread cannot report it.

### Rely on each agent's own sandbox

Rejected as the only option, and kept as one mode (`native`). The agents' sandboxes do not cover
the same things. Claude Code's confines the shell but not the file-edit tools. Codex's covers
commands. pi has none. A relay that offered only this mode would show the same word on three
different boundaries.

### Make the environment a per-thread setting with no operator bound

Rejected for the same reason that `CCDB_CODEX_SANDBOX_OVERRIDE` and `CCDB_PI_ALLOW_UNSANDBOXED`
are environment-only: any Discord user could pick the weakest mode for their own thread.

### An execution-environment layer chosen by the operator, selectable within an allowlist

Accepted. Each mode implements `preflight` and a pure `transform` of `(argv, env, cwd)`. Runners
call `prepare_launch` immediately before `create_subprocess_exec`, so the change to each runner is
a few lines and does not depend on how argv or the environment were built. The operator sets the
default and the allowlist in the environment. `/sandbox` can choose only from that list. The runner
checks the list again at spawn time, so a Cog that sets the mode directly is held to it too. The
completion notice names the environment that served the turn.

Four rules shape the details:

- **`host` is today's code path.** It is not a no-op wrapper: there is no check and no rewrite, so
  existing deployments and tests are unaffected (zero-config principle).
- **Configuration errors fail closed.** If the operator mistypes the mode, every turn is refused
  with the bad value named, rather than running unconfined (decision 16: a guard's failure
  direction follows what it guards).
- **A mode never claims a boundary it does not provide.** `native` refuses pi (ADR-0007).
  `native` also refuses Codex when `CCDB_CODEX_SANDBOX_OVERRIDE=danger-full-access` contradicts it,
  and it removes Codex's sandbox-bypass flag, because otherwise the thread would show `native`
  while running unsandboxed.
- **Secrets stay off command lines.** The container runtime receives variable names, not values.
  ssh sends operator-listed variables with `SendEnv`, so only their names are on the local command
  line. Remote command lines are visible in process listings on both machines.
- **A sandbox must not be able to arm the next unsandboxed run.** The agent's state directory has
  to be writable, but the parts of it that execute later (Claude Code's `settings.json` hooks,
  `~/.claude.json` MCP servers, plugins and skills; Codex's `config.toml`, rules and skills;
  a plain repository's whole `.git`, and a worktree's git pointers, hooks and config) are re-bound read-only inside it. The most dangerous ones are
  created empty first if they are missing.
- **The home directory is an allowlist, not a deny list.** `bwrap` mounts `$HOME` as an empty tmpfs
  and binds back only the working directory, the agent's state, the CLI's own installation and
  `~/.gitconfig`. A deny list of secrets (`~/.ssh`, the relay's `.env`) was the first design. A
  review showed it leaks whatever nobody thought to list (`~/.config/gh`, `~/.aws`, other
  repositories), so it was replaced. Binds that would re-expose home (a working directory equal to
  `$HOME`) are refused, and paths are resolved with `realpath` and checked again immediately before
  the spawn.
- **Paths the sandbox can write are not trusted.** A linked worktree's `.git` file is honoured only
  when git's back-link confirms it. A symlink at a protected config name is refused, because a
  mount cannot cover a symlink.
- **Host services are hidden, not just files.** The user's runtime directory (systemd user bus,
  agent sockets) and the system D-Bus socket are hidden, and the variables pointing at them are
  dropped, because `systemd-run --user` would otherwise run a command outside the sandbox.
- **Choosing the environment is itself a privilege.** `/sandbox`, `/backend`, `/model`, `/effort`,
  `/engine-status` and `/ollama pull|rm|use` are limited to the users allowed to run `/skill`.

## Consequences

- **`bwrap`, `container` and `ssh` ship as preview.** `host` stays the default. The recommended
  setup for any sandboxed deployment is a dedicated `CLAUDE_CONFIG_DIR` / `CODEX_HOME` (one per
  sandboxed deployment, which account pools make natural) and agents working in linked worktrees.
- `bwrap` and `container` share one mount plan (`guarded_paths.py`), so a protection added for one
  applies to both. A plain repository's `.git` is read-only in both, because a `commondir` file or
  `modules/` would otherwise let the agent redirect a later git run. Commits happen in validated
  worktrees, of which only `objects/`, `refs/`, `logs/` and the worktree's git dir are writable.
- The CLI's install is discovered by following its symlinks and interpreter, but never through a
  file the agent can write. Otherwise a rewritten shebang could choose a credential directory to
  bind.
- A configuration error for one mode refuses only that mode. `ssh` refuses the `local` backend,
  whose guarantee depends on a relay-owned `CODEX_HOME` on this machine.

- An operator can ship the relay with `bwrap` (or `container`, or `ssh`) as the default and know
  that an agent cannot write outside its working directory, gain root, or read the relay's
  `.env`, `~/.ssh` or the Docker socket. Measured end to end with Claude Code 2.1.289 and codex-cli
  0.160.0 under bubblewrap 0.6.1: a write to `$HOME` failed with `Read-only file system`, `sudo -n
  true` failed with the `no new privileges` message, and a write inside the working directory
  succeeded.
- A read-only `$HOME` breaks tools that write outside the agent's state, and hiding `~/.ssh` breaks
  `git push` over SSH. Both are configurable, and both are documented as the price of the boundary.
- bubblewrap does not forward `SIGINT`, so Stop ends a wrapped CLI rather than stopping it
  gracefully. Session logs are written as the CLI goes, so the thread can still be resumed.
- Under `ssh`, session data lives on the remote host, so local readers of it (Codex resume
  recovery, cross-backend handoff) do not see it.
- Measured escape probes under `bwrap`, for both CLIs: `systemd-run --user true` and
  `busctl --user list` fail (no bus address, and with the address given explicitly the socket does
  not exist). Writing `~/.claude/settings.json`, `~/.claude.json` or `~/.codex/config.toml` fails
  with `Read-only file system`, and the Docker socket refuses connections. Both CLIs work normally
  with their configuration read-only.
- Under `bwrap`, `ls -A ~` shows only the allowlist. `cat ~/.config/gh/hosts.yml` and reading
  the relay's own clone fail with `No such file or directory`. Writes to `$HOME` land in the empty
  tmpfs and never reach the host.
- The host's default `~/.claude` and `~/.codex` keep symlinked `hooks`/`skills`/`AGENTS.md`, so
  `bwrap` refuses them. Sandboxed threads are expected to use dedicated state directories, which
  also keeps unrelated files in the operator's `~/.claude` out of the agent's view.
- **Residual risks, accepted and documented:** the agent can read (and could exfiltrate) its own
  OAuth credential in the state directory; it must stay writable for token refresh. The network namespace is shared, so localhost
  services, the LAN and abstract Unix sockets stay reachable. `CCDB_BWRAP_UNSHARE_NET` cannot fix
  that for an API-backed agent. Project configuration in the writable working directory
  (`.claude/`, `.mcp.json`, `.codex/`, in-repo hook directories) can still affect a later
  unsandboxed run in the same directory. Scripts stored under unprotected names in the state
  directory can be rewritten. Exported environment variables reach the agent in every mode. A
  concurrent unsandboxed process could race the `realpath` check against bwrap's mount. The recommended mitigation for the last two is a dedicated
  `CLAUDE_CONFIG_DIR` / `CODEX_HOME` per sandbox and not reusing a sandboxed worktree from `host`.
- pi still needs `CCDB_PI_ALLOW_UNSANDBOXED` in every mode. Relaxing that gate for `bwrap` and
  `container`, which do provide an OS boundary, is a separate decision that would supersede part
  of ADR-0007.
- Released as a minor version per ADR-0005.
