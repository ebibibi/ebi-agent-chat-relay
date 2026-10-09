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

## Limits

- The queue lives in memory. A bot restart drops waiting runs and pending resumes, exactly as it
  did before this feature.
- A deferred thread can wait indefinitely while new runs keep arriving. Prioritize it if that
  happens.
- Pausing interrupts mid-turn. A tool call in flight at that moment is cut off; the resume prompt
  tells the agent to continue without redoing completed steps.
