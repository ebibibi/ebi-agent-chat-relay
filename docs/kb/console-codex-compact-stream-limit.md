---
type: kb
title: Codex compact returns HTTP 500 on a long conversation
symptom: /compact fails with ValueError or LimitOverrunError before compaction starts.
status: solved
date: 2026-10-14
updated: 2026-10-14
component: [console, codex-compact]
tags: [codex, compact, asyncio, stream-limit]
scope: context
context: ebi-agent-chat-relay
---

## Cause

Console uses Codex app-server to resume the session and then request compaction. The
`thread/resume` JSON-RPC response includes conversation history in one JSON line. Python's
default asyncio subprocess stream limit is 64 KiB. A long resume response makes
`stdout.readline()` raise `ValueError: Separator is not found, and chunk exceed the limit`
(from `LimitOverrunError`). The unhandled exception becomes HTTP 500 before
`thread/compact/start` is sent. This is an input-buffer limit, not a model context limit.

## Resolution

Pass `limit=10 * 1024 * 1024` to `asyncio.create_subprocess_exec`, matching the Codex runner's
existing bound. Convert an over-limit response to `CompactError`, which the Console returns
as a JSON error with a useful explanation. The bound remains finite; conversations exceeding
it are still refused instead of consuming unbounded memory.

The existing fix is [PR #1021](https://github.com/ebibibi/ebi-agent-chat-relay/pull/1021),
commit `43da7d5`, for [Issue #1020](https://github.com/ebibibi/ebi-agent-chat-relay/issues/1020).
It must be included in the active dev worktree; a passing PR alone does not change the code
loaded by the running service. Reload the service after incorporating Python changes, using
the reserved restart so active tasks can finish.

## Verification

`tests/test_console_codex_compact.py` runs a fake app-server subprocess. A 256 KiB resume
response must reach compaction completion; an 11 MiB response must return the explicit
bounded error. Existing tests also cover unknown sessions, no-op completion, timeout and
session-ID validation. `tests/test_console_conversations.py` covers the Console command API.
These tests do not call a model or compact a real user's session.
