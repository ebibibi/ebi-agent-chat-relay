# Auto-close for routine threads, with a way back

Evidence-based completion ([auto-complete.md](auto-complete.md)) closes a thread whose agent proves
the work is over. Two kinds of thread never get that far and still wait for someone to press Done:

| Kind | What it is | When it closes |
|---|---|---|
| `upgrade` | The thread ccdb opens for every release it applies | When the upgrade finished with nothing for a human: already up to date, applied, a quiet restart reserved, or restarted. A warning or a failure stays open. |
| `sweep` | A periodic housekeeping job reviews candidates and stalled threads | When the job calls `POST /api/threads/{id}/auto-close` for a thread it judged over |

It is **off by default**; nothing changes until the operator sets `CCDB_AUTO_CLOSE`.

## What always keeps a thread open

ccdb measures these itself, whatever the caller says:

- a turn is running, an active wait, an open decision request, an unfinished child thread;
- a Teams reply draft (those are always completed by a human);
- a sweep reports a human next step (`human_next_step: true`) without saying where it was recorded
  (`followup`). Put the promise, deadline, customer matter or payment in the task list first, then
  close the thread.

Nothing here is time-based on its own: a sweep closes a thread because it reviewed it, not because it
is old.

## The rescue record

Every automatic close:

1. posts a short note in the thread saying it closed and how to bring it back;
2. tags the thread's work item `auto-closed` and `auto-closed:<kind>` (plus `auto-closed:human-next`
   when a next step was moved elsewhere) and appends a note: why it closed, the last state, and where
   the next step went;
3. marks the item done **without** marking the bound Workspace entry done — the thread is over, not
   necessarily the task it served.

Evidence-based completion ([auto-complete.md](auto-complete.md)) keeps the same record with kind
`evidence`, so threads that closed themselves on evidence are listed and reopened the same way. It
still mirrors into the Workspace entry as before, because its evidence says the work itself is done.

Reopen it with one tap (**Reopen** on a done thread in the Console) or by replying in the thread.
A saved view on the `auto-closed` tag shows them all; `GET /api/auto-closures?days=N` lists the
closures of the rescue window (`CCDB_AUTO_CLOSE_RESCUE_DAYS`), newest first. The record is never
deleted — the window only decides how long it is listed.

## API for a housekeeping job

`GET /api/sweep/threads?stale_days=7` — threads to review: completion candidates of any age, plus
threads that stopped without a marker and were quiet for `stale_days`. Running and pinned threads are
left out.

`POST /api/threads/{thread_id}/auto-close`

```json
{
  "from_thread": 123,
  "reason": "the PR merged and the deploy is green",
  "summary": "last key points, one or two sentences",
  "human_next_step": false,
  "followup": ""
}
```

Returns 200 (`status: closed`, or `kept` in dry-run), 403 while sweeps are not enabled, 409 when a
measured fact or a missing `followup` keeps it open (the reasons are in `auto_close.reasons`), 400
for a malformed body or a thread closing itself (use `/candidate` for that).

## Configuration

| Variable | Values | Default |
|---|---|---|
| `CCDB_AUTO_CLOSE` | `off`, `dry-run` (judge and log, never close), `on` | `off` |
| `CCDB_AUTO_CLOSE_KINDS` | comma list of `upgrade`, `sweep` | `upgrade` |
| `CCDB_AUTO_CLOSE_RESCUE_DAYS` | 1–365 | `30` |

An unknown mode is `off` and unknown kinds are ignored: a typo must never widen what closes on its
own.

Code: `claude_discord/auto_close.py` (rules), `claude_discord/ext/auto_close_api.py` (measuring,
recording, routes). Issue #1490.
