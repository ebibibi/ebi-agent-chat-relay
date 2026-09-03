# Contributing to claude-code-discord-bridge

Thanks for your interest in contributing! This project was built by Claude Code and welcomes contributions from both humans and AI agents.

## Branch Workflow

We use **GitHub Flow** — a simple, PR-based workflow:

```
main (always releasable)
  ├── feature/add-xxx   → PR → CI passes → review → merge
  ├── fix/issue-123     → PR → CI passes → review → merge
  └── (direct push to main is not allowed)
```

### Steps

1. **Fork** the repo (or create a branch if you have write access)
2. **Create a branch** from `main`:
   ```bash
   git checkout -b feature/your-feature-name
   ```
3. **Make your changes** — write code, add tests
4. **Push** your branch and **open a PR** against `main`
5. **CI runs automatically** — tests + lint on Python 3.12/3.13, plus CodeQL security scanning
6. Once CI passes and the PR is reviewed, it gets **merged to main**

### Branch Naming

- `feature/description` — New functionality
- `fix/description` or `fix/issue-123` — Bug fixes
- `docs/description` — Documentation only
- `refactor/description` — Code restructuring without behavior change

## Development Setup

```bash
git clone https://github.com/ebibibi/ebi-agent-chat-relay.git
cd ebi-agent-chat-relay
uv sync --dev
make setup   # register git hooks (one-time per clone)
```

> **`make setup` is required** after every fresh clone. It configures git to use the
> pre-commit hook in `.githooks/`, which auto-formats and lints staged Python files.
> Without it, the hook never runs and bad code can slip through locally (CI will still
> catch it, but you'll get a surprise red build).
>
> Run `make check-setup` at any time to verify your environment is ready.

## Running Tests

```bash
uv run pytest tests/ -v --cov=claude_discord
```

All tests must pass before submitting a PR.

## Testing Against a Live Bot (dev worktree mode)

Unit tests do not cover everything a Discord surface does. If you run a bot from this
repository, you can point it at a worktree so it loads your branch instead of the main
tree — no reinstall, no editable-install juggling:

```bash
git worktree add ../wt-my-feature -b feature/my-feature
cd ../wt-my-feature
make dev-on    # write ~/.ccdb-dev-worktree and restart the bot
# ... exercise the change on Discord ...
make dev-off   # remove the marker and restart back onto the main tree
make drift     # is the bot running the newest code on origin/main?
```

`make dev-on` writes the worktree path to `~/.ccdb-dev-worktree`; the import hook that
`scripts/pre-start.sh` installs reads that marker and redirects `claude_discord` /
`claude_code_core` imports to the worktree. `dev-on` / `dev-off` restart a systemd unit
named `discord-bot` — adjust the Makefile if your deployment differs.

**Nothing expires dev mode.** A forgotten `make dev-on` keeps a side branch in production
indefinitely, while every merged PR *appears* to deploy and does not. `make drift`
(`scripts/check-deploy-drift.sh`) answers that: it names the worktree and branch, says
whether that commit is an ancestor of `origin/main`, counts how many merged commits are
therefore not running, and reports how long dev mode has been on. It compares against the
remote ref rather than a local `main`, which may itself be stale. `pre-start.sh` prints
the same report on every boot.

**Main-tree mode goes stale too.** Answering only the dev-worktree question and returning
`OK` for everything else would be a guard that reports on the case it was written for and
waves the rest through. The checkout is only pulled by `pre-start.sh`, so a bot that has
not restarted keeps serving whatever was merged before it booted — and that is silent in
exactly the same way: the tree says `main` and every `git pull` keeps succeeding. So
`make drift` also compares the *running process* against `origin/main`. The honest measure
is not "is the checkout behind" (a pull without a restart changes nothing) but "which
commits landed after the bot started", taken from the service's `ActiveEnterTimestamp`;
it then reports how long the **oldest** of those has been waiting, which is the length of
the actual wait rather than the age of the newest commit. Because restarts are batched
deliberately — an in-flight session does not survive one — it reports from day one but
only fails past `CCDB_STALE_DAYS` (default `3`). A machine without systemd, or a unit it
cannot read, says so rather than silently passing. `CCDB_SERVICE` overrides the unit name
(default `discord-bot`).

Exit codes: `0` running the newest code, or dev mode on already-merged code; `1` drift —
dev mode on unmerged code, or merged commits undeployed past the threshold; `2` dev mode
configured but unusable, the marker points nowhere (the hook silently falls back to the
main tree).

Before switching back, check `.env`: a value only your branch understands falls back to a
default on the main tree, which changes behaviour without erroring.

## Code Style

- **Formatter**: `ruff format`
- **Linter**: `ruff check`
- **Type hints**: Required on all function signatures
- **Python**: 3.12+ (use `from __future__ import annotations` for modern syntax)

```bash
uv run ruff check claude_discord/
uv run ruff format claude_discord/
```

## Creating Discord Threads

Every `create_thread()` call must pass the shared auto-archive window:

```python
from ..thread_policy import THREAD_AUTO_ARCHIVE_MINUTES

thread = await channel.create_thread(
    name=name,
    auto_archive_duration=THREAD_AUTO_ARCHIVE_MINUTES,
)
```

Discord fixes the window when the thread is created and defaults to a short one. An archived
thread leaves the channel's thread list, so a conversation the user still considers open looks
deleted. `tests/test_thread_policy.py` scans both `claude_discord/` and the example Cogs in
`examples/ebibot/cogs/`, and fails when a call site omits the keyword or hardcodes a number
instead of using the constant. The rule is about Discord's behaviour, not about which package the
call lives in, so a custom Cog is held to it too.

## Personal Detail Stays Out of Shipped Source

This repository is public, and `examples/ebibot/` is a real instance's configuration — which makes
it the place where personal detail leaks in. A docstring explaining *why* a Cog exists is the most
natural thing in the world to write, and the natural way to write it is to name the person whose
workflow it serves.

`tests/test_no_personal_identifiers.py` scans `claude_discord/`, `claude_code_core/`,
`claude_teams/` and `examples/`, and fails when shipped source names a real person. The check is
deliberately narrow — names, not topics. A broad "no Japanese" rule, or a list of tools someone
might use, produces false positives that get suppressed, and a suppressed guard is not a guard.

When a feature genuinely needs one person's conventions, point at them from outside the repository
rather than embedding them. `ThreadCompletionCog` is the reference pattern: the Cog keeps the
generic half (batching, session and transcript resolution, the manifest) and reads the
instance-specific instructions from the file named by `THREAD_COMPLETION_PROMPT_FILE`. An
unreadable path falls back to a generic prompt rather than dropping the work.

## Project Structure

- `claude_code_core/` — Backend-agnostic core library: `SessionBackend` protocol, `ClaudeRunner`, `CodexRunner`, `create_backend()` factory, parser, types, SQLite models
- `claude_discord/claude/` — Re-exports from `claude_code_core` for backward compatibility
- `claude_discord/cogs/` — Discord.py Cogs (chat, skill command, webhook trigger, auto-upgrade)
- `claude_discord/database/` — SQLite session and notification persistence
- `claude_discord/discord_ui/` — Discord UI components (status, chunker, embeds)
- `claude_discord/ext/` — Optional extensions (REST API server — requires aiohttp)
- `tests/` — pytest test suite

## Submitting Changes

1. Fork the repo and create a feature branch
2. Write tests for new functionality
3. Run locally before pushing:
   ```bash
   uv run ruff check claude_discord/
   uv run ruff format --check claude_discord/
   uv run pytest tests/ -v
   ```
4. Submit a PR with a clear description of what and why
5. CI will run automatically — all checks must pass

## Versioning

This project uses automatic versioning — **you never need to manually bump the version** for regular contributions.

- **Automatic patch bump**: Every PR merged to `main` triggers an automatic patch version increment (e.g., `1.3.0` → `1.3.1`). No release tag is created — the version is committed directly to `main`.
- **Manual minor/major release**: To cut a minor or major release (e.g., `1.4.0`), update `pyproject.toml` and `CHANGELOG.md` manually, then include `[release]` in your PR title. This tags and publishes the current version as a GitHub Release without bumping the patch.

## Adding a New Cog

1. Create `claude_discord/cogs/your_cog.py`
2. Use `_run_helper.run_claude_with_config(RunConfig(...))` for Claude CLI execution
   (The legacy `run_claude_in_thread()` shim is still available but prefer `run_claude_with_config`)
3. Export from `claude_discord/cogs/__init__.py`
4. Add to `claude_discord/__init__.py` public API
5. Write tests in `tests/test_your_cog.py`

## A Note on AI-Generated Code

This project was written by Claude Code. If you use Claude Code or other AI tools to contribute, that's perfectly fine — just make sure the code works, is tested, and makes sense.
