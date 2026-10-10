---
type: adr
id: ADR-0011
title: Let the Relay Console own its conversations
decision: Work started from the Relay Console runs in a conversation the console owns — a ConversationSurface/SessionFrontend backed by its own tables and keyed through the frontend_threads ledger — and goes through the same session runner as Discord and Teams. No chat thread is created.
status: accepted
date: 2026-10-10
deciders: [Masahiko Ebi, Claude]
scope: repository
supersedes:
superseded_by:
---

# ADR-0011: Let the Relay Console own its conversations

## Context

ADR-0010 shipped the console as a view over chat threads: "start" called `spawn_session`, which
opened a Discord thread, and replies and history went through Discord. Every task written down in
the console therefore also appeared as a Discord thread, and the console could not start work
without a chat platform. ADR-0010 named the fix as its follow-up: a `SessionFrontend` for the
console.

## Options considered

1. **Keep spawning chat threads and hide them.** No new code, but the console stays a client of
   Discord, and Teams-only or chat-free deployments still cannot use it.
2. **A separate engine for the console.** Duplicates backend selection, resume, slots, the
   registry, the lounge and the completion gates, and would drift from them.
3. **A console frontend on the existing seam.** `ConsoleSurface` / `ConsoleFrontend` implement the
   same protocols as Discord and Teams; `ConsoleSessionHost` builds the runner the way
   `TeamsSessionHost` does and calls `run_claude_with_config`. Chosen.

## Decision

- `POST /console/api/items/{id}/start` registers the capture in `frontend_threads` under the
  frontend name `console` and runs the turn on a `ConsoleSurface`. The resulting item id is
  `t<thread_key>`, so the board, lineage, slots, the registry and the session table treat it like
  any other thread.
- The transcript and the conversation's name (with its outcome marker) live in
  `console_conversations` / `console_messages`. Only what a reader needs afterwards is stored:
  answers, questions, warnings and errors. Live progress (tool activity, status) is not.
- A reply resumes the same session. Turns of one conversation run one at a time; a reply waits for
  the turn in flight rather than interrupting it.
- Outcome markers: the agent's `/api/threads/{id}/done|waiting|review|action` and the runner's own
  error/waiting markers land on the console conversation. The runner reaches them through an
  optional `set_outcome` on the surface, so a surface with no title of its own is unaffected.
- AskUserQuestion on a surface without buttons writes the questions into the conversation and ends
  the turn waiting; the human's reply resumes the session with the answer.
- Chat threads are still read and replied to as before. Only work *started* from the console is
  the console's own.

## Consequences

- Starting work from the console no longer depends on Discord or Teams.
- With `CCDB_FRONTENDS` omitting `discord`, the process does not log in to Discord at all
  (#891): the bot object only hosts the Cogs, `wait_until_ready()` does not block, and the console
  replaces Discord as the router's primary frontend, so scheduled tasks open console
  conversations.
- No live progress in the console yet; the board shows that a turn is running, and the answer
  appears when it is written.
- Files the agent delivers are listed by name and path; there is no download endpoint yet.
