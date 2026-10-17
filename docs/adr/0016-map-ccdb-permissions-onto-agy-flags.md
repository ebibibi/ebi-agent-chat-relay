---
type: adr
id: ADR-0016
title: Map ccdb permissions onto agy's own flags instead of gating the Antigravity backend
decision: Ship the Google Antigravity CLI as a sixth CLI SessionBackend whose permissions follow ccdb's existing settings (skip-permissions and permission mode), without a pi-style opt-in, and surface agy's headless auto-denials as errors.
status: accepted
date: 2026-10-17
deciders: [Masahiko Ebisuda, Claude]
scope: repository
supersedes:
superseded_by:
---

# ADR-0016: Map ccdb permissions onto agy's own flags instead of gating the Antigravity backend

## Context

`agy --print` has an approval loop, but print mode cannot prompt. Measured on agy 1.3.5, a tool
that needs approval is auto-denied, and the turn still ends with `status: "SUCCESS"`, an empty
`response` and a `denied_actions` list. agy has `--dangerously-skip-permissions` and
`--mode accept-edits|auto|plan`, but no tool allowlist.

ADR-0007 gated pi behind `CCDB_PI_ALLOW_UNSANDBOXED` because pi has no approval loop at all, so
no ccdb setting described what a pi thread could do.

## Decision

- `dangerously_skip_permissions` (or permission mode `bypassPermissions`) maps to
  `--dangerously-skip-permissions`; `acceptEdits`, `plan` and `auto` map to `--mode`. Everything
  else leaves agy's default, which denies.
- No separate opt-in. agy's default fails closed, so the ccdb settings an operator already chose
  for Claude describe an agy thread truthfully.
- A turn whose only outcome is a denial is reported as an error naming the denied action and the
  two ways to allow it. A denial alongside an answer is reported as a warning.
- `allowed_tools` with skipped permissions is refused: agy cannot enforce the list, and running
  every tool while the operator believes a list is in force is the false claim ADR-0007 rejected.

## Consequences

- A deployment that already runs Claude with skipped permissions gets the same posture on
  `/backend antigravity`, with nothing new to configure.
- A deployment that does not skip permissions gets a backend that can answer but not act, with a
  message saying why, rather than silent empty answers.
