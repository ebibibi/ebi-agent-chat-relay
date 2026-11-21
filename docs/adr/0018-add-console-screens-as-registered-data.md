---
type: adr
id: ADR-0018
title: Add Console screens as registered data, not loaded code
decision: A screen is a named list registered over the REST API and drawn as text by the Console. No extension code is loaded into the browser.
status: accepted
date: 2026-11-06
decided_by: ai
deciders: [Claude]
scope: repository
---

# ADR-0018: Add Console screens as registered data, not loaded code

## Context

Agents finish a lot of work and then leave one step for a person: send this post, submit this form.
That step was handed over as a link in whichever thread produced it, and it was lost when the thread
scrolled away or closed. A list of those steps is useful to anyone running the relay, but what goes
on the list differs per deployment, so a fixed built-in screen for one kind of step would not do.

The Console had no way to add a screen other than changing its code. The obvious extension point
mirrors the custom Cog loader: a directory of JavaScript the Console loads.

## Decision

A page is data. `PUT /api/pages/{slug}` defines it and `POST /api/pages/{slug}/items` fills it. The
Console renders every field through `textContent`, stores only `http` and `https` links, and opens
them in a new tab with no referrer.

- **Registered data, not loaded code.** The Console acts with the signed-in person's authority: it can
  reply into any session, and those sessions run arbitrary commands. A script in that origin has all
  of it. Registering text cannot escalate to that, so a session may add a screen without anyone
  reviewing code.
- **Zero configuration.** Updating the package brings the feature; the first registration brings the
  screen. Nothing is deployed, which also means an agent can do it mid-turn.
- **Generators cannot undo people.** Registering an existing key keeps its state, because generators
  retry and a finished item coming back would destroy the list's meaning.
- **Who resolved an item is recorded.** The Console writes `person` itself and ignores what the browser
  sends, so a click can be told from an automated check.
- **The relay knows nothing about what is on a page.** Whoever can observe an outcome lists the open
  items and resolves them. Matching a post against a network belongs to the job reading that network.

## Consequences

- A page is one list with fixed actions, not arbitrary layout. If a screen that does not fit is ever
  needed, a `kind` on the page is the place to add it. That is deliberately not built before a real
  case exists.
- Pages are defined with the same trust as the rest of `/api/*`: whoever can reach the REST API can add
  one. Text and links are the most that can result.
