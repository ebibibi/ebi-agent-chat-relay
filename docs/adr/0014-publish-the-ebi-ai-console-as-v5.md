---
type: adr
id: ADR-0014
title: Publish the Ebi AI Console as version 5
decision: Treat the web GUI (the Ebi AI Console) as the v5 product boundary, while keeping every 4.x runtime interface — frontends, backends, REST API, settings — compatible.
status: accepted
date: 2026-10-13
deciders: [Masahiko Ebisuda, Claude]
scope: repository
supersedes:
superseded_by:
---

# ADR-0014: Publish the Ebi AI Console as version 5

## Context

ADR-0006 set the v4 boundary at "multi-frontend, multi-backend relay": Discord and Teams in front,
Claude Code, Codex, a local model and AG-UI behind. Through 4.x the console (ADR-0010) grew from a
triage list into a full GUI: it owns conversations (ADR-0011), signs in with passkeys (ADR-0012),
triages work with a model, previews and attaches files, pins work, and controls running sessions
and backends.

None of that breaks a 4.x deployment. But what the project *is* changed: it is no longer only a
relay you reach through chat apps — it has a screen of its own, and that is how most people will
first meet it.

## Decision

Release the GUI, renamed from "Relay Console" to **Ebi AI Console**, as **5.0.0**.

- The version marks the product boundary, as v4 did, not an incompatible API.
- Every 4.x interface stays: the console's URLs, `CCDB_CONSOLE_*` settings, the `[console]` extra,
  the JSON API, the REST API, both frontends and all backends.
- The console stays opt-in (`CCDB_CONSOLE_PORT`).
- Two defaults change for people who enable the console, and the release notes say so: automatic
  triage is on (`CCDB_CONSOLE_TRIAGE=0` turns it off), and Focus is the default view.

## Consequences

- The README and release notes lead with the GUI; `docs/releases/v5.0.0.md` explains the boundary
  and compatibility, as `v4.0.0.md` did.
- Release checks accept any version from 4 up, and check the v5 notes alongside the v4 ones.
- Later GUI work ships as 5.x minor and patch releases. Scheduler integration is next.
