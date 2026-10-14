---
type: kb
title: Codex active-writer conflicts across entry points
symptom: Scheduled follow-ups and replies fail with already has an active writer
status: solved
date: 2026-10-15
updated: 2026-10-15
component: [SessionSlotScheduler, CodexRunner]
tags: [concurrency, codex]
scope: context
context: ebi-agent-chat-relay
---

# Codex active-writer conflicts across entry points

## Symptom and cause

`thread-store conflict: ... already has an active writer` can occur when a
scheduled follow-up and a reply resume the same conversation concurrently.
The chat Cog's per-thread lock covers chat arrivals, but the scheduler calls
the shared run helper independently. A global concurrency limit alone does
not prevent two slots from belonging to the same conversation.

The natural-exit drain in CodexRunner (#665 / #694) addresses a different
race: a turn can finish before its process releases the writer. Check whether
that fix is already present before attributing every writer conflict to it.

## Fix and verification

The shared slot scheduler grants at most one running slot per thread key.
It skips blocked follow-ups when filling other free slots, so unrelated
conversations retain concurrency. Priority changes and limit increases must
also preserve this constraint.

Run `uv run pytest tests/test_session_slots.py tests/test_run_helper.py -q`.
Regression tests cover exclusion, unrelated progress, priority/limit changes,
waiter cancellation, and queue-wait prediction.

This constraint applies within one configured slot scheduler. It does not
coordinate separate bot processes or independent CLI clients sharing a Codex
session. Do not remove CLI lock files or kill another process to hide a
conflict. The production rollout still needs runtime validation.

Related: [Issue #1040](https://github.com/ebibibi/ebi-agent-chat-relay/issues/1040).
