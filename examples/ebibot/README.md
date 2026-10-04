# EbiBot — Example Custom Bot using ccdb

Personal Discord bot built on top of [claude-code-discord-bridge](https://github.com/ebibibi/ebi-agent-chat-relay).

This example demonstrates how to extend ccdb with custom Cogs using the `CUSTOM_COGS_DIR` mechanism — no need for a separate repository.

## Custom Cogs

| Cog | File | Description |
|-----|------|-------------|
| ReminderCog | `cogs/reminder.py` | `/remind HH:MM "message"` slash command + 30s send loop |
| AutoUpgradeCog | `cogs/auto_upgrade.py` | Self-update via GitHub webhook + systemctl restart |
| DocsSyncCog | `cogs/docs_sync.py` | Auto-translate docs on push via webhook |
| AlertResponderCog | `cogs/alert_responder.py` | Watch a channel for ⚠️ alerts → auto-investigate with Claude Code |
| JobFailureTriageCog | `cogs/job_failure_triage.py` | Scheduler job failure embeds → auto-triage with Claude Code |
| ThreadCompletionCog | `cogs/thread_completion.py` | Thread deleted = work finished → file a record from the session transcript |

## Quick Start

```bash
# 1. Clone and install ccdb
git clone https://github.com/ebibibi/ebi-agent-chat-relay.git
cd ebi-agent-chat-relay
uv sync

# 2. Copy and edit .env
cp examples/ebibot/.env.example .env
# Edit .env with your Discord bot token and channel IDs

# 3. Start with custom Cogs
ccdb start --cogs-dir examples/ebibot/cogs/

# Or via environment variable:
CUSTOM_COGS_DIR=examples/ebibot/cogs ccdb start
```

## How Custom Cogs Work

Each `.py` file in the cogs directory must expose a `setup()` function:

```python
async def setup(bot, runner, components):
    """Called by ccdb's custom Cog loader.

    Args:
        bot: discord.ext.commands.Bot instance
        runner: ClaudeRunner (Claude CLI invocation) — may be None
        components: BridgeComponents (session_repo, task_repo, etc.)
    """
    await bot.add_cog(MyCog(bot))
```

Files prefixed with `_` are skipped.  If one Cog fails to load, others still load normally.

## Architecture

```
ccdb (framework)
  |
  +-- setup_bridge() -> ClaudeChatCog, SessionManageCog, SkillCommandCog, SchedulerCog
  |
  +-- load_custom_cogs(cogs_dir) -> ReminderCog, AutoUpgradeCog, DocsSyncCog,
                                    AlertResponderCog, JobFailureTriageCog, ThreadCompletionCog
```

All Cogs share the same bot instance, event loop, and Discord connection.

## Thread Deletion as a Completion Signal

`ThreadCompletionCog` exists because this bot's owner uses Discord threads as a todo list and
deletes them when the work is done. That deletion is a completion label that costs nobody any
effort, so the Cog turns it into a written record.

The constraint that shapes the whole design: **by the time the delete event arrives, the thread's
messages are gone.** Nothing can be fetched back from Discord. The only material left is what ccdb
already held — the session row, and the transcript at
`~/.claude/projects/<project>/<session_id>.jsonl`, which contains every user turn, reply, and tool
call. That transcript is the primary source, so there is no need to mirror Discord separately.

**Recording is off until you turn it on.** Deleting a thread is an everyday, destructive act, and
having it silently start a Claude session is a surprise — so consent is explicit:

```
/thread-completion on      # deletions start being recorded
/thread-completion off     # deletions do nothing again
/thread-completion         # show the current state
```

The environment variables below decide only whether the switch *exists*, never whether it is
thrown. The answer is stored in the settings repo, so it survives a restart. It is also re-checked
after the quiet period: turning it off during the wait drops the pending batch, because "stop" said
before anything ran should be honoured.

Three consequences worth knowing before turning it on:

- **Deletions are batched.** Threads are usually cleaned up in bursts, so the Cog waits for a quiet
  period and then starts *one* session for the whole batch. Filing one session per deleted thread
  would create more threads than the cleanup removed.
- **Threads that never held a session are dropped.** Notification threads (scheduler alerts, PR
  watches) have no session row, and filing "work completed" for them would be a lie. The Cog's own
  record threads are ignored too, so deleting a record does not file a record about it.
- **Where the record goes is not decided here.** The Cog resolves the deleted threads and hands
  Claude a manifest; the instructions for what to write and where live in the prompt file named by
  `THREAD_COMPLETION_PROMPT_FILE`, because that part is one person's note-taking convention and
  this repository is public. With no file configured, a generic prompt asks for a record following
  the workspace's own conventions — and an unreadable path falls back to that same prompt rather
  than dropping the batch, since losing the record's wording is recoverable and losing the batch is
  not.

The prompt template takes two placeholders:

| Placeholder | Expands to |
|-------------|------------|
| `{count}` | How many threads were deleted in this batch |
| `{manifest}` | Path to the JSON listing them. Each entry carries `summary` and `transcript_path`, the latter `null` when no transcript survives |

Configuration (the Cog is not loaded at all unless the channel is set, and recording
stays off until `/thread-completion on`):

| Variable | Default | Purpose |
|----------|---------|---------|
| `THREAD_COMPLETION_CHANNEL_ID` | — (required) | Channel the record thread is created in |
| `THREAD_COMPLETION_PROMPT_FILE` | — (generic prompt) | Prompt template holding the instance's own instructions, with `{count}` and `{manifest}` placeholders |
| `THREAD_COMPLETION_DEBOUNCE` | `180` | Seconds of quiet before the batch is filed |
