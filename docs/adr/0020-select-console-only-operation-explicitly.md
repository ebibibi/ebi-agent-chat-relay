---
type: adr
id: ADR-0020
title: Select Console-only operation explicitly
decision: Reuse CCDB_FRONTENDS with an explicit CLI override and route native work without a Discord fallback.
status: accepted
date: 2026-11-11
decided_by: ai
deciders: [Codex]
scope: repository
---

# ADR-0020: Select Console-only operation explicitly

## Context

The operator requested an explicitly selectable mode that works without Discord
and does not create Discord threads. Headless startup already existed, but the
control-plane spawn and ingest routes still required Discord channels. Startup
resume also depended on Discord's ready event.

## Options considered

- Remove Discord globally: breaks existing installations and integrations.
- Add a second mode environment variable: creates two conflicting authorities.
- Fall back to Discord when native creation fails: violates the selected mode.
- Reuse `CCDB_FRONTENDS`, add a CLI override, and route work natively: selected.

## Decision and rationale

`ccdb start --mode console` selects Console alone after loading dotenv, supplies
local port defaults, and allows a fresh installation without an env file.
Services persist `CCDB_FRONTENDS=console`; the original default remains Discord.

Create conversations through ConsoleSessionHost, with child preferences and
lineage saved before launch. Pass ingest's result callback through the shared
runner, preserving its authentication and result retrieval contract. Read native
history from the same transcript repository. Never use Discord as a fallback in
this mode, including stale channel IDs and lounge mirrors.

Restore pending native turns after the host is built. Leave legacy Discord
resume markers untouched until their normal expiry rather than fetching their
channels or accidentally consuming them.

These implementation choices are AI decisions, not separately approved by the
human; they may be revised without changing the requested Discord-free behavior.

## Consequences and limits

The framework still uses discord.py internally as its Cog lifecycle host. No
Discord network connection is needed. This change does not migrate historical
Discord threads, remove the Discord package dependency, or switch a running
operator deployment automatically. Custom Cogs remain responsible for their own
external side effects. Console authentication and the local control-plane trust
boundary are unchanged.

## Related

- [Console-only operation](../console.md#run-without-discord)
- [Issue #1317](https://github.com/ebibibi/ebi-agent-chat-relay/issues/1317)
