# Account pools

When one subscription login hits its usage limit, every thread on that backend
stops until the window resets — even if you hold another login with headroom.
An **account pool** lets the relay choose, per session or per turn, which of
several logins a Claude Code or Codex turn runs as.

- A **profile** is a configuration directory you have already logged in with the
  vendor CLI: a `CLAUDE_CONFIG_DIR` for Claude Code, a `CODEX_HOME` for Codex.
- A **pool** is the ordered list of profiles for one backend, plus the strategy
  that picks among them.

The relay never reads, copies or stores tokens. Choosing a profile means setting
one environment variable on the child process.

Without a pool file nothing changes: one implicit login per backend, exactly as
before.

## Terms of service

You are responsible for complying with each vendor's terms for every login you
put in a pool. This feature only selects among logins you already hold — for
example a personal plan and an organisation plan that you legitimately use — and
never handles credentials. Check the consumer and commercial terms of Anthropic
and OpenAI before pooling accounts, and do not use pools to circumvent a limit
the terms forbid circumventing.

## Setup

1. Log in once per profile, each into its own directory:

   ```bash
   CLAUDE_CONFIG_DIR=$HOME/.claude-work claude      # then /login
   CODEX_HOME=$HOME/.codex-work codex login
   ```

   A profile may omit its directory to mean "the relay's own login" (whatever
   `CLAUDE_CONFIG_DIR` / `CODEX_HOME` the bot already has, or `~/.claude` /
   `~/.codex`). At most one profile per pool may do this.

2. Write a pool file (see [`examples/account-pools.example.toml`](../examples/account-pools.example.toml)):

   ```toml
   [pools.claude]
   strategy = "priority"          # priority | sticky | round_robin | most_headroom
   switch_at = 0.95               # utilization at which a profile counts as exhausted
   windows = ["five_hour", "seven_day"]
   assign = "session"             # session | turn
   retry_on_exhaustion = false    # rerun a rejected turn on the next profile
   cooldown_seconds = 3600        # how long a rejection without a reset time blocks

   [[pools.claude.profiles]]
   name = "personal"              # inherits the relay's own login

   [[pools.claude.profiles]]
   name = "work"
   config_dir = "/home/me/.claude-work"

   [pools.codex]
   strategy = "most_headroom"

   [[pools.codex.profiles]]
   name = "codex-personal"

   [[pools.codex.profiles]]
   name = "codex-work"
   codex_home = "/home/me/.codex-work"
   ```

3. Point the relay at it and restart:

   ```bash
   CCDB_ACCOUNT_POOLS_FILE=/home/me/.config/ccdb/account-pools.toml
   ```

The file is validated strictly at startup. An unknown key, an unknown strategy,
a relative or missing directory, a duplicate name, or a name used in two pools
stops the bot with a message naming the offending key — a typo must not route
turns to the wrong login. Profile names must be unique across the whole file
because usage is stored per profile name.

## Strategies

| Strategy | Behaviour |
|---|---|
| `priority` | Use profiles in listed order. Move to the next one when the current one is exhausted, and return to an earlier profile as soon as its window resets ("drain one, then the next"). |
| `sticky` | Keep using the current profile until it is exhausted, then switch to the next one and **stay**; do not go back when the old one resets. |
| `round_robin` | Rotate across available profiles for each new session. The cursor is persisted, so a restart continues the rotation. |
| `most_headroom` | Pick the available profile with the lowest utilization (the highest of its live windows). Ties go to the earlier profile; a profile with no usage data counts as fully available. |

A profile is **exhausted** while any of these holds:

- a window listed in `windows` is at or above `switch_at` and its reset time is
  still in the future;
- any window reported a rate-limit **rejection** that has not reset yet — a
  rejection always counts, whether or not the window is listed;
- a rejection marker is still active (a rejected turn whose CLI gave no reset
  time blocks the profile for `cooldown_seconds`).

A window whose reset time has passed says nothing about the present and is
ignored. When every profile is exhausted, the turn runs on the one that frees up
first and the thread is told so.

### `assign`

- `session` (default): a thread keeps its profile while that profile is
  available, whatever the strategy, so the session's prompt cache stays warm.
  The strategy decides for new sessions and for threads whose profile ran out.
  With `priority`, this means a thread that moved to the second profile stays
  there after the first one resets; new threads go back to the first.
- `turn`: the strategy decides every turn. `priority` then returns immediately,
  and `round_robin` rotates on every message.

## What happens when a thread changes profile

A session id is only resumable from the configuration directory that holds its
transcript. The relay therefore **copies the transcript** into the new profile
before resuming:

- Claude Code: `<CLAUDE_CONFIG_DIR>/projects/<escaped cwd>/<session_id>.jsonl`
  (and the `<session_id>/` directory of sub-agent transcripts, if any).
- Codex: `<CODEX_HOME>/sessions/YYYY/MM/DD/rollout-<ts>-<session_id>.jsonl`.

The copy overwrites an older copy at the target, so a thread that moves
A → B → A resumes on A with the turns it took on B. If the copy is impossible
(the transcript is missing or unreadable), the relay starts a fresh session on
the new profile and seeds it with the text of the previous conversation, using
the same mechanism as a [cross-backend handoff](../README.md#backend-switching--claude--codex--local--ag-ui--pi-on-demand).

The thread sees one line when this happens, e.g.
`🔀 Account personal → work (session transcript copied, resuming).`

### Measured behaviour

Measured on Claude Code 2.1.289 and codex-cli 0.160.0, using two profile
directories logged in to the same account (enough to test the mechanics; the
CLI does not know whether two directories hold the same account):

| Step | Claude Code | Codex |
|---|---|---|
| Resume a session id from another profile directory, no copy | Fails: `No conversation found with session ID` | Fails: `no rollout found for thread id` |
| Copy the transcript to the same relative path, then resume | Works: same session id, earlier turns recalled | Works: same thread id, earlier turns recalled |
| Move back A → B → A through the relay | Works: a fact introduced on B is recalled on A | — |

With two *different* accounts the prompt cache does not carry over, so the first
turn after a switch is billed as uncached input.

## Failover

A turn counts as rejected for quota when Claude Code reports a `rate_limit_event`
with `status: "rejected"`, or when the turn ends with a usage-limit error
("usage limit", "hit your limit", "limit reached"). Codex's `exec --json` stream
carries no structured rate-limit event, so for Codex only the error text counts.

On rejection the profile is marked exhausted until the window's reset time (or
`cooldown_seconds` if none was reported), and:

- `retry_on_exhaustion = false` (default): the thread is told which profile ran
  out, when it is available again, and which profile the next message will use.
- `retry_on_exhaustion = true`: the same message is rerun once on the next
  profile. A second rejection only reports.

## Visibility

- `/usage` lists every profile of every pool with its windows, utilization,
  reset countdown and whether it is currently exhausted.
- The turn's completion notice gains an `Account` field naming the profile that
  served it. Without a pool the notice is unchanged.

Where the usage numbers come from:

- Claude Code: every `rate_limit_event` in the turn. Claude Code 2.1.289 reports
  all windows in `unifiedWindows` (and no top-level utilization), and the relay
  stores each one under the profile the turn ran as.
- Codex: after each Codex turn the relay asks that profile's `codex app-server`
  for `account/rateLimits/read` — the read-only call the Codex TUI makes on
  startup — and stores the windows it reports.

`usage_stats` is keyed by `(profile, rate_limit_type)`. Rows recorded before
pools existed, and every row recorded without a pool, belong to the profile
named `default`; naming a profile `default` adopts that history.

## Limitations

- Pools apply to interactive chat threads (Discord). Scheduled tasks, webhook
  triggers, `/skill` and the Teams frontend still run on the relay's own login.
- Cross-*backend* failover (Claude → Codex) is not automatic; switch with
  `/backend` as before. The cross-backend handoff, `/rewind` and `/fork` read
  transcripts from the relay's own directories, so they may not find a session
  that lives in another profile.
- The `default` profile's usage history is shown only when a pool profile is
  named `default`.
