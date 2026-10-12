---
type: adr
id: ADR-0013
title: Keep the relay an interface layer; leave governance to an external control plane
decision: The relay launches and drives unmodified agent CLIs and owns the chat and console experience. Identity mapping, policy decisions, audit evidence, budgets and execution-environment provisioning belong to an external control plane. The only integration point is "start a session for this person in this execution environment". One instance has one owner, and account pools only hold that owner's own logins.
status: accepted
date: 2026-10-12
deciders: [Masahiko Ebi, Claude]
scope: repository
supersedes:
superseded_by:
---

# ADR-0013: Keep the relay an interface layer; leave governance to an external control plane

## Context

With the Relay Console, the relay is becoming a full user interface. Operators now ask for it
to serve teams: several people talking to one bot, per-user limits, audit trails, policy. Every
one of those features could be built inside the relay, but each also exists one layer down:

| Layer | What it is | Who owns it |
|---|---|---|
| Interface | Discord, Teams, Relay Console, terminal, IDE | the relay (one interface among several) |
| Execution | unmodified Claude Code / Codex running in an execution environment | the vendor CLIs |
| Governance | who is who, which seat and environment they use, allow/deny decisions, audit evidence, budgets, distribution of hooks and managed settings, provisioning of environments | an external control plane |
| Inference and billing | each person's own subscription seat, or an operator-billed API lane | the vendors and the operator |

Two forces decide where governance must live:

1. **Bypass.** Anything enforced only inside the relay is skipped by a person who runs the
   same CLI from a terminal. Controls that live in the execution environment (managed
   settings, managed hooks, OS permissions, egress rules) hold for every interface.
2. **Vendor terms.** Claude Code may be hosted, but each end user must sign in with their own
   credentials, and a third party must not route requests through Free/Pro/Max credentials on
   behalf of other users or collect, store or intermediate those credentials
   ([Legal and compliance](https://code.claude.com/docs/en/legal-and-compliance), checked
   2026-10-12). A relay instance that many people talk to, backed by one person's plan, is
   exactly that.

## Options considered

1. **Grow the relay into a multi-user platform** (users, roles, quotas, audit inside the relay).
   Rejected. Duplicates the control plane, is bypassed from the terminal, and invites one
   instance to serve many people on one seat.
2. **Make the relay depend on a specific control plane.** Rejected. The relay is OSS and must
   stay useful on a single machine with nothing else installed.
3. **Keep the relay an interface layer with one integration point** (chosen).

## Decision

- The relay launches unmodified agent CLIs and owns session orchestration, concurrency,
  priorities, the conversation tree and the chat/console UX.
- The relay does not implement multi-user identity, role-based access, policy decisions,
  governance-grade audit or budgets. Its own logs stay operational logs.
- The relay injects nothing that governance depends on. Hooks and managed settings come from
  the execution environment, so they apply whether a session starts from the relay or a
  terminal.
- **One integration point:** "start a session for person P in execution environment E"
  (ADR-0008 already lets the operator choose the environment). Nothing else couples the relay
  to a control plane.
- **One owner per instance.** Serving several people means one instance and one environment
  per person, provisioned by whatever control plane the operator uses — not one shared
  instance.
- **Account pools (ADR-0009) only contain the instance owner's own logins.** Pools must never
  put one person's turns on another person's seat.

## Consequences

- A test for new feature requests: "would this still hold if the person ran the CLI from a
  terminal?" If it must, it belongs in the execution environment or the control plane, not
  here.
- Team deployments need a provisioning layer outside the relay. The relay stays small and
  readable, which is part of its pitch.
- Docs for ADR-0009 and the console should say that pools and console accounts are for the
  instance owner, not for sharing a plan.
