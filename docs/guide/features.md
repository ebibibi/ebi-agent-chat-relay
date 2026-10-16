# What You Can Do

← Back to the [README](../../README.md) · **What You Can Do** · [Feature Reference](reference.md) · [Getting Started](getting-started.md) · [Automation, Scheduler & REST API](automation.md) · [Architecture & Project Background](architecture.md)


### Interactive Chat (Mobile / Desktop)

Use Claude Code _or_ OpenAI Codex from anywhere Discord runs — phone, tablet, or desktop. Each message creates or continues a thread that maps 1:1 to a persistent AI session. Switch backend at any time with `/backend claude` or `/backend codex` — per thread, or globally as the new default.

### Parallel Development

Open multiple threads simultaneously. Each is an independent AI session — Claude Code or Codex — with its own context, working directory, and git worktree. Useful patterns:

- **Feature + review in parallel**: Start a feature with Claude in one thread while Codex reviews the PR in another.
- **Multiple contributors**: Different team members each get their own thread (and their preferred backend); sessions stay aware of each other via the AI Lounge.
- **Experiment safely**: Try an approach in thread A while keeping thread B on stable code.
- **A/B the same prompt on both AIs**: Spawn two threads with the same task, one on `/backend claude` and one on `/backend codex`, then compare the diffs side-by-side.

### Scheduled Tasks (SchedulerCog)

Register periodic Claude Code tasks from a Discord conversation or via REST API — no code changes, no redeploys. Tasks are stored in SQLite and run on a configurable schedule. Claude can self-register tasks during a session using `POST /api/tasks`.

```
/skill name:goodmorning         → runs immediately
Claude calls POST /api/tasks    → registers a periodic task
SchedulerCog (30s master loop)  → fires due tasks automatically
```

### Waiting on CI Without Holding a Slot (WaitWatcherCog)

A turn that watches a pipeline holds a concurrency slot while doing nothing. Instead, the session registers a **wait** — a probe command such as `gh pr checks 12` — and ends its turn. ccdb re-runs the probe (no shell, no model, no slot) and resumes the thread with the result when it says done, times out, or keeps failing. Works for GitHub, Azure Pipelines or anything with a status command; no webhook or public ingress needed. See [docs/waits.md](../waits.md).

```
Claude calls POST /api/waits    → registers the probe, ends its turn
WaitWatcherCog (15s loop)       → runs due probes
probe says done                 → "[WAIT FINISHED]" continuation in the same thread
```

### CI/CD Automation

Trigger Claude Code tasks from GitHub Actions via Discord webhooks. Claude runs autonomously — reads code, updates docs, creates PRs, enables auto-merge.

```
GitHub Actions → Discord Webhook → Bridge → Claude Code CLI
                                                  ↓
GitHub PR ←── git push ←── Claude Code ──────────┘
```

**Real example:** On every push to `main`, Claude analyzes the diff, updates English + Japanese documentation, creates a bilingual PR, and enables auto-merge. Zero human interaction.

### Session Sync

Already use Claude Code CLI directly? Sync your existing terminal sessions into Discord threads with `/sync-sessions`. Backfills recent conversation messages so you can continue a CLI session from your phone without losing context.

### AI Lounge

A shared "breakroom" channel where all concurrent sessions announce themselves, read each other's updates, and coordinate before disruptive operations.

Each session receives the lounge context automatically as ephemeral system/developer instructions (`--append-system-prompt` for Claude, `developer_instructions` for Codex), rather than as part of the conversation history. This prevents the context from accumulating across turns, which would otherwise cause "Prompt is too long" errors in long-running sessions. The injected context includes recent messages from other sessions plus the rule to check before doing anything destructive.

```bash
# Sessions post their intentions before starting:
curl -X POST "$CCDB_API_URL/api/lounge" \
  -H "Content-Type: application/json" \
  -d '{"message": "Starting auth refactor on feature/oauth — worktree-A", "label": "feature dev"}'

# Read recent lounge messages (also injected into each session automatically):
curl "$CCDB_API_URL/api/lounge"
```

**Keep posts short — 200 characters, one or two lines.** Every lounge message is
injected into every session that starts after it, so length is a cost shared by all
of them, not a private one. Post *what* you are doing or *what* changed; the
root-cause narrative, the list of merged PRs and the lessons learned belong in the
PR or the repo docs, where they can be searched later. The same limit applies to
the closing note a session leaves when it finishes.

The limit is a nudge, not a rejection: an over-long message is still stored **in
full** (truncating would destroy the one sentence that mattered), and `POST
/api/lounge` simply returns an extra `hint` field telling the poster how long the
message was and what to leave out next time. A prompt rule alone proved easy to
talk past, so the API says it too.

The lounge channel doubles as a human-visible activity feed — open it in Discord to see at a glance what every active Claude session is currently doing.

**Lounge vs. the coordination APIs.** Since the cross-session endpoints below landed, the lounge is no longer the place to *discover* who is running, read another thread, or lock a resource — `GET /api/sessions`, `GET /api/threads/{id}/messages` and `POST /api/claims` do that precisely and even surface sessions that never posted. The lounge keeps what no structured call carries: **broadcast announcements with no single target** ("restarting the bot", "cut release v3.2.0") and **intent announced before acting**. Treat it as the room's announcements, not its database.

**The Discord mirror is optional (on/off).** The AI-to-AI layer is the DB-backed lounge that is injected into every session's prompt; mirroring it into a Discord channel is a separate, purely human-facing convenience. It is controlled by one setting:

- **On** — set `COORDINATION_CHANNEL_ID` (or `lounge_channel_id`) to a channel, and lounge messages are echoed there for a human to watch.
- **Off** — leave it unset. The lounge and every coordination API keep working exactly the same; you simply don't get the Discord feed. If the configured channel is later deleted, the mirror notices and disables itself for the rest of the process (the DB lounge is never affected).

So a deployment whose humans don't read the channel can run mirror-off and lose nothing.

### Cross-Session Observability

A lounge note tells a session *that* another thread exists. These two read-only endpoints let it go and look — so two sessions that started on the same task can discover the overlap instead of both charging ahead.

```bash
# Who else is alive, where are they working, what did they last announce?
curl "$CCDB_API_URL/api/sessions?exclude_thread=$DISCORD_THREAD_ID"

# Read that thread's actual conversation
curl "$CCDB_API_URL/api/threads/1529338965000192110/messages?limit=30"
```

`/api/sessions` merges three sources: the `sessions` table (created_at, working dir, backend), the in-memory registry (what each live session is doing *right now*), and each thread's latest lounge note. A session appears with `"state": "running"` while a turn is in flight — including sessions that never posted to the lounge at all, which is exactly when this matters. A saved conversation without an in-flight turn appears as `"state": "history"`; this means it is available to resume, not that an agent is waiting for work or user input. Sessions have no Discord token of their own, so the bot performs the read and the endpoints stay on the localhost control plane.

### Resource Claims

Observability tells a session that a collision *happened*. A claim prevents it — no reading, no negotiating, no LLM round trip. A session claims what it is about to work on; the next session asking for the same thing is refused before it does any work.

```bash
# Before starting: claim it
curl -X POST "$CCDB_API_URL/api/claims" \
  -H "Content-Type: application/json" \
  -d '{"resource": "repo:ccdb#issue-123", "thread_id": "'$DISCORD_THREAD_ID'", "note": "fixing the parser"}'
# 201 {"status": "acquired", ...}

# A second session asking for the same resource:
# 409 {"status": "held", "claim": {"thread_id": ..., "note": "fixing the parser",
#      "holder_state": "running", "holder_thread_name": "..."}}

# When done
curl -X DELETE "$CCDB_API_URL/api/claims?resource=repo:ccdb%23issue-123&thread_id=$DISCORD_THREAD_ID"
```

Claims are **advisory** — nothing enforces them at the git or filesystem level — and every claim carries a TTL (default 2h, max 24h) so a session that dies cannot pin a resource forever. The 409 body reports whether the holder is still running, which is how a caller decides whether to wait, work on something else, or take over with `force=true`. Resource names are free-form and normalized (case and whitespace), so `repo:ccdb` and `Repo: CCDB` are the same claim.

The lounge prompt tells every session to claim before starting and to release when finished.

### Session-to-Session Relay

Observability lets a session see a peer; a claim keeps them apart. When two sessions have already collided, they need to actually talk — and one of them needs to stop.

```bash
curl -X POST "$CCDB_API_URL/api/threads/<their_thread_id>/message" \
  -H "Content-Type: application/json" \
  -d '{"text": "I started this at 13:02 on branch fix/parser and already pushed 3 commits.",
       "from_thread": "'$DISCORD_THREAD_ID'", "mode": "queue", "hop": 0}'
```

`on_message` ignores anything a bot wrote — that guard is what stops the bot from talking to itself — so relays go through this endpoint instead, the same way `/api/spawn` does.

- **`mode: "queue"`** (default) waits for the receiver's current turn to finish.
- **`mode: "interrupt"`** SIGINTs the turn in flight, so "stop now" lands within seconds. It can cost the receiver uncommitted work, so it is reserved for real conflicts.
- The relayed text is **posted into the thread** before it reaches Claude, so the humans watching see the whole AI-to-AI exchange. A relay is never a back channel.
- Every message is **wrapped in a marker** naming the sending thread and stating that it is not from the human — an unmarked instruction would be obeyed as if the owner had written it.

Loops are the real risk (two sessions answering each other burn tokens and interrupt each other indefinitely), so a guard bounds every chain: **max 2 hops**, a 60s cooldown per thread pair, 5 relays per sender per 10 minutes, and no self-sends. Refusals come back as 429 with the reason.

The lounge prompt also gives sessions a tie-break rule so the conversation converges instead of ending in mutual politeness: whoever has commits or a PR beats whoever is still investigating; otherwise the earlier session continues; ties go to the lower thread ID. Whoever stands down pushes its branch first and hands over what it learned.

### Automatic Collision Detection

The lounge and claims both depend on a session *saying* something. This catches the overlaps nobody announced, from what the sessions actually did: if two live sessions write to the same file within 15 minutes, they are working on the same thing whether or not either mentioned it.

`EventProcessor` records the path of every write-type tool call (`Write`, `Edit`, `MultiEdit`, `NotebookEdit`); `CollisionWatchCog` compares those sets across live sessions once a minute.

> Why file paths and not working directories: on a single-user host every session tends to start in the same home directory, so `working_dir` equality flags every pair and means nothing. A shared *edited file* is almost never a coincidence. Reads are deliberately ignored — two sessions reading the same file is normal and would drown the signal.

When an overlap is found, the watcher posts:

- a line in the **AI Lounge**, which is injected into every session's next turn at no token cost and without interrupting anything, and
- a message in **each colliding thread**, naming the peer, the shared files, and the endpoints that resolve it.

It never relays into a running session — preempting a turn on a mere suspicion would cost more than the collision. Escalating is the sessions' decision, using the relay endpoint above. Each pair is announced at most once every 30 minutes, because a warning repeated every minute is a warning everyone learns to ignore.

Enabled automatically; it stays dormant until two sessions actually overlap.

### Programmatic Session Creation

Spawn new Claude Code sessions from scripts, GitHub Actions, or other Claude sessions — without Discord message interaction.

```bash
# From another Claude session or a CI script:
curl -X POST "$CCDB_API_URL/api/spawn" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "Run security scan on the repo", "thread_name": "Security Scan"}'
# Returns immediately with the thread ID; Claude runs in the background
```

**Deferred start (`auto_start=false`)** — Create a thread and post a seed message without starting Claude immediately. Claude starts only when a user replies, and receives the seed message as context automatically. A seed longer than Discord's per-message limit is posted as several messages, and all of them are recovered as context — the seed is read up to the first human reply, so it is never truncated mid-sentence.

```bash
# Post a notification; Claude starts when the user replies
curl -X POST "$CCDB_API_URL/api/spawn" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Good morning! Here is your daily summary: ...",
    "thread_name": "Morning Briefing",
    "auto_start": false
  }'
```

This is useful for notification-style workflows (e.g. daily briefings, CI alerts) where you want to display information upfront and let the user decide whether to engage Claude.

**Adding the requester to the thread (`user_id`)** — A spawned thread is created by the bot, so nobody is watching it: it sits in the channel list until someone goes looking. Pass a Discord `user_id` and ccdb adds that user as a thread member before the seed message is posted, so the thread lands in their joined list and they see it from its first line.

```bash
curl -X POST "$CCDB_API_URL/api/spawn" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Triage the failing nightly build",
    "thread_name": "Nightly Triage",
    "user_id": 123456789012345678
  }'
```

A `user_id` that is not a positive integer is a caller bug and is rejected with 400. A Discord-side failure to add the member is only a visibility miss and is suppressed — a spawn that already created the thread and started Claude is never reported as failed.

**Telling agent-started threads apart (`🤖`)** — A spawned thread looks exactly like one a person opened by posting in the channel, and Discord offers no per-thread colour or badge, so the title is the only surface left. ccdb prepends a marker to the name the caller chose — `{"thread_name": "Nightly Triage"}` becomes **🤖 Nightly Triage** — which keeps the agent's own wording intact and still reads at a glance in the channel list. The marker is never applied twice, and it survives the 100-character limit (the tail is trimmed, not the head). Set `CCDB_SPAWN_THREAD_MARKER` to use a different marker, or to an empty string to turn it off. `/fork` and session resume are left alone: they carry their own prefixes (`🔀`, `▶`) and a human asked for them.

**Telling *whose* child it is (`parent_thread_id`)** — one marker is enough for one spawner; with several sessions fanning out at once the channel list becomes a pile of identical `🤖` titles and the tree is gone. Pass the calling thread and both ends get the same two-character **family code**, derived from that thread's ID (never allocated, so anyone holding the ID can recompute it):

```bash
curl -X POST "$CCDB_API_URL/api/spawn" \
  -H "Content-Type: application/json" \
  -d '{
    "prompt": "Triage the failing nightly build",
    "thread_name": "Nightly Triage",
    "parent_thread_id": '$DISCORD_THREAD_ID'
  }'
```

- child: **🤖K2 Nightly Triage**
- parent, renamed once on its first spawn: **🌳K2 <its own title>**
- a child that spawns in turn keeps both — **🤖K2 🌳P9 …** — child of K2, root of P9
- both threads get a one-line cross-link, so the jump is one click either way

The link is also recorded, so a session managing a fan-out can read it instead of parsing titles: `GET /api/sessions` reports `parent_thread_id`, `family` and `children` per thread. Renaming, cross-linking and recording are each best-effort — a parent that was archived or renamed past Discord's two-per-ten-minutes limit costs the decoration, never the spawn. `CCDB_SPAWN_PARENT_MARKER` changes `🌳` (empty disables it), mirroring `CCDB_SPAWN_THREAD_MARKER`.

Claude subprocesses receive `DISCORD_THREAD_ID` as an environment variable, so a running session can spawn child sessions to parallelize work.

**Manager sessions (bundled skill)** — The endpoints above are the mechanics; [`skills/relay-thread-manager`](../../skills/relay-thread-manager/SKILL.md) teaches an agent the workflow on top of them: when to fan out, how to write a self-contained brief, tracking workers through the lineage in `/api/sessions`, relaying follow-ups within the hop and rate limits, verifying reports against the actual PR or commit, escalating to the human, and closing the tree with `✅`. It is plain `curl`, so it works the same for Claude Code and Codex. Install it by copying or symlinking the directory into the agent's skill folder:

```bash
ln -s "$PWD/skills/relay-thread-manager" ~/.claude/skills/relay-thread-manager   # Claude Code
ln -s "$PWD/skills/relay-thread-manager" ~/.codex/skills/relay-thread-manager    # Codex
```

Then ask a thread to "split this across three worker threads and report back".

When a thread's work looks finished, the agent tells the user the thread can be closed and calls `POST /api/threads/{thread_id}/candidate` (the older `/done` does the same), which prefixes the title with **🏁** — a completion candidate. It is a proposal, not a verdict: only a human closes the work (✅, Done in the console), so an agent's guess never makes an unfinished request disappear from the list. A new human reply removes the marker automatically. `CCDB_CANDIDATE_THREAD_MARKER` changes `🏁` (empty disables the marker and the instruction); `CCDB_DONE_THREAD_MARKER` changes the human's `✅`.

A thread that a scheduled task will post into later — a follow-up registered with `thread_id`, or a `ScheduleWakeup` — carries **⏰** at the front of its title for as long as the task is waiting, so a thread that will resume on its own no longer looks abandoned. The scheduler reconciles the marker from the task table every tick: it disappears when a one-shot fires or the task is disabled or deleted, sits behind `✅` when both apply, and survives an automatic retitle. Threads with an active wait (`POST /api/waits`) carry the same **⏰** until the wait finishes or is cancelled. `CCDB_SCHEDULED_THREAD_MARKER` changes `⏰` (empty disables it).

The same slot as `✅` tells you whose move it is when a turn stops: **❓** means the thread is waiting on you — an AskUserQuestion is open, or the agent called `POST /api/threads/{thread_id}/waiting` because it needs your decision — and **⚠️** means the turn ended in an error. Only one outcome marker (`✅` / `❓` / `👀` / `📋` / `⚠️`) shows at a time, always in front of `⏰`, and your next reply clears it. States that change on every message (such as "running") stay on the emoji reactions: Discord allows a thread two renames per ten minutes, so the title only carries states that outlast a turn. When a mark hits that limit, the endpoint answers within a few seconds with `202 {"status": "queued"}` and the rename lands on its own once the limit passes; marks requested meanwhile coalesce, so the thread is renamed once more to the latest state rather than replaying stale ones. `CCDB_WAITING_THREAD_MARKER` and `CCDB_ERROR_THREAD_MARKER` change `❓` and `⚠️` (empty disables them).

"Your move" is split by what is expected of you, so the channel list doubles as a todo list: **❓** a reply is enough (a question, a choice, a go/no-go), **👀** a deliverable is waiting for you to look over (`POST /api/threads/{thread_id}/review`), and **📋** there is a task only you can do outside the chat — a manual step in a UI, sign-in/MFA, an approval, a payment (`POST /api/threads/{thread_id}/action`). The agent picks one when its turn ends; all of them share the single outcome slot with `✅` / `⚠️`. `CCDB_REVIEW_THREAD_MARKER` and `CCDB_ACTION_THREAD_MARKER` change `👀` and `📋` (empty disables them).

### Authenticated External Ingest with Result Retrieval (`/api/ingest`)

`POST /api/ingest` is the **authenticated, attachment-aware spawn** for untrusted external clients (browser extensions, mobile shortcuts, webhooks). Unlike `/api/spawn` (trusted, localhost), it requires a dedicated `ingest_token` (set `CCDB_INGEST_TOKEN`; independent of `api_secret`) and can carry base64 file attachments that are written to `{working_dir}/ingest/{thread_id}/` so the spawned session can read them. It creates a real Discord thread, so the full interaction stays observable.

The session is **interactive** (a real Discord thread you can keep replying in) — but you can still get its final answer back programmatically. When result retrieval is configured (auto-wired via `setup_bridge()`), the response includes a `result_id`, and `GET /api/ingest/{result_id}` polls for the session's final reply. The same final reply is also attached to the Discord thread as `ccdb-answer.md`, so integrations can treat the attachment as the canonical answer payload. This is the round-trip pattern: post a thread + attachments → wait → read the answer file or poll result → write it back to your own system (e.g. a Teams thread), while Discord keeps the history.

```bash
# Post work (optionally with attachments); returns immediately
curl -X POST "$CCDB_API_URL/api/ingest" \
  -H "Authorization: Bearer $CCDB_INGEST_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"content": "Summarize this thread and draft a reply",
       "attachments": [{"filename": "thread.txt", "data": "<base64>"}]}'
# → {"status": "spawned", "thread_id": "…", "result_id": "ab12…", "attachments_saved": 1}

# Poll for the final reply
curl "$CCDB_API_URL/api/ingest/ab12…" -H "Authorization: Bearer $CCDB_INGEST_TOKEN"
# → {"status": "done", "result": "…", "error": null, "thread_id": "…", "thread_name": "…"}
```

The endpoint is opt-in: with no `ingest_token` configured, `POST` responds `503`. When result retrieval is unavailable, `POST` simply omits `result_id` and `GET /api/ingest/{id}` returns `503` — the spawn behaviour is otherwise unchanged. The request body and attachments are **not** persisted in the result store (only status, the final text, and the thread id); results are capped at 200 rows.

#### Teams reply drafts start at P0

A request from the Teams Channel Exporter (recognised by its fixed request envelope, see `claude_discord/teams_exporter.py`) is a reply someone in Teams is waiting for. Its thread is put on the console board at **P0** as soon as it is spawned — written as the item's own priority, so triage leaves it alone and you can still lower it — and it is never classified "done" by a model. Without the console this is a no-op.

#### Zip bundles are expanded on arrival

A client can pack a whole thread's files into one `.zip` to stay under the 20-attachment / 50 MB request caps: ccdb extracts it into a sibling `<name>_files/` directory and gives the session the member paths instead of the archive, so the prompt stays paths-only and the session reads what it needs. Extraction is bounded (5000 members, 200 MB uncompressed) and skips any member that would escape its directory.

The archive is replaced **only** when extraction actually produced files. `zipfile.is_zipfile()` matches an end-of-central-directory record near the *end* of a file — it does not require the file to *start* like an archive — so a large opaque binary (a Windows `.evtx` log, a memory dump, a packet capture) can be taken for an empty archive by chance. Such a file, and a genuinely empty zip, is kept exactly as it arrived rather than "expanded" into nothing and deleted; a refused or malformed archive is likewise left untouched. Nothing is dropped on the way in.

#### Verified attachment delivery (`attachments_manifest`)

An attachment that goes missing on the client side used to be invisible: ccdb saved what it was given and reported a count nobody could check, so a session answered as though it had a file it never received. Send a **manifest** and ccdb verifies the delivery instead of assuming it — one entry per attachment you found upstream, with `status` telling ccdb whether its bytes are in this request:

```bash
curl -X POST "$CCDB_API_URL/api/ingest" \
  -H "Authorization: Bearer $CCDB_INGEST_TOKEN" -H "Content-Type: application/json" \
  -d '{"content": "Draft a reply",
       "attachments": [{"filename": "bundle.zip", "data": "<base64>"}],
       "attachments_manifest": [
         {"name": "shot.png",  "status": "embedded", "sha256": "…", "message": "返信 11"},
         {"name": "debug.log", "status": "linked", "url": "https://…", "message": "返信 12",
          "reason": "SharePoint download returned 403"}
       ]}'
# → {"status": "spawned", …, "attachments": {"verified": true, "complete": false,
#      "missing": [], "not_delivered": [{"name": "debug.log", "message": "返信 12", …}]}}
```

| `status` | Meaning |
|---|---|
| `embedded` (default) | The bytes are in this request — ccdb expects to find a matching file |
| `linked` | Only a URL was obtained (no host permission, auth wall, …) |
| `skipped` | Deliberately not sent (size cap, filtered out) |
| `failed` | Capture or download failed |

ccdb matches each `embedded` entry to a delivered file by **sha256 first**, then exact name, then the `4_image.png` index prefix a bundler adds for colliding names, then size — consuming each file at most once, so two attachments named `image.png` can't both claim the one file that arrived. Anything unaccounted for is surfaced four ways: a ⚠️ block at the **top of the session prompt** naming the missing files and telling the session not to invent their contents (with an extra callout when the loss is on the newest message), an `ATTACHMENTS-REPORT.md` ledger written next to the files, the `attachments` verdict in the response above, and a `WARNING` in the log. Supplying `message` also groups the prompt's path list by upstream message and marks the newest group as the one to read first.

Set `CCDB_INGEST_REQUIRE_COMPLETE=1` (or `ingest_require_complete=True`) to **refuse** a lossy ingest with `409` rather than start a session on partial evidence — appropriate where the attachments *are* the request. Omit the manifest entirely and nothing changes: the ingest is reported as `verified: false`, never as verified-complete.

#### Running per-thread summaries for long ingest threads (`/api/ingest/summary`)

An ingest client that keeps replying in one **upstream** thread for months (notably the Teams browser extension) would otherwise have to re-export the entire history on every run — ccdb's "Thread = Session" model spawns a *fresh* Discord thread + Claude session per ingest and remembers nothing about the upstream thread. Opt into a compact **running summary** keyed by a client-supplied stable `summary_key` (e.g. the upstream thread's root message id), stored in the new `thread_summaries` table, and the client can send only the **diff** while the session still gets full historical context:

1. Before exporting, the client calls `GET /api/ingest/summary?key=…` (external, ingest-token gated) to read the stored `summary` + `marker`, and bounds its export to messages newer than `marker`.
2. `POST /api/ingest` accepts `summary_key` + `latest_marker`; ccdb injects the stored summary into the prompt as context and asks the session to save an updated one.
3. The session calls `POST /api/ingest/summary` (internal control plane, localhost — same trust model as `/api/tasks`) with its own `result_id` and the new `summary`; ccdb resolves the key and advances the `marker` **from the ingest row**, so the read position only moves forward when a summary is actually saved (a failed session re-exports the same diff, never skips messages).

`DELETE /api/ingest/summary?key=…` forces a full re-summary. The marker is opaque to ccdb and never handled by the session, so it cannot drift. Fully backward-compatible and Zero-Config: omit `summary_key` and ingest behaves exactly as before. The external listener exposes only the `GET` (read) route; writing a summary is a localhost-only action. `ingest_results` gains `summary_key`/`pending_marker` columns (auto-migrated on existing DBs).

#### Mirroring an upstream thread as raw files (`/api/teams/sync`)

The running summary above keeps a *distillation* of an upstream thread. When you want the **raw conversation** on disk instead — so a session can read what was actually said, and so you can check an answer against it — use the sync pair. It stores one file per message and asks the client to keep **no sync state at all**:

1. `POST /api/teams/sync/plan` — the client sends the id + content hash of every message it can see (no bodies, so a 1000-reply thread costs tens of KB). ccdb answers with `want_messages` / `want_attachments`: the subset it is missing or holds at a different hash, plus `newest_have_mid` for the client to stop scrolling early.
2. `POST /api/teams/sync/push` — the client uploads exactly that subset, with attachment bytes as base64.

A *changed* hash and a *never-seen* id are the same question, so following an upstream **edit** is not a separate feature — it falls out of the same comparison, and the superseded version is preserved under `_history/` rather than overwritten.

Hashes are only compared within one scheme: when a client changes its hash formula (`fnv1a:` → `fnv1a2:`), the stored message is re-keyed in place without writing `_history/`, so a client upgrade does not look like thousands of edits. Teams lazy-loads images, so an attachment already on disk keeps its `📎` link even when a later push does not declare it, and an attachment name declared twice renders once.

```
{title}--{root_mid}/
  thread.json      identity, coverage, unresolved attachment gaps
  chain.jsonl      append-only order + revision journal
  README.md        how a session should read this folder
  messages/{mid}.md          one message, YAML frontmatter (author, timestamp, prev, hash, edited, deleted)
  messages/{mid}/…           that message's attachments
  _history/{mid}.{hash}.md   superseded versions
```

`next` is deliberately **not** stored: writing it would mean rewriting an existing file on every new reply. Order lives in `chain.jsonl`, and because the identity is the upstream Unix-ms message id, filenames sort chronologically on their own.

The directory is the single source of truth — `plan` is answered by reading it. Delete a message file and the next sync fetches it again; an interrupted push completes on the next one; pressing the button twice is a no-op. An attachment that could not be stored is **never** reported as success: it is listed in `thread.json`, in the folder's `README.md`, in the push response, and it keeps appearing in `want_attachments` until its bytes actually arrive.

Threads are filed one folder per company: `{root}/{company}/{title}--{root_mid}/`. A client may send `thread.org`, but the authority is `orgs.json` at the sync root — a hand-editable `team GUID → company` map that wins over the client, so a correction made there sticks. A team it does not know yet is learned from the first label a client sends; a thread with no company stays at the root. The company is a label, never part of the identity (`{team}/{root_mid}`), so re-filing never re-uploads anything — and a thread you move into a company folder by hand keeps syncing, which is how an existing flat vault is migrated.

Threads live under `{working_dir}/teams` by default, beside the `ingest/` tree — set `CCDB_TEAMS_VAULT_ROOT` (or `teams_vault_root=`) to keep them somewhere else, such as a notes vault you already read in an editor. Both routes use the same ingest bearer token as `/api/ingest`, are available on the external listener, and spawn nothing.

### Your Own Attention, Metered (`/attention`)

Agent time is cheap; the operator's own time — reading replies, deciding, instructing — is what runs out. The relay records metadata (never text) for every human message that reaches a session, on Discord and Teams, and estimates minutes of human attention per day and per thread: messages within 10 minutes of each other form a burst, a burst costs its span plus a 2-minute lead-in, and its minutes are split across threads by characters written (each attachment counts as 50 characters).

```bash
/attention                                   # ephemeral: today, last 7 days, top threads
curl "$CCDB_API_URL/api/attention?group_by=thread&from=2026-09-28&to=2026-10-04"
ccdb attention-backfill --guild <id> --since 2026-09-01   # seed history (owner only)
```

On by default (`CCDB_ATTENTION_ENABLED=false` stops recording); every number is labelled an estimate and reports its parameters. See [docs/attention.md](../attention.md).

### Ebi AI Console — Triage Agent Work from Desktop or Phone

An API-first triage screen on its own authenticated listener. The default **Focus** view answers "what do I do now?": a fast model (the cheapest one of the backend chosen with `/backend`, one-shot and tool-less) reads each open thread with new activity and fills in what nobody set by hand — priority, a deadline the conversation names, the next step, whether it can be done now or must wait, and whether it is bound to a place or device (📍 home, office, work PC). Only work you can do here and now ranks first; a value a person sets always wins and is pinned (`CCDB_CONSOLE_TRIAGE=0` turns triage off). Every thread is also sorted by whose move it is — **Inbox** (❓ / 👀 / 📋 / ⚠️: the ball is on you), **Running**, **To do**, **Idle**, a project/parent **Tree**, **Snoozed** and **Done**, plus a **Recent** view of every item (finished ones included) by last activity — with priority P0–P3, due dates and snooze, and each thread shows how full its context window was after its last turn (🧠 45%, amber from 70%, red from 90%) so a thread close to compaction is visible before it happens. Reply, mark done, or write down new work — quick capture hands it to an agent right away by default (a checkbox, remembered per browser, keeps it as a plain note) — and it runs in a conversation the console owns (no chat thread), on the same session runner, with its transcript in the session database and replies resuming the same session. Status is derived from what ccdb already knows (outcome markers, running turns, the slot queue, spawn lineage), so agents do nothing new; only priority/due/snooze/project/parent live in a new `work_items` table. Threads render as Markdown, with the relay's own activity (tool calls, status lines, automatic prompts) behind the "⚙ Activity" button. Columns resize, new work arriving for you chimes and can raise a desktop notification, images and text files (Markdown, sandboxed HTML, CSV, JSON, logs, code) preview in place instead of downloading, any file can be pasted, dropped or picked into a reply or into new work, and the reply box autocompletes slash commands and skills (with a real `/compact` for Codex conversations). The nav shows running / limit with − / + to change the concurrent-session limit on the spot (kept across restarts), and each thread carries one-click ⏸ Pause, ■ Stop and ▶ Resume, or ⏫ First / ⏬ Later while it waits for a slot. A picker above the reply box switches a conversation's backend, model and effort from its next turn, and the same picker in the nav sets the default new conversations start with. Keyboard-driven on desktop, installable to a phone's home screen.

Parent conversations show a collapsible **Delegated work** panel with child and grandchild
status, latest agent reports, and counts of running, attention-needed and completed tasks.
Open a child to inspect it, use **Back to parent** for its immediate parent or **Main conversation**
for the root, and send normal requests and decisions to the parent. Child replies are hidden until
you choose **Reply directly to this child**. See the
[console guide](../console.md#parent-conversations-and-delegated-work).

Scheduled reminders and API notifications using the standard notification color appear in the
normal conversation view, including cards delivered before this update; you do not need to enable
"⚙ Activity" to see them. Scheduling a notification posts a reminder without starting an agent
turn; replying to it starts the agent. See the [console guide](../console.md#reading-a-thread).

```bash
uv add "claude-code-discord-bridge[console] @ git+https://github.com/ebibibi/ebi-agent-chat-relay.git"
CCDB_CONSOLE_PORT=8100                                     # off unless set; passkey sign-in by default
# Optional extras on top of (or instead of) passkeys:
# CCDB_CONSOLE_ACCESS_TEAM_DOMAIN=myteam.cloudflareaccess.com  # Cloudflare Access JWT…
# CCDB_CONSOLE_ACCESS_AUD=<aud tag>
# CCDB_CONSOLE_ALLOWED_EMAILS=me@example.com               # …plus a required allowlist
# CCDB_CONSOLE_TOKEN=...                                   # a 32+ char bearer token for scripts
```

It **never runs unauthenticated**. By default you sign in with a **passkey** (WebAuthn, user verification required) — phishing-resistant multi-factor sign-in with no third-party account. The first passkey is registered with a single-use setup code that ccdb writes to its log; further devices are added from a signed-in one. Sessions are server-side `HttpOnly`, `SameSite=Strict` cookies (only a hash is stored). Passkeys need a secure origin (`https://…` or `http://localhost`, not an IP address). The JSON API under `/console/api` is the contract; the bundled web client is one consumer. See [docs/console.md](../console.md), [ADR-0010](../adr/0010-add-an-api-first-console-on-its-own-listener.md), [ADR-0011](../adr/0011-let-the-console-own-its-conversations.md) and [ADR-0012](../adr/0012-sign-in-to-the-console-with-passkeys-by-default.md).

### Startup Resume

If the bot restarts mid-session, interrupted Claude sessions are automatically resumed when the bot comes back online. Sessions are marked for resume in three ways:

- **Automatic (upgrade restart)** — `AutoUpgradeCog` snapshots all active sessions just before a package upgrade restart and marks them automatically.
- **Automatic (any shutdown)** — `ClaudeChatCog.cog_unload()` marks all mid-run sessions whenever the bot shuts down via any mechanism (`systemctl stop`, `bot.close()`, SIGTERM, etc.).
- **Manual** — Any session can call `POST /api/mark-resume` directly.

### Backend Switching — Claude / Codex / local / AG-UI / pi on Demand

ccdb introduces four slash commands that change which AI handles the next session, with no bot restart:

- `/backend [name] [scope]` — show or switch backend. `name` is `claude`, `codex`, `local`, `agui`, or `pi`. `scope` is `thread` (this thread only) or `global` (server-wide default). When you omit `scope`, the command auto-resolves: in a thread it scopes to that thread, otherwise it sets the global default.
- `/model [name] [scope]` — show or switch the model used by the **current** backend. Each backend remembers its own model preference, so flipping backend back and forth keeps your favoured models intact. Leave a backend's model unset to defer to that CLI's own default (e.g. Codex uses the `model` in `~/.codex/config.toml`, so ccdb tracks the console default instead of pinning a version).
  The `name` autocomplete is **discovered live**: ccdb asks the Anthropic models endpoint (using the credentials the Claude Code CLI already has) which models your account can see, so a model released this morning shows up in the dropdown without a ccdb upgrade. Aliases (`opus`, `sonnet`, …) are labelled with the model they currently resolve to. Offline, unauthenticated, or on Bedrock/Vertex/Foundry it silently falls back to a small static list; set `CCDB_MODEL_DISCOVERY=0` to always use that list. Codex suggestions are discovered too, but locally: the Codex CLI exposes no model listing, so ccdb reads the catalog the CLI already fetched for itself (`$CODEX_HOME/models_cache.json`) rather than calling OpenAI — a new generation such as `gpt-6-astra` appears as soon as the Codex CLI has seen it. Never run the Codex CLI on this host and it falls back to a small static list. pi is discovered the same way and for the same reason — ccdb reads the two catalogs in `~/.pi/agent` (override the directory with `CCDB_PI_HOME`): `models-store.json`, which pi downloaded for the providers it knows, and `models.json`, where you declare your own (a local Ollama endpoint, a gateway). Both are merged, because a thread whose only usable provider is the hand-declared one would otherwise see a dropdown that omits the only model it can run. pi suggestions are fully qualified (`anthropic/claude-opus-5`, `ollama/gpt-oss:120b`) because pi's bare `--model` is a fuzzy *pattern* — `opus` resolves to whichever opus pi ranks first, which is not a choice ccdb should make for you. Any id you type still works.
- `/effort [level] [scope]` — show or switch the **reasoning effort** used by the current backend. Valid levels are backend-specific: Claude accepts `low/medium/high/max`; Codex accepts `minimal/low/medium/high/xhigh/max/ultra` (mapped to the CLI's `model_reasoning_effort`). That Codex set is the *union* across models, not what any one model takes — `minimal` is offered by the older GPT-5.x models only, and `max`/`ultra` by GPT-5.6 and GPT-6 only. The Codex CLI rejects a level its selected model does not support, and that error reaches the thread. Leave it unset to defer to the CLI default.
- `/ollama status|list|ps|show|pull|rm|use` — manage the runtime behind the `local` backend. `/backend` and `/model` choose a model; they cannot tell you what is installed, what fits, or what is resident in memory — and when the cloud backends are unavailable those are the only questions that matter. `/ollama` mirrors Ollama's own API for exactly those, with autocompleted model arguments. It flags a model that does not advertise the `tools` capability (Codex acts only through tool calls, so such a model *describes* the edit instead of making it) and refuses to delete the selected one — see [docs/local-backend.md](../local-backend.md#managing-the-runtime-ollama).

**The local model has no environment variable.** `CCDB_LOCAL_MODEL` has been removed: it was a second, invisible source of truth that could disagree with the selection shown in Discord. What `/ollama use` (or `/model`) selects is what runs, and `/ollama list` marks it with `▶`.

All four commands persist to SQLite via `SettingsRepository`, so the choice survives bot restarts. Calling them with no arguments prints the current global default plus any thread override.

**What happens to a thread that already has a session?** Session IDs are not interoperable between the CLIs — handing a Codex rollout ID to `claude --resume` (or a Claude UUID to `codex exec resume`) fails at the CLI level. ccdb therefore performs a **file-backed conversation handoff** for both thread-scoped and global switches:

1. It keeps the old session ID long enough to locate that backend's local JSONL (`~/.claude/projects` or `~/.codex/sessions`).
2. It extracts a bounded transcript of user and assistant text. System/developer instructions, hidden reasoning, tool calls/results, and image payloads are excluded.
3. It starts a native session in the new backend and prepends that transcript to the first new user message. The new session ID then replaces the old mapping normally.

Scheduled follow-ups that post into an existing thread (tasks registered with `thread_id`, `ScheduleWakeup`) apply the same check: if the thread's session was created by a different backend than the one the run uses, the scheduler starts a fresh session with the transcript handed over instead of resuming an ID the CLI cannot find. A resume the CLI rejects is never saved back to the thread, so one failed run cannot mislabel the session for every later turn.

If the local JSONL is missing or unreadable, ccdb safely degrades to a fresh session and logs the missing handoff. Records written before ccdb tracked backend ownership keep the legacy resume behaviour because their source backend cannot be identified reliably.

Visual cues so you never forget which one you're talking to:

- **Claude sessions** open with a blurple embed titled "🤖 Claude Code session started".
- **Codex sessions** open with an OpenAI-teal embed titled "🌀 OpenAI Codex session started".
- The completion embed prepends a `🧠 Claude · sonnet` / `🧠 Codex · gpt-5.6-sol` chip alongside the usual duration / cost / token / context metrics. (When a backend's model is left at the CLI default, the chip shows just the backend name.)

Concrete example:

```text
/backend codex                        # global → codex (next new sessions use codex)
/model gpt-6-astra                    # global → codex uses GPT-6
/effort xhigh                          # global → codex reasons at xhigh effort
                                       # …open a thread, send a message…
/backend claude scope:thread          # this thread only → switch back to claude
/backend agui scope:thread            # this thread only → configured remote AG-UI agent
/model opus scope:thread              # this thread only → claude/opus
/effort max scope:thread              # this thread only → claude reasons at max
                                       # other threads keep the global codex defaults
```

Behind the scenes:

- `BackendFactory` — captures the static configuration at boot (per-backend command path or AG-UI endpoint, permission mode, working dir, allowed tools, timeout, append-system-prompt, effort, api_port, api_secret) and builds a fresh `ClaudeRunner`, `CodexRunner`, or `AgUiBackend` on demand. `api_port` is wired automatically by `setup_bridge` after the REST API server starts, so factory-built CLI runners always have `CCDB_API_URL` injected into their subprocess environment.
- `BackendSettings` — thin wrapper over `SettingsRepository` that resolves the active backend with **thread > global > env** precedence and persists writes from the slash commands.
- `SessionBackend` Protocol — the abstract interface that both runners satisfy. Internal plumbing (cogs, embeds, views, scheduler, webhook trigger) takes a `SessionBackend`, never one concrete runner class.

**Where does each backend authenticate?** Claude Code uses your existing Claude Pro/Max subscription via the `claude` CLI's `claude login`. Codex uses your existing ChatGPT Plus/Pro/Business subscription via the `codex` CLI's `codex login`. ccdb never sees raw API keys — it just shells out to whichever CLI is selected.

### Account Pools — Several Logins per Backend

When one login hits its usage limit, every thread on that backend normally stops until the window resets. If you hold more than one login, list them in a TOML file and point `CCDB_ACCOUNT_POOLS_FILE` at it; the relay then picks a profile (a pre-logged-in `CLAUDE_CONFIG_DIR` or `CODEX_HOME`) for each session or turn:

```toml
[pools.claude]
strategy = "priority"        # priority | sticky | round_robin | most_headroom
[[pools.claude.profiles]]
name = "personal"            # the relay's own login
[[pools.claude.profiles]]
name = "work"
config_dir = "/home/me/.claude-work"
```

- **Strategies**: `priority` drains profiles in order and returns to an earlier one when it resets; `sticky` switches on exhaustion and stays; `round_robin` rotates per new session; `most_headroom` picks the lowest utilization.
- **Continuity**: when a thread moves to another profile, its session transcript is copied into that profile and the same session resumes there (measured on Claude Code 2.1.289 and codex-cli 0.160.0). If that is impossible, the new session is seeded with the conversation text.
- **Failover**: a quota rejection marks the profile exhausted until its reset and tells the thread which profile is next; `retry_on_exhaustion = true` reruns the turn there instead.
- **Visibility**: `/usage` lists every profile; the completion notice names the profile that served the turn.

**Terms of service.** You are responsible for complying with each vendor's terms for every login you put in a pool. The feature only selects among logins you already hold — it sets one environment variable on the child process and never reads, copies or stores tokens.

Provider credentials in the relay's environment (`ANTHROPIC_API_KEY`, `CLAUDE_CODE_OAUTH_TOKEN`, `OPENAI_API_KEY`, …) take precedence over each profile's login and defeat pooling — unset them; the relay warns at startup.

Without the file, nothing changes. Full reference: [docs/account-pools.md](../account-pools.md) · sample: [examples/account-pools.example.toml](../../examples/account-pools.example.toml).

### Execution Environments — Where the Agent Runs

By default every CLI backend runs on the host as the relay's own user (`host`, unchanged from earlier versions). An operator can put an OS boundary around it instead. `bwrap`, `container` and `ssh` are **preview**; `host` stays the default, and the recommended setup for a sandboxed deployment is a dedicated `CLAUDE_CONFIG_DIR` / `CODEX_HOME` (never your own `~/.claude`):

| Mode | What it does |
|---|---|
| `host` | Today's behaviour. Default |
| `native` | The agent's own sandbox: Claude Code's `sandbox` settings via `--settings`, Codex `--sandbox workspace-write`. pi refuses (it has none) |
| `bwrap` (preview) | bubblewrap: read-only system, **empty `$HOME`** with only the working directory, the agent's state (its hook/config files read-only) and the CLI's install bound back; no `sudo`; the relay's `.env`, the user runtime dir / D-Bus and the Docker socket hidden. Network stays shared |
| `container` (preview) | `docker run --rm -i` with your image, same config/git protection as `bwrap` ([sample Dockerfile](../../deploy/agent-container/Dockerfile)) |
| `ssh` (preview) | Runs the CLI on another machine, streaming stdin/stdout |

The operator sets the default and an allowlist in the environment (`CCDB_EXECUTION_MODE`, `CCDB_EXECUTION_ALLOWED_MODES`); `/sandbox` lets a user pick a mode for one thread **from that allowlist only**. Preflight failures (no `bwrap`, missing image, unreachable host) fail the turn with one sentence and start no process, and the completion notice shows which environment served the turn. See [docs/execution-environments.md](../execution-environments.md) and ADR-0008.

---

**What You Can Do** · [Feature Reference](reference.md) · [Getting Started](getting-started.md) · [Automation, Scheduler & REST API](automation.md) · [Architecture & Project Background](architecture.md)
