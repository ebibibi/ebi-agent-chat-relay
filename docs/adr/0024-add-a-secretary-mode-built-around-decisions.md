---
type: adr
id: ADR-0024
title: Add a secretary mode built around decisions, without new model calls
decision: Add a second Console display mode whose home lists at most three decisions, worded by fields the existing per-item triage call adds, and assembled without a model. The classic mode is unchanged.
status: accepted
date: 2026-11-14
decided_by: human+ai
deciders: [Masahiko Ebisuda, Claude]
scope: repository
---

# ADR-0024: Add a secretary mode built around decisions, without new model calls

## Context

The Console's unit is the agent thread. Its main user rated it 40/100 for ease of use. In the
latest 100 threads, about 36 were waiting on that person (9 asked a question, 7 had something
to review, 3 needed a manual step, 4 had failed, 13 were finished and needed confirming). Each one
had to be opened and re-read to learn what was being asked. Focus ranks threads, and its guide
names the next one, but it still hands over a thread rather than a decision.

The obvious fix is a "secretary" agent that re-reads every open thread on a timer and writes a
digest. The user rejected that cost: every pass reads every thread again, whether or not
anything changed.

## Decision

Add **secretary mode** as a second display mode, switched per browser. The classic mode keeps its
look and behavior; the only things it gains are the switch and a silent record of opened threads.

- **The same triage call says what is asked.** For an item whose next move is the person's
  (`waiting`, `review`, `action`, `error`), the per-item triage prompt also asks for `ask` (one
  sentence), `suggest` (the recommended reply) and `options` (0–3 ready-made replies). Other items
  get the prompt and the answer they always had. The number of calls stays the same: there is
  still one call per item with new activity, at most six a minute.
- **No prompt-version bump.** Bumping `PROMPT_VERSION` would re-triage every open item. Instead,
  a row with no decision recorded (`ask IS NULL`) is due once more only while its item waits on the
  person. `""` records "asked, nothing to decide", so that row is never asked again.
- **The home screen is a pure function of the board** (`secretary.py`, like `guide.py`). It has at
  most three decisions ranked like Focus, then items to review, agents at work and today's finished
  work. There is no model and no state, so every poll gives the same answer twice.
- **Answers use the existing reply endpoint.** A card's button or short reply goes to the thread as
  if typed there, and the agent carries on. In this version, agents, threads and notifications are
  not changed.
- **The trial is measured, with metadata only.** Opens and card answers are recorded with the mode
  and how long the question had waited. That measures the trial's two questions: how many threads
  are opened per day, and how long decisions stay pending.

## Consequences

- An item triaged before this change shows its next step on the card until it is asked again,
  which happens once, at the same rate limit as any other triage.
- The cheapest model writes the question and the recommended answer. The card always shows the
  thread's title and a way into the thread, and one click sends only the text the button displays.
- A later stage can move the agents themselves to talk to the person through this screen, or send
  web-push notifications for "decide now" only. Neither is in scope until the trial shows the mode
  helps.
