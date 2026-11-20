---
type: kb
title: Codex compact returns HTTP 500 on a long conversation
symptom: /compact fails with ValueError or LimitOverrunError before compaction starts.
status: solved
date: 2026-10-14
updated: 2026-11-13
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

## App-server chat resume can exceed even 10 MiB

Issue [#1341](https://github.com/ebibibi/ebi-agent-chat-relay/issues/1341) tracks the same
signature in `AppServerSession._read`, where the stream limit was already 10 MiB.
The daily check found 33 occurrences. The logged traceback proves a line-size overflow,
but does not identify the payload; full resume history is a plausible source.

The chat runner now sends `excludeTurns: true` with `thread/resume`: it consumes only
`result.thread.id`, so returning the entire history serves no purpose. The installed
Codex CLI 0.167.1's generated `ThreadResumeParams` schema documents this as returning
metadata and live-resume state without populating `thread.turns`. The server still
resumes the existing conversation. Fresh starts and missing-session recovery omit
this resume-only field. The bounded reader remains in place.

`tests/test_codex_app_server.py::test_resume_omits_large_unused_history` uses a real
stdio peer that returns 11 MiB of history unless exclusion is requested. Before the
change, the resume case reproduces the exact stream-limit exception; afterwards it
reaches turn completion. This verifies the resume mitigation, not the identity of the
original production payload. Oversized non-history events can still exceed the bound.
