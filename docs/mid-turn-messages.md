# Messages sent while the agent works

Claude Code and Codex take input while they work: type a follow-up mid-turn and the agent reads
it at its next step, without stopping. ccdb does the same for Claude and, with the
[experimental app-server transport](codex-app-server.md), cloud Codex.

## What happens when you send a message during a turn

| Where you write | Claude or enabled Codex app-server, turn running | Otherwise |
|---|---|---|
| Discord thread reply | Goes into the running turn; the message gets 📥 | Runs as the next turn |
| Ebi AI Console conversation | Goes into the running turn; the toast says *Sent into the running turn* | Runs as the next turn |
| Ebi AI Console reply to a Discord thread | Same as a Discord reply (posted in the thread, then 📥) | Runs as the next turn |
| `/api/threads/<id>/message` (another session) | Unchanged: `queue` waits, `interrupt` stops the turn | — |
| Wait results, scheduler, restart replays | Unchanged: run as the next turn | — |

"Otherwise" covers Codex using the default exec transport, local models, pi and AG-UI,
anonymized runs (a mid-turn message would skip the privacy gateway,
so it runs as a normal turn that goes through it), a run that is about to pause for another
thread, and a turn that is already finishing.

## How it works

The Claude CLI runs with `--input-format stream-json`, and ccdb keeps its stdin open. A message
for a running turn is written there as another user line with its own uuid. With
`--replay-user-messages` the CLI echoes each user line — same uuid, `isReplay: true` — at the
moment it reads it. Measured on claude 2.1.300:

- A message written while a tool runs is read after that tool's result, in the same turn: one
  `result`.
- A message written while the final answer streams, or just after the `result`, starts another
  turn in the same process: `result`, then the new turn, then a second `result`.
- Two messages in a row are echoed separately, in order.

So the reader does not stop at a `result` while any delivered message is still unread
(`StreamEvent.turn_continues`). That intermediate result posts its answer but no "Done"; the
next one closes the run. A message is only accepted between the first prompt and the final
`result`, and the check and the close happen without an `await` in between, so a message is
either read by this run or refused and queued — never both, never neither.

When the agent reads a delivered message, the reply being streamed above it is closed, so the
answer to it appears under it.

## Messages the agent never read

If a run ends before the agent read a delivered message — `/stop`, a crash, a timeout — the
thread shows them under **Not read by the agent** so you can send them again. ccdb does not
start that work on its own: after `/stop` you may not want it. When the run continues by itself
(pause for another thread, compaction, an `AskUserQuestion` answer, the PR completion gate), the
unread messages are added to that continuation's prompt instead.

## Code

- `claude_code_core/runner.py` — `ClaudeRunner.inject_user_message()`, `unconsumed_messages()`
- `claude_code_core/codex_app_server.py` — Codex `turn/steer` and correlated input echoes
- `claude_discord/live_runs.py` — the runner each conversation is running, `try_inject()`
- `claude_discord/cogs/_run_helper.py` — publishes the live run, carries or reports unread messages
