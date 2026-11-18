---
type: kb
title: Decision review reports JSONDecodeError after a backend timeout
symptom: decision review failed follows a triage backend timeout
status: solved
date: 2026-11-19
updated: 2026-11-19
component: [console, decision_cleanup, triage_models]
tags: [timeouts, decision-review]
scope: context
context: ebi-agent-chat-relay
---

# Decision review reports JSONDecodeError after a backend timeout

## Symptom and cause

The Console logs `decision review failed` with `JSONDecodeError: Expecting value`
after the triage backend reports a timeout. `ask_codex()` returns `None` when it
cannot answer, but the reviewer used to parse that as an empty JSON string.
The exception handler kept the decision cards open and scheduled a retry;
the traceback was a secondary error, not evidence that stored JSON was corrupt.

## Resolution

`DecisionReviewer` now checks for absent or blank replies before JSON parsing.
It keeps every card open, logs a warning without model output, and retries after
the existing 60-second cooldown. Other threads can still be reviewed. A later
valid answer must still contain evidence from a later human message to close a card.
Nonempty invalid JSON or invalid response structure remains an error.

Run the regression tests with:

```bash
uv run pytest tests/test_decision_cleanup.py tests/test_console_triage_models.py -q
```

This change handles missing answers; it does not fix or extend backend timeouts.
Treat the backend's optional return value explicitly at its consumer rather than
turning a transport failure into a parsing failure.

Related: [Issue #1459](https://github.com/ebibibi/ebi-agent-chat-relay/issues/1459).
