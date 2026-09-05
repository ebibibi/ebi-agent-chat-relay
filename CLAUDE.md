# claude-code-discord-bridge (ccdb)

Discord frontend for Claude Code CLI. **This is a framework (OSS library), not a personal bot.**

**略称: ccdb** (claude-code-discord-bridge)

## Framework vs Instance

- **claude-code-discord-bridge** (this repo) = reusable OSS framework. No personal config, no secrets, no server-specific logic.
- Personal instances (e.g. EbiBot) use the custom Cog loader (`CUSTOM_COGS_DIR` / `--cogs-dir`) to add their own Cogs. See `examples/ebibot/` for the reference implementation.
- When adding features: if it's useful to anyone → add here. If it's personal workflow → add as a custom Cog.

### Zero-Config Principle (Critical)

**Consumers must get new features by updating the package alone — no code changes required.**

- New features should be enabled by default (auto-discovery, sensible defaults)
- New constructor parameters must have backward-compatible defaults (`= None`)
- If a feature requires consumers to wire something up, the design is wrong — fix it in ccdb
- Consumers should NEVER need to copy, wrap, or subclass ccdb Cogs. If they do, ccdb is missing an extension point

## Architecture

- **Python 3.12+** with discord.py v2
- **Cog pattern** for modular features
- **Repository pattern** for data access (SQLite via aiosqlite)
- **asyncio.subprocess** for Claude Code CLI invocation (never shell=True)

## Key Design Decisions

1. **CLI spawn, not API**: We invoke `claude -p --output-format stream-json` as a subprocess, not the Anthropic API directly. This gives us all Claude Code features (CLAUDE.md, skills, tools, memory) for free.
2. **Thread = Session**: Each Discord thread maps 1:1 to a Claude Code session ID. Replies in a thread continue the same session via `--resume`.
3. **Emoji reactions for status**: Non-intrusive progress indication on the user's message. Debounced to avoid Discord rate limits.
4. **Fence-aware chunking**: Never split messages inside a code block. The logic lives in `claude_code_core/rendering/` and is driven by `SurfaceCapabilities`, not by Discord's numbers — a surface with a 100 KB limit sends the same answer as one message instead of inheriting Discord's 2,000-character fragmentation.
5. **Installable package**: `claude_discord` is a proper Python package. Consumers install via `uv add git+...` or `pip install git+...`, not by copying files.
6. **Shared run helper**: `cogs/_run_helper.py` centralizes Claude CLI execution logic used by both ClaudeChatCog and SkillCommandCog.
7. **REST API as the control plane**: Claude Code subprocesses communicate back to ccdb via REST API (`CCDB_API_URL` env var), not via stdout markers or special output formats. This makes the interface explicit, testable, and usable by external systems (GitHub Actions, etc.). See `ext/api_server.py`.
8. **SQLite-backed dynamic scheduler**: Scheduled tasks are stored in `scheduled_tasks` DB table and executed by a single `discord.ext.tasks` master loop (every 30s). Tasks are registered at runtime via REST API — no code changes needed to add new tasks. `discord.ext.tasks` decorators are only used for the master loop, not per-task (they're static/compile-time constructs).
9. **Claude handles "what", ccdb handles "when"**: For scheduled tasks, ccdb only manages the schedule. All domain logic (what to check, how to deduplicate, what to post) lives in the Claude prompt. No GitHub/AzureDevOps-specific code in ccdb itself.
10. **CLI env overlay** (`CCDB_CLI_ENV_FILE`): Optional `KEY=VALUE` file read on every CLI spawn to inject env vars into the subprocess. Enables live configuration changes (e.g., switching to Azure Foundry) without restarting the bot. The file is read by `_build_env()` in `runner.py`.
11. **Model suggestions are discovered, not hardcoded** (`model_catalog.py`): the `/model` autocomplete asks `GET /v1/models` (reusing the CLI's own credentials) instead of shipping a constant that goes stale on every model launch. This is the *only* sanctioned direct Anthropic API call, and it is strictly non-essential: no credentials, no network, a 3P provider, or `CCDB_MODEL_DISCOVERY=0` all degrade to the static `SUGGESTED_MODELS` fallback. Never let it raise into a command path, and never log the token. Codex is discovered too, but *without* a second vendor call: the Codex CLI already fetches its own catalog and writes it to `$CODEX_HOME/models_cache.json`, so `codex_model_choices()` reads that file. A new generation (`gpt-6-astra`) appears as soon as the CLI has seen it, and decision 13's "ccdb does not phone home" still holds.

12. **Anonymization replaces by rule, inspects by model** (`claude_code_core/privacy/`): an optional gateway wraps any `SessionBackend` and swaps organisation-identifying terms for stable aliases before the prompt reaches the CLI, restoring them in the answer. The substitution is a rule table — never a model — because a model produces a different alias every run and an alias that changes cannot be restored. The local LLM's only job is to *report* proper nouns the rules missed. Reversing those two roles breaks the feature. `CCDB_ANONYMIZE_POLICY=adopt` keeps that split while closing the loop: a reported term is minted into the mapping table and replaced *by the table*, so it is as restorable as a rule term and never needs the model again — without it, every unseen customer name stops `/ask` until someone hand-edits the rules file. Off until a rules file exists; a malformed rules file raises rather than silently degrading to "send the real names". Scope defaults to `escalation` (the `/ask` hop only) — wrapping every backend is wrong when the agent already runs locally, and with the fail-closed default it would stop every thread whenever the inspector is unreachable; `CCDB_ANONYMIZE_SCOPE=all` restores it. See `docs/anonymization.md`.

13. **A local backend is a measured claim, not a configured one** (`claude_code_core/local_backend.py`): `/backend local` runs the Codex CLI against a model on the user's own hardware. Pointing the CLI at a local endpoint is not sufficient — measured on codex-cli 0.145.0, a fully local, logged-out run still contacts `chatgpt.com` for the startup update check and analytics. ccdb therefore generates and owns a separate `CODEX_HOME` with both disabled, re-verifies those settings on every spawn, and refuses to start rather than run a "local" thread that phones home. The check is structural (works on Windows too), not an OS egress rule; re-measure after a CLI upgrade. The separate `CODEX_HOME` does **not** cost the operator their skills, despite the obvious worry: codex-cli discovers `~/.codex/skills` and `~/.claude/skills` independently of `CODEX_HOME` (measured on 0.147.0 by reading the session rollout — 135 skills reached the model from a `CODEX_HOME` with no `skills/` directory at all). When a local run ignores a skill the index was present and the model did not act on it; do not "fix" that by mirroring directories. See `docs/local-backend.md`.

14. **Escalation is isolated by verified argv, not by procedure** (`claude_code_core/escalation.py`): `/ask` sends one anonymized, self-contained question to an external model. The CLI runs with `--setting-sources ""`, an empty temp cwd, every tool disallowed, and `--` before the prompt; `verify_isolation()` checks the argv immediately before spawning and refuses otherwise. The first flag is load-bearing — measured with cwd=/home/ebi and all tools already off, the CLI reports a CLAUDE.md-only term as present in its context without it and absent with it, so a naive escalation ships the whole file regardless of how well the question was anonymized. See `docs/escalation.md`.

15. **Runtime management belongs to the runtime's own API** (`claude_code_core/ollama_client.py`, `cogs/ollama_command.py`): `/backend` and `/model` choose a model; they cannot say what is installed, what fits, or what is resident in memory — and when the cloud backends are unavailable those are the only questions that matter. `/ollama` mirrors Ollama's native API (`/api/tags`, `/api/ps`, `/api/show`, `/api/pull`, `/api/delete`), deriving its origin from `CCDB_LOCAL_BASE_URL` by replacing the terminal `/v1` so there is still one address to configure. Two judgements are enforced rather than documented: a model that does not advertise the `tools` capability is flagged, because Codex acts only through tool calls and such a model *describes* work instead of doing it; and the selected model cannot be deleted, because that failure would otherwise surface mid-thread. The install catalog is a static shortlist, never fetched at runtime — a catalog refresh would be exactly the vendor call decision 13 exists to prevent. Relatedly, `APPEND_SYSTEM_PROMPT` reaches Codex and `local` as `developer_instructions`, not just Claude: a small model follows one blunt directive far more reliably than the 15 KB skill index it is already given. See `docs/local-backend.md`.

16. **A guard's failure direction follows what it guards** (`claude_code_core/privacy/answerability.py`): `/ask` runs two local checks on the anonymized text, and they fail in opposite directions on purpose. The leak inspector guards a *safety* property, so an unreachable inspector blocks — the harm is a real name leaving the machine. The answerability judge guards a *quality* property (replacement can hide the very subject the question is about, making "the pros and cons of `org-002`" perfectly anonymized and unanswerable), so an unreachable judge sends — the harm is refusing a good question, and being wrong costs one call. `AnswerabilityVerdict.blocks` is the single place that asymmetry lives; copying the inspector's fail-closed stance here would take `/ask` down whenever the local model is busy, in the name of saving an API call. The judge is skipped entirely when nothing was replaced (replacement cannot break what it did not touch) and overridable with `force: true`. See `docs/escalation.md`.

### Why REST API over stdout markers for Claude→ccdb communication

Alternative considered: Claude embeds `<!-- ccdb:schedule {...} -->` in response text; ccdb parses stdout.

**Rejected because**: fragile text parsing, untestable, can't be triggered externally, implicit side effect from output.

**REST API chosen because**: clean interface, independently testable, usable by external systems, already an established ccdb pattern (`ext/api_server.py`). Claude uses its Bash tool to `curl $CCDB_API_URL/api/tasks`.

## Development

### Setup

```bash
git clone https://github.com/ebibibi/ebi-agent-chat-relay.git
cd ebi-agent-chat-relay
uv sync --dev
```

### Running Tests

```bash
uv run pytest tests/ -v --cov=claude_discord
```

All tests must pass before submitting a PR. CI runs on Python 3.12 and 3.13.

### Linting & Formatting

```bash
uv run ruff check claude_discord/    # lint
uv run ruff format claude_discord/   # format
```

CI enforces both `ruff check` and `ruff format --check`. Fix all issues before pushing.

### Type Checking

```bash
uv run pyright claude_discord/    # CI runs this too — a clean ruff run is NOT enough
```

`getattr(obj, "attr", None)` narrows to `None` for pyright, and discord.py's channel
union has no common `history()`; annotate such locals as `Any` rather than ignoring.

### Running (standalone)

```bash
cp .env.example .env
# Edit .env with your Discord bot token and channel ID
uv run python -m claude_discord.main
```

### EbiBot のデプロイ（⚠️ 手動操作が必要と断言する前に読め）

「手動で `git pull` が必要」「`systemctl restart` が必要」等と言う前に、
`scripts/pre-start.sh`・`.github/workflows/`・`examples/ebibot/cogs/` を読むこと。
自動化されている可能性が高い。

### 開発フロー（worktree + ローカルテスト）

EbiBot は `/home/ebi/claude-code-discord-bridge/` から直接起動する（systemd `WorkingDirectory`）。
コード変更をPRマージ前に EbiBot で動作確認するための **dev worktree モード**が用意されている。

```bash
# 1. worktreeを作成してブランチで作業（Claude Codeが自動で行う）
git worktree add ../wt-my-feature -b feat/my-feature

# 2. worktreeで実装・ユニットテスト
cd /home/ebi/wt-my-feature
uv run pytest tests/ -v

# 3. dev modeを有効化してDiscord上で動作確認
make dev-on    # ~/.ccdb-dev-worktree にパスを書いてbotを再起動
# → Discord上でEbiBotを実際に操作してテスト
# → ユーザーが「OK」と確認するまでこのフェーズを続ける

# 4. 動作確認OKの後、dev modeを解除してPR作成・マージ
make dev-off   # ~/.ccdb-dev-worktree を削除してbotを再起動
make pr        # ブランチをpushしてGitHub PRを作成
# → PRマージは動作確認が完了した後に行う。先にマージしない！
```

**仕組み（`pre-start.sh` の実装）:**

1. `uv sync` 実行後、`_ccdb_dev_hook.py` と `_ccdb_dev_hook.pth` を venv の site-packages に配置する
2. `_ccdb_dev_hook.pth` が Python 起動時に `import _ccdb_dev_hook` を実行する
3. `_ccdb_dev_hook.py` が `sys.meta_path[0]` に `_Finder` を挿入する
4. `_Finder` が `~/.ccdb-dev-worktree` を読み、`claude_discord` のimportをworktreeに横取りする

`sys.meta_path` フックは `python -m` の CWD-first（`sys.path[0] = ''`）より優先される。
`.pth` / `PYTHONPATH` / `sitecustomize.py` では CWD に勝てないため、この方式を採用した。

**通常起動（本番モード）:**

`~/.ccdb-dev-worktree` が存在しない場合、フックは何もしない。
mainブランチで変更なしの場合、`pre-start.sh` が `git pull` して最新コードを取得する。

## Code Conventions

### Style

- **Formatter/Linter**: ruff (config in `pyproject.toml`)
- **Type hints**: Required on all function signatures
- **Python**: 3.12+ — use `from __future__ import annotations` in every file
- **Line length**: 100 characters max
- **Imports**: Sorted by ruff (`I` rule). Use `TYPE_CHECKING` for type-only imports

### Error Handling

- Use `contextlib.suppress(discord.HTTPException)` for Discord API calls that may fail (reactions, message edits)
- Never silently swallow errors in business logic — log them
- CLI subprocess errors should yield a `StreamEvent` with `error` field, not raise exceptions

### Security (Critical — Auto-Enforced)

This project runs arbitrary Claude Code sessions. Security is non-negotiable.

**Before every commit**, run the security audit (see `.claude/skills/security-audit/SKILL.md`):

- **Always `create_subprocess_exec`**: Never use `shell=True`. The prompt is a direct argument, not shell-interpolated.
- **`--` separator**: Always use `--` before the prompt argument to prevent flag injection
- **Session ID validation**: Strict regex `^[a-f0-9\-]+$` before passing to `--resume`
- **Skill name validation**: Strict regex `^[\w-]+$` before passing to Claude
- **Environment stripping**: `DISCORD_BOT_TOKEN` and other secrets are removed from the subprocess env so Claude's Bash tool can't read them
- **No `dangerously_skip_permissions` by default**: This flag exists for advanced users who understand the risk

If you modify `runner.py`, `_run_helper.py`, or any Cog, the security audit is **mandatory** before committing.

### Naming

- Files: `snake_case.py`
- Classes: `PascalCase` (e.g., `ClaudeRunner`, `StatusManager`)
- Functions/methods: `snake_case`
- Private: prefix with `_` (e.g., `_build_args`, `_run_helper.py`)
- Constants: `UPPER_SNAKE_CASE`

### Testing (TDD Enforced)

**All new features and bug fixes MUST follow TDD: write tests FIRST, then implement.**

1. **RED**: Write a failing test → `uv run pytest tests/test_xxx.py -v` → confirm it FAILS
2. **GREEN**: Write minimal code to pass → confirm it PASSES
3. **REFACTOR**: Clean up, keeping tests green
4. **VERIFY**: `uv run ruff check claude_discord/ && uv run pyright claude_discord/ && uv run pytest tests/ -v --cov=claude_discord`

See `.claude/skills/tdd/SKILL.md` for detailed patterns per module type.

- Use `pytest` with `pytest-asyncio` (auto mode)
- Test files go in `tests/` mirroring the source structure
- Pure logic (parser, chunker, types): 90%+ coverage
- Discord-dependent code (Cogs, StatusManager): use mocks, 30%+ coverage
- **Never write implementation code without a corresponding test**

## Project Structure

```
claude_discord/          # Installable Python package
  __init__.py            # Public API exports
  cli.py                 # CLI entry point (ccdb setup/start commands)
  main.py                # Standalone entry point (setup_bridge + custom cog loader)
  setup.py               # setup_bridge() — one-call Cog wiring
  cog_loader.py          # Dynamic custom Cog loader (CUSTOM_COGS_DIR / --cogs-dir)
  bot.py                 # Discord Bot class
  protocols.py           # Shared protocols (DrainAware)
  frontend.py            # DiscordFrontend — resolve/create a conversation by key
  stores.py              # build_session_stores() — every repo, no frontend
  concurrency.py         # Worktree instructions + active session registry
  lounge.py              # AI Lounge prompt builder
  session_sync.py        # CLI session discovery and import
  worktree.py            # WorktreeManager — safe git worktree lifecycle
  cogs/
    claude_chat.py       # Main chat Cog (thread creation, message handling)
    skill_command.py     # /skill slash command with autocomplete
    session_manage.py    # /sessions, /sync-sessions, /resume, /resume-info, /sync-settings
    session_sync.py      # Thread-creation and message-posting for sync-sessions
    prompt_builder.py    # build_prompt_and_images() — pure function
    webhook_trigger.py   # Webhook → Claude Code task execution (CI/CD)
    auto_upgrade.py      # Webhook → package upgrade + drain-aware restart
    ollama_command.py    # /ollama status|list|ps|show|pull|rm|use — the local runtime
    scheduler.py         # Scheduled task executor (SQLite-backed, master loop)
    event_processor.py   # EventProcessor — state machine for stream-json events
    run_config.py        # RunConfig dataclass — bundles all CLI execution params
    _run_helper.py       # Thin orchestration layer (run_claude_with_config)
  claude/
    runner.py            # Claude CLI subprocess manager
    parser.py            # stream-json event parser
    types.py             # Type definitions for SDK messages
  coordination/
    service.py           # Posts session lifecycle events to shared channel
  database/
    models.py            # SQLite schema
    repository.py        # Session CRUD operations
    task_repo.py         # Scheduled task CRUD (SchedulerCog)
    ask_repo.py          # Pending AskUserQuestion CRUD
    notification_repo.py # Scheduled notification CRUD (REST API)
    lounge_repo.py       # AI Lounge message CRUD
    resume_repo.py       # Startup resume CRUD (pending resumes)
    settings_repo.py     # Per-guild settings
    frontend_thread_repo.py # ThreadKey → where the conversation lives
  discord_ui/
    status.py            # Emoji reaction status manager (debounced)
    chunker.py           # Discord's limits + shim over claude_code_core.rendering
    embeds.py            # Discord embed builders
    views.py             # Stop button, ToolSelectView, and shared UI components
    prompt_views.py      # ChoiceView / FormModal — renders the protocol's prompts
    ask_bus.py           # Event bus for AskUserQuestion communication
    ask_view.py          # Buttons/Select Menus for AskUserQuestion
    ask_handler.py       # collect_ask_answers() — AskUserQuestion UI + DB lifecycle
    streaming_manager.py # StreamingMessageManager — debounced message edits
    tool_timer.py        # LiveToolTimer — elapsed time counter
    thread_dashboard.py  # Live pinned embed showing session states
    file_sender.py       # File delivery via .ccdb-attachments-{thread_id}
    thread_renamer.py    # suggest_title() — background claude -p call for auto thread renaming
  ext/
    api_server.py        # REST API server (optional, requires aiohttp)
    ingest_manifest.py   # Reconciles attachments_manifest against delivered files
  utils/
    logger.py            # Logging setup
claude_teams/            # Microsoft Teams frontend (optional extra: [teams])
  capabilities.py        # The Teams column of SurfaceCapabilities — the shipped one, imported by the conformance tests
  config.py              # TeamsConfig — validates the identity/addressing whose mistakes Teams reports as silence
  manifest.py            # App package generator (RSC + SSO declared); icons.py writes placeholder PNGs with no dependency
  auth.py                # Inbound token verification — algorithms pinned; the claim is `serviceurl`, lower case (measured)
  serve.py               # `python -m claude_teams serve` — echo-only endpoint for first-contact verification
  jwks.py                # Signing keys: refresh on unknown kid, rate-limited because that trigger is public
  token.py               # Outbound client-credentials token, cached and refreshed before it expires
  activity.py            # InboundActivity — the fields a reply needs, parsed once
  conversation.py        # ConversationRef — the Bot Connector host + conversation a message belongs to
  connector.py           # Posting and editing activities on the serviceUrl the conversation named
  surface.py             # TeamsSurface — the ConversationSurface. One card, not a column of embeds
  frontend.py            # TeamsFrontend — resolve/create a conversation by key. Passes check_frontend
  cards.py               # The session card and the prompt cards, bounded to the 28 KB payload Teams accepts
  interactions.py        # Who may answer a prompt and with what — every field of an inbound action is untrusted
  files.py               # The consent handshake, and the host allowlist the file's bytes are checked against
  commands.py            # The text command router — Teams has no slash commands, so this is the whole surface
  mentions.py            # Who was addressed (by id, not name), and taking the <at> markup out of the prompt
  pacer.py               # Coalescing per target, one update per interval — the 1,800/hour budget
  endpoint.py            # The aiohttp route. Nothing is done before the token check; 5xx is never the answer after acceptance
  http.py                # The only file that knows about aiohttp — everything else takes its transport as a callable
  relay/                 # Inbound for Teams without inbound on the session host — see docs/teams-relay.md
    receiver.py          # Public side: verify, enqueue, answer. No client secret, no route to the host
    puller.py            # Private side: poll outbound, ack only when done, filter duplicates, drop poison
    envelope.py          # What crosses the queue — carries the token's serviceurl, not the body's
    queue.py             # push/pull/ack. A separate ack is what makes it at-least-once
    storage_queue.py     # Azure Queue Storage over REST, defusedxml, pop receipts fully URL-encoded
tests/                   # pytest test suite
examples/
  ebibot/                # Real-world example: personal bot with custom Cogs
    cogs/                # ReminderCog, WatchdogCog, AutoUpgradeCog, DocsSyncCog,
                         # AlertResponderCog, JobFailureTriageCog, ThreadCompletionCog
pyproject.toml           # Package metadata + dependencies
uv.lock                  # Dependency lock file
CONTRIBUTING.md          # Contribution guidelines
```

`claude_code_core/privacy/` — the anonymization gateway (surface-agnostic, so a
Teams or CLI frontend gets it for free):

```
  rules.py               # Rule table loader (literals, regexes, builtin detectors)
  mapping.py             # 対応表 — persistent, local, bidirectional alias store
  engine.py              # Deterministic replace + restore. Calls no model, ever
  local_llm.py           # The one Ollama transport both local guards share (think:false lives here)
  inspector.py           # Local leftover check. Reports only. Fails closed
  answerability.py       # Did replacement leave a question worth asking? Fails open
  config.py              # Env-driven config; absent rules file = feature off
  audit.py               # JSONL trail of what actually left the machine
  gateway.py             # Policy (block/warn/off) + process-wide accessor
  backend.py             # AnonymizingBackend — SessionBackend decorator
```

### Adding a New Cog

1. Create `claude_discord/cogs/your_cog.py`
2. If it runs Claude CLI, use `_run_helper.run_claude_in_thread()` — don't duplicate the streaming logic
3. Export from `claude_discord/cogs/__init__.py`
4. Add to `claude_discord/__init__.py` public API
5. Write tests in `tests/test_your_cog.py`

### Custom Cog Protocol (for external Cogs loaded via `--cogs-dir`)

Custom Cog files are loaded by `cog_loader.py` from the directory specified by `CUSTOM_COGS_DIR` env or `--cogs-dir` CLI flag. Each `.py` file must expose:

```python
async def setup(bot, runner, components):
    """Called by load_custom_cogs().

    Args:
        bot: discord.ext.commands.Bot instance
        runner: ClaudeRunner (may be None if Claude chat is disabled)
        components: BridgeComponents (session_repo, task_repo, etc.)
    """
    await bot.add_cog(MyCog(bot))
```

Rules:
- Files prefixed with `_` are skipped
- Load order is deterministic (`sorted()` by filename)
- One Cog's failure is logged and skipped — never blocks others
- `examples/ebibot/cogs/` is the canonical reference implementation

### Adding a New Discord UI Component

1. Add to the appropriate file in `claude_discord/discord_ui/`
2. Export from `__init__.py` if it's part of the public API
3. Test edge cases (empty strings, very long strings, Unicode, code blocks)

## Git & PR Workflow

- **Branch from `main`**: `feature/description`, `fix/description`, `docs/description`
- **CI must pass**: Both Python versions x (ruff check + ruff format + pytest)
- **No direct push to main**: Always create a PR
- **Squash merge preferred**: Keeps main history clean
- **Commit style**: `<type>: <description>` — types: feat, fix, refactor, docs, test, chore, security

## AI Agent Configuration

This project ships AI agent configs for all major tools:

| File | Tool | Purpose |
|------|------|---------|
| `CLAUDE.md` | Claude Code | Project context (this file) |
| `AGENTS.md` | OpenAI Codex | Symlink → CLAUDE.md |
| `.github/copilot-instructions.md` | GitHub Copilot | Condensed instructions |
| `.cursorrules` | Cursor | IDE-specific rules |

### Skills (`.claude/skills/`)

Project-specific skills that help AI agents work effectively on this codebase:

| Skill | Purpose |
|-------|---------|
| `tdd` | **Enforced** test-driven development — write tests FIRST, then implement |
| `verify` | Pre-commit quality gate (lint + format + test + security) |
| `add-cog` | Step-by-step guide to scaffold a new Cog |
| `security-audit` | Security checklist specific to subprocess/injection threats |
| `python-quality` | Python coding patterns and project conventions |
| `test-guide` | Testing patterns, mocking Discord objects, coverage goals |

### Commands (`.claude/commands/`)

| Command | Usage |
|---------|-------|
| `/verify` | Run full verification pipeline |
| `/new-cog <name>` | Scaffold a new Cog with tests |

### Hooks (`.claude/settings.json`)

- **PostToolUse (Edit/Write)**: Auto-format `.py` files with ruff after editing

## What Does NOT Belong Here

- Personal bot configuration (tokens, channel IDs, user IDs)
- Server-specific Cogs or workflows
- Direct Anthropic API calls (we use Claude Code CLI, not the API). Sole exception: the read-only model lookup in `model_catalog.py`, because the CLI can't enumerate models — see Key Design Decision 11 for the rules it has to obey
- Heavy dependencies that most users won't need
- Anything that requires secrets to import the package
