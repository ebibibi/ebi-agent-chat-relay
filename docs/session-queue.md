# Session queue: run next, defer, pause

`MAX_CONCURRENT_SESSIONS` (default 3) caps how many agent runs execute at once. Runs beyond the
cap wait for a free slot. The queue used to be strictly first come, first served. It can now be
steered, because the operator often knows better than arrival order.

## Controls

| Action | Applies to | Effect |
|---|---|---|
| ⏫ **Run this next** (`prioritize`) | a waiting thread | Starts before every waiter that was not prioritized. Prioritized threads keep their arrival order among themselves. |
| ⏬ **Let others go first** (`defer`) | a waiting thread | Waits behind everyone else. Starts on its own once nobody is ahead of it. |
| ⏸ **Pause** (`pause`) | a running thread | Interrupts the turn so a waiting thread gets the slot. The paused session joins the queue as deferred and resumes automatically with `--resume`, continuing the interrupted work. |

Pause is refused when no other thread is waiting. Freeing a slot that nobody takes would only
restart the same session immediately. To end a run, use ⏹ Stop instead.

## Where to use them

- **The waiting thread**: the "Waiting for a free session slot" message has the ⏫ / ⏬ buttons. Clicks from users outside `allowed_user_ids` are refused.
- **`/queue`** (any channel, ephemeral): lists running and waiting threads, with menus to
  prioritize, defer, or pause. It follows the same `allowed_user_ids` rule as `/skill`.
- **REST API** (loopback control plane):

```bash
curl -s "$CCDB_API_URL/api/slots"
curl -s -X POST "$CCDB_API_URL/api/slots/<thread_id>/prioritize"   # or defer / pause
```

Errors: `404` when the thread is not in the state the action needs (not waiting, not running),
`409` for conflicts (`already_running`, `already_pausing`, `nothing_waiting`, `not_pausable`),
and `503` when no limit is configured.

## Reserved restart

Restarting the bot while sessions run used to mean watching for an idle moment that, with new
turns arriving, might never come. A reservation makes the moment happen:

1. **Hold** — the queue stops starting runs. Running turns continue untouched; every new request
   (chat, Ebi AI Console, scheduler, `/api/spawn`) joins the queue instead of starting, and its
   waiting message says a restart is reserved.
2. **Restart** — once nothing is running, each queued run is saved with the prompt it was about to
   send and the bot restarts.
3. **Replay** — on startup the saved runs start again through the normal slot limit, re-entering
   the queue in roughly the order they were saved (priority marks are not kept). A short notice
   in the thread says why the turn is starting now.

```bash
curl -s -X POST "$CCDB_API_URL/api/restart" -H "Content-Type: application/json" \
  -d '{"reason": "deploy v4.3", "requested_by": "thread 123"}'   # 202 reserved, 200 if already
curl -s "$CCDB_API_URL/api/restart"                               # phase, running, waiting
curl -s -X DELETE "$CCDB_API_URL/api/restart"                     # cancel; the queue resumes
```

A session that reserves a restart for its own deploy usually wants to check the result afterwards.
Add `"resume_thread_id": "<its thread id>", "resume_prompt": "Verify the new build"`: that thread
is continued after the restart (unless a message of its own is already queued — that message runs
instead, since a thread keeps one saved run). It is saved just before restarting, so waiting for the running
sessions cannot outlast the 5-minute pending-resume window the way `POST /api/mark-resume` would.

How the bot restarts:

- `CCDB_RESTART_COMMAND` set (e.g. `sudo systemctl restart my-bot`): that command runs, split into
  words with no shell.
- Unset: the bot closes itself and its supervisor starts it again (systemd `Restart=always`,
  Docker `restart: unless-stopped`). Without a supervisor the bot simply stops.

`"max_wait_seconds": 1800` gives up after that long if sessions are still running: the hold is
released and nothing restarts. Without it the reservation waits as long as it takes; `DELETE`
cancels at any time before the restart itself begins.

If the restart fails — the command exits with an error code, or this process is still alive
30 seconds after the command ran — the hold is released, the saved rows are dropped (the queue runs in this
process, so replaying it later would run it twice) and `GET /api/restart` reports `last_error`.
A bot stuck holding its queue is worse than one that did not restart. A command ended by a
signal (`sudo systemctl restart` is killed along with the service it is restarting) is not an
error: only this process surviving the grace period counts as a failure. Pausing a running thread is
refused while the queue is held (`409 held`): the freed slot would stay empty.

Saved runs expire with the other pending resumes (5 minutes), which a normal restart is far
inside. Images attached to a queued message are not saved; the text is. Runs queued in a plain
channel (inline replies, no thread) are not replayed.

## Limits

- The queue lives in memory. An ordinary restart (without a reservation) keeps runs that were
  queued for a slot — they are saved on shutdown and replayed — but a Ebi AI Console reply waiting
  behind its own conversation's running turn is not yet in the slot queue and is dropped.
- A deferred thread can wait indefinitely while new runs keep arriving. Prioritize it if that
  happens.
- Pausing interrupts mid-turn. A tool call in flight at that moment is cut off; the resume prompt
  tells the agent to continue without redoing completed steps.
