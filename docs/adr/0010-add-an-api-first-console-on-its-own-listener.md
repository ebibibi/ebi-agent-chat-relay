---
type: adr
id: ADR-0010
title: Add an API-first console on its own listener
decision: Manage agent work (priority, due date, snooze, project, tree) in a Relay Console served on a separate, always-authenticated listener, with a JSON API as the contract and a dependency-free web client as its first consumer; derive status from state ccdb already keeps instead of asking agents to report it.
status: accepted
date: 2026-10-10
deciders: [Masahiko Ebi, Claude]
scope: repository
supersedes:
superseded_by:
---

# ADR-0010: Add an API-first console on its own listener

## Context

One operator runs dozens of agent threads in parallel. The chat sidebar shows them as one flat,
chronological list. Threads waiting on the human (❓👀📋) sit next to threads where the agent is just
working. Fan-outs have no visible parent. Nothing carries a priority or a due date. Past a certain
number of threads, finding the next thing to do takes longer than doing it. #768 proposed a
first-party frontend but left open what it should replace and how to measure it.

## Options considered

1. **Encode priority and due dates in thread titles.** No new surface, but a title is 100
   characters, renames are rate limited (two per ten minutes), and the list still cannot be
   sorted or nested.
2. **A native desktop/mobile app first** (Tauri, Flutter, MAUI). Better notifications, but weeks
   of distribution work before the first day of use.
3. **Endpoints on the existing control plane.** Least code, but the control plane is privileged
   (spawn, schedule, post as the bot) and trusts every caller on loopback. Exposing any of it
   through a tunnel makes it remotely reachable.
4. **A separate console listener with its own narrow API, plus a web client.** Chosen.

## Decision

- The console runs on its own listener (`CCDB_CONSOLE_PORT`) and never shares routes with the
  control plane. It exposes only board, history, item changes, reply, done/reopen and start.
- Every API request is authenticated, either with a Cloudflare Access JWT (signature, audience,
  issuer and an email allowlist are all verified) or with a static bearer token for local
  clients. A configuration that would leave the console open is refused at startup. The listener
  does not fall back to a weaker mode.
- Mutations require a custom header (`X-Console-Request: 1`) and are rate limited per identity.
- Status is **derived**, not stored: the outcome marker, running turns, the slot queue and
  `thread_lineage`. `work_items` stores only what nothing else knows (priority, due, snooze,
  project, parent override, title override, captures). Agents change nothing.
- `/console/api` is the contract. The bundled web client is one consumer; a terminal or desktop
  client can be added later without server changes.

## Consequences

- Day-one value with no migration: every existing thread appears, already classified.
- Conversations still live in the chat platform in this version. A reply from the console is
  posted into the thread and queued like a human reply. A frontend that owns its conversations
  (`SessionFrontend` for the console) is the follow-up.
- A console reply is posted by the bot, so it does not yet count toward attention metering.
- Listing archived threads costs one REST call per watched channel, so the result is cached for
  two minutes.
