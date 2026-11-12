---
type: adr
id: ADR-0023
title: Persist relative pin moves atomically
decision: Store pin positions on the server and move one pin relative to another under a write transaction.
status: accepted
date: 2026-11-12
decided_by: ai
deciders: [Codex]
scope: repository
---

# ADR-0023: Persist relative pin moves atomically

## Context

Console pins were ordered by pin time. People need their own order across
reloads and devices, including when only part of the list is visible.

## Alternatives

- Local browser storage cannot share order with another device.
- Replacing the complete ordered list from a client risks dropping pins hidden
  by a filter or added since that client last refreshed.
- Rewriting pin timestamps would confuse when an item was actually pinned.

## Decision

Keep pin timestamps and persist a nullable integer position separately. New pins
are unranked and lead the saved order. A relative before/after request reads the
current pins and assigns positions inside a SQLite write transaction. Ordinary
overlay writes preserve positions; changing pin membership clears the old rank.
Moving a pin never updates activity timestamps, priorities, or family relations.

Offer always-visible ↑/↓ buttons with accessible labels, keyboard activation
and touch targets. Family grouping remains intact, with the representative pin
moving the family. No drag gesture is required on narrow screens.

## Consequences

The existing database gains one nullable column automatically. All devices use
the same order. Concurrent relative moves serialize without replacing membership;
a move targeting an unpinned item fails without changing the order. Reordering
updates the small set of pinned rows rather than every work item.


## Follow-up: chronological Recent and boundary moves (2026-11-12)

The user requested a strictly chronological Recent view. It bypasses both pin
promotion and family regrouping, since either would move an older item above a
newer item. Other views keep family grouping and saved pin order. First/last
controls target the first/last visible pin with the same relative API, preserving
hidden pins. Four controls use two columns to fit narrow screens.
