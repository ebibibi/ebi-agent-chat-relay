---
type: adr
id: ADR-0007
title: Require an operator opt-in for the unsandboxed pi backend
decision: Ship pi as a fifth CLI SessionBackend, and refuse to spawn it unless CCDB_PI_ALLOW_UNSANDBOXED is set, rather than silently accepting that pi has no sandbox and no approval loop.
status: accepted
date: 2026-09-22
deciders: [Masahiko Ebi, Claude]
scope: repository
supersedes:
superseded_by:
---

# ADR-0007: Require an operator opt-in for the unsandboxed pi backend

## Context

pi is a terminal coding agent that normalises many providers behind one CLI, reads `AGENTS.md` and
skills on its own, and can run a subscription the operator already pays for. That is the shape
decision 1 ("CLI spawn, not API") exists for, so adding it as a fifth `SessionBackend` is
unremarkable.

Its permission model is not. pi documents that it has no built-in sandbox, that built-in tools run
with the permissions of the process, and that the non-interactive modes ccdb uses show no trust
prompt at all. There is therefore no flag that corresponds to ccdb's
`dangerously_skip_permissions`, because unsandboxed is the only mode pi has. The single restriction
that survives is the `--tools` allowlist.

Every other backend lets an operator reason about this from configuration. Claude Code has
permission modes; Codex has its own `--sandbox` policy, overridable only from deployment
environment. A `/backend pi` that behaved like the others on the surface would be making a claim
about its blast radius that is not true.

## Options considered

### Spawn pi like any other backend

Rejected. The difference would be real, invisible, and discovered from a thread that had already
run. ccdb's session prompts do impose worktree discipline, but that is a policy the agent is asked
to follow, not a boundary that holds when it does not.

### Map `permission_mode` onto `--tools` and present it as equivalent

Rejected for the same reason the anonymization gateway does not let a model mint aliases: a control
that looks like the thing it replaces, but cannot enforce the same property, is worse than an
absent control. A tool allowlist is a useful restriction and is wired through; it is not an
approval loop and must not be labelled as one.

### Refuse to spawn without an explicit environment opt-in

Accepted. `CCDB_PI_ALLOW_UNSANDBOXED=1` is deployment-scoped and environment-only, so no Discord
user can grant it for their own thread. Without it, a turn returns one sentence naming the missing
property and no process starts. Project trust is a second, separate opt-in
(`CCDB_PI_APPROVE_PROJECT`), defaulting to `--no-approve`, so a repository ccdb checks out cannot
reconfigure the agent about to run inside it.

This follows decision 13: a property ccdb cannot provide is surfaced to the operator once, rather
than quietly dropped.

## Consequences

- `/backend pi` is inert until a deployment says otherwise, which is a deliberate extra step for a
  new user and the correct default for an existing one.
- The refusal is per-turn and arrives as an ordinary failed result, so it is visible in the thread
  where the attempt happened rather than only in the log.
- The published JSON event contract is not the measured one — the terminal event, per-message usage
  and the shape of a failed turn all differ — so the backend is tested against recorded fixtures
  from a real run, and the pi version they were recorded from is named in `docs/pi-backend.md`.
- Released as a minor version per ADR-0005.
