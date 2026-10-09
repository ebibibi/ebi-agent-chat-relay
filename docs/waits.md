# Waits: end the turn while CI runs

A session holds a concurrency slot (`MAX_CONCURRENT_SESSIONS`) for exactly one turn. A
session that waits for a pipeline *inside* its turn (`gh run watch`, `gh pr checks --watch`,
a `sleep` loop) holds a slot while doing nothing; a few of those fill every slot and every
other thread queues behind them.

Ending the turn costs nothing: the next turn resumes the same session with `--resume`, so
the whole context is still there. A **wait** supplies the missing piece — something that
starts that next turn when the pipeline is done.

```
session: push → POST /api/waits → "waiting for PR #12 checks" → turn ends (slot free)
ccdb:    every interval, run the probe (no shell, no model, no slot)
ccdb:    probe says done / wait times out / probe keeps failing
ccdb:    post "[WAIT FINISHED — automatic continuation] …" into the thread → next turn
```

## The probe

A wait is an `argv` ccdb runs without a shell, with the relay's environment minus its own
credentials (the same filter the agent CLI gets — `gh`/`az` logins work, the bot token does
not). Two rules say "still pending"; at least one is required:

| Rule | Meaning |
|---|---|
| `pending_exit_codes` | The probe's exit code is one of these → still pending. Any other exit code ends the wait (when no `done_values` are set). |
| `done_values` | Up to 8 strings. The probe exits 0 and no output line (trimmed) equals one of them yet → still pending. A non-zero exit is an *error*, not "pending" — an expired login must not look like a pipeline that is still running. Exact line match on purpose: a caller-supplied regex can backtrack for minutes in a thread nobody can cancel. |

Five consecutive probe errors (cannot start, runs longer than 60 s, non-zero exit with
`done_values`) end the wait and resume the thread with the error, so a broken probe never
waits silently until the timeout.

ccdb does not interpret the result. It resumes the thread with the exit code and the last
4,000 characters of output, and the agent decides what they mean (Key Design Decision 9:
Claude decides *what*, ccdb decides *when*).

## Recipes

| What | argv | Rule |
|---|---|---|
| GitHub PR checks | `gh pr checks 12 --repo owner/repo` | `pending_exit_codes: [8]` (0 = passed, 1 = failed) |
| GitHub Actions run | `gh run view 123 --repo owner/repo --json status -q .status` | `done_values: ["completed"]` |
| Azure Pipelines run | `az pipelines runs show --id 45 --org https://dev.azure.com/org --project p --query status -o tsv` | `done_values: ["completed"]` |
| Anything else | a script that exits 75 while not ready | `pending_exit_codes: [75]` |

## API

Control plane (`CCDB_API_URL`, loopback only — same trust model as `/api/tasks`).

```bash
curl -s -X POST "$CCDB_API_URL/api/waits" -H "Content-Type: application/json" -d '{
  "thread_id": '"$DISCORD_THREAD_ID"',
  "label": "PR #12 checks",
  "argv": ["gh", "pr", "checks", "12", "--repo", "owner/repo"],
  "pending_exit_codes": [8],
  "note": "merge when green, then verify the deploy"
}'
```

| Field | Default | Notes |
|---|---|---|
| `thread_id` | required | Thread to resume |
| `argv` | required | 1–64 strings, run without a shell |
| `pending_exit_codes` / `done_values` | — | At least one (see above) |
| `interval_seconds` | 60 | Clamped to 30–1800. The first probe runs one interval after registration |
| `timeout_seconds` | 10800 (3 h) | 60–86400. On timeout the thread is resumed and told so |
| `label` | the argv | Shown in the resume prompt |
| `note` | — | Up to 1,000 chars, handed back in the resume prompt |
| `cwd` | session working dir | Must be an absolute directory under `CCDB_WAIT_CWD_ROOTS` (`os.pathsep`-separated; default: the relay user's home), checked after `realpath` (a missing directory surfaces as a probe error). Defaults to the thread's working directory when it exists and qualifies |

Responses: `201` with the stored wait; `200` with the live wait when the thread already waits on
the same `argv` (an agent told twice to wait is resumed once); `400` for an invalid spec; `429`
when the thread has 5 active waits or the deployment has 50; `503` when waits are off.

- `GET /api/waits[?thread_id=N]` — active waits, with the last probe result.
- `DELETE /api/waits/{id}[?thread_id=N]` — cancel (with `thread_id`, only if that thread owns it).

## Behaviour

- **Persistent.** Waits live in the session database, so a bot restart (often the very deploy
  being waited on) does not lose them.
- **Never lost.** A finished wait is `resuming` until the thread's next turn has run. A failed
  delivery (thread unreachable, Discord error) puts it back to `active`, so the next probe decides
  again; after 5 failed deliveries it becomes `undeliverable`. A wait still `resuming` when the bot
  starts was cut off by the restart and is retried — if the restart hit the resumed turn itself,
  the session may see the same result twice, which is preferred to never waking up.
- **Resumes like a reply.** The continuation is posted into the thread, so the humans watching
  see why the session woke up, and it queues behind any turn already running in the thread.
- **Once.** Finishing a wait is an atomic state change; only the caller that made it resumes the
  thread.
- **Marked.** Threads with an active wait carry the scheduled marker (`⏰`) while the scheduler is
  enabled.
- **Advertised.** While the watcher runs and the control plane is reachable, every turn's system
  instructions tell the agent to register a wait instead of watching a pipeline, and the PR
  completion gate says the same.
- **Host only.** A probe runs as the relay user on the host, outside any execution environment.
  When `CCDB_EXECUTION_ALLOWED_MODES` allows any mode other than `host`, a confined agent could use
  a probe to run commands outside its boundary, so waits are disabled (logged at startup, API
  answers `503`, nothing is advertised). The control plane is loopback-only with the same trust
  model as `/api/tasks` and `/api/spawn`.
- **Exit codes are not interpreted.** With `pending_exit_codes` alone, any other exit code ends
  the wait — including a transient `gh` failure. The agent sees the exit code and output and can
  register again.
- **Off switch.** `setup_bridge(..., enable_waits=False)`.

CI webhooks are not needed: polling with the operator's own `gh`/`az` credentials works for every
repository, including ones where adding a webhook or service hook is not allowed, and needs no
public ingress. A webhook could later be added as a "check now" nudge; the probe would still be
the source of truth.
