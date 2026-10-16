# Architecture & Project Background

← Back to the [README](../../README.md) · [What You Can Do](features.md) · [Feature Reference](reference.md) · [Getting Started](getting-started.md) · [Automation, Scheduler & REST API](automation.md) · **Architecture & Project Background**

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
  session_slots.py         # Steerable session slot queue (prioritize / defer / pause + auto-resume)
  relay.py                 # RelayGuard + relay prompt wrapper (hop/cooldown/rate limits)
  session_sync.py          # CLI session discovery and import
  worktree.py              # WorktreeManager — safe git worktree lifecycle
  cogs/
    claude_chat.py         # Interactive chat (thread creation, message handling)
    skill_command.py       # /skill slash command with autocomplete
    session_manage.py      # /sessions, /search, /sync-sessions, /resume, /resume-info, /sync-settings
    session_queue.py       # /queue — see and steer the session slot queue
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
    slot_views.py          # Queue buttons/menus (⏫ / ⏬ / ⏸) for the waiting message and /queue
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

## Testing

```bash
uv run pytest tests/ -v --cov=claude_discord
```

1690+ tests covering parser, chunker, repository, runner, streaming, webhook triggers, auto-upgrade (including `/upgrade` slash command, thread-invocation, and approval button), REST API, AskUserQuestion UI, thread dashboard, scheduled tasks, session sync, AI Lounge, cross-session observability, resource claims, session-to-session relay, startup resume, model switching, compact detection, TodoWrite progress embeds, custom Cog loader, permission/elicitation/plan-mode event parsing, thread inbox classification, per-thread lock behavior, SessionBackend protocol, CodexRunner, backend factory, and cross-backend session ownership.

## How This Project Was Built

**This codebase is developed by [Claude Code](https://docs.anthropic.com/en/docs/claude-code)**, Anthropic's AI coding agent, under the direction of [@ebibibi](https://github.com/ebibibi). The human author defines requirements, reviews pull requests, and approves all changes — Claude Code does the implementation.

This means:

- **Implementation is AI-generated** — architecture, code, tests, documentation
- **Human review is applied at the PR level** — every change goes through GitHub pull requests and CI before merging
- **Bug reports and PRs are welcome** — Claude Code will be used to address them
- **This is a real-world example of human-directed, AI-implemented open source software**

The project started on 2026-02-18 and continues to evolve through iterative conversation with Claude Code.

## Real-World Example

**[`examples/ebibot/`](../../examples/ebibot)** — A personal Discord bot built on this framework, included right in this repo. Demonstrates the custom Cog loader with:

- **ReminderCog** — `/remind HH:MM "message"` slash command + 30-second send loop
- **AutoUpgradeCog** — Self-updating via GitHub webhook + systemctl restart
- **DocsSyncCog** — Auto-translate documentation on push via webhook
- **AlertResponderCog** — Generic alert-monitoring Cog; watches a configurable source and posts severity-annotated notifications to Discord
- **JobFailureTriageCog** — Picks up scheduler job-failure embeds and starts a triage session
- **ThreadCompletionCog** — Deleting a thread means the work is done; deletions are batched and filed as a written record built from the session transcript, since the thread's messages are already gone. What that record says and where it goes comes from an external prompt file (`THREAD_COMPLETION_PROMPT_FILE`), not from the Cog. Recording is off until you run `/thread-completion on` — the environment variables decide only whether the switch exists

Run it with: `ccdb start --cogs-dir examples/ebibot/cogs/`

> The EbiBot custom Cogs were previously maintained in a [separate repository](https://github.com/ebibibi/discord-bot). They are now co-located here so Claude Code always has full context of both the framework and the customizations — preventing accidental feature duplication.

## Inspired By

- [OpenClaw](https://github.com/openclaw/openclaw) — Emoji status reactions, message debouncing, fence-aware chunking
- [claude-code-discord-bot](https://github.com/timoconnellaus/claude-code-discord-bot) — CLI spawn + stream-json approach
- [claude-code-discord](https://github.com/zebbern/claude-code-discord) — Permission control patterns
- [claude-sandbox-bot](https://github.com/RhysSullivan/claude-sandbox-bot) — Thread-per-conversation model

---

[What You Can Do](features.md) · [Feature Reference](reference.md) · [Getting Started](getting-started.md) · [Automation, Scheduler & REST API](automation.md) · **Architecture & Project Background**
