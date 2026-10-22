# Getting Started

← Back to the [README](../../README.md) · [What You Can Do](features.md) · [Feature Reference](reference.md) · **Getting Started** · [Automation, Scheduler & REST API](automation.md) · [Architecture & Project Background](architecture.md)

## Quick Start — Claude or Codex in Discord in 5 Minutes

**Prerequisites:**

- Python 3.12+
- At least one of:
  - [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) — installed and authenticated (`claude login`). Recommended for Anthropic Pro/Max subscribers.
  - [OpenAI Codex CLI](https://github.com/openai/codex) — `npm install -g @openai/codex` then `codex login`. Uses your existing ChatGPT Plus/Pro/Business subscription.
- You can install both. Switch between them at runtime with `/backend` (see [Backend Switching](features.md#backend-switching--claude--codex--local--ag-ui--pi-on-demand)).

**Platform support:** Developed, tested and run in production on **Linux**; CI runs on Linux only. macOS and Windows are expected to work (Windows npm `.cmd` wrappers for the Claude and Codex CLIs are resolved to `node` + script) but get little real-world testing — bug reports welcome.

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

See [`examples/ebibot/`](../../examples/ebibot) for a full real-world example with reminders, auto-upgrade, and docs sync.

**Built-in examples in `examples/ebibot/cogs/`:**

| Cog | Purpose |
|-----|---------|
| `ReminderCog` | Discord-based reminder scheduling |
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

## Configuration

See [runtime recovery and control-plane boundaries](../runtime-reliability.md)
for idle deadlines, attachment retries, credentials and startup rollback.

| Variable | Description | Default |
|----------|-------------|---------|
| `DISCORD_BOT_TOKEN` | Your Discord bot token | (required) |
| `DISCORD_CHANNEL_ID` | Channel ID for Claude chat | (required) |
| `CCDB_BACKEND` | Backend to use: `claude`, `codex`, `local`, `agui`, `pi`, or `antigravity` | `claude` |
| `CCDB_COMMAND` | Path or name of the CLI binary (overrides `CLAUDE_COMMAND`). Used by the initial runner picked from `CCDB_BACKEND`; superseded by the two per-backend variables below when `/backend` switches at runtime. | _(auto: `claude` or `codex`)_ |
| `CCDB_CLAUDE_COMMAND` | Explicit path to the Claude CLI binary. Used by `BackendFactory` whenever `/backend claude` is active, regardless of the initial `CCDB_BACKEND`. Falls back to `CLAUDE_COMMAND`, then `claude` (PATH). | (optional) |
| `CCDB_CODEX_COMMAND` | Explicit path to the OpenAI Codex CLI binary. Required when running the bot under systemd (default service PATH does not include `~/.npm-global/bin`). Falls back to `codex` (PATH). | (optional) |
| `CCDB_CODEX_SANDBOX_OVERRIDE` | Optional deployment-wide Codex `--sandbox` override: `read-only`, `workspace-write`, or `danger-full-access`. Leave unset to use the Codex CLI default. Use `danger-full-access` only when the host's outer isolation is trusted and OS namespace restrictions prevent Codex's own sandbox from starting. This setting is intentionally not exposed per thread. | (optional) |
| `CCDB_AGUI_URL` | Exact HTTP(S) run endpoint for `/backend agui`. Redirects are rejected. | (required for `agui`) |
| `CCDB_AGUI_TOKEN` | Optional bearer token for the AG-UI endpoint. Stripped from Claude/Codex subprocess environments. | (optional) |
| `CCDB_AGY_COMMAND` | Explicit path to the Google Antigravity CLI (`agy`) used by `/backend antigravity`. agy installs to `~/.local/bin`, which a systemd unit's PATH usually lacks. Falls back to `agy` (PATH). See [Antigravity](../backends.md#google-antigravity-agy). | (optional) |
| `CCDB_PI_COMMAND` | Explicit path to the [pi](https://github.com/earendil-works/pi) CLI binary. Falls back to `pi` (PATH). | (optional) |
| `CCDB_PI_ALLOW_UNSANDBOXED` | Set to `1` to allow `/backend pi` to spawn at all. pi has no sandbox and no approval loop in its non-interactive modes, so ccdb refuses the turn instead of running unsandboxed by default. See [pi backend](../pi-backend.md). | `0` (backend refuses) |
| `CCDB_PI_MODEL` | Default pi model, fully qualified (`anthropic/claude-opus-5`, `ollama/gpt-oss:120b`). Unset means pi picks its own default, which is not necessarily one the account's credentials can call. `/model set` overrides it per thread or globally. See [pi backend](../pi-backend.md). | (optional) |
| `CCDB_PI_HOME` | pi config directory, when it is not `~/.pi`. Only used to find the model catalog the `/model` autocomplete reads. | `~/.pi` |
| `CONTEXT_LINKS_CONFIG` | Path to the context-links JSON file read by `ContextLinksCog`. A missing or malformed file silently disables the feature. | `context_links.json` |
| `CCDB_PUBLIC_API_URL` | Base URL of an HTTPS endpoint that redirects `/open/obsidian?vault=…&file=…` to the matching `obsidian://` URI. Set it to render Obsidian context links as clickable Discord buttons instead of embed text. ccdb does not serve this redirect itself. | (optional) |
| `CCDB_PI_APPROVE_PROJECT` | Set to `1` to let pi load project-local `.pi/` settings, skills and extensions. Off by default so a checked-out repository cannot reconfigure the agent running inside it. | `0` |
| `CCDB_PI_CLAUDE_PLUGIN_SKILLS` | Pass the skills of every enabled, user-scope Claude Code plugin to pi as `--skill`, so a pi thread keeps the plugin skills a Claude thread has. Set to `0` to turn it off. | `1` |
| `PATH` | Binary search path for the bot **and every CLI session it spawns** — sessions inherit the bot's environment. Set it in `.env` when running under systemd, which starts units with a minimal PATH and never reads `~/.bashrc` / `~/.profile`. See [Toolchain PATH](#toolchain-path--set-it-in-env). | (inherited from the parent process) |
| `CCDB_MODEL` | Model to use (overrides `CLAUDE_MODEL`) | `sonnet` |
| `CCDB_MODEL_DISCOVERY` | Set to `0` to stop the `/model` autocomplete from asking the Anthropic models endpoint which models your credentials can see (and from reading the Codex and pi CLIs' local model catalogs), and always use the static suggestion list instead. Discovery is read-only, reuses the Claude Code CLI's own auth, and already falls back on its own when offline, unauthenticated, or on Bedrock/Vertex/Foundry | `1` |
| `CCDB_PERMISSION_MODE` | Permission mode for CLI (overrides `CLAUDE_PERMISSION_MODE`) | `acceptEdits` |
| `CCDB_STATUS_LANG` | Language for the Codex status-line labels (`週次` / `クレジット` / `上限到達`). `en` renders them as `7d` / `credits` / `limit reached`, matching the English used elsewhere in the UI. Unrecognised values fall back to `ja`. Affects only those labels — Japanese prompt text and phrase matching are unaffected. | `ja` |
| `CCDB_DANGEROUSLY_SKIP_PERMISSIONS` | Skip all permission checks — overrides `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS` | `false` |
| `CCDB_WORKING_DIR` | Working directory for CLI (overrides `CLAUDE_WORKING_DIR`) | current dir |
| `CCDB_ALLOWED_TOOLS` | Comma-separated list of allowed tools (overrides `CLAUDE_ALLOWED_TOOLS`) | (optional) |
| `CCDB_CHANNEL_IDS` | Additional channel IDs, comma-separated (overrides `CLAUDE_CHANNEL_IDS`) | (optional) |
| `CLAUDE_COMMAND` | Path or name of the Claude CLI binary (legacy name — prefer `CCDB_COMMAND`). Use to pin a specific version (e.g. `CLAUDE_COMMAND=/usr/local/lib/node_modules/@anthropic-ai/claude-code@2.1.77/cli.js`) — useful to avoid regressions in newer CLI releases. | `claude` |
| `CLAUDE_MODEL` | Model to use (legacy — prefer `CCDB_MODEL`) | `sonnet` |
| `CLAUDE_PERMISSION_MODE` | Permission mode for CLI (legacy — prefer `CCDB_PERMISSION_MODE`) | `acceptEdits` |
| `CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS` | Skip all permission checks (legacy — prefer `CCDB_DANGEROUSLY_SKIP_PERMISSIONS`) | `false` |
| `CLAUDE_WORKING_DIR` | Working directory for Claude (legacy — prefer `CCDB_WORKING_DIR`) | current dir |
| `MAX_CONCURRENT_SESSIONS` | Max parallel Claude CLI sessions across all code paths (chat, skills, scheduler, webhooks). A limit changed from the [Ebi AI Console](../console.md#running-work) (1–32) is stored and wins over this value on restart | `3` |
| `SESSION_TIMEOUT_SECONDS` | Session **idle** timeout in seconds — reset every time output arrives, so an active stream may outlive it. Set `0` to disable the deadline explicitly | `300` |
| `CCDB_PR_COMPLETION_OWNER` | GitHub owner whose non-draft `session/<thread_id>` PRs trigger one automatic completion continuation. Requires authenticated `gh`; disabled when empty. | (optional) |
| `DISCORD_OWNER_ID` | User ID to @-mention when Claude needs input | (optional) |
| `COORDINATION_CHANNEL_ID` | Channel ID used as default fallback for AI Lounge channel | (optional) |
| `CCDB_MENTION_ANYWHERE` | When true, an @mention summons Claude in any guild channel or thread; set `false` to listen only in the configured channels | `true` |
| `CCDB_THREAD_CONTEXT_DAYS` | Days of the surrounding channel or thread's history prepended to the prompt when a mention wakes Claude there (`0` disables) | `7` |
| `MENTION_ONLY_CHANNEL_IDS` | Comma-separated channel IDs carved back out of the no-mention set (legacy; not listing a channel now has the same effect) | (optional) |
| `INLINE_REPLY_CHANNEL_IDS` | Comma-separated channel IDs where the bot replies inline (no thread created) | (optional) |
| `CHAT_ONLY_CHANNEL_IDS` | Comma-separated channel IDs in chat-only mode — only Claude's text responses are shown; all technical embeds (tools, thinking, session info, todos) are hidden | (optional) |
| `WORKTREE_BASE_DIR` | Parent directory of your repositories, scanned for session worktrees. Strongly recommended: sessions are always told to create `wt-{thread_id}`, and with this unset nothing ever removes them | (optional) |
| `CLI_SESSIONS_PATH` | Path to `~/.claude/projects` for CLI session discovery (enables `/sync-sessions`) and transcript body search (`/search body:True`, `GET /api/search?body=1`). Defaults to the standard `~/.claude/projects`, so body search stays Zero-Config wherever Claude Code has run | (optional) |
| `CUSTOM_COGS_DIR` | Directory containing custom Cog files to load at startup (see [Custom Cogs](#custom-cogs-extend-without-forking)) | (optional) |
| `CLAUDE_ALLOWED_TOOLS` | Comma-separated list of allowed tools for Claude CLI (legacy — prefer `CCDB_ALLOWED_TOOLS`) | (optional) |
| `CLAUDE_CHANNEL_IDS` | Additional channel IDs (comma-separated) for multi-channel setup (legacy — prefer `CCDB_CHANNEL_IDS`) | (optional) |
| `CCDB_SPAWN_THREAD_MARKER` | Marker prepended to the title of a thread opened by `POST /api/spawn`, so agent-started threads are distinguishable in the channel list. Set to an empty string to disable | `🤖` |
| `CCDB_SPAWN_PARENT_MARKER` | Marker prepended to the title of a thread that spawned children, in front of its own name. Set to an empty string to disable | `🌳` |
| `CCDB_DONE_THREAD_MARKER` | Marker prepended to a thread the agent reported finished (removed on the next human reply). Set to an empty string to disable | `✅` |
| `CCDB_WAITING_THREAD_MARKER` | Marker prepended when a turn ends waiting on the human (open AskUserQuestion, or `POST /api/threads/{id}/waiting`). Removed on the next human reply. Set to an empty string to disable | `❓` |
| `CCDB_REVIEW_THREAD_MARKER` | Marker prepended when a turn ends on a deliverable for the human to review (`POST /api/threads/{id}/review`). Removed on the next human reply. Set to an empty string to disable | `👀` |
| `CCDB_ACTION_THREAD_MARKER` | Marker prepended when a turn ends on a task the human must do outside the chat — a todo (`POST /api/threads/{id}/action`). Removed on the next human reply. Set to an empty string to disable | `📋` |
| `CCDB_ERROR_THREAD_MARKER` | Marker prepended when a turn ends in an error. Removed on the next human reply. Set to an empty string to disable | `⚠️` |
| `CCDB_SCHEDULED_THREAD_MARKER` | Marker prepended to a thread while a scheduled task is waiting to post into it (removed when it fires, or the task is disabled or deleted). Set to an empty string to disable | `⏰` |
| `CCDB_WAIT_CWD_ROOTS` | Directories a wait probe's `cwd` must sit under (`os.pathsep`-separated, checked after `realpath`) | relay user's home |
| `THREAD_INBOX_ENABLED` | Enable the persistent thread inbox (classifies sessions as `waiting`/`done`/`ambiguous` via `claude -p`; shown in thread dashboard) | `false` |
| `THREAD_AUTO_RENAME` | Auto-rename new thread titles using Claude AI — generates a short, descriptive title from the first user message via a background `claude -p` call (never delays session start), and re-titles the thread later when its subject has clearly moved on (rate-limited to one rename per 15 minutes; lineage tags are preserved) | `false` |
| `CCDB_ACCOUNT_POOLS_FILE` | Path to a TOML file listing pre-logged-in Claude Code / Codex profile directories per backend and the strategy that picks one per session or turn. Validated at startup; unset keeps one implicit login per backend. See [Account Pools](../account-pools.md) | (optional) |
| `CCDB_CLI_ENV_FILE` | Path to a `KEY=VALUE` file whose variables are merged into the CLI subprocess environment on every invocation. Changes take effect immediately without restarting the bot. Useful for temporary API routing (e.g., Azure Foundry) | (optional) |
| `CCDB_LOG_FILE` | Path to a log file. When set, a rotating file handler (10 MB × 5 backups) is added alongside the default stdout handler. Useful for monitoring and alerting. | (optional) |
| `API_HOST` | REST API bind address | `127.0.0.1` |
| `API_PORT` | REST API port (enables REST API when set) | (optional) |
| `CCDB_API_SECRET` | Optional control-plane Bearer secret, also supplied to session runners. Required for non-loopback binds | (optional) |
| `CCDB_CONTROL_PLANE_HOST_GUARD` | Reject nonlocal Host/Origin and forwarding headers on the control plane; `0` opts out for deliberately authenticated proxies | `1` |
| `CCDB_INGEST_TOKEN` | Bearer token for `POST /api/ingest` (independent of `api_secret`); unset ⇒ the endpoint responds `503` | (optional) |
| `CCDB_INGEST_ENTRA_TENANT_ID` / `CCDB_INGEST_ENTRA_CLIENT_ID` / `CCDB_INGEST_ENTRA_ALLOWED_OIDS` | Also accept an Entra ID v2 access token as the bearer on the ingest and Teams sync routes. All three are required (allowlist = comma-separated user object IDs); half-set is logged and left off. Either this or `CCDB_INGEST_TOKEN` enables the external listener | (optional) |
| `CCDB_INGEST_ENTRA_SCOPE` | Delegated scope the token must carry in `scp` | `access_as_user` |
| `CCDB_INGEST_REQUIRE_COMPLETE` | Set to `1` to reject an ingest with `409` when its `attachments_manifest` proves attachments went missing, instead of starting a session on partial evidence | `0` |
| `CCDB_TEAMS_VAULT_ROOT` | Directory where `POST /api/teams/sync` mirrors upstream threads (one file per message). Gated by `CCDB_INGEST_TOKEN` | `{working_dir}/teams` |
| `CCDB_CONSOLE_PORT` | Port for the [Ebi AI Console](../console.md) listener (requires the `console` extra). The console is off unless set and never runs unauthenticated (passkey sign-in by default) | (optional) |
| `CCDB_CONSOLE_HOST` | Ebi AI Console bind address. Keep it on loopback behind a tunnel | `127.0.0.1` |
| `CCDB_CONSOLE_ACCESS_TEAM_DOMAIN` | Cloudflare Access team domain (`myteam.cloudflareaccess.com`) whose JWTs the console verifies | (optional) |
| `CCDB_CONSOLE_ACCESS_AUD` | AUD tag of the Cloudflare Access application in front of the console | (optional) |
| `CCDB_CONSOLE_ALLOWED_EMAILS` | Comma-separated emails allowed in via Access. Required with Access, so a mistaken Access policy edit still leaves the console closed | (optional) |
| `CCDB_CONSOLE_TOKEN` | Bearer token (32+ characters) for local clients; on its own, use only on a private network (tailnet, SSH tunnel, loopback) | (optional) |
| `CCDB_CONSOLE_PASSKEYS` | `0` turns passkey sign-in off (then Access or a token is required) | `1` |
| `CCDB_CONSOLE_ORIGIN` | Comma-separated origins the console may be used from (e.g. `https://console.example.com`). Set it when a proxy rewrites `Host` | (from the request) |
| `CCDB_CONSOLE_SESSION_DAYS` | How long a console sign-in lasts, in days | `30` |
| `CCDB_CONSOLE_ENROLL` | `1` logs a new single-use passkey setup code on start, even when passkeys exist (recovery after losing every passkey) | (optional) |
| `CCDB_CONSOLE_TRIAGE` | `0` turns off the console's [automatic triage](../console.md#automatic-triage) (priority, due date, next step and place guessed by a fast model) | `1` |
| `CCDB_CONSOLE_TRIAGE_MODEL` | Model for triage on the Claude backend (`claude --model`) | `haiku` |
| `CCDB_CONSOLE_TRIAGE_CODEX_MODEL` | Model for triage on the Codex backend (`codex exec -m`) | (cheapest in Codex's catalog) |
| `EBI_WORKSPACE_NOTES_FOLDER` | Absolute notes-folder path for optional [Console Workspace integration](../console.md#optional-ebi-workspace); must contain `02_Contexts`. Also accepts `CLAUDE_WORKSPACE_NOTES_FOLDER`. When unset, checks `${XDG_CONFIG_HOME:-~/.config}/ebi-workspace/notes-folder`, then the same variables under `env` in `~/.claude/settings.local.json`. No guessed default vault | (optional) |

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

Follow the [end-to-end Teams setup guide](../teams-setup.md) from registration through the first
Teams → agent → Teams round trip. See [Teams surface behavior](../teams.md) for capability details
and [the relay security model](../teams-relay.md) for the trust boundary and measured operation.

---

[What You Can Do](features.md) · [Feature Reference](reference.md) · **Getting Started** · [Automation, Scheduler & REST API](automation.md) · [Architecture & Project Background](architecture.md)
