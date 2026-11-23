---
type: adr
id: ADR-0025
title: Let a resident secretary agent lay out the Console as a canvas of widgets
decision: Add a third display mode, canvas. One long-lived Console conversation acts as the secretary; it keeps a note per thread, is woken only when the board changed, within a daily cap, and places structured widgets that the Console draws deterministically. Classic and secretary modes are unchanged.
status: rejected
retired: 2026-11-23
date: 2026-11-14
decided_by: human+ai
deciders: [Masahiko Ebisuda, Claude]
scope: repository
---

# ADR-0025: Let a resident secretary agent lay out the Console as a canvas of widgets

> **Status: rejected and retired (2026-11-23, owner's decision after trial: not adopted).**
> Canvas mode has been removed from the code (#1552). See the retirement note at the end.

## Context

Secretary mode (ADR-0024) went through two versions in one day. v1 had the cheapest model
shorten each thread's last message; the cards explained nothing and could not be answered.
v2 had the asking agent write a decision request (a six-line card with the full text folded
under it). The person approved the request format, but the mode itself still felt like the
classic Console: only the second column changed, and the person still had to work out the
order, what belongs together and how long it all takes.

What the person asked for instead: "a secretary that keeps every thread in mind and leads me to
what to do", allowed to "use the whole web screen to show what to do now, and to move at any
moment". Two constraints carried over from ADR-0024: no secretary that re-reads every thread on
a timer, and nothing the person cannot answer from the screen alone.

## Decision

Add **canvas mode**, a third display mode chosen per browser beside classic and secretary.

### The secretary is one long-lived agent conversation

- It is an ordinary Console conversation (same runner, transcript, `/model`, Stop), created
  when the person first starts it from canvas mode. A deployment that never starts it pays
  nothing.
- **It remembers through notes, not re-reading.** The relay keeps one short note per thread
  that the secretary writes (`PUT /api/secretary/notes/{thread_id}`). On a wake it asks for a
  **digest of what changed since its cursor** (`GET /api/secretary/digest?since=`): only the
  threads whose status, activity or decision request changed, each with its note, its open
  request, its family's `doing`/`decided` and its last few messages. Because the notes live
  in the relay, the conversation can be restarted (to reset a full context) without losing
  what the secretary knew.
- **It is woken by changes, never by a timer.** A watcher compares a fingerprint of the board
  (status, last activity and open request of every item; no model) every 30 seconds. A wake is
  a short message into the secretary's conversation. It is sent only when all hold: something
  changed (the secretary's own conversation excluded), the secretary is not already running,
  at least `CCDB_CANVAS_MIN_INTERVAL` seconds (default 600) passed since the last automatic
  wake, so bursts coalesce, the day's automatic wakes are under `CCDB_CANVAS_MAX_WAKES_PER_DAY`
  (default 24), and the person has not paused it. What the person types, and widget buttons,
  always go through and are not counted: they are the person's own requests.
- **Model.** `claude` with `sonnet` by default (`CCDB_CANVAS_SECRETARY_BACKEND`,
  `CCDB_CANVAS_SECRETARY_MODEL`). v1 failed on its input (one footer line), not only on its
  model; the secretary reads notes, decision requests and recent messages, which a mid-sized
  model handles. The person can still switch the conversation with `/model` like any other.
- **Usage is on screen.** The canvas header shows automatic wakes today against the cap, the
  secretary's context occupancy, and an estimate of today's input tokens (the context size
  measured after each of today's turns, summed). It is an estimate, labelled as one: cached
  input is billed below that figure.

### The screen is the secretary's canvas

- Chat on the right (the secretary's conversation), widgets on the left. The thread list is
  not shown; a widget or the secretary can open a thread, and the person can switch back to
  the classic mode at any time.
- **Widgets are registered data, as pages are (ADR-0018).** The secretary sends
  `PUT /api/canvas/widgets/{key}` with a `kind` and structured `data`; the Console draws each
  kind with its own fixed renderer through `textContent` and the existing sanitised Markdown.
  No HTML or script from a model reaches the page. Kinds in v1: `decision` (a v2 decision
  request by thread, drawn as the same card), `diff`, `table`, `markdown`, `timeline`,
  `agents` and `metric`. Each is validated by deterministic rules with size limits and refused
  with 400 and every problem listed, like decision requests.
- **Buttons talk to the secretary, decisions to the asker.** A decision card answers the
  asking thread directly through the v2 path (the request closes, the change wakes the
  secretary). Any other widget may carry buttons whose text is sent to the secretary as the
  person's message.

### Addendum (#1360): focus sessions and today's timetable

The person asked for two more views on the same foundation. Both reuse the canvas rather than
add a mechanism: a **focus session** is a full-screen queue of the open decision requests,
optionally ordered and given material by the secretary (`PUT /api/canvas/session`, validated
like widgets, material drawn by the widget renderers), answered through the v2 answer path and
reported back to the secretary in one message when it ends. **Today's timetable** is one more
widget kind, `schedule`, with three lanes (the person, agents, waiting on others) on one time
axis; any widget can be shown full screen. ccdb reads no calendar for it: calendar access stays
with the agent's own tools, so the relay gains no new credential.

### Addendum (#1374): the way to the source, and review cards with material

The first trial found dead ends: rows and messages that named a thread but did not link to it,
no way back once a thread was opened, and a review card that said "check the answer" while the
answer was only an attachment the card did not show. Every piece from a thread now links to it
and the opened thread offers the way back. Whether a card has anything to look at is decided by
deterministic rules (`material_state`): delivered files are shown on the card with text files
inline; a card with nothing to look at sends the person to the thread instead of asking for a
review; and a 👀 request whose materials only point at an attachment is refused, because
attachments are delivered after the mark and would not be on the card yet.

### Revision (2026-11-15): every turn a fresh session; limits change live

**What went wrong.** The first full day reached the cap of 24 wakes and an estimated 4.1M input
tokens. Each wake resumed one growing conversation, so every turn re-read all earlier turns
(about 220k tokens of context by the end of the day), and each turn makes several API calls.

**Decision.**
- **Every secretary turn starts a new agent session.** The relay replaces the turn's prompt with
  a brief: the standing instructions, counts of notes, unread changes and widgets, the last six
  messages of the conversation, and the turn's own message. The transcript stays one
  conversation for the person; the agent's context does not grow. Its memory was always meant
  to be the notes, the digest and the canvas.
- **Turns run in an empty directory with `--setting-sources ""`**: no CLAUDE.md, skills or user
  settings. Measured on production data with Sonnet, one read-only wake: from the home
  directory about 58k tokens per API call (175k for the turn's three calls, $0.22); isolated
  about 31k per call (94k for the turn, $0.11). Most of what remains is the CLI's own baseline.
- **The default cap is 8 automatic wakes a day**, and the person changes the cap and the interval
  from the canvas (**⚙**). The values are stored in the relay and apply from the next look,
  without a restart.

## Consequences

- One resident conversation costs tokens on every wake. The defaults bound it to 24
  automatic wakes a day, at least ten minutes apart; the person's own messages are extra.
  Raising the cap is an environment change, not a code change.
- The secretary is only as current as its last wake: between wakes the canvas shows what it
  last placed. Decision cards read the live store, so an answered question disappears at once.
- A widget the secretary forgets to remove stays. Widgets record when they were last updated,
  and the Console shows it on each, so a stale widget looks stale.
- Notes and widgets are text in the relay database and are never executed. **Only the
  secretary's conversation may write them.** Starting (or restarting) the secretary issues a
  random write key, given only in its charter; widget, order, note and deliver calls without it
  are refused with 403, and a digest read without it never moves the cursor. Reads stay open
  like the rest of `/api/*`. This keeps other agents (or a prompt injected into one) from
  planting notes the secretary would trust, placing buttons with hidden words, or answering on
  the person's behalf. It is not a boundary against code running as the same OS user, which can
  read the database directly. What `/api/secretary/deliver` sends is labelled as relayed by the
  secretary, and a widget button shows the words it will send before it is pressed.
- If the trial fails, the mode is removed with its tables; classic and secretary modes do not
  depend on it.

## Retirement (2026-11-23, #1552)

**Decision.** The owner tried canvas mode and rejected it ("not adopted; remove the feature").
It is removed, not hidden behind a switch.

**Removed.** The canvas screen, its widgets and renderers, the focus session, the "this screen's
AI" bar, the change watcher and its wakes, the fresh-session brief for the secretary's turns,
the write key, and the routes `/api/canvas/*`, `/api/secretary/{digest,notes,deliver}` and
`/console/api/canvas/*`. A browser that still has canvas mode saved opens in secretary mode.

**Kept, because other features use them.**
- The night settings, the 🌙 night shift and the morning brief (#1476, #1511) had been wired
  through the canvas objects. They now have their own modules (`console/night_desk.py`,
  `console/night_routes.py`, `ext/night_api.py`) and keep reading and writing their values in
  the `canvas_state` table, so nothing the person chose is lost. `/api/secretary/morning` and
  `/api/secretary/night` keep their paths.
- The per-turn AI (`turn_ai.py`) is used by automation entries and stays. The Console reply's
  `ai` field stays as a generic per-turn override.
- Secretary mode and decision cards (ADR-0024) never depended on canvas mode.

**Data.** Contrary to the last consequence above, the tables are **not** dropped: deleting
production data needs the owner's approval, and an unread table costs nothing.
`canvas_widgets`, `canvas_notes`, `canvas_changes` and `canvas_turns` stay in the database and
are no longer read or written; `canvas_state` stays in use for the night values, and its
canvas-only rows (the secretary's thread, cursor, key, limits) are simply ignored. The
secretary's conversation remains an ordinary Console conversation; nothing wakes it any more.
