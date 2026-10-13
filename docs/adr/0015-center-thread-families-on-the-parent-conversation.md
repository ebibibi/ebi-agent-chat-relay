---
type: adr
id: ADR-0015
title: Center thread families on the parent conversation
decision: Show delegated work beside the open conversation and make child inspection read-only by default, with explicit direct reply.
status: accepted
date: 2026-10-13
decided_by: ai
deciders: [Codex]
scope: repository
---

# ADR-0015: Center thread families on the parent conversation

## Context

The board already merges spawned-thread lineage and manually assigned parents. Its tree view
indents descendants, but opening a parent conversation loses sight of delegated work. Users
normally speak to the coordinating parent rather than directing each child separately.

## Decision

Add a collapsible delegated-work panel to conversation details. Resolve the full family from
the existing board, include finished descendants, indent grandchildren, and show running,
parent-attention and finished counts. Child cards display a plain-text excerpt of the latest
agent message; an AI-suggested next step is explicitly labelled when used as fallback.

Opening a child keeps its conversation accessible but hides its reply form initially. Provide
links to the immediate parent and root conversation, and an explicit direct-child reply button.
This is a navigation convention, not an authorization restriction or automatic redirection of
messages. Existing per-thread drafts, attachments, activity and controls retain their targets.

Use the existing message endpoint, at most three concurrent requests, only while the panel is
expanded. Cache by activity and status. Keep conversation DOM and drafts intact during board
refreshes, preserve panel scroll, and render reports as text rather than HTML. The family panel
has a bounded height so the conversation remains usable on narrow screens.

## Alternatives

- A tree-only list preserves hierarchy but cannot show progress while reading the parent chat.
- A separate full-screen diagram consumes the space needed for conversation and reply.
- Automatically sending child replies to the root would silently change the recipient.

## Validation

- `node --test tests/console_family.test.cjs`: roots, nested completed children, counts, missing
  parents, cyclic input, and agent-only excerpts.
- `uv run pytest tests/test_console_family_client.py`: runs the model tests in the usual suite
  when Node is installed.
- `node tests/console_family.browser.cjs` with Playwright available (optional
  `CONSOLE_TEST_CHROMIUM` executable): desktop/mobile navigation, drafts on refresh, explicit
  child replies, standalone conversations, and client errors. Uses synthetic data and no live
  mutations.
