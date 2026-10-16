# Feature Reference

← Back to the [README](../../README.md) · [What You Can Do](features.md) · **Feature Reference** · [Getting Started](getting-started.md) · [Automation, Scheduler & REST API](automation.md) · [Architecture & Project Background](architecture.md)

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
- **Steerable session queue** — When every `MAX_CONCURRENT_SESSIONS` slot is busy, the waiting message carries **⏫ Run this next** / **⏬ Let others go first** buttons; `/queue` lists running and waiting threads from any channel and can **⏸ pause** a running thread, which frees its slot and resumes automatically (same session, via `--resume`) once the threads it yielded to are through. Pause is refused when nothing is waiting. Same controls on `GET /api/slots` and `POST /api/slots/{thread_id}/{prioritize|defer|pause}`. See [docs/session-queue.md](../session-queue.md)
- **Stop without clearing** — `/stop` halts a session while preserving it for resume
- **Session interrupt** — Sending a new message to an active thread sends SIGINT to the running session and starts fresh with the new instruction; no manual `/stop` needed
- **Auto-rename threads** — When `THREAD_AUTO_RENAME=true`, each new thread is automatically renamed with a Claude-generated title derived from the first message (background task, never delays session start). The title is kept honest as the thread goes on: once the work has clearly moved to a different subject, it is re-titled — at most once every 15 minutes, and only for threads ccdb opened

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
- **Automatic worktree cleanup** — Requires `WORKTREE_BASE_DIR`; leave it unset and the worktree instruction still fires, so the directory fills up silently. When set, session worktrees are removed at session end and orphans are swept at bot startup. A worktree is identified by its `session/{thread_id}` branch, not by its directory name, so a session's extra labelled worktrees (`wt-{thread_id}-obsidian`, `wt-obsidian-{thread_id}`) are collected too. Dirty worktrees are never auto-removed (safety invariant)
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
- **Per-task backend and model** — pin a task to one backend (and optionally one model) with `backend` / `model`; unpinned tasks keep following the active `/backend` and `/model`
- **Waits** — `POST /api/waits` lets a session end its turn while CI runs; ccdb polls the probe and resumes the thread when it is done (persistent across restarts, see `docs/waits.md`)

### CI/CD Automation
- **Webhook triggers** — Trigger Claude Code tasks from GitHub Actions or any CI/CD system
- **Auto-upgrade** — Automatically update the bot when upstream packages are released
- **DrainAware restart** — Waits for active sessions to finish before restarting
- **Reserved restart** — `POST /api/restart` holds the session queue, lets running turns finish, restarts once nothing is running (via `CCDB_RESTART_COMMAND`, or by closing the bot for its supervisor to restart), then replays everything that was queued in order; `GET` shows the state, `DELETE` cancels. See [docs/session-queue.md](../session-queue.md)
- **Auto-resume marking** — Active sessions are automatically marked for resume on any shutdown (upgrade restart via `AutoUpgradeCog`, or any other shutdown via `ClaudeChatCog.cog_unload()`); on restart Claude reports its previous state and re-confirms with the user before resuming any implementation work
- **Restart approval** — Optional gate to confirm upgrades; approve via ✅ reaction in the upgrade thread or via button posted to the parent channel; the button re-posts itself at the bottom as new messages arrive so it stays visible; if the bot shuts down while the prompt is still waiting, the button is removed and the thread is told the request is no longer pending (run `/upgrade` again to apply the release)
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
- **Conversation rewind** — `/rewind` shows a select menu of past user turns and truncates the session JSONL at the chosen point, removing that message and everything after it so the session resumes from the exact state before that turn; keeps all working files Claude created; useful when a session has gone off-track. Claude sessions only — on Codex/local/pi/AG-UI sessions it declines and leaves the conversation untouched
- **Conversation fork** — `/fork` branches the current thread into a new thread that continues from the same session state via `--fork-session`, creating a truly independent session copy; lets you explore a different direction without affecting the original

### Context Links
- **Project links on thread creation** — When a new thread's name matches a configured project, `ContextLinksCog` posts a compact set of links for that project (notes, repositories, dashboards). Matching is case-insensitive substring search over the thread name, and the **first** matching project wins, so order your config from most specific to least
- **Zero-config** — The Cog is always registered but stays silent until a config file exists. It reads `context_links.json` from the working directory, or the path in `CONTEXT_LINKS_CONFIG`. A missing or malformed file disables the feature rather than raising
- **Two link kinds** — `{"label": ..., "url": ...}` for a plain URL, and `{"label": ..., "obsidian": "path/to/note.md"}` which is expanded against the config's `obsidian_vault`
- **Why some links are buttons and some are text** — Discord link buttons accept only `http(s)` URLs, so an `obsidian://` URI cannot be one. Those render as text in the embed instead. Set `CCDB_PUBLIC_API_URL` to a base URL that 302s `/open/obsidian?vault=…&file=…` to the `obsidian://` URI and they become real buttons. **ccdb does not serve that redirect** — point the variable at your own endpoint

```json
{
  "obsidian_vault": "MyVault",
  "projects": [
    {
      "match": ["ccdb", "discord-bridge"],
      "links": [
        { "label": "Repo", "url": "https://github.com/ebibibi/ebi-agent-chat-relay" },
        { "label": "Notes", "obsidian": "Projects/ccdb.md" }
      ]
    }
  ]
}
```

### Security
- **No shell injection** — `asyncio.create_subprocess_exec` only, never `shell=True`
- **Session ID validation** — Strict regex before passing to `--resume`
- **Flag injection prevention** — `--` separator before all prompts
- **Secret isolation** — Bot token stripped from subprocess environment
- **Execution environments** — run agents behind an OS boundary (`bwrap`, the agent's own sandbox, a container, or another host) chosen by the operator; users can only pick from the operator's allowlist with `/sandbox` — see [docs/execution-environments.md](../execution-environments.md)
- **User authorization** — `allowed_user_ids` restricts who can invoke Claude
- **Log injection prevention** — User-provided API values are sanitized (newlines stripped) before writing to logs
- **Credential files stay untracked** — `.gitignore` covers `.env.*`, not just `.env`, because operators leave dated backups (`.env.bak-…`) beside the real file and each one holds a live bot token; `.env.example` is re-included explicitly so the template stays tracked
- **Local-model backend** (optional) — `/backend local` runs a thread against a model on your own hardware. ccdb owns a separate CLI home with the update check and analytics disabled, because a "local" run otherwise still contacts the vendor; it refuses to start if those settings are missing — see [docs/local-backend.md](docs/local-backend.md. `/ollama` manages that runtime from Discord, and the model in use is whatever was selected there — there is no environment variable that can silently disagree with it)
- **Remote AG-UI backend** (optional) — `/backend agui` connects the existing Discord/Teams session machinery to any HTTP/SSE AG-UI agent while preserving ccdb's session ledger, rendering, cancellation, and operational controls — see [docs/agui-backend.md](../agui-backend.md)
- **pi backend** (optional) — `/backend pi` runs the [pi](https://github.com/earendil-works/pi) CLI, which normalises Anthropic, OpenAI, Google, GitHub Copilot and OpenAI-compatible local servers behind one agent. pi has no sandbox and no approval loop in its non-interactive modes, so ccdb refuses the turn until `CCDB_PI_ALLOW_UNSANDBOXED=1` is set in the deployment environment (never per thread, or any Discord user could grant it to themselves), and declines project-local `.pi/` settings, skills and extensions unless `CCDB_PI_APPROVE_PROJECT=1` — see [docs/pi-backend.md](../pi-backend.md)
- **`/ask` — explicit escalation** (optional) — sends one anonymized, self-contained question to a strong external model with no project context, no files and no tools, then restores your real names in the answer; tools are an empty *allow* list (`--tools ""`) rather than a deny list that has to be rewritten for every tool the CLI adds, and the isolation is verified before every spawn. A local judge first checks that replacement did not hide the very subject of the question — asking for "the pros and cons of `org-002`" is perfectly anonymized and unanswerable — and withholds it if so (`force: true` overrides) — see [docs/escalation.md](../escalation.md)
- **Anonymization gateway** (optional) — Replaces organisation-identifying terms with stable aliases before the prompt reaches Claude or Codex, and restores them in the answer. A local model checks the result for replacement misses and, by default, blocks the send when it finds one; `CCDB_ANONYMIZE_POLICY=adopt` instead mints an alias for the reported term into the mapping table and sends, so a new customer name does not stop the command until someone hand-edits the rules file. Off until you write a rules file — see [docs/anonymization.md](../anonymization.md)

---

[What You Can Do](features.md) · **Feature Reference** · [Getting Started](getting-started.md) · [Automation, Scheduler & REST API](automation.md) · [Architecture & Project Background](architecture.md)
