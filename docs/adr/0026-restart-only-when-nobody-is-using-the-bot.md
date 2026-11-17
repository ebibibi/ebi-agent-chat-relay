---
type: adr
id: ADR-0026
title: Restart the bot only when nobody is using it, and only when a change needs it
decision: Decide from the changed files whether a merged change needs a restart; apply the rest by fast-forwarding the running checkout. A reserved restart waits until no person has acted for a quiet period (default 15 minutes) before holding the queue, and lets go when a person acts during the drain. Only an explicit urgent request, for a security fix or a broken bot, skips the wait.
status: accepted
date: 2026-11-15
decided_by: human+ai
deciders: [Masahiko Ebisuda, Claude]
scope: repository
---

# ADR-0026: Restart the bot only when nobody is using it, and only when a change needs it

## Context

The bot runs from a git checkout, and its startup script pulls `main`. So deploying meant
restarting, and agent sessions reserved a restart (`POST /api/restart`) after merging their
pull requests. A reservation held the slot queue at once and restarted the moment no turn was
running. On a busy morning, two restarts happened within an hour while the owner was in the
middle of conversations. Their next messages were held in the queue, then the bot restarted.
The messages were replayed, but what the owner saw was a bot that stopped answering and then
restarted without warning.

Two more facts made it worse:

- Every merged PR was treated as needing a restart, even when it only changed Console static
  files, which the Console reads from disk on every request, or docs and tests, which the
  running bot never loads.
- The webhook-driven auto-upgrade ran `uv lock --upgrade-package` against the checkout. That
  does not update a git checkout, so it changed nothing, then waited for an approval nobody
  gave. Deploys depended entirely on agents reserving restarts.

"No session running" cannot tell a quiet bot from a person between two messages. Child sessions
run all the time, so "wait until nothing runs, without holding" may never happen. Holding at
once interrupts the person.

## Decision

1. **The changed files decide.** `deploy_policy.needs_restart` returns false only for paths that
   match a short allowlist of patterns: `claude_discord/console/static/*`, `docs/*`, `tests/*`,
   `.github/*`, `*.md`, `scripts/*` and a few others. A `.py` file outside tests, scripts, docs
   and CI always needs a restart, and so does any path the list does not name. Unknown means
   restart.
2. **The auto-upgrade works on the checkout** (`UpgradeConfig.git_checkout`). It fetches, reads
   `git diff --name-only HEAD...origin/main`, fast-forwards when no file needs a restart (only on
   a clean `main`; a development checkout is left alone), and otherwise reserves a restart without
   pulling, so the running process never imports a mix of old and new Python.
3. **A reservation waits for quiet before it holds.** Phase `waiting_for_quiet` holds nothing
   until no person has acted for `CCDB_RESTART_QUIET_SECONDS` (default 900). Acting means a
   message from an allowed Discord user, or a change made in the Console. Reading does not
   count, so an open tab cannot block restarts forever, and agents never count. Then the
   existing hold-and-drain runs. A person acting during the drain releases the hold and sends the
   reservation back to waiting.
4. **One restart for many requests.** A request made while one is pending joins it: its thread to
   resume is added, and the restart still happens once.
5. **People can choose.** The Console shows "🔄 Update waiting" with "Restart now" and "In 1
   hour". Each step (reserved, joined, held, released, postponed, restarting, failed) is logged
   and kept in the reservation's `events`.
6. **Urgent is explicit and rare.** `"urgent": true` skips the quiet wait and is not interrupted
   by activity. Running turns still finish. It is for a security fix that must take effect now,
   or a bot that is broken for its users. An agent that uses it says why in `reason`. Without
   such a reason, the quiet rule applies, including to agents' own `POST /api/restart`.

## Consequences

- A static-only or docs-only merge takes effect within seconds and nobody notices a restart.
- A Python merge can wait a long time on a day of continuous use. That is the intended trade:
  the owner sees the pending update in the Console and can restart now when it suits them.
  `max_wait_seconds` still bounds a reservation that should give up.
- The activity clock lives in memory, fed by the frontend-neutral `AttentionRecorder` (every
  human message on Discord, Console and Teams) and by Console changes. On startup it is seeded
  from the latest live row of `human_activity`, so a fresh process does not treat itself as
  quiet.
- The allowlist must grow deliberately. A new non-Python file that the bot loads at startup
  restarts by default. Adding it to the allowlist by mistake would leave it stale until the
  next restart.

## Alternatives considered

- **Restart only when nothing runs, without holding the queue.** Child sessions keep slots busy
  for hours, and this still cannot see a person between messages.
- **Approve every restart by hand.** The old approval prompt was ignored because it appeared for
  every merge, including ones that needed no restart.
- **Hot-reload Python modules.** Not safe for a long-running asyncio process with module-level
  state.
