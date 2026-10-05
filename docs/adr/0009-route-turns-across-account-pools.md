---
type: adr
id: ADR-0009
title: Route turns across operator-configured account pools
decision: Let the operator list pre-logged-in CLI profile directories per backend and a selection strategy in a TOML file; choose a profile per session or turn with a pure selector, move the session transcript when a thread changes profile, and never handle tokens.
status: accepted
date: 2026-10-05
deciders: [Masahiko Ebi, Claude]
scope: repository
supersedes:
superseded_by:
---

# ADR-0009: Route turns across operator-configured account pools

## Context

The relay already reads every turn's `rate_limit_event` into `usage_stats`, but it assumes one
login per backend. When that login is exhausted every thread stops until the window resets, even
when the operator holds a second subscription with headroom.

Three questions decided the shape:

1. Who decides which login serves a turn, and by what rule?
2. What happens to a thread's session when it moves to another login? A Claude Code session id
   is tied to the `CLAUDE_CONFIG_DIR` whose `projects/` holds its transcript; a Codex thread id to
   the `CODEX_HOME` whose `sessions/` holds its rollout.
3. Where is the line between "choosing a directory" and "handling credentials"?

## Options considered

### One built-in rule (most headroom)

Rejected. Operators hold logins for different reasons: drain a personal plan before touching an
organisation plan (`priority`), avoid flapping between plans (`sticky`), spread load
(`round_robin`), or keep the most headroom (`most_headroom`). No single rule is right, so the
rule is configuration, chosen per backend.

### Pin a thread to the login that created it

Rejected as the only behaviour: the thread would still stop when that login runs out, which is
the problem being solved. Kept as the default *preference* (`assign = "session"`), because a
thread that stays on one login keeps its prompt cache warm.

### Start a fresh session with a text summary on every switch

Rejected as the primary path once measured. On Claude Code 2.1.289 and codex-cli 0.160.0,
copying the transcript file to the same relative path under the target directory lets the CLI
resume the *same* session id there with full history, tool calls included, while a resume
without the copy fails. A text handoff drops tool calls and results, so it is kept only as the
fallback when the copy is impossible.

### Store or refresh tokens in the relay

Rejected. A profile is a directory the operator logged in to with the vendor CLI; the relay sets
`CLAUDE_CONFIG_DIR` / `CODEX_HOME` on the child process and nothing else. Token refresh stays
with the CLI that owns the directory, and the relay cannot leak what it never reads.

## Decision

- `CCDB_ACCOUNT_POOLS_FILE` names a TOML file of pools, validated strictly at startup. No file
  means exactly the previous behaviour.
- Selection is a pure function of (pool, usage per profile, rejection markers, cursor, clock),
  fully unit-tested. Exhausted means a listed window at or above `switch_at` that has not reset,
  any rejected window that has not reset, or an active rejection marker. Unknown usage means
  available.
- `usage_stats` is keyed by `(profile, rate_limit_type)`; existing rows migrate to `default`.
- Thread pins, the round-robin cursor, the sticky profile and rejection markers are persisted.
- On a profile change the transcript is copied (overwriting a stale copy), else a text handoff.
- A quota rejection marks the profile until its reset (or a cooldown) and either tells the user
  which profile comes next or, with `retry_on_exhaustion`, reruns the turn once.

## Consequences

- An operator with two logins keeps working when one runs out, without manual action.
- Terms of service are the operator's responsibility; the README says so. The feature only
  selects among logins the operator already holds.
- Pools apply to interactive chat threads first. Headless paths (scheduler, webhooks, `/skill`,
  Teams) still use the relay's own login, and transcript readers such as `/rewind` look only in
  the relay's own directories; both are follow-ups.
- The transcript-copy behaviour is a measured property of two CLI versions, not a documented
  contract. Re-measure after a CLI upgrade that changes the on-disk layout; the text handoff is
  the safety net if it stops working.
