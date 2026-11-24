---
type: adr
id: ADR-0029
title: Keep goals in one Markdown note and place threads under them
decision: The Goals view reads the ### headings of one operator-chosen note in the Ebi Workspace vault as goals and writes a person's edits back to it. Threads are placed under a goal by a person's goal:<id> tag, else by the Workspace project the thread is bound to, else by their parent. Pins stay as they are.
status: accepted
date: 2026-11-24
decided_by: human
deciders: [owner, Claude]
scope: repository
extends: ADR-0017
---

# ADR-0029: Keep goals in one Markdown note and place threads under them

## Context

Someone running dozens of agent threads sorted them with pins ("📌 working on now"). A pin says
*which* thread to touch next; nothing on screen said *what for*. The owner wanted to write down
the outcomes they are working toward in words, and see under each one which threads are running,
which wait on them, what was decided and what was delivered.

Those outcomes are already written down: the Ebi Workspace keeps projects (`status.md` with a
milestone table) and many people keep a short "current focus" note. A Console-only goal table would
be a second copy that drifts from the note people actually read and edit in Obsidian.

## Decision

- **The note is the only copy.** The operator names one Markdown note in the vault
  (`CCDB_CONSOLE_GOALS_NOTE` or `~/.config/ebi-workspace/console-goals.txt`). Inside its goals
  section (`## Goals`, `## 目標`, `## いま集中するもの` …, or the whole note), each `###` heading is a
  goal. A leading keycap number is kept and renumbered; a leading ⏸️ or ✅ pauses or completes it.
  A few labelled bullets are read with aliases — done when, due (or a month in the done-when label),
  projects — and everything else under the heading is left alone. This is the same approach as next
  actions: aliases in code, no new file format.
- **Hierarchy: goal → Workspace project → thread.** A goal's projects are the links on its projects
  line, plus links in its section that point at a project's `status.md` or `overview.md` (prose links
  to ordinary notes do not count). Progress is the project's milestone table.
- **Placing a thread reuses the existing binding.** The Console already binds threads to Workspace
  projects with recorded strength (ADR-0011 era). The only new link is project → goal. Strongest
  first: a person's choice, stored as a `goal:<id>` tag on the work item (`goal:none` for "no goal");
  the thread's project linked to a goal; the parent's placement. A thread bound to a project no goal
  links is shown under "projects moving outside your goals", the rest under "not linked". A tag, not
  a new column: it is already a person-set label, saved views can filter by it, and no migration is
  needed. The id is a hash of the goal's title; renaming a goal from the Console moves the tags.
- **Edits are line edits.** Add, rename, change done-when or due, pause / complete / resume, link or
  unlink a project and reorder are sent with the note's revision and written through the vault's
  revision-checked atomic save, so an edit made in Obsidian meanwhile answers 409 instead of being
  overwritten. Only the lines an edit is about change; reordering moves whole `###` blocks.
- **Pins stay.** Pins answer "what do I touch today", goals "what is it for". In the Goals view a
  pinned thread leads its goal.
- **The view needs a Workspace.** Without one the Goals entry is not shown.

## Considered and rejected

- **A goals table in the Console database.** A second copy of what the person writes in their notes;
  it would drift, and agents reading the vault would not see it.
- **Replacing pins with goals.** Pins are a short-term ordering; removing them would make "today"
  harder to set.
- **Model-guessed goals for unplaced threads.** The project binding already uses the triage model;
  adding a goal guess would put a second unexplained label on every thread. A person moves a thread
  with one tap instead.

## Consequences

- A goal heading renamed in Obsidian gets a new id: threads placed by a person's tag fall back to
  the project rule until they are moved again.
- The parser accepts a fixed set of label aliases; a deployment writing other labels sees the goal
  without that field until the alias is added.
