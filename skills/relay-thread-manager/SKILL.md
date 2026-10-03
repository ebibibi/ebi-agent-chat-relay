---
name: relay-thread-manager
description: Act as a manager session in ebi-agent-chat-relay (ccdb) — spawn worker threads, brief them, track them through the spawn lineage, relay follow-up instructions, collect results and close the threads. Use when asked to "split this across threads", "run these in parallel", "spawn workers", "manage sub-threads", or "check on the workers". Not for the worker side of the job.
allowed-tools: Bash
---

# Relay thread manager

You are a session running inside ebi-agent-chat-relay (ccdb). Every thread is its own
agent session, and you can start more of them. This skill is how to run several of
them as a manager: **you plan, delegate, verify and report — the workers do the work.**

Everything below is plain HTTP against the localhost control plane. ccdb injects the
two variables you need into every session:

| Variable | Meaning |
|---|---|
| `CCDB_API_URL` | Base URL of the control plane (default `http://127.0.0.1:8099`) |
| `DISCORD_THREAD_ID` | Your own thread ID — the parent of anything you spawn |

If either is missing you are not running under ccdb; stop and say so.

## 0. Decide whether to fan out at all

Spawning costs a full session per worker. Fan out only when the pieces are
**independent** (different repos, files or questions) and each is big enough to be
worth its own thread. Two workers editing the same file is worse than one worker.

Before spawning, write down for yourself:

1. The work items, one per worker, with a clear "done" condition each
2. Which resource each one touches (repo, branch, file) — no two workers share one
3. How many run at once. Keep it small (3 is a good default); the channel list is
   also the human's view, and every extra thread costs their attention

## 1. Spawn workers

```bash
curl -s -X POST "$CCDB_API_URL/api/spawn" \
  -H "Content-Type: application/json" \
  -d '{
    "thread_name": "Worker A: fix parser tests",
    "parent_thread_id": '"$DISCORD_THREAD_ID"',
    "prompt": "<self-contained brief, see below>"
  }'
# → {"status": "spawned", "thread_id": "…", "thread_name": "🤖K2 Worker A: fix parser tests"}
```

- **Always pass `parent_thread_id`.** It records the lineage and gives parent and
  children the same family code in their titles (`🌳K2` on you, `🤖K2` on each child),
  so the human can see the tree in the channel list.
- **Keep the returned `thread_id`.** It is the only handle you have on the worker.
- Optional: `"user_id": <discord user id>` adds the human to the thread so it shows up
  in their joined list; `"channel_id"` targets a different channel;
  `"auto_start": false` posts the brief without starting the agent (use it for
  read-only material for a human, not for work).
- The call returns immediately. The worker runs in the background — keep going.

### Write the brief as if the worker knows nothing

A spawned session **does not inherit your conversation.** Everything it needs must be
in `prompt`. Include:

1. **Goal and done condition** — what "finished" looks like, measurably
2. **What is already decided** — so the worker does not reopen it
3. **Scope and prohibitions** — which repo/branch/files are theirs; what they must not
   touch (other workers' files, production, anything that costs money)
4. **Escalation rule** — "if a decision outside this scope comes up, stop and report
   instead of guessing"
5. **How to report** — e.g. "end your final reply with a 3-line summary: result,
   evidence (PR URL / commit / test output), open questions"
6. **Who the manager is** — "your manager is thread `<your DISCORD_THREAD_ID>`"

## 2. Track workers by measurement, not by memory

```bash
# Your children, as recorded by ccdb (limit=100: the default 20 can hide older workers)
curl -s "$CCDB_API_URL/api/sessions?limit=100" | python3 -c '
import json, os, sys
me = int(os.environ["DISCORD_THREAD_ID"])
for s in json.load(sys.stdin)["sessions"]:
    if s.get("parent_thread_id") == me:
        print(s["thread_id"], s["state"], s.get("thread_name"), "|", s.get("current_task") or "")
'

# What a worker actually said (newest last)
curl -s "$CCDB_API_URL/api/threads/<worker_thread_id>/messages?limit=20"
```

Fields worth knowing in each `/api/sessions` entry:

| Field | Use it for | Do not use it for |
|---|---|---|
| `state` | `running` = a turn is in flight right now | "the work is unfinished" — `history` only means no turn is running |
| `children` / `parent_thread_id` / `family` | Reconstructing the tree | — |
| `current_task` | What a running turn is doing now | — |
| `summary` | What the thread was **started** for | What it is doing **now** (threads get reused) |
| `latest_lounge` | The worker's last announcement | Proof of completion |

**A report is a claim; verify it.** "Opened a PR" is not "merged"; "merged" is not
"the file is on main". Check the artifact itself (`gh pr view`, `git cat-file -e
origin/main:<path>`, the test output) before you tell the human it is done.

Thread title markers tell you whose move it is at a glance: `✅` done, `❓` waiting
for the human's reply, `👀` deliverable to review, `📋` manual task for the human,
`⚠️` the last turn errored, `⏰` a scheduled follow-up is pending.

## 3. Send follow-up instructions (relay)

Bots ignore other bots' Discord messages, so you cannot "just post" in a worker's
thread. Use the relay endpoint:

```bash
curl -s -X POST "$CCDB_API_URL/api/threads/<worker_thread_id>/message" \
  -H "Content-Type: application/json" \
  -d '{"text": "[measured 14:05] PR #12 CI is red on test_parser. Fix that before anything else.",
       "from_thread": "'"$DISCORD_THREAD_ID"'", "mode": "queue", "hop": 0}'
```

- **`mode: "queue"`** (default) is delivered when the worker's current turn ends.
  **`mode: "interrupt"`** stops the turn within seconds and can cost uncommitted work —
  only for "stop now, you are about to do damage".
- The message is posted visibly in the worker's thread, marked as coming from you,
  not from the human.
- **Limits** (refusals return 429 with the reason): max 2 hops per chain, 60 s
  cooldown per thread pair, 5 relays per sender per 10 minutes, no self-sends, 4000
  characters. Start each new topic at `hop: 0`.

### Relay pitfalls

- **Queued messages arrive stale.** A queued relay is read after the worker's turn
  ends — possibly much later. Put the time you measured at the top of the message,
  re-measure right before sending, and add "if you have a newer measurement, yours
  wins".
- **Check before you nag.** Before sending "X is still broken", check the one value
  that changes when X is done (PR status, latest tag, latest build result). If it
  changed, do not send.
- **Do not repeat yourself.** If you want to send the same instruction a third time,
  your picture is probably out of date — re-read the thread instead.
- **Durable decisions belong in a durable place.** Relays are conversation. Anything
  that must survive (an approval, a decision, a hand-off) should also go into the
  issue / PR / work item, and you should tell the worker which one to read.

## 4. Keep workers out of each other's way

- Give each worker its own branch / worktree in the brief.
- Workers can claim resources themselves (`POST /api/claims`); a 409 tells them who
  holds it. If you split one repo between workers, name the claim for each in the brief.
- ccdb also flags two live sessions writing the same file within 15 minutes, in the
  lounge and in both threads. Treat that alert as your scheduling mistake to fix —
  move one of the items to a later round instead of letting both continue.

## 5. Decide what you can decide, escalate the rest

You may decide: how work is split, ordering, which worker owns what, resolving
overlaps between workers, re-briefing a stuck worker.

Escalate to the human (and tell workers to stop and wait) for: anything that costs
money, destructive or irreversible operations, production or customer-facing
changes, anything sent outside (posts, emails), and product or contractual choices.
Do not invent a threshold under which you approve these "on their behalf".

When you escalate, bring options, your recommendation, and what unblocks once they
decide — then mark your own thread as waiting:

```bash
curl -s -X POST "$CCDB_API_URL/api/threads/$DISCORD_THREAD_ID/waiting"
```

## 6. Collect and close

When every worker has reported and you have verified the artifacts:

1. Write one consolidated report for the human: per worker — result, evidence link,
   open items. Lead with what needs the human's action, if anything.
2. Make sure each worker thread ended with `✅` (the worker calls
   `POST /api/threads/<id>/done` itself; if it forgot and the work is verified, you may
   call it for that thread).
3. Release any claims you took (`DELETE /api/claims?resource=…&thread_id=…`).
4. Post a one-line closing note to the lounge, then mark your own thread done:

```bash
curl -s -X POST "$CCDB_API_URL/api/threads/$DISCORD_THREAD_ID/done"
```

## Anti-patterns

- Spawning a worker with "continue what we discussed" — it has no idea what you discussed
- Two workers on the same file or branch
- Reporting "done" from a worker's summary without checking the PR / commit / output
- Reading `summary` as the worker's current task
- Using `interrupt` to deliver ordinary instructions
- Approving spending or irreversible actions on the human's behalf
- Spawning more workers to make things go faster when the existing ones are blocked —
  unblock them first
