# Relay Console

The Relay Console is a triage screen for the agent threads ccdb runs. It shows every thread
sorted by whose move it is, with priority, due date, snooze, project and parent/child tree, and
lets you reply, mark things done, or write down new work and hand it to an agent. It works on
desktop and phone (add it to the home screen).

The JSON API under `/console/api` is the contract. The bundled web client is one consumer of it.
See [ADR-0010](adr/0010-add-an-api-first-console-on-its-own-listener.md).

## Views

| View | Shows |
|---|---|
| **Inbox** | The ball is on you: ❓ waiting for an answer, 👀 something to review, 📋 a task only you can do, ⚠️ a failed turn |
| **Running** | A turn is in flight (or queued for a slot) |
| **To do** | Work you wrote down that no agent has yet |
| **Idle** | The agent stopped without saying whose move it is |
| **Tree** | Everything open, grouped by project, children under their parent |
| **Snoozed / Done** | Hidden until the snooze ends / finished |

Within a view items sort by priority (P0 first), then overdue, then due date, then whoever has
waited longest.

Status is derived from what ccdb already knows: the outcome marker on the thread title, running
turns, the slot queue and the spawn lineage. Agents do not have to do anything new. What the
console stores itself (`work_items` in the session database) is only what nothing else knows:
priority, due date, snooze, project, a parent override, a title override, and captures.

## Backend usage strip

A strip across the top of every screen shows how much each backend has left: per rate-limit
window (5-hour, weekly, …) a bar, the percentage used and the time until it resets. Colours turn
amber at 70% and red at 90%. A backend that is at its limit shows **⛔ back HH:MM** with the time
remaining. On a phone the strip wraps, so every backend stays visible.

- **Claude**: the windows the CLI reports after every turn. A window whose reset time has passed
  reads as reset until the next turn refreshes it.
- **Codex**: read through the read-only `codex app-server` probe, cached for two minutes. Shown
  when `CCDB_CODEX_COMMAND` is set. Available rate-limit reset credits are shown too.
- **Account pools** (`CCDB_ACCOUNT_POOLS_FILE`): one entry per profile, using the router's own
  view of whether the profile is blocked.


`j`/`k` move, `Enter` opens, `Esc` closes, `e` done, `1`–`4` priority P0–P3, `s` snooze until
tomorrow 09:00, `c` write down work, `r` reply, `/` filter, `g` then `i`/`a`/`t`/`w`/`d` switches
view, `?` help. URLs carry the view and the open item (`/#me/t1234`), so a link can point at a
single item.

## Enable it

The console is off unless `CCDB_CONSOLE_PORT` is set, and it **refuses to start without
authentication**.

```dotenv
CCDB_CONSOLE_PORT=8100
# CCDB_CONSOLE_HOST=127.0.0.1           # default; keep it on loopback behind a tunnel

# Browser access through Cloudflare Access (recommended)
CCDB_CONSOLE_ACCESS_TEAM_DOMAIN=myteam.cloudflareaccess.com
CCDB_CONSOLE_ACCESS_AUD=<the Access application's AUD tag>
CCDB_CONSOLE_ALLOWED_EMAILS=me@example.com

# And/or a bearer token for local clients (scripts, a terminal UI); 32+ characters
# CCDB_CONSOLE_TOKEN=...
```

With Access, the Access JWT's signature is checked against the team's published keys, along
with its audience, issuer, expiry and the email allowlist. The allowlist is required even though
the Access policy already restricts who gets in: if someone edits the policy by mistake, the
console still stays closed.

### Token mode (tailnet, SSH tunnel, local)

With only `CCDB_CONSOLE_TOKEN` set, the web client asks for the token once and keeps it in the
browser's local storage. Use this only where the network itself is private (a tailnet, an SSH
tunnel, loopback). For example, inside a Tailscale tailnet:

```bash
tailscale serve --bg --https=8443 http://127.0.0.1:8100
```

### Cloudflare Tunnel

```bash
cloudflared tunnel create relay-console
cloudflared tunnel route dns relay-console console.example.com
# config.yml
#   ingress:
#     - hostname: console.example.com
#       service: http://127.0.0.1:8100
#     - service: http_status:404
```

Then add a self-hosted Access application for `console.example.com` with a policy for your
email, and copy its AUD tag into `CCDB_CONSOLE_ACCESS_AUD`.

## API

All endpoints need authentication. Every method except `GET` also needs `X-Console-Request: 1`.

| Method | Path | Does |
|---|---|---|
| GET | `/console/api/me` | Who you are authenticated as |
| GET | `/console/api/board` | Every item, sorted for triage, plus slot usage |
| GET | `/console/api/usage` | Per backend (or pool profile): `available`, `unavailable_until`, and `windows[]` of `{type, utilization, resets_at, status, reset}` |
| GET | `/console/api/items/{id}/messages?limit=50` | The conversation's recent messages |
| POST | `/console/api/items` | Write down work: `{title, note?, priority?, due_at?, parent_id?, project?}` |
| PATCH | `/console/api/items/{id}` | Change `title`, `note`, `priority` (0–3), `due_at`, `snoozed_until`, `project`, `parent_id`, `state` |
| POST | `/console/api/items/{id}/reply` | `{text}` — continues the session, queued behind a running turn |
| POST | `/console/api/items/{id}/done` | Mark done (also sets the ✅ marker on the thread) |
| POST | `/console/api/items/{id}/reopen` | Clear the done/someday verdict |
| POST | `/console/api/items/{id}/start` | Hand a written-down item to an agent in a console conversation (no chat thread) |

Item ids are `t<thread_id>` for threads and conversations and `c<hex>` for written-down work.

## Conversations the console owns

Work started from the console does not open a chat thread. It runs in a conversation the console
owns, through the same session runner, backends, slots and resume logic as Discord and Teams
([ADR-0011](adr/0011-let-the-console-own-its-conversations.md)). The transcript is stored in the
session database (`console_conversations`, `console_messages`).

- A reply continues the same session. If a turn is running, the reply waits for it.
- The agent marks the outcome with the usual `/api/threads/$DISCORD_THREAD_ID/done` (and
  `waiting`, `review`, `action`); the board shows it like a thread's marker.
- When the agent asks a question (AskUserQuestion), the question appears in the conversation and
  the item moves to the Inbox. Answer it with a reply.
- Only answers, questions, warnings and errors are kept. Tool activity is not shown live; the
  board shows the item under Running while a turn is in flight.

Threads that live in a chat platform still appear and can be replied to as before. Timestamps are ISO
8601 and are stored in UTC.

## Limits of this version

- Chat threads (work not started from the console) still live in the chat platform; the console
  reads and posts there.
- The bot process still starts its Discord client; a console-only deployment is not supported yet.
- Files an agent delivers in a console conversation are listed by name; there is no download yet.
- A console reply is posted by the bot, so it does not yet count in attention metering.
- The board polls every 8 seconds; there is no push channel yet.
- Archived threads come from the last 50 per watched channel, refreshed every two minutes.
