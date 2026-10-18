# Automation, Scheduler & REST API

← Back to the [README](../../README.md) · [What You Can Do](features.md) · [Feature Reference](reference.md) · [Getting Started](getting-started.md) · **Automation, Scheduler & REST API** · [Architecture & Project Background](architecture.md)

## GitHub + Claude Code Automation

### Example: Automated Documentation Sync

On every push to `main`, Claude Code:
1. Pulls the latest changes and analyzes the diff
2. Updates English documentation
3. Translates to Japanese (or any target languages)
4. Creates a PR with a bilingual summary
5. Enables auto-merge — merges automatically when CI passes

**GitHub Actions:**

```yaml
# .github/workflows/docs-sync.yml
name: Documentation Sync
on:
  push:
    branches: [main]
jobs:
  trigger:
    if: "!contains(github.event.head_commit.message, '[docs-sync]')"
    runs-on: ubuntu-latest
    steps:
      - run: |
          curl -X POST "${{ secrets.DISCORD_WEBHOOK_URL }}" \
            -H "Content-Type: application/json" \
            -d '{"content": "🔄 docs-sync"}'
```

**Bot configuration:**

```python
from claude_discord import WebhookTriggerCog, WebhookTrigger, ClaudeRunner

runner = ClaudeRunner(command="claude", model="sonnet")

triggers = {
    "🔄 docs-sync": WebhookTrigger(
        prompt="Analyze changes, update docs, create a PR with bilingual summary, enable auto-merge.",
        working_dir="/home/user/my-project",
        timeout=600,
    ),
}

await bot.add_cog(WebhookTriggerCog(
    bot=bot,
    runner=runner,
    triggers=triggers,
    channel_ids={YOUR_CHANNEL_ID},
))
```

**Security:** Prompts are defined server-side. Webhooks only select which trigger to fire — no arbitrary prompt injection.

### Example: Auto-Approve Owner PRs

```yaml
# .github/workflows/auto-approve.yml
name: Auto Approve Owner PRs
on:
  pull_request:
    types: [opened, synchronize, reopened]
jobs:
  auto-approve:
    if: github.event.pull_request.user.login == 'your-username'
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write
    steps:
      - name: Approve with the workflow token
        env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
        run: gh pr review "$PR_NUMBER" --repo "$GITHUB_REPOSITORY" --approve
      - name: Enable auto-merge as the repository owner
        env:
          GH_TOKEN: ${{ secrets.ADMIN_PAT }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
        run: gh pr merge "$PR_NUMBER" --repo "$GITHUB_REPOSITORY" --auto --squash
```

Use a narrowly scoped owner token for `ADMIN_PAT`, store it as a repository
actions secret, and protect the workflow like any other release credential. The
token matters because the actor that enables auto-merge becomes the eventual
merge actor. A `GITHUB_TOKEN`-attributed merge emits no `push` event and does not
close issues named by `Closes #123`; an owner-attributed merge does both. Put
post-merge work in a separate workflow triggered by `push` to `main` instead of
holding this job open in a polling loop. See the measurement and trade-offs in
[docs/merge-events.md](../merge-events.md).

## Scheduled Tasks

Register periodic Claude Code tasks at runtime — no code changes, no redeploys.

From within a Discord session, Claude can register a task:

```bash
# Claude calls this inside a session:
curl -X POST "$CCDB_API_URL/api/tasks" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Check for outdated deps and open an issue if found", "interval_seconds": 604800}'
```

Or register from your own scripts:

```bash
curl -X POST http://localhost:8080/api/tasks \
  -H "Authorization: Bearer your-secret" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Weekly security scan", "interval_seconds": 604800}'
```

The 30-second master loop picks up due tasks and spawns Claude Code sessions automatically.

### Pinning a task to a backend and model

A task runs on whatever backend `/backend` resolves to when it fires. That is usually
what you want — switch the deployment to Codex and the nightly jobs move with it — but
not always: a cheap digest belongs on a small model no matter what the humans are using,
and a task written against one agent's tool vocabulary should not silently move to
another.

`backend` pins the backend, `model` pins the model inside it:

```bash
curl -X POST http://localhost:8080/api/tasks \
  -H "Content-Type: application/json" \
  -d '{"name": "nightly-digest", "prompt": "Summarise today'"'"'s commits",
       "interval_seconds": 86400, "channel_id": 123,
       "backend": "codex", "model": "gpt-5.6-sol"}'
```

- `backend` must be one of `claude`, `codex`, `local`, `agui`, `pi`, `antigravity`.
- `model` is free text — the same ids `/model set` accepts — but is only valid
  **together with a backend**, because a model id belongs to one backend. Sending a
  model alone is a `400` rather than a task that fails every night on whichever backend
  happened to be active.
- Omit both and nothing changes: the task follows the thread/global setting, as every
  existing task already does.
- `PATCH /api/tasks/{id}` sets or changes the pin; `null` clears it. Clearing the
  backend while a model is still pinned is refused for the same reason.
- `/effort` is **not** pinned. It resolves from the settings of whichever backend the
  task ends up on, so an effort change still reaches pinned tasks.

## Auto-Upgrade

Automatically upgrade the bot when a new release is published:

```python
from claude_discord import AutoUpgradeCog, UpgradeConfig

config = UpgradeConfig(
    package_name="claude-code-discord-bridge",
    trigger_prefix="🔄 bot-upgrade",
    working_dir="/home/user/my-bot",
    restart_command=["sudo", "systemctl", "restart", "my-bot.service"],
    restart_approval=True,       # React ✅ in thread, or click button in channel
    slash_command_enabled=True,  # Enable /upgrade slash command (opt-in, default False)
)

await bot.add_cog(AutoUpgradeCog(bot, config))
```

#### Manual Trigger via `/upgrade`

When `slash_command_enabled=True`, any authorised user can run `/upgrade` directly in Discord to trigger the same upgrade pipeline — no webhook required. The command works from both text channels and threads (running it inside a thread creates the upgrade thread in the parent channel). It respects `upgrade_approval` and `restart_approval` gates, creates a progress thread, and gracefully handles concurrent runs (replies ephemerally if an upgrade is already in progress).

The upgrade thread's title carries the same outcome marker as agent threads: 📋 while it waits for an approval, ✅ once the restart starts (or the upgrade completes without one), ⚠️ when a step fails. A prompt left behind because the bot restarted before anyone approved it is closed as ✅ — the next release opens a new thread. So the Console lists only the upgrade that actually waits for you.

Before restarting, `AutoUpgradeCog`:

1. **Snapshots active sessions** — Collects all threads with running Claude sessions (duck-typed: any Cog with `_active_runners` dict is discovered automatically).
2. **Drains** — Waits for active sessions to finish naturally.
3. **Marks for resume** — Saves active thread IDs to the pending-resumes table. On next startup, those sessions are resumed with a safety-first prompt: Claude reports what it was working on and asks the user to re-confirm before resuming any implementation work (code changes, commits, PRs). This prevents unintended actions after context compression may have erased task approval state.
4. **Restarts** — Executes the configured restart command.

Any Cog with an `active_count` property is auto-discovered and drained:

```python
class MyCog(commands.Cog):
    @property
    def active_count(self) -> int:
        return len(self._running_tasks)
```

Session marking is fully opt-in — it only activates when `setup_bridge()` has initialized the session database (the default). When enabled, sessions resume with `--resume` continuity so Claude Code can pick up the exact conversation where it left off.

> **Coverage:** `AutoUpgradeCog` covers upgrade-triggered restarts. For *all other* shutdowns (`systemctl stop`, `bot.close()`, SIGTERM), `ClaudeChatCog.cog_unload()` provides a second automatic safety net.

## REST API

Optional REST API for notifications and task management. Requires aiohttp:

```bash
uv sync --extra api
```

### Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check |
| POST | `/api/notify` | Send immediate notification |
| POST | `/api/schedule` | Schedule a notification |
| GET | `/api/scheduled` | List pending notifications |
| DELETE | `/api/scheduled/{id}` | Cancel a notification |
| POST | `/api/tasks` | Register a scheduled Claude Code task |
| GET | `/api/tasks` | List registered tasks |
| DELETE | `/api/tasks/{id}` | Remove a task |
| PATCH | `/api/tasks/{id}` | Update a task (enable/disable, change schedule, pin backend/model) |
| POST | `/api/spawn` | Create a new Discord thread and start a Claude Code session (non-blocking); pass `auto_start: false` to defer Claude until the first user reply, or `user_id` to add the requester to the thread |
| POST | `/api/ingest` | Authenticated external spawn (browser extension / webhook) with base64 attachments; returns a `result_id` when result retrieval is configured |
| GET | `/api/ingest/{result_id}` | Poll the spawned session's final reply (`status`/`result`/`error`/`thread_id`) |
| GET | `/api/ingest/summary` | Read the running summary + `marker` for a long ingest thread by `key` (ingest-token gated) so the client can export only the diff |
| POST | `/api/ingest/summary` | Save an updated running summary (`result_id` + `summary`) from the session — localhost control plane; ccdb advances the `marker` from the ingest row |
| DELETE | `/api/ingest/summary` | Clear the stored summary for `key`, forcing a full re-summary on the next ingest |
| POST | `/api/teams/sync/plan` | Ask what an upstream thread's mirror is missing — send ids + hashes, get back `want_messages`/`want_attachments`/`newest_have_mid` (ingest-token gated) |
| POST | `/api/teams/sync/push` | Store the wanted messages as one file each under the vault, with attachments and an append-only `chain.jsonl` |
| POST | `/api/mark-resume` | Mark a thread for automatic resume on next bot startup |
| GET | `/api/lounge` | Read recent AI Lounge messages |
| POST | `/api/lounge` | Post a message to the AI Lounge (with optional `label`); returns a `hint` field when the message exceeds 200 characters |
| GET | `/api/sessions` | List every session — live and stored — with state, working dir and latest lounge note (`state=running`, `exclude_thread`, `limit`) |
| GET | `/api/slots` | Session slot queue: `max_slots`, threads holding a slot (`running`) and the queue in start order (`waiting`) |
| GET | `/api/restart` | Reserved restart state: `reserved`, `phase` (`idle` / `draining` / `restarting`), running and waiting counts, `last_error` |
| POST | `/api/restart` | Reserve a restart: hold the queue, restart once nothing runs, then replay everything queued (202; 200 if already reserved) |
| DELETE | `/api/restart` | Cancel the reservation; the queue resumes (404 none, 409 already restarting) |
| POST | `/api/slots/{thread_id}/{action}` | Steer the queue — `prioritize` / `defer` a waiting thread, `pause` a running one (404 wrong state, 409 conflict such as `nothing_waiting`, 503 no limit configured) |
| GET | `/api/search` | Find a past thread by keyword — `LIKE` over summary and working dir; add `body=1` to also grep local Claude transcripts (each hit then carries a `snippet` and `source`); returns each hit with a Discord `deep_link` (`q` required, optional `origin`, `limit` max 50) |
| GET | `/api/threads/{thread_id}/messages` | Read another thread's conversation, oldest first (`limit`) |
| POST | `/api/claims` | Claim a resource before working on it — 201 when acquired, 409 with the holder when taken |
| GET | `/api/claims` | List live claims (optional `resource` filter) |
| DELETE | `/api/claims` | Release a claim (`resource`, `thread_id`, optional `force=true`) |
| POST | `/api/waits` | End the turn while CI runs: register a probe (`thread_id`, `argv`, `pending_exit_codes` and/or `done_values`, optional `interval_seconds`, `timeout_seconds`, `label`, `note`, `cwd`); the thread is resumed when it says done, times out, or fails 5 times in a row. 201 created, 200 when the same probe is already waiting, 429 over the limit, 503 when waits are off. See [docs/waits.md](../waits.md) |
| GET | `/api/waits` | Active waits with their last probe result (optional `thread_id`) |
| DELETE | `/api/waits/{id}` | Cancel a wait (with `thread_id`, only if that thread owns it) |
| POST | `/api/threads/{thread_id}/message` | Relay a message from one session to another (`text`, `from_thread`, `mode`, `hop`) |
| POST | `/api/threads/{thread_id}/candidate` | Propose closing a thread: a completion candidate (prefixes the title with `🏁`; idempotent). Only a human marks it done |
| POST | `/api/threads/{thread_id}/done` | Same as `/candidate`, kept for existing prompts |
| POST | `/api/threads/{thread_id}/waiting` | Mark a thread as waiting on the human (prefixes the title with `❓`, replacing `✅`/`⚠️`; removed on the next human reply; idempotent) |
| POST | `/api/threads/{thread_id}/review` | Mark a thread as having a deliverable for the human to review (prefixes the title with `👀`, replacing any other outcome marker; removed on the next human reply; idempotent) |
| POST | `/api/threads/{thread_id}/action` | Mark a thread as waiting on a task the human must do outside the chat — a todo (prefixes the title with `📋`, replacing any other outcome marker; removed on the next human reply; idempotent) |

The four mark endpoints return `200` with `marked` or `unchanged`, or `202` with `queued` when the rename is waiting out Discord's rate limit (it is applied in the background — do not retry).

```bash
# Send notification (embed format, default)
curl -X POST http://localhost:8080/api/notify \
  -H "Authorization: Bearer your-secret" \
  -H "Content-Type: application/json" \
  -d '{"message": "Build succeeded!", "title": "CI/CD"}'

# Send plain text notification (no embed)
curl -X POST http://localhost:8080/api/notify \
  -H "Authorization: Bearer your-secret" \
  -H "Content-Type: application/json" \
  -d '{"message": "Deployment done!", "format": "text"}'

# Send a Discord Poll
curl -X POST http://localhost:8080/api/notify \
  -H "Authorization: Bearer your-secret" \
  -H "Content-Type: application/json" \
  -d '{
    "message": "Vote now",
    "poll": {
      "question": "Which release track?",
      "answers": ["Stable", "Beta", "Nightly"],
      "duration_hours": 24,
      "allow_multiselect": false
    }
  }'

# Register a recurring task
curl -X POST http://localhost:8080/api/tasks \
  -H "Authorization: Bearer your-secret" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Daily standup summary", "interval_seconds": 86400}'
```

---

[What You Can Do](features.md) · [Feature Reference](reference.md) · [Getting Started](getting-started.md) · **Automation, Scheduler & REST API** · [Architecture & Project Background](architecture.md)
