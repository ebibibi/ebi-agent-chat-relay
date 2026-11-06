---
type: adr
id: ADR-0017
title: Keep a family's situation beside the conversation, per thread
decision: Show a panel that does not scroll with the conversation; measure what can be measured and store what only the parent can say per thread, not in the Workspace's status.md.
status: accepted
date: 2026-11-06
decided_by: ai
deciders: [Claude]
scope: repository
---

# ADR-0017: Keep a family's situation beside the conversation, per thread

## Context

A parent thread coordinating several children receives a steady flow of reports. Whatever the
person is being asked scrolls away, and the current state has to be rebuilt by reading back.
Measured on one real parent thread, 172 of 959 messages were relays, about 48% of its characters.

The obvious home for "where things stand" is the Ebi Workspace `status.md`, which the Console can
already show folded above the conversation (ADR-0015 added the delegated-work panel beside it).
It does not fit as the only source:

- The same real parent was bound to a whole context (recorded from its `log.md`), which has no
  `status.md` at all, so the panel would have been empty for the case that motivated it.
- One project can have several families at once, each with its own open questions. A shared file
  would mix them and have several writers editing one section.
- `status.md` is the project's record, read by the morning plan and the weekly review. The Workspace
  rules keep it to actions and waiting-for; "what I am asking you this hour" is neither.
- The Workspace is optional. A family exists without one.

## Decision

Show a panel beside the conversation of any thread family, with three fixed headings: what is needed
from the person, what is running, what is decided and done. Below a width of 640 px it becomes a tab
next to the conversation and the reply box stays visible, because the point is to read a question and
answer it.

Two sources, kept visibly apart:

- **Measured, needing no agent:** thread status, running and queued turns, active waits, finished
  children. These cannot go stale and exist from the first child on.
- **Written by the parent:** `asks`, `doing` and `decided`, through
  `GET`/`PUT /api/threads/{id}/situation` into the `thread_situation` table. A write replaces only
  the fields it names, so dropping an answered question does not mean resending every decision. Unknown fields are
  refused so a typo cannot leave the panel silently stale. The panel states how old the record is and
  flags one older than the family's latest activity.

The agent is told about it in its run context, on every turn. A bound Workspace entry stays visible in the
same panel, so the project's `status.md` is still one glance away. Project-level facts still belong there
and in `log.md`; the situation is about this family, now.

An ask can be quoted into the parent's reply box in one click, from a child too, which opens the parent
first. This keeps ADR-0015's convention that decisions go to the parent.

Separately, prompts the relay starts a turn with (relays, finished waits, restart resumes) are classified
by author as `activity`. They were stored without the bot flag so the agent receives them as prompts, and
were shown as if the person had written them. Agent answers in such a turn stay visible and are labelled.

## Consequences

- The written part is only as current as the agent keeps it, which the staleness note makes visible rather
  than solves. The measured part does not depend on it.
- There is one more place a parent writes state. It is deliberately short-lived, and the instruction tells
  agents to keep decisions in it and project facts in the project's own records.
- `status.md` is not edited by the panel and gains no new sections, so goodmorning and the weekly review
  keep reading what they read today.

## Alternatives considered

- **Render `status.md` in the panel and nothing else.** Rejected for the reasons above: empty for
  context-bound and Workspace-less threads, and shared between families.
- **Add asks and decisions as new `status.md` sections.** Rejected: it conflicts with the Workspace's rule
  against custom sections and leaves several families writing one file.
- **Have a model summarize the parent's conversation.** Rejected as the source of asks: it costs a call per
  change and a person would have to trust a guess about what they were asked. Triage's existing next-step
  guess is still shown under a measured row, where it is already labelled as a guess elsewhere.
