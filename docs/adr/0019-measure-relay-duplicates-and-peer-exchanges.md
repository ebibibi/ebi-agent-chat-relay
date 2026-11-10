---
type: adr
id: ADR-0019
title: Measure relay duplicates and peer exchanges separately from fan-out
decision: Replace the low sender quota and unconditional pair cooldown with content deduplication, a bidirectional pair burst budget, and a generous emergency sender ceiling.
status: accepted
date: 2026-11-10
decided_by: ai
deciders: [Codex]
scope: repository
---

# ADR-0019: Measure relay duplicates and peer exchanges separately from fan-out

## Context

Five messages per sender per ten minutes prevents a coordinator from briefing even
six workers. A sixty-second cooldown between distinct messages to the same worker
also blocks corrections and evidence. Neither count distinguishes those actions
from an automated conversation loop.

## Options considered

- Raise only the sender quota: retains the unnecessary follow-up delay and lets
  one looping peer consume a coordinator's entire budget.
- Trust only the caller's hop field: preserves useful traffic, but peers that
  mistakenly reset hop can loop indefinitely at high speed.
- Classify message importance using a model or add a force flag: introduces an
  uncertain dependency or a bypass that an automated loop can also use.
- Measure duplicates, peer exchanges, and sender floods separately: selected.

## Decision and rationale

Keep the existing two-hop limit and self-send prohibition. Block the same
whitespace-normalized text to the same recipient for sixty seconds. Distinct
follow-ups and identical fan-out instructions are admitted immediately.

Count a pair's traffic in both directions together, with twenty messages per
rolling minute. This permits ten rapid exchanges but bounds a fast loop even
when every message has different text and hop zero. The budget is local to the
pair, so other workers remain reachable. A separate sender ceiling of 120 per
rolling minute limits extraordinary fan-out floods, allowing briefings to dozens
of workers under normal use. Queue and interrupt consume the same budgets.

Reserve synchronously before asynchronous dispatch, release on dispatch failure,
and return stable refusal codes plus retry times for temporary limits. Retain
only hashes of normalized text in the guard and prune inactive state globally.

The thresholds and measurement policy are AI implementation decisions; the human
has not separately confirmed these values and may revise them.

## Consequences and limits

This is deterministic burst protection, not a semantic detector. Slow loops or
loops spread across many pairs may still pass; hop discipline remains necessary.
Very large legitimate bursts can still hit the emergency ceiling. Process-local
history resets on restart and does not coordinate multiple server processes.
Discord acceptance means a background delivery task was scheduled; a later task
failure still consumes capacity. Console dispatch failures release capacity.

## Related

- [Relay API and limits](../guide/features.md#session-to-session-relay)
- [Issue #1307](https://github.com/ebibibi/ebi-agent-chat-relay/issues/1307)
