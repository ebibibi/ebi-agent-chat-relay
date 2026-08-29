# Ebi Agent Chat Relay

*Formerly Claude Code Discord Bridge, then Claude & Codex Discord Bridge. Every existing
identifier still works: the package is `claude-code-discord-bridge` (kebab-case), the
command is `ccdb`, and `ccdb` remains the short name used throughout this document.*

[![CI](https://github.com/ebibibi/ebi-agent-chat-relay/actions/workflows/ci.yml/badge.svg)](https://github.com/ebibibi/ebi-agent-chat-relay/actions/workflows/ci.yml)
[![CodeQL](https://github.com/ebibibi/ebi-agent-chat-relay/actions/workflows/codeql.yml/badge.svg)](https://github.com/ebibibi/ebi-agent-chat-relay/actions/workflows/codeql.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**Run coding agents from Discord or Microsoft Teams. Choose Claude Code, OpenAI Codex,
a local model, or any compatible AG-UI agent behind the same conversation.**

Ebi Agent Chat Relay turns each Discord thread or Teams conversation into an isolated,
persistent agent session. Work on a feature in one conversation, review a PR in another, and run
a background task in a third — simultaneously. Discord can mix backends per thread; Teams uses the
configured/global backend in v4. The relay handles coordination so sessions do not clobber each
other.

**Why the name changed.** This started as a bridge between one AI and one chat app. It is now a
relay with two production frontends and four backend choices. Three of the four words in the old
name had stopped being true. See [ADR-0001](docs/adr/0001-adopt-ebi-agent-chat-relay.md) for the
decision and [the rename plan](docs/RENAME_PLAN.md) for the compatibility-preserving transition.

**Use your existing subscriptions, your own infrastructure, or a remote agent.** ccdb can run the
official Claude Code and Codex CLIs, a Codex-compatible local endpoint, or an AG-UI HTTP/SSE
agent. Discord exposes runtime `/backend` switching; Teams uses the configured backend through the
same factory in v4.

## What's new in v4

Version 4 makes two independent choices explicit: **where people talk** and **which agent does the
work**. Any supported frontend can use any supported backend.

### Frontend × backend

| | Claude Code | OpenAI Codex | Local | AG-UI |
|---|---:|---:|---:|---:|
| Discord | ✅ | ✅ | ✅ | ✅ |
| Microsoft Teams | ✅ | ✅ | ✅ | ✅ |

- **Discord** remains the zero-migration default. Existing deployments start exactly as before.
- **Microsoft Teams** is production-ready through a small public receiver and an outbound-only
  `ActivityPuller` on the private session host. Discord and Teams can run together in one process
  with `CCDB_FRONTENDS=discord,teams`.
- **AG-UI** connects either chat surface to an HTTP/SSE agent implementing the Agent–User
  Interaction Protocol. Claude Code, Codex, and the guarded local backend remain available.

Start with the [backend guide](docs/backends.md). For Teams, follow the complete
[Microsoft Teams setup guide](docs/teams-setup.md), then use the deeper
[surface behavior](docs/teams.md) and [relay security model](docs/teams-relay.md) references.

**[日本語](docs/ja/README.md)** | **[简体中文](docs/zh-CN/README.md)** | **[한국어](docs/ko/README.md)** | **[Español](docs/es/README.md)** | **[Português](docs/pt-BR/README.md)** | **[Français](docs/fr/README.md)**

> **Disclaimer:** This project is not affiliated with, endorsed by, or officially connected to Anthropic or OpenAI. "Claude" and "Claude Code" are trademarks of Anthropic, PBC; "OpenAI", "Codex", and "ChatGPT" are trademarks of OpenAI. This is an independent open-source tool that interfaces with the Claude Code CLI and the OpenAI Codex CLI.

> **Built entirely by Claude Code.** This entire codebase — architecture, implementation, tests, documentation — was written by Claude Code itself. The human author provided requirements and direction via natural language. See [How This Project Was Built](#how-this-project-was-built).

---

## The Big Idea: Parallel Sessions Without Fear

When you send tasks to Claude Code or OpenAI Codex in separate Discord threads, the relay does four things automatically — regardless of which backend you picked:

1. **Concurrency notice injection** — Every session's system prompt includes mandatory instructions: create a git worktree, work only inside it, never touch the main working directory directly.

2. **Active session registry** — Each running session knows about the others. If two sessions are about to touch the same repo, they can coordinate rather than conflict.

3. **AI Lounge** — A session-to-session "breakroom" injected into every prompt. Before starting, each session reads recent lounge messages to see what other sessions are doing, and claims the repo, issue or file it is about to touch (see [Resource Claims](#resource-claims)) so a second session is turned away before it duplicates the work. Before disruptive operations (force push, bot restart, DB drop), sessions check the lounge first so they don't stomp on each other's work.

4. **Backend-agnostic surface** — The same Discord UI, slash commands, scheduler, API, and Lounge work the same way whether a thread runs Claude or Codex. Mix backends across threads if you want — e.g. Claude for refactors, Codex for code review — using `/backend` per thread.

```
Thread A (feature)    ──→  Claude Code  (worktree-A)  ─┐
Thread B (PR review)  ──→  OpenAI Codex (worktree-B)   ├─→  #ai-lounge
Thread C (docs)       ──→  Claude Code  (worktree-C)  ─┘    "A: auth refactor in progress"
                                                             "B: PR #42 review done (codex)"
                                                             "C: updating README"
```

No race conditions. No lost work. No merge surprises. No backend lock-in.

---

## What You Can Do

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

Claude subprocesses receive `DISCORD_THREAD_ID` as an environment variable, so a running session can spawn child sessions to parallelize work.

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

### Startup Resume

If the bot restarts mid-session, interrupted Claude sessions are automatically resumed when the bot comes back online. Sessions are marked for resume in three ways:

- **Automatic (upgrade restart)** — `AutoUpgradeCog` snapshots all active sessions just before a package upgrade restart and marks them automatically.
- **Automatic (any shutdown)** — `ClaudeChatCog.cog_unload()` marks all mid-run sessions whenever the bot shuts down via any mechanism (`systemctl stop`, `bot.close()`, SIGTERM, etc.).
- **Manual** — Any session can call `POST /api/mark-resume` directly.

### Backend Switching — Claude / Codex / AG-UI on Demand

ccdb 3.0 introduces three slash commands that change which AI handles the next session, with no bot restart:

- `/backend [name] [scope]` — show or switch backend. `name` is `claude`, `codex`, `local`, or `agui`. `scope` is `thread` (this thread only) or `global` (server-wide default). When you omit `scope`, the command auto-resolves: in a thread it scopes to that thread, otherwise it sets the global default.
- `/model [name] [scope]` — show or switch the model used by the **current** backend. Each backend remembers its own model preference, so flipping backend back and forth keeps your favoured models intact. Leave a backend's model unset to defer to that CLI's own default (e.g. Codex uses the `model` in `~/.codex/config.toml`, so ccdb tracks the console default instead of pinning a version).
  The `name` autocomplete is **discovered live**: ccdb asks the Anthropic models endpoint (using the credentials the Claude Code CLI already has) which models your account can see, so a model released this morning shows up in the dropdown without a ccdb upgrade. Aliases (`opus`, `sonnet`, …) are labelled with the model they currently resolve to. Offline, unauthenticated, or on Bedrock/Vertex/Foundry it silently falls back to a small static list; set `CCDB_MODEL_DISCOVERY=0` to always use that list. Codex suggestions stay static (the Codex CLI exposes no model listing) — any id you type still works.
- `/effort [level] [scope]` — show or switch the **reasoning effort** used by the current backend. Valid levels are backend-specific: Claude accepts `low/medium/high/max`; Codex accepts `minimal/low/medium/high/xhigh` (mapped to the CLI's `model_reasoning_effort`). Leave it unset to defer to the CLI default.
- `/ollama status|list|ps|show|pull|rm|use` — manage the runtime behind the `local` backend. `/backend` and `/model` choose a model; they cannot tell you what is installed, what fits, or what is resident in memory — and when the cloud backends are unavailable those are the only questions that matter. `/ollama` mirrors Ollama's own API for exactly those, with autocompleted model arguments. It flags a model that does not advertise the `tools` capability (Codex acts only through tool calls, so such a model *describes* the edit instead of making it) and refuses to delete the selected one — see [docs/local-backend.md](docs/local-backend.md#managing-the-runtime-ollama).

**The local model has no environment variable.** `CCDB_LOCAL_MODEL` has been removed: it was a second, invisible source of truth that could disagree with the selection shown in Discord. What `/ollama use` (or `/model`) selects is what runs, and `/ollama list` marks it with `▶`.

All three commands persist to SQLite via `SettingsRepository`, so the choice survives bot restarts. Calling them with no arguments prints the current global default plus any thread override.

**What happens to a thread that already has a session?** Session IDs are not interoperable between the two CLIs — handing a Codex rollout ID to `claude --resume` (or a Claude UUID to `codex exec resume`) fails at the CLI level. ccdb therefore performs a **file-backed conversation handoff** for both thread-scoped and global switches:

1. It keeps the old session ID long enough to locate that backend's local JSONL (`~/.claude/projects` or `~/.codex/sessions`).
2. It extracts a bounded transcript of user and assistant text. System/developer instructions, hidden reasoning, tool calls/results, and image payloads are excluded.
3. It starts a native session in the new backend and prepends that transcript to the first new user message. The new session ID then replaces the old mapping normally.

If the local JSONL is missing or unreadable, ccdb safely degrades to a fresh session and logs the missing handoff. Records written before ccdb tracked backend ownership keep the legacy resume behaviour because their source backend cannot be identified reliably.

Visual cues so you never forget which one you're talking to:

- **Claude sessions** open with a blurple embed titled "🤖 Claude Code session started".
- **Codex sessions** open with an OpenAI-teal embed titled "🌀 OpenAI Codex session started".
- The completion embed prepends a `🧠 Claude · sonnet` / `🧠 Codex · gpt-5.6-sol` chip alongside the usual duration / cost / token / context metrics. (When a backend's model is left at the CLI default, the chip shows just the backend name.)

Concrete example:

```text
/backend codex                        # global → codex (next new sessions use codex)
/model gpt-5-codex                    # global → codex uses gpt-5-codex
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

---

## Features

### Interactive Chat

#### 🔗 Session Basics
- **Chat-only mode** — When `CHAT_ONLY_CHANNEL_IDS` includes a channel, only Claude's text responses are shown; tool embeds, thinking blocks, session start/complete embeds, and todo lists are hidden. Permission requests and `AskUserQuestion` are always shown. Ideal for public channels where non-technical users are watching.
- **Thread = Session** — 1:1 mapping between Discord thread and Claude Code session
- **Threads stay visible for a week** — every thread ccdb creates asks Discord for its maximum auto-archive window (7 days), so a conversation you are still working on keeps its place in the channel's thread list instead of dropping out of the sidebar an hour after the last reply
- **Goal tracking** — `/goal <condition>` sets a completion condition; Claude keeps working until the condition is met. Omit the condition to check status; pass `clear` to cancel
- **Session persistence** — Resume conversations across messages via `--resume`
- **Cross-backend conversation handoff** — Switching a live thread between Claude and Codex seeds the new native session from a bounded, text-only reading of the previous backend's local JSONL; no manual summary or copy/paste required
- **Automatic Codex resume recovery** — If a resumed Codex session repeatedly loses its WebSocket before producing output, ccdb starts a replacement session with a bounded, text-only transcript of the prior conversation; image and tool payloads are excluded
- **Concurrent sessions** — Multiple parallel sessions with configurable limit
- **Stop without clearing** — `/stop` halts a session while preserving it for resume
- **Session interrupt** — Sending a new message to an active thread sends SIGINT to the running session and starts fresh with the new instruction; no manual `/stop` needed
- **Auto-rename threads** — When `THREAD_AUTO_RENAME=true`, each new thread is automatically renamed with a Claude-generated title derived from the first message (background task, never delays session start)

#### 📡 Real-time Feedback
- **Real-time status** — Emoji reactions: 🧠 thinking, 🛠️ reading files, 💻 editing, 🌐 web search
- **Streaming text** — Intermediate assistant text appears as Claude works
- **Tool result embeds** — Live tool call results with elapsed time shown immediately (0s) and ticking up every 5s; single-line outputs shown inline, multi-line outputs collapsed behind an expand button
- **Extended thinking** — Reasoning shown as spoiler-tagged embeds (click to reveal)
- **Thread dashboard** — Live pinned embed showing which threads are active vs. waiting; owner @-mentioned when input is needed

#### 🤝 Human-in-the-Loop
- **Interactive questions** — `AskUserQuestion` renders as Discord Buttons or Select Menu; session resumes with your answer; buttons survive bot restarts; requester is @mentioned when input is needed
- **Plan Mode** — When Claude calls `ExitPlanMode`, a Discord embed shows the full plan with Approve/Cancel buttons; Claude proceeds only after approval; requester @mentioned on prompt; auto-cancel on 5-minute timeout
- **Tool permission requests** — When Claude needs permission to execute a tool, Discord shows Allow/Deny buttons with the tool name and input; requester @mentioned; auto-deny after 2 minutes
- **MCP Elicitation** — MCP servers can request user input via Discord (form-mode: up to 5 Modal fields from JSON schema; url-mode: URL button + Done confirmation); requester @mentioned; 5-minute timeout
- **Live TodoWrite progress** — When Claude calls `TodoWrite`, a single Discord embed is posted and edited in-place on each update; shows ✅ completed, 🔄 active (with `activeForm` label), ⬜ pending items

#### 📊 Observability
- **Token usage** — Cache hit rate and token counts shown in session-complete embed
- **Context usage** — Context window percentage (input + cache tokens, excluding output) and remaining capacity until auto-compact shown in session-complete embed; ⚠️ warning when above 83.5%
- **Compact detection** — Notifies in-thread when context compaction occurs (trigger type + token count before compact)
- **Hard stall notification** — Thread message after no activity (extended thinking or context compression); resets automatically when Claude resumes. Thresholds are model-aware: 30 s for standard models, 120 s for Opus (which has longer thinking pauses)
- **Timeout notifications** — Embed with elapsed time and resume guidance on timeout
- **StatusLine display** — When Claude configures a `statusLine` (via `/statusline-setup`), the current status is shown in Discord after each session as a concise, always-visible indicator
- **API provider indicator** — After each session, a `🔗 API: <provider>` line shows which endpoint the CLI is actually using (`Anthropic API (direct)`, `AWS Bedrock`, `Google Vertex AI`, `Azure AI Foundry`, or a custom base URL), derived from the real subprocess environment so CLI env overlays are reflected. Always shown — even without a configured `statusLine`.
- **Thread inbox** — When `THREAD_INBOX_ENABLED=true`, the dashboard shows a persistent 📬 inbox section: after each session ends, Claude classifies the final message (`waiting` / `done` / `ambiguous`) via a lightweight `claude -p` call; threads awaiting your reply survive bot restarts and are surfaced until you respond

#### 🔌 Input & Skills
- **Attachment support** — Text files auto-appended to prompt (up to 5 files, 200 KB each / 500 KB total; oversized files are truncated with a notice rather than skipped); images sent as Discord CDN URLs via `--input-format stream-json` (up to 4 × 5 MB); long pasted messages that Discord auto-converts to file attachments (without `content_type`) are handled via extension-based detection
- **On-demand file delivery** — Ask Claude to "send me" or "attach" a file and it writes the path to `.ccdb-attachments`; the bot reads it and delivers the file as a Discord attachment when the session completes. Local instructions can also require substantial written deliverables to be saved as Markdown and attached.
- **Skill execution** — `/skill` command with autocomplete, optional args, in-thread resume; skills from installed plugins are also auto-discovered
- **Hot reload** — New skills added to `~/.claude/skills/` are picked up automatically (60s refresh, no restart)

### Concurrency & Coordination
- **Worktree instructions auto-injected** — Every session prompted to use `git worktree` before touching any file
- **Automatic worktree cleanup** — Session worktrees (`wt-{thread_id}`) are removed automatically at session end and on bot startup; dirty worktrees are never auto-removed (safety invariant)
- **Active session registry** — In-memory registry; each session sees what the others are doing
- **AI Lounge** — Shared "breakroom" channel; context injected as backend-specific system/developer instructions (ephemeral, never accumulates in history) so long sessions never hit "Prompt is too long"; sessions post intentions, read each other's status, and check before disruptive operations; humans see it as a live activity feed
- **Cross-session observability** — `GET /api/sessions` lists every session (live and stored) with its state, working dir and latest lounge note; `GET /api/threads/{thread_id}/messages` reads another thread's conversation. Read-only, so a session can look before it edits — including at sessions that never posted to the lounge
- **Resource claims** — `POST /api/claims` reserves a repo, issue or file before work starts; a second session asking for the same resource gets 409 with the holder's thread, note and live state. Advisory and TTL-bound (default 2h, max 24h), so a dead session cannot pin a resource forever
- **Session-to-session relay** — `POST /api/threads/{thread_id}/message` lets one session speak to another when they have already collided; `queue` waits for the receiver's turn, `interrupt` SIGINTs it. Every relay is posted into the thread (never a back channel), wrapped in a marker so it is not mistaken for the human, and bounded by hop/cooldown/rate limits so two sessions cannot loop
- **Automatic collision detection** — `CollisionWatchCog` compares the files live sessions actually wrote (recorded from `Write`/`Edit`/`MultiEdit`/`NotebookEdit`) once a minute; two sessions writing the same file within 15 minutes are announced in the AI Lounge and in both threads. Catches the overlaps nobody announced; one alert per pair per 30 minutes, and it never interrupts a running turn
- **Coordination channel** — `COORDINATION_CHANNEL_ID` env var is used as the default fallback for the AI Lounge channel (no separate bot-side lifecycle events)

### Scheduled Tasks
- **SchedulerCog** — SQLite-backed periodic task executor with a 30-second master loop
- **Self-registration** — Claude registers tasks via `POST /api/tasks` during a chat session
- **No code changes** — Add, remove, or modify tasks at runtime
- **Enable/disable** — Pause tasks without deleting them (`PATCH /api/tasks/{id}`)

### CI/CD Automation
- **Webhook triggers** — Trigger Claude Code tasks from GitHub Actions or any CI/CD system
- **Auto-upgrade** — Automatically update the bot when upstream packages are released
- **DrainAware restart** — Waits for active sessions to finish before restarting
- **Auto-resume marking** — Active sessions are automatically marked for resume on any shutdown (upgrade restart via `AutoUpgradeCog`, or any other shutdown via `ClaudeChatCog.cog_unload()`); on restart Claude reports its previous state and re-confirms with the user before resuming any implementation work
- **Restart approval** — Optional gate to confirm upgrades; approve via ✅ reaction in the upgrade thread or via button posted to the parent channel; the button re-posts itself at the bottom as new messages arrive so it stays visible
- **Manual upgrade trigger** — `/upgrade` slash command lets authorised users trigger the upgrade pipeline directly from Discord (opt-in via `slash_command_enabled=True`)

### Session Management
- **Built-in help** — `/help` shows all available slash commands and basic usage (ephemeral, only visible to the caller)
- **Session sync** — Import CLI sessions as Discord threads (`/sync-sessions`); `/sync-settings` to view or change sync preferences (thread style, time window, minimum results)
- **Session list** — `/sessions` with filtering by origin (Discord / CLI / all) and time window
- **Thread search** — `/search <query>` finds a past thread by keyword, matching the persistent per-thread summary (the opening prompt) and working directory; renders hits as a scannable embed with a Discord deep-link that reopens even an archived (sidebar-hidden) thread; optional `origin` filter (Discord / CLI). Add `body:True` to also grep the full local Claude transcripts (`~/.claude/projects`), so keywords that appear only mid-conversation are found too — each body hit shows the matching snippet with a `💬` badge, and a transcript with no Discord thread offers a `claude --resume <id>` hint instead of a link. The same lookup is exposed as `GET /api/search` (add `body=1`) for other sessions and skills. No AI tokens — a `LIKE` query over data ccdb already keeps, plus a safe `grep` (never `shell=True`) over the transcripts on disk
- **Session resume** — `/resume` shows a select menu of recent sessions (up to 25) and resumes the selected one in a new thread; optional `query` parameter for keyword search (matches summary and working directory); optional `filter=orphaned` to show only sessions from deleted threads; works from any channel or thread — always creates a new thread in the configured main channel
- **Resume info** — `/resume-info` shows the CLI command to continue the current session in a terminal (thread-only)
- **Clear session** — `/clear` resets the Claude Code session for the current conversation (thread or inline-reply channel), starting fresh without creating a new thread
- **Startup resume** — Interrupted sessions restart automatically after any bot reboot; `AutoUpgradeCog` (upgrade restarts) and `ClaudeChatCog.cog_unload()` (all other shutdowns) mark them automatically, or use `POST /api/mark-resume` manually
- **Programmatic spawn** — `POST /api/spawn` creates a new Discord thread + Claude session from any script or Claude subprocess; returns non-blocking 201 immediately after thread creation
- **Thread ID injection** — `DISCORD_THREAD_ID` env var is passed to every Claude subprocess, enabling sessions to spawn child sessions via `$CCDB_API_URL/api/spawn`
- **StatusLine display** — If your Claude Code `settings.json` has a `statusLine` configured, its output is shown in Discord after each session response
- **Worktree management** — `/worktree-list` shows all active session worktrees with clean/dirty status; `/worktree-cleanup` removes orphaned clean worktrees (supports `dry_run` preview)
- **Runtime model switching** — `/model-show` displays the current global model and per-thread session model; `/model-set` changes the model for all new sessions without restart
- **Runtime tool permissions** — `/tools-show` displays the current allowed tools; `/tools-set` opens a select menu to toggle tools on/off; `/tools-reset` reverts to `.env` default — all without restart
- **Context usage** — `/context` shows context window percentage with a visual progress bar; ⚠️ warning when nearing the 83.5% autocompact threshold; ephemeral (only visible to the caller)
- **Rate limit usage** — `/usage` shows Claude API rate limit utilization with percentage bar and time-until-reset countdown for 5-hour and 7-day windows; ⚠️ flag when utilization ≥ 80%
- **Conversation rewind** — `/rewind` shows a select menu of past user turns and truncates the session JSONL at the chosen point, removing that message and everything after it so the session resumes from the exact state before that turn; keeps all working files Claude created; useful when a session has gone off-track
- **Conversation fork** — `/fork` branches the current thread into a new thread that continues from the same session state via `--fork-session`, creating a truly independent session copy; lets you explore a different direction without affecting the original

### Security
- **No shell injection** — `asyncio.create_subprocess_exec` only, never `shell=True`
- **Session ID validation** — Strict regex before passing to `--resume`
- **Flag injection prevention** — `--` separator before all prompts
- **Secret isolation** — Bot token stripped from subprocess environment
- **User authorization** — `allowed_user_ids` restricts who can invoke Claude
- **Log injection prevention** — User-provided API values are sanitized (newlines stripped) before writing to logs
- **Credential files stay untracked** — `.gitignore` covers `.env.*`, not just `.env`, because operators leave dated backups (`.env.bak-…`) beside the real file and each one holds a live bot token; `.env.example` is re-included explicitly so the template stays tracked
- **Local-model backend** (optional) — `/backend local` runs a thread against a model on your own hardware. ccdb owns a separate CLI home with the update check and analytics disabled, because a "local" run otherwise still contacts the vendor; it refuses to start if those settings are missing — see [docs/local-backend.md](docs/local-backend.md. `/ollama` manages that runtime from Discord, and the model in use is whatever was selected there — there is no environment variable that can silently disagree with it)
- **Remote AG-UI backend** (optional) — `/backend agui` connects the existing Discord/Teams session machinery to any HTTP/SSE AG-UI agent while preserving ccdb's session ledger, rendering, cancellation, and operational controls — see [docs/agui-backend.md](docs/agui-backend.md)
- **`/ask` — explicit escalation** (optional) — sends one anonymized, self-contained question to a strong external model with no project context, no files and no tools, then restores your real names in the answer; tools are an empty *allow* list (`--tools ""`) rather than a deny list that has to be rewritten for every tool the CLI adds, and the isolation is verified before every spawn. A local judge first checks that replacement did not hide the very subject of the question — asking for "the pros and cons of `org-002`" is perfectly anonymized and unanswerable — and withholds it if so (`force: true` overrides) — see [docs/escalation.md](docs/escalation.md)
- **Anonymization gateway** (optional) — Replaces organisation-identifying terms with stable aliases before the prompt reaches Claude or Codex, and restores them in the answer. A local model checks the result for replacement misses and, by default, blocks the send when it finds one; `CCDB_ANONYMIZE_POLICY=adopt` instead mints an alias for the reported term into the mapping table and sends, so a new customer name does not stop the command until someone hand-edits the rules file. Off until you write a rules file — see [docs/anonymization.md](docs/anonymization.md)

---

## Quick Start — Claude or Codex in Discord in 5 Minutes

**Prerequisites:**

- Python 3.12+
- At least one of:
  - [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) — installed and authenticated (`claude login`). Recommended for Anthropic Pro/Max subscribers.
  - [OpenAI Codex CLI](https://github.com/openai/codex) — `npm install -g @openai/codex` then `codex login`. Uses your existing ChatGPT Plus/Pro/Business subscription.
- You can install both. Switch between them at runtime with `/backend` (see [Backend Switching](#backend-switching--claude--codex-on-demand)).

**Platform support:** Primarily developed and tested on **Linux**. macOS and Windows are supported and pass CI, but receive less real-world testing — bug reports welcome.

### Step 1 — Create a Discord Bot (one-time, ~2 minutes)

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications) → **New Application**
2. Navigate to **Bot** → enable **Message Content Intent** under Privileged Gateway Intents
3. Copy the bot **Token**
4. Go to **OAuth2 → URL Generator**: Scopes `bot` + `applications.commands`, Permissions: Send Messages, Create Public Threads, Send Messages in Threads, Add Reactions, Manage Messages, Read Message History
5. Open the generated URL → invite the bot to your server

### Step 2 — Run the Setup Wizard

No cloning or `.env` editing required — the wizard does it for you:

```bash
# With uvx (no install needed):
uvx --from "git+https://github.com/ebibibi/ebi-agent-chat-relay.git" ccdb setup

# Or after cloning:
git clone https://github.com/ebibibi/ebi-agent-chat-relay.git
cd ebi-agent-chat-relay
uv run ccdb setup
```

The wizard will:
1. Validate your bot token against the Discord API
2. **Automatically list available channels** — just pick a number (no ID copying)
3. Ask for your working directory and model preference
4. Write `.env` and offer to start the bot immediately

```
╔══════════════════════════════════════════════════════╗
║          ccdb setup — interactive wizard             ║
╚══════════════════════════════════════════════════════╝

Step 1 — Claude Code CLI
  ✅  claude found

Step 2 — Discord Bot Token
  Bot Token: [paste here]
  Validating token… ✅  Logged in as MyBot#1234

Step 3 — Discord Channel ID
  Fetching channels via Discord API… ✅  Found 5 text channel(s)

   1. #general        (My Server)
   2. #claude-code    (My Server)
   3. #dev            (My Server)
   ...

  Select channel [1-5]: 2
  ✅  #claude-code (123456789012345678)

  ...

  ✅  Written: .env
  Start the bot now? [Y/n]: y
```

### Start / Stop

```bash
ccdb start    # start the bot (reads .env in current dir)
ccdb start --env /path/to/.env   # custom .env location
```

Send a message in the configured channel — Claude will reply in a new thread.

### Running as a systemd Service (Production)

For production deployments, run the bot under systemd so it starts on boot and auto-restarts on failure.

The repo ships a ready-to-adapt template (`discord-bot.service`) and a pre-start script (`scripts/pre-start.sh`). Copy and customize them:

```bash
# 1. Edit the service file — replace /home/ebi and User=ebi with your paths/user
sudo cp discord-bot.service /etc/systemd/system/mybot.service
sudo nano /etc/systemd/system/mybot.service

# 2. Enable and start
sudo systemctl daemon-reload
sudo systemctl enable mybot.service
sudo systemctl start mybot.service

# 3. Check status
sudo systemctl status mybot.service
journalctl -u mybot.service -f
```

**What `scripts/pre-start.sh` does** (runs as `ExecStartPre` before the bot process):

1. **`git pull --ff-only`** — pulls the latest code from `origin main`
2. **`uv sync`** — keeps dependencies in sync with `uv.lock`
3. **Import validation** — verifies that `claude_discord.main` imports cleanly
4. **Auto-rollback** — if import fails, reverts to the previous commit and retries; posts a Discord webhook notification on failure or success
5. **Worktree cleanup** — removes stale git worktrees left by crashed sessions

The script detects the repository root dynamically (via `readlink -f` on `$0`), so it works for any user regardless of where they cloned the repo — no path editing needed in the script itself. It also auto-discovers the `uv` binary from `PATH`; override via `CCDB_UV_BIN` env var if needed.

The script requires the `DISCORD_WEBHOOK_URL` variable in `.env` for failure notifications (optional — the script works without it).

#### Toolchain PATH — set it in `.env`

systemd starts a unit with a minimal default `PATH` (typically `/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin`) and never sources `~/.bashrc` or `~/.profile`. The bot inherits that `PATH`, and so does every Claude/Codex session it spawns — sessions run with the bot's environment minus the stripped secrets.

The result is confusing: a build that works in your terminal fails inside a Discord session, or silently runs against an older system-wide binary, because tools installed under `~/.local/bin` or `~/.npm-global/bin` are invisible to the service.

Since the service loads `.env` via `EnvironmentFile=`, setting `PATH` there fixes the bot and every session at once:

```bash
# .env — match your interactive shell's PATH
PATH=/home/you/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin
```

Restart the service (`sudo systemctl restart mybot.service`), then confirm from a Discord session by asking Claude to run `which node && node --version`.

### Custom Cogs (Extend Without Forking)

Add your own features by dropping Python files into a directory — no fork, no subclass, no package needed:

```bash
ccdb start --cogs-dir ./my-cogs/
# Or: CUSTOM_COGS_DIR=./my-cogs ccdb start
```

Each `.py` file in the directory must expose an `async def setup(bot, runner, components)`:

```python
from discord.ext import commands

class GreeterCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_member_join(self, member):
        channel = self.bot.get_channel(self.bot.channel_id)
        await channel.send(f"Welcome {member.mention}!")

async def setup(bot, runner, components):
    await bot.add_cog(GreeterCog(bot))
```

Files prefixed with `_` are skipped. If one Cog fails to load, others still load normally.

See [`examples/ebibot/`](examples/ebibot/) for a full real-world example with reminders, Todoist watchdog, auto-upgrade, and docs sync.

**Built-in examples in `examples/ebibot/cogs/`:**

| Cog | Purpose |
|-----|---------|
| `ReminderCog` | Discord-based reminder scheduling |
| `WatchdogCog` | Todoist / external service watchdog |
| `AutoUpgradeCog` | Webhook-triggered package upgrade |
| `DocsSyncCog` | Automated documentation sync on push |
| `AlertResponderCog` | Generic alert monitoring — forwards alerts from monitoring systems to Discord and triggers a Claude Code investigation session |
| `JobFailureTriageCog` | Auto-investigates scheduler job failures posted as webhook embeds |
| `ThreadCompletionCog` | Treats "the user deleted the thread" as "that work is finished" — batches deletions and files a work record from the surviving transcripts. Off until `/thread-completion on` |

---

### Minimal Bot (Install as a Package)

If you already have a discord.py bot, add ccdb as a package instead:

```bash
uv add git+https://github.com/ebibibi/ebi-agent-chat-relay.git
```

Create a `bot.py`:

```python
import asyncio
import os
from dotenv import load_dotenv
import discord
from discord.ext import commands
from claude_discord import ClaudeRunner, setup_bridge

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
runner = ClaudeRunner(
    command="claude",
    model="sonnet",
    working_dir="/path/to/your/project",
)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    await setup_bridge(
        bot,
        runner,
        claude_channel_id=int(os.environ["DISCORD_CHANNEL_ID"]),
        allowed_user_ids={int(os.environ["DISCORD_OWNER_ID"])},
    )

asyncio.run(bot.start(os.environ["DISCORD_BOT_TOKEN"]))
```

`setup_bridge()` wires all Cogs automatically. Update to the latest version:

```bash
uv lock --upgrade-package claude-code-discord-bridge && uv sync
```

#### Multi-Channel Setup

To deploy the bot across multiple Discord channels, pass `claude_channel_ids` in addition to (or instead of) `claude_channel_id`:

```python
await setup_bridge(
    bot,
    runner,
    claude_channel_id=int(os.environ["DISCORD_CHANNEL_ID"]),   # primary (fallback for thread creation)
    claude_channel_ids={
        int(os.environ["DISCORD_CHANNEL_ID"]),
        int(os.environ["DISCORD_CHANNEL_ID_2"]),
    },
    allowed_user_ids={int(os.environ["DISCORD_OWNER_ID"])},
)
```

Each channel is fully independent — messages in any of the configured channels spawn a new Claude session thread, and `/skill` commands work across all of them.  `claude_channel_id` is kept for backward compatibility and is used as the fallback thread-creation target when the `/skill` command is invoked outside a configured channel.

#### Where the Bot Listens — No-Mention Channels vs. @Mentions

`claude_channel_ids` is a list of channels that need **no @mention**: everything posted there, and in any thread under them, goes straight to Claude. Everywhere else in the guild the bot answers **only when someone @mentions it**.

So the configuration describes where ccdb speaks *freely*, not where it exists. A channel nobody thought about is quiet by construction, and the bot is still reachable by name from anywhere without a config change.

```python
await setup_bridge(
    bot,
    runner,
    claude_channel_ids={111},          # #111: no mention needed, every message runs Claude
    allowed_user_ids={int(os.environ["DISCORD_OWNER_ID"])},
)
# Anywhere else in the guild: "@YourBot what do you think?" starts a session; silence otherwise.
```

Claude engages when either of these holds:

- the message is in a **no-mention channel** (or a thread under it) — this is the session flow: a channel message opens a thread, replies in that thread continue its session, or
- the bot is **@mentioned in that message** — *every* message, everywhere else, including in threads ccdb opened itself. Owning a thread is not standing consent: people keep talking to each other in those threads, and a run nobody asked for is noise.

**A mention is answered in place.** No thread is created: a mention in a channel is answered in that channel, a mention in a thread is answered in that thread. Spinning off a thread would move the answer away from the discussion that prompted it and leave a session running where nobody is reading. If a session already belongs to that channel or thread it is resumed, so a follow-up mention continues the same work.

Direct messages are never picked up by the mention path, and the per-user gate (`allowed_user_ids`) still applies first everywhere.

To restore the old strict behaviour — the bot exists *only* in the configured channels and a mention elsewhere does nothing — turn the mention path off:

```
CCDB_MENTION_ANYWHERE=false
```

##### Reading the room before answering

A mention usually lands in the middle of a conversation Claude has not seen. Before answering, ccdb prepends a transcript of the **last 7 days of that exact channel or thread** so the reply is about what was actually being discussed. Reading the whole thread would be the obvious move and the wrong one — a months-old thread is a large, mostly irrelevant token bill — so the window is bounded three ways: by age, by message count (200), and by total size (12,000 characters, trimmed from the oldest end so the turns being replied to always survive).

```
CCDB_THREAD_CONTEXT_DAYS=7   # 0 disables the transcript entirely
```

Inside the no-mention channels the transcript is only used for threads ccdb did not create — in its own session threads it saw every turn already, so re-sending them would just burn tokens.

##### Mention-only channels (legacy)

`mention_only_channel_ids` carves a channel back out of the no-mention set. With the mention path on, simply *not listing* a channel has the same effect, so this is only useful when a parent channel is listed and one child should be excluded.

```
MENTION_ONLY_CHANNEL_IDS=222,333
```

#### Inline-Reply Channels

To make the bot respond **directly in the channel** (without creating a thread) for specific channels (useful for personal command channels where threads add unnecessary clutter):

```python
await setup_bridge(
    bot,
    runner,
    claude_channel_ids={111, 333},
    inline_reply_channel_ids={333},  # bot replies inline in #333, no thread created
    allowed_user_ids={int(os.environ["DISCORD_OWNER_ID"])},
)
```

Or via environment variable (comma-separated channel IDs):

```
INLINE_REPLY_CHANNEL_IDS=333,444
```

In inline-reply mode, Claude's response is sent directly as a message in the channel rather than spawning a new thread. The channel itself is one continuous conversation: sessions are tracked internally, so follow-up messages resume the same Claude session until `/clear` resets it.

#### Chat-Only Channels

To hide technical UI (tool embeds, thinking blocks, session start/complete notices, todo lists) and show **only Claude's text responses** in specific channels — useful for public-facing channels where non-technical users are watching:

```python
await setup_bridge(
    bot,
    runner,
    claude_channel_ids={111, 444},
    chat_only_channel_ids={444},  # only text shown in #444; tool details hidden
    allowed_user_ids={int(os.environ["DISCORD_OWNER_ID"])},
)
```

Or via environment variable (comma-separated channel IDs):

```
CHAT_ONLY_CHANNEL_IDS=444,555
```

In chat-only mode, permission requests and `AskUserQuestion` prompts are **always shown** regardless of the setting — they require human input and must be visible.

---

## Configuration

| Variable | Description | Default |
|----------|-------------|---------|
| `DISCORD_BOT_TOKEN` | Your Discord bot token | (required) |
| `DISCORD_CHANNEL_ID` | Channel ID for Claude chat | (required) |
| `CCDB_BACKEND` | Backend to use: `claude`, `codex`, `local`, or `agui` | `claude` |
| `CCDB_COMMAND` | Path or name of the CLI binary (overrides `CLAUDE_COMMAND`). Used by the initial runner picked from `CCDB_BACKEND`; superseded by the two per-backend variables below when `/backend` switches at runtime. | _(auto: `claude` or `codex`)_ |
| `CCDB_CLAUDE_COMMAND` | Explicit path to the Claude CLI binary. Used by `BackendFactory` whenever `/backend claude` is active, regardless of the initial `CCDB_BACKEND`. Falls back to `CLAUDE_COMMAND`, then `claude` (PATH). | (optional) |
| `CCDB_CODEX_COMMAND` | Explicit path to the OpenAI Codex CLI binary. Required when running the bot under systemd (default service PATH does not include `~/.npm-global/bin`). Falls back to `codex` (PATH). | (optional) |
| `CCDB_CODEX_SANDBOX_OVERRIDE` | Optional deployment-wide Codex `--sandbox` override: `read-only`, `workspace-write`, or `danger-full-access`. Leave unset to use the Codex CLI default. Use `danger-full-access` only when the host's outer isolation is trusted and OS namespace restrictions prevent Codex's own sandbox from starting. This setting is intentionally not exposed per thread. | (optional) |
| `CCDB_AGUI_URL` | Exact HTTP(S) run endpoint for `/backend agui`. Redirects are rejected. | (required for `agui`) |
| `CCDB_AGUI_TOKEN` | Optional bearer token for the AG-UI endpoint. Stripped from Claude/Codex subprocess environments. | (optional) |
| `PATH` | Binary search path for the bot **and every CLI session it spawns** — sessions inherit the bot's environment. Set it in `.env` when running under systemd, which starts units with a minimal PATH and never reads `~/.bashrc` / `~/.profile`. See [Toolchain PATH](#toolchain-path--set-it-in-env). | (inherited from the parent process) |
| `CCDB_MODEL` | Model to use (overrides `CLAUDE_MODEL`) | `sonnet` |
| `CCDB_MODEL_DISCOVERY` | Set to `0` to stop the `/model` autocomplete from asking the Anthropic models endpoint which models your credentials can see, and always use the static suggestion list instead. Discovery is read-only, reuses the Claude Code CLI's own auth, and already falls back on its own when offline, unauthenticated, or on Bedrock/Vertex/Foundry | `1` |
| `CCDB_PERMISSION_MODE` | Permission mode for CLI (overrides `CLAUDE_PERMISSION_MODE`) | `acceptEdits` |
| `CCDB_DANGEROUSLY_SKIP_PERMISSIONS` | Skip all permission checks — overrides `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS` | `false` |
| `CCDB_WORKING_DIR` | Working directory for CLI (overrides `CLAUDE_WORKING_DIR`) | current dir |
| `CCDB_ALLOWED_TOOLS` | Comma-separated list of allowed tools (overrides `CLAUDE_ALLOWED_TOOLS`) | (optional) |
| `CCDB_CHANNEL_IDS` | Additional channel IDs, comma-separated (overrides `CLAUDE_CHANNEL_IDS`) | (optional) |
| `CLAUDE_COMMAND` | Path or name of the Claude CLI binary (legacy name — prefer `CCDB_COMMAND`). Use to pin a specific version (e.g. `CLAUDE_COMMAND=/usr/local/lib/node_modules/@anthropic-ai/claude-code@2.1.77/cli.js`) — useful to avoid regressions in newer CLI releases. | `claude` |
| `CLAUDE_MODEL` | Model to use (legacy — prefer `CCDB_MODEL`) | `sonnet` |
| `CLAUDE_PERMISSION_MODE` | Permission mode for CLI (legacy — prefer `CCDB_PERMISSION_MODE`) | `acceptEdits` |
| `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS` | Skip all permission checks (legacy — prefer `CCDB_DANGEROUSLY_SKIP_PERMISSIONS`) | `false` |
| `CLAUDE_WORKING_DIR` | Working directory for Claude (legacy — prefer `CCDB_WORKING_DIR`) | current dir |
| `MAX_CONCURRENT_SESSIONS` | Max parallel Claude CLI sessions across all code paths (chat, skills, scheduler, webhooks) | `3` |
| `SESSION_TIMEOUT_SECONDS` | Session inactivity timeout | `300` |
| `CCDB_PR_COMPLETION_OWNER` | GitHub owner whose non-draft `session/<thread_id>` PRs trigger one automatic completion continuation. Requires authenticated `gh`; disabled when empty. | (optional) |
| `DISCORD_OWNER_ID` | User ID to @-mention when Claude needs input | (optional) |
| `COORDINATION_CHANNEL_ID` | Channel ID used as default fallback for AI Lounge channel | (optional) |
| `CCDB_MENTION_ANYWHERE` | When true, an @mention summons Claude in any guild channel or thread; set `false` to listen only in the configured channels | `true` |
| `CCDB_THREAD_CONTEXT_DAYS` | Days of the surrounding channel or thread's history prepended to the prompt when a mention wakes Claude there (`0` disables) | `7` |
| `MENTION_ONLY_CHANNEL_IDS` | Comma-separated channel IDs carved back out of the no-mention set (legacy; not listing a channel now has the same effect) | (optional) |
| `INLINE_REPLY_CHANNEL_IDS` | Comma-separated channel IDs where the bot replies inline (no thread created) | (optional) |
| `CHAT_ONLY_CHANNEL_IDS` | Comma-separated channel IDs in chat-only mode — only Claude's text responses are shown; all technical embeds (tools, thinking, session info, todos) are hidden | (optional) |
| `WORKTREE_BASE_DIR` | Base directory to scan for session worktrees (enables automatic cleanup) | (optional) |
| `CLI_SESSIONS_PATH` | Path to `~/.claude/projects` for CLI session discovery (enables `/sync-sessions`) and transcript body search (`/search body:True`, `GET /api/search?body=1`). Defaults to the standard `~/.claude/projects`, so body search stays Zero-Config wherever Claude Code has run | (optional) |
| `CUSTOM_COGS_DIR` | Directory containing custom Cog files to load at startup (see [Custom Cogs](#custom-cogs-extend-without-forking)) | (optional) |
| `CLAUDE_ALLOWED_TOOLS` | Comma-separated list of allowed tools for Claude CLI (legacy — prefer `CCDB_ALLOWED_TOOLS`) | (optional) |
| `CLAUDE_CHANNEL_IDS` | Additional channel IDs (comma-separated) for multi-channel setup (legacy — prefer `CCDB_CHANNEL_IDS`) | (optional) |
| `THREAD_INBOX_ENABLED` | Enable the persistent thread inbox (classifies sessions as `waiting`/`done`/`ambiguous` via `claude -p`; shown in thread dashboard) | `false` |
| `THREAD_AUTO_RENAME` | Auto-rename new thread titles using Claude AI — generates a short, descriptive title from the first user message via a background `claude -p` call (never delays session start) | `false` |
| `CCDB_CLI_ENV_FILE` | Path to a `KEY=VALUE` file whose variables are merged into the CLI subprocess environment on every invocation. Changes take effect immediately without restarting the bot. Useful for temporary API routing (e.g., Azure Foundry) | (optional) |
| `CCDB_LOG_FILE` | Path to a log file. When set, a rotating file handler (10 MB × 5 backups) is added alongside the default stdout handler. Useful for monitoring and alerting. | (optional) |
| `API_HOST` | REST API bind address | `127.0.0.1` |
| `API_PORT` | REST API port (enables REST API when set) | (optional) |
| `CCDB_INGEST_TOKEN` | Bearer token for `POST /api/ingest` (independent of `api_secret`); unset ⇒ the endpoint responds `503` | (optional) |
| `CCDB_INGEST_REQUIRE_COMPLETE` | Set to `1` to reject an ingest with `409` when its `attachments_manifest` proves attachments went missing, instead of starting a session on partial evidence | `0` |
| `CCDB_TEAMS_VAULT_ROOT` | Directory where `POST /api/teams/sync` mirrors upstream threads (one file per message). Gated by `CCDB_INGEST_TOKEN` | `{working_dir}/teams` |

### Permission Modes — What Works in `-p` Mode

Claude Code CLI runs in **`-p` (non-interactive) mode** when used through ccdb. In this mode, the CLI **cannot prompt for permission** — tools that require approval are immediately rejected. This is a [CLI design constraint](https://code.claude.com/docs/en/headless), not a ccdb limitation.

| Mode | Behavior in `-p` mode | Recommendation |
|------|----------------------|----------------|
| `default` | ❌ **All tools rejected** — unusable | Do not use |
| `acceptEdits` | ⚠️ Edit/Write auto-approved, Bash rejected (Claude falls back to Write for file ops) | Minimum viable option |
| `bypassPermissions` | ✅ All tools approved | Works, but prefer the flag below |
| **`auto`** | ✅ **AI-classified safety** — safe operations auto-approved, dangerous operations blocked | **Recommended** — best balance of safety and usability |
| `plan` | ✅ AI-classified (read-only bias) — similar to auto but more conservative | For read-heavy workflows |
| **`CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS=true`** | ✅ **All tools approved, no safety checks** | Legacy "yolo" mode — use when auto mode is too restrictive |

**Our recommendation:** Set `CLAUDE_PERMISSION_MODE=auto`. Auto mode uses an AI classifier to automatically approve safe operations (file edits, local testing, git push to working branch) while blocking dangerous ones (force push, production deploys, credential leakage). This gives Claude full autonomy for normal development work without the "anything goes" risk of yolo mode.

**Fallback to yolo mode:** If auto mode blocks operations you need, set `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS=true` instead. Since ccdb controls who can interact with Claude via `allowed_user_ids`, the CLI-level permission checks add friction without meaningful security benefit. The "dangerously" in the name reflects the CLI's general-purpose warning; in the ccdb context where access is already gated, it's a practical choice.

> **Note:** When `CLAUDE_PERMISSION_MODE` is set to `auto` or `plan`, `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS` is automatically ignored — these modes have their own safety classifiers that would be overridden by the yolo flag.

**For fine-grained control**, use `CLAUDE_ALLOWED_TOOLS` to allow specific tools without fully bypassing permissions:

```env
# Example: allow file operations and code execution, but not web access
CLAUDE_ALLOWED_TOOLS=Bash,Read,Write,Edit,Glob,Grep

# Example: read-only mode — Claude can explore but not modify
CLAUDE_ALLOWED_TOOLS=Read,Glob,Grep
```

Common tool names: `Bash`, `Read`, `Write`, `Edit`, `Glob`, `Grep`, `WebFetch`, `WebSearch`, `NotebookEdit`. Set `CLAUDE_PERMISSION_MODE=default` when using this (other modes may override).

**Runtime changes via Discord:** Use `/tools-set` to change allowed tools at runtime without restarting the bot. The setting is persisted and takes effect for all new sessions immediately. Use `/tools-show` to see the current configuration, or `/tools-reset` to revert to the `.env` default.

> **Permission buttons in Discord:** When `CLAUDE_PERMISSION_MODE=default`, Claude emits `permission_request` events and ccdb displays Allow/Deny buttons in the thread. stdin is always kept open (stream-json input mode) so the bot can send responses back to Claude. If you are using `auto` or `plan` mode, Claude handles permissions automatically without requiring user interaction. When `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS=true` (yolo mode), ccdb **auto-approves** any `permission_request` events immediately — no Allow/Deny buttons are shown. This is a workaround for a CLI regression (v2.1.78+, upstream [#35895](https://github.com/anthropics/claude-code/issues/35895)) where `--dangerously-skip-permissions` fails to bypass the file-level sensitive-path check.

---

## Discord Bot Setup

1. Create a new application at [Discord Developer Portal](https://discord.com/developers/applications)
2. Create a bot and copy the token
3. Enable **Message Content Intent** under Privileged Gateway Intents
4. Invite the bot with these permissions:
   - Send Messages
   - Create Public Threads
   - Send Messages in Threads
   - Add Reactions
   - Manage Messages (for reaction cleanup)
   - Read Message History

---

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
      contents: write
    steps:
      - env:
          GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}
          PR_NUMBER: ${{ github.event.pull_request.number }}
        run: |
          gh pr review "$PR_NUMBER" --repo "$GITHUB_REPOSITORY" --approve
          gh pr merge "$PR_NUMBER" --repo "$GITHUB_REPOSITORY" --auto --squash
```

---

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

---

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

---

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
| PATCH | `/api/tasks/{id}` | Update a task (enable/disable, change schedule) |
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
| GET | `/api/search` | Find a past thread by keyword — `LIKE` over summary and working dir; add `body=1` to also grep local Claude transcripts (each hit then carries a `snippet` and `source`); returns each hit with a Discord `deep_link` (`q` required, optional `origin`, `limit` max 50) |
| GET | `/api/threads/{thread_id}/messages` | Read another thread's conversation, oldest first (`limit`) |
| POST | `/api/claims` | Claim a resource before working on it — 201 when acquired, 409 with the holder when taken |
| GET | `/api/claims` | List live claims (optional `resource` filter) |
| DELETE | `/api/claims` | Release a claim (`resource`, `thread_id`, optional `force=true`) |
| POST | `/api/threads/{thread_id}/message` | Relay a message from one session to another (`text`, `from_thread`, `mode`, `hop`) |

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

## Microsoft Teams

Teams is a production **sibling** frontend, not a port. `claude_discord` and
`claude_teams` each implement the vocabulary in `claude_code_core.frontend`,
neither imports the other, and the same conformance contract runs against both —
which is what stops "the Teams side is missing something" from being found by a
user months later.

```bash
uv sync --extra teams
python -m claude_teams manifest --out dist/teams-app.zip
```

The normal launcher runs Discord and Teams concurrently with
`CCDB_FRONTENDS=discord,teams`. The public receiver verifies Bot Framework tokens and enqueues
activities; the private `ActivityPuller` consumes them outbound, sends each prompt through the same
session runner Discord uses, and posts the result back to Teams. The session host does not need an
inbound Teams listener.

The Teams experience is not a port of the Discord one. An answer that Discord
fragments into fifteen messages arrives as **one**, and the column of embeds
Discord posts per tool call becomes **a single card that keeps up to date** —
because Teams allows 1,800 operations per hour per conversation, and the
Discord design would spend a long session's whole budget on scrollback.

Prompts are answerable: a card press arrives as an invoke, is checked against
the conversation it was posted in, and resolves the session waiting on it. An
unanswered permission request denies, and so does one whose card could not be
posted — a prompt nobody could see must not be safer to ignore than one nobody
answered.

The Teams surface supports personal-chat file consent through a one-time upload URL — whose host is
checked against Microsoft's own domains before a byte moves. Channel file delivery is not
supported, and the private queue relay does not yet bridge the file-consent invoke. The conformance
contract runs twice because of the surface-level channel gap: a personal chat passes all 18 checks,
a channel fails exactly one, and the test asserts which.

Unlike Discord, Teams needs a **public HTTPS endpoint**, an Entra application, an Azure Bot,
an installable Teams app package, and a queue between the public and private sides. The values are
tenant-specific, so no one-size-fits-all checked-in manifest can be safe or correct.

Follow the [end-to-end Teams setup guide](docs/teams-setup.md) from registration through the first
Teams → agent → Teams round trip. See [Teams surface behavior](docs/teams.md) for capability details
and [the relay security model](docs/teams-relay.md) for the trust boundary and measured operation.

---

## Architecture

```
claude_code_core/          # Shared core library (backend-agnostic)
  backend.py               # SessionBackend protocol + create_backend() factory
  codex_runner.py          # OpenAI Codex CLI backend
  runner.py                # Claude CLI subprocess manager
  parser.py                # stream-json event parser
  types.py                 # Type definitions for SDK messages
  models.py                # SQLite schema
  session_repo.py          # Session CRUD
  thread_search.py         # /search orchestration — summary + body merge, dedupe by thread
  transcript_search.py     # grep/scan of ~/.claude/projects transcripts + snippet extraction;
                           # find_transcript() locates one session's file without knowing its cwd
  lounge_repo.py           # AI Lounge message CRUD
  rewind.py                # Session rewind helpers
claude_discord/
  main.py                  # Standalone entry point (setup_bridge + custom cog loader)
  cli.py                   # CLI entry point (ccdb setup/start commands)
  setup.py                 # setup_bridge() — one-call Cog wiring
  cog_loader.py            # Dynamic custom Cog loader (CUSTOM_COGS_DIR)
  bot.py                   # Discord Bot class
  protocols.py             # Shared protocols (DrainAware)
  frontend.py              # DiscordFrontend — resolve/create a conversation by key
  stores.py                # build_session_stores() — every repo, no frontend
  concurrency.py           # Worktree instructions + active session registry
  collision.py             # File-write tracking + collision rules (pure, clock-injected)
  lounge.py                # AI Lounge prompt builder
  session_view.py          # Cross-session views for GET /api/sessions (pure merge logic)
  relay.py                 # RelayGuard + relay prompt wrapper (hop/cooldown/rate limits)
  session_sync.py          # CLI session discovery and import
  worktree.py              # WorktreeManager — safe git worktree lifecycle
  cogs/
    claude_chat.py         # Interactive chat (thread creation, message handling)
    skill_command.py       # /skill slash command with autocomplete
    session_manage.py      # /sessions, /search, /sync-sessions, /resume, /resume-info, /sync-settings
    session_sync.py        # Thread-creation and message-posting logic for sync-sessions
    prompt_builder.py      # build_prompt_and_images() — pure function, no Cog/Bot state
    scheduler.py           # Periodic Claude Code task executor
    webhook_trigger.py     # Webhook → Claude Code task execution (CI/CD)
    auto_upgrade.py        # Webhook → package upgrade + drain-aware restart
    collision_watch.py     # Announces sessions writing the same files (60s loop)
    event_processor.py     # EventProcessor — state machine for stream-json events
    run_config.py          # RunConfig dataclass — bundles all CLI execution params
    _run_helper.py         # Thin orchestration layer (run_claude_with_config + shim)
  claude/
    runner.py              # Re-exports ClaudeRunner from claude_code_core
    parser.py              # Re-exports parse_line from claude_code_core
    types.py               # Re-exports type definitions from claude_code_core
  database/
    models.py              # SQLite schema
    repository.py          # Session CRUD
    task_repo.py           # Scheduled task CRUD
    ask_repo.py            # Pending AskUserQuestion CRUD
    notification_repo.py   # Scheduled notification CRUD
    lounge_repo.py         # AI Lounge message CRUD
    claims_repo.py         # Advisory resource claim CRUD (TTL-bound)
    resume_repo.py         # Startup resume CRUD (pending resumes across bot restarts)
    settings_repo.py       # Per-guild settings
    frontend_thread_repo.py  # ThreadKey → where the conversation lives
    inbox_repo.py          # Thread inbox CRUD (THREAD_INBOX_ENABLED)
  discord_ui/
    status.py              # Emoji reaction manager (debounced)
    chunker.py             # Fence- and table-aware message splitting
    embeds.py              # Discord embed builders
    views.py               # Stop button and shared UI components
    prompt_views.py        # ChoiceView / FormModal — renders the protocol's prompts
    mentions.py            # user_mention_kwargs() — notify requester when Claude pauses for input
    ask_bus.py             # Event bus for AskUserQuestion communication
    ask_view.py            # Buttons/Select Menus for AskUserQuestion
    ask_handler.py         # collect_ask_answers() — AskUserQuestion UI + DB lifecycle
    streaming_manager.py   # StreamingMessageManager — debounced in-place message edits
    tool_timer.py          # LiveToolTimer — elapsed time counter for long-running tools
    thread_dashboard.py    # Live pinned embed showing session states
    file_sender.py         # File delivery via .ccdb-attachments
    inbox_classifier.py    # classify() — lightweight claude -p call to label sessions
    thread_renamer.py      # suggest_title() — background claude -p call for auto thread naming
  ext/
    api_server.py          # REST API (optional, requires aiohttp)
    ingest_manifest.py     # Reconciles attachments_manifest against delivered files
    teams_sync.py          # have/want negotiation behind /api/teams/sync (plan + push)
    teams_store.py         # TeamsVaultStore — one file per message, chain.jsonl, _history/
  utils/
    logger.py              # Logging setup
examples/
  ebibot/                  # Real-world example: personal bot with custom Cogs
    cogs/
      reminder.py          # /remind slash command + scheduled notifications
      watchdog.py          # Todoist overdue task monitor
      auto_upgrade.py      # Self-update via GitHub webhook
      docs_sync.py         # Auto-translate docs on push
```

### Design Philosophy

- **CLI spawn, not API** — Invokes `claude -p --output-format stream-json`, giving full Claude Code features (CLAUDE.md, skills, tools, memory) without reimplementing them. Runs on your Claude Pro/Max subscription — no API key, no per-token billing
- **Concurrency first** — Multiple simultaneous sessions are the expected case, not an edge case; every session gets worktree instructions, the registry and AI Lounge handle the rest
- **Discord as glue** — Discord provides UI, threading, reactions, webhooks, and persistent notifications; no custom frontend needed
- **Framework, not application** — Install as a package, add Cogs to your existing bot, configure via code
- **Zero-code extensibility** — Add scheduled tasks and webhook triggers without touching source
- **Security by simplicity** — ~8000 lines of auditable Python; subprocess exec only, no shell expansion

---

## Testing

```bash
uv run pytest tests/ -v --cov=claude_discord
```

1690+ tests covering parser, chunker, repository, runner, streaming, webhook triggers, auto-upgrade (including `/upgrade` slash command, thread-invocation, and approval button), REST API, AskUserQuestion UI, thread dashboard, scheduled tasks, session sync, AI Lounge, cross-session observability, resource claims, session-to-session relay, startup resume, model switching, compact detection, TodoWrite progress embeds, custom Cog loader, permission/elicitation/plan-mode event parsing, thread inbox classification, per-thread lock behavior, SessionBackend protocol, CodexRunner, backend factory, and cross-backend session ownership.

---

## How This Project Was Built

**This codebase is developed by [Claude Code](https://docs.anthropic.com/en/docs/claude-code)**, Anthropic's AI coding agent, under the direction of [@ebibibi](https://github.com/ebibibi). The human author defines requirements, reviews pull requests, and approves all changes — Claude Code does the implementation.

This means:

- **Implementation is AI-generated** — architecture, code, tests, documentation
- **Human review is applied at the PR level** — every change goes through GitHub pull requests and CI before merging
- **Bug reports and PRs are welcome** — Claude Code will be used to address them
- **This is a real-world example of human-directed, AI-implemented open source software**

The project started on 2026-02-18 and continues to evolve through iterative conversation with Claude Code.

---

## Real-World Example

**[`examples/ebibot/`](examples/ebibot/)** — A personal Discord bot built on this framework, included right in this repo. Demonstrates the custom Cog loader with:

- **ReminderCog** — `/remind HH:MM "message"` slash command + 30-second send loop
- **WatchdogCog** — Todoist overdue task monitor (30-minute check, daily dedup, severity-based alerts)
- **AutoUpgradeCog** — Self-updating via GitHub webhook + systemctl restart
- **DocsSyncCog** — Auto-translate documentation on push via webhook
- **AlertResponderCog** — Generic alert-monitoring Cog; watches a configurable source and posts severity-annotated notifications to Discord
- **JobFailureTriageCog** — Picks up scheduler job-failure embeds and starts a triage session
- **ThreadCompletionCog** — Deleting a thread means the work is done; deletions are batched and filed as a written record built from the session transcript, since the thread's messages are already gone. What that record says and where it goes comes from an external prompt file (`THREAD_COMPLETION_PROMPT_FILE`), not from the Cog. Recording is off until you run `/thread-completion on` — the environment variables decide only whether the switch exists

Run it with: `ccdb start --cogs-dir examples/ebibot/cogs/`

> The EbiBot custom Cogs were previously maintained in a [separate repository](https://github.com/ebibibi/discord-bot). They are now co-located here so Claude Code always has full context of both the framework and the customizations — preventing accidental feature duplication.

---

## Inspired By

- [OpenClaw](https://github.com/openclaw/openclaw) — Emoji status reactions, message debouncing, fence-aware chunking
- [claude-code-discord-bot](https://github.com/timoconnellaus/claude-code-discord-bot) — CLI spawn + stream-json approach
- [claude-code-discord](https://github.com/zebbern/claude-code-discord) — Permission control patterns
- [claude-sandbox-bot](https://github.com/RhysSullivan/claude-sandbox-bot) — Thread-per-conversation model

---

## License

MIT
