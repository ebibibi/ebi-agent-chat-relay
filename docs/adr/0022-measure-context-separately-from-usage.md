---
type: adr
id: ADR-0022
title: Measure context occupancy separately from token usage
decision: Preserve provider-reported context size and direct occupancy, invalidate old measurements after manual compaction, and make exact Codex compact executable on Enter.
status: accepted
date: 2026-11-11
decided_by: ai
deciders: [Codex]
scope: repository
---

# ADR-0022: Measure context occupancy separately from token usage

## Context

The user reported that Codex /compact appeared stuck and questioned the context
badge. A read-only browser probe could type the command, but Enter completed it
instead of submitting; no native command reached the endpoint in that interaction.
The app-server result also omitted modelContextWindow, leaving an older backend's
persisted measurement visible. Manual compaction did not invalidate that measurement.

## Alternatives

- Change Enter for every reply: breaks multiline composition and Claude built-ins.
- Guess a percentage after compaction: creates a new misleading measurement.
- Reinterpret generic token usage fields: changes existing billing semantics.
- Carry explicit context occupancy separately and improve this exact command:
  selected.

## Decision

Exact, single-line /compact uses Enter only when the current catalog advertises a
handled builtin. Partial commands complete as before, Shift+Enter remains a newline,
and Claude's unhandled builtin keeps its existing behavior. Progress is persistent
until success or failure, and failures preserve the command for retry.

Codex app-server reports modelContextWindow and last.inputTokens. The latter
already includes cached input; store it as explicit context_used rather than adding
cached tokens again. Other backends retain the existing input-plus-cache fallback.
The optional field leaves usage counters unchanged.

Successful manual compaction clears the previous window and occupancy. The next
normal turn provides a new measurement. Failures retain the previous stats. No
session history or session ID is rewritten by the display fix.

These implementation choices are AI decisions, not separately approved by the
human; they can be revised without removing truthful measurements or usable input.

## Limits

The badge describes the last measured turn, not live token-by-token occupancy.
A missing provider measurement stays unknown. This change does not claim to have
reproduced a browser process freeze; the observed completion-only interaction and
stale meter are independently tested defects.

## Related

- [Issue #1328](https://github.com/ebibibi/ebi-agent-chat-relay/issues/1328)
- [Console](../console.md)
