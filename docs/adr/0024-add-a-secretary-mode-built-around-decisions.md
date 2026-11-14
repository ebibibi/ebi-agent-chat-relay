---
type: adr
id: ADR-0024
title: Add a secretary mode built around decisions, written by the agent that asks
decision: Add a second Console display mode whose home shows decision cards. In v2 each card is a structured decision request written by the asking agent and checked by deterministic rules (v1 worded cards with the triage model, and its cards could not be answered). The home is assembled without a model. The classic mode is unchanged.
status: accepted
date: 2026-11-14
decided_by: human+ai
deciders: [Masahiko Ebisuda, Claude]
scope: repository
---

# ADR-0024: Add a secretary mode built around decisions, written by the agent that asks

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

## Revision (v2, 2026-11-14): the agent that asks writes the card

### What went wrong with v1

The person rated v1 "not usable at all", worse than the classic mode: the cards had no
explanation, so nothing on them could be answered on the spot. The cause was the source of
the words. v1 had the cheapest model shorten each thread's **last message** into one line, and
a thread's last message is usually a usage footer or a CI notice. Shortening text that holds
no explanation cannot produce one. The real question often lived in another thread (a parent
gathering its children's questions), written in shorthand there too: the situation panel's
instructions asked for `asks` that are "understandable on their own" but also described them as
"short strings", and the agents wrote the short strings.

An experiment settled the alternative. The agents that had asked three real questions were each
given the same seven-part format (what this is about; what is happening and why now; what to
decide; the choices and their results; the recommendation and why; the material to decide with;
the deadline and what happens without a decision). The person could answer all three without
opening anything else, but found the full text too much to read at a glance. Two layers — a
short card with the full request folded under it — were approved.

### Decision

- **The asking agent writes a decision request**, sent as the JSON body of `/waiting`,
  `/review` or `/action`. The card: `title` (what to decide), `context` (what it is about, from
  zero), `options` (each a button label and a one-line effect, exactly one recommended for a
  question), `deadline`. The `detail` holds the seven parts in full.
- **The format is enforced by deterministic checks, never by a model** (`decision_request.py`):
  every part present, one line each, at most three choices, at most 300 characters on the card,
  all seven detail parts. A failing request is refused with 400 and every problem listed, so the
  agent rewrites it in the same turn. The bar given to agents: someone who has forgotten the
  whole matter can answer from the card alone.
- **One store, two views.** Requests live only in `decision_requests` (one open request per
  thread and key; a new one replaces the old). The secretary home is the cross-project inbox
  of all of them. A family's situation panel ("where things stand") shows the same cards for
  its own threads under "needed from you", so a parent never copies a child's question and
  there is nothing to keep in sync. The panel's `asks` text is retired: agents are told not
  to write questions there; strings are still accepted for compatibility and shown tagged
  "no request". `doing` and `decided` stay the panel's job and are not shown by the secretary
  home.
- **Answered questions go away by themselves.** An answer from a card (or any Console reply)
  closes the request; a new mark without a request or 🏁/✅ closes it too. A request shows only
  while its thread carries ❓/👀/📋 and is not running, which also covers replies typed in
  Discord. This addresses decided questions that stayed listed in a parent's `asks`.
- **No summaries.** Threads that wait on the person without a request are listed apart by title.
  The v1 triage fields are no longer requested: they cost output tokens and the cards no longer
  use them. The columns stay in `ai_triage`, unused.
- **Compatibility.** Bare marker calls and string `asks` work as before; the classic mode is
  unchanged.

### Consequences

- A card is as good as the agent that wrote it, but it can no longer be empty or a fragment:
  the checks reject those. Shorthand that passes the checks is still possible; the instructions
  name the bar so the agent writes for a reader who has forgotten everything.
- Every agent's run context grows by one section that describes the format (about 2 KB).
- Agents that do not send requests (older prompts, other relays) still show up, under
  "waiting without a request", so nothing that waits on the person disappears.
