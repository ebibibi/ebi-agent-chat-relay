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
  ssh forwards only an explicit list of names, because remote command lines are visible in
  process listings on both machines.

## Consequences

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
- pi still needs `CCDB_PI_ALLOW_UNSANDBOXED` in every mode. Relaxing that gate for `bwrap` and
  `container`, which do provide an OS boundary, is a separate decision that would supersede part
  of ADR-0007.
- Released as a minor version per ADR-0005.
