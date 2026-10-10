# Relay Console

The Relay Console is a triage screen for the agent threads ccdb runs. It shows every thread
sorted by whose move it is, with priority, due date, snooze, project and parent/child tree, and
lets you reply, mark things done, or write down new work and hand it to an agent. It works on
desktop and phone (add it to the home screen).

The JSON API under `/console/api` is the contract. The bundled web client is one consumer of it.
See [ADR-0010](adr/0010-add-an-api-first-console-on-its-own-listener.md) and, for sign-in,
[ADR-0012](adr/0012-sign-in-to-the-console-with-passkeys-by-default.md).

## Views

| View | Shows |
|---|---|
| **Inbox** | The ball is on you: ❓ waiting for an answer, 👀 something to review, 📋 a task only you can do, ⚠️ a failed turn |
| **Running** | A turn is in flight (or queued for a slot) |
| **To do** | Work you wrote down that no agent has yet |
| **Idle** | The agent stopped without saying whose move it is |
| **Tree** | Everything open, grouped by project, children under their parent |
| **Snoozed / Done** | Hidden until the snooze ends / finished |

## Reading a thread

Messages are rendered as Markdown (Discord flavour: headings, lists, tables, code blocks, quotes,
links, spoilers). The renderer builds DOM nodes and never parses HTML, so a message can format
itself but cannot inject markup.

Each message carries a `kind`:

| Kind | What it is | Shown |
|---|---|---|
| `human` | What a person wrote in the chat (console replies are posted by the bot, so they show as `agent` with their footer) | Always |
| `agent` | Answers, questions, errors and files from the agent | Always |
| `activity` | The relay's own bookkeeping: tool calls, thinking, session start/finish embeds, `-#` status notes, the usage footer, notification pings, rename notices and automatic continuation prompts | Only with "Show relay activity" |

The open thread reloads with the board while a turn is running, and the list keeps your scroll
position unless you were already at the bottom.

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
tomorrow 09:00, `c` write down work (the first line is the title; `Shift+Enter` adds note lines), `r` reply, `/` filter, `g` then `i`/`a`/`t`/`w`/`d` switches
view, `?` help. URLs carry the view and the open item (`/#me/t1234`), so a link can point at a
single item.

## Enable it

The console is off unless `CCDB_CONSOLE_PORT` is set. It **never runs unauthenticated**: by
default you sign in with a passkey, and nothing else has to be configured.

```dotenv
CCDB_CONSOLE_PORT=8100
# CCDB_CONSOLE_HOST=127.0.0.1           # default; keep it on loopback behind a tunnel
```

Install the extra (`pip install "claude-code-discord-bridge[console]"` or
`uv add "claude-code-discord-bridge[console]"`) and restart ccdb.

### Sign in with a passkey (default)

A passkey is the device you hold plus your fingerprint, face or PIN, checked in one gesture.
It cannot be phished, and it needs no account with anyone.

1. Start ccdb. While no passkey is registered, the log shows a line like
   `Relay Console setup code: K7QPM-3XW9D` (single use, valid 15 minutes).
2. Open the console and enter the code. The browser creates a passkey and you are signed in.
3. To add a phone or another computer, open **🔑 Passkeys → Add a device** on a signed-in
   device. Enter the code it shows on the new device.

Code expired? Press **Write a new setup code to the log** on the sign-in screen. Lost every
passkey? Start ccdb with `CCDB_CONSOLE_ENROLL=1` once and use the code it logs.

Passkeys need a secure origin: `https://…`, or `http://localhost`. Browsers refuse IP addresses,
so open `http://localhost:8100`, not `http://127.0.0.1:8100`. A passkey belongs to one host name.

| Setting | Default | Meaning |
|---|---|---|
| `CCDB_CONSOLE_PASSKEYS` | `1` | `0` turns passkeys off (then Access or a token is required) |
| `CCDB_CONSOLE_ORIGIN` | *(from the request)* | Comma-separated origins the console may be used from, e.g. `https://console.example.com`. Set it when a proxy rewrites `Host` |
| `CCDB_CONSOLE_SESSION_DAYS` | `30` | How long a sign-in lasts |
| `CCDB_CONSOLE_ENROLL` | — | `1` logs a new setup code on start, even when passkeys exist |

Sessions are `HttpOnly`, `SameSite=Strict` cookies, and only their hash is stored. Removing a
passkey signs out every session it opened.

### Reach it from your phone

The console listens on loopback. Put something that terminates HTTPS in front of it:

**Tailscale** (private to your tailnet):

```bash
tailscale serve --bg --https=8443 http://127.0.0.1:8100
# open https://<machine>.<tailnet>.ts.net:8443
```

**Cloudflare Tunnel** (on the internet):

```bash
cloudflared tunnel create relay-console
cloudflared tunnel route dns relay-console console.example.com
# config.yml
#   ingress:
#     - hostname: console.example.com
#       service: http://127.0.0.1:8100
#     - service: http_status:404
```

Passkeys alone are enough to put the console on the internet. You can add Cloudflare Access in
front as well (below).

### Optional: Cloudflare Access (Google, GitHub, Entra ID, one-time PIN, …)

Access signs in at Cloudflare's edge before a request reaches your machine. Any identity
provider Access supports works, Google included. Add a self-hosted Access application for
`console.example.com` with a policy for your email, then:

```dotenv
CCDB_CONSOLE_ACCESS_TEAM_DOMAIN=myteam.cloudflareaccess.com
CCDB_CONSOLE_ACCESS_AUD=<the Access application's AUD tag>
CCDB_CONSOLE_ALLOWED_EMAILS=me@example.com
```

The Access JWT's signature is checked against the team's published keys, along with its
audience, issuer, expiry and the email allowlist. The allowlist is required even though the
Access policy already restricts who gets in: if someone edits the policy by mistake, the console
still stays closed. Multi-factor sign-in then depends on the identity provider (turn on 2-step
verification or passkeys on that account).

### Optional: a bearer token for scripts

```dotenv
CCDB_CONSOLE_TOKEN=...   # 32+ characters
```

For a terminal UI or a script: `Authorization: Bearer <token>`. It is one shared secret, not
multi-factor, so leave it unset unless something needs it. With a token set, the sign-in screen
also offers **Use a token instead**.

## Run without Discord

The console can be the only frontend. With `CCDB_FRONTENDS` set and `discord` left out, ccdb
never logs in to Discord and does not need `DISCORD_BOT_TOKEN` or `DISCORD_CHANNEL_ID`:

```dotenv
CCDB_FRONTENDS=console          # or console,teams
API_PORT=8099                   # the console starts beside the control plane
CCDB_CONSOLE_PORT=8100
CCDB_CONSOLE_TOKEN=...          # or the Cloudflare Access settings above
```

The scheduler, waits and the control-plane API keep running. New conversations, scheduled tasks
included, open in the console. Startup refuses a configuration without Discord that nobody could
reach (no console ports and no Teams).

## API

Endpoints need authentication unless marked *(no sign-in)*. Every method except `GET` also needs
`X-Console-Request: 1`. The routes that check a setup code share one rate limit (120 a minute).

| Method | Path | Does |
|---|---|---|
| GET | `/console/api/me` | Who you are authenticated as |
| GET | `/console/api/auth/status` | *(no sign-in)* Which methods are on, whether setup is pending, whether you are signed in |
| POST | `/console/api/auth/passkey/register/options` | *(no sign-in)* `{code?}` — start registering a passkey; a code is needed unless signed in |
| POST | `/console/api/auth/passkey/register/verify` | *(no sign-in)* `{ticket, credential, name}` — finish registering; signs in |
| POST | `/console/api/auth/passkey/login/options` | *(no sign-in)* Start a passkey sign-in |
| POST | `/console/api/auth/passkey/login/verify` | *(no sign-in)* `{ticket, credential}` — finish signing in |
| POST | `/console/api/auth/setup-code` | *(no sign-in)* Log a new setup code; only while no passkey exists |
| POST | `/console/api/auth/logout` | End this session |
| GET | `/console/api/passkeys` | Registered passkeys (names and dates, never key material) |
| POST | `/console/api/passkeys/invite` | A single-use code for adding another device |
| DELETE | `/console/api/passkeys/{id}` | Remove a passkey and its sessions |
| GET | `/console/api/board` | Every item, sorted for triage, plus slot usage |
| GET | `/console/api/usage` | Per backend (or pool profile): `available`, `unavailable_until`, and `windows[]` of `{type, utilization, resets_at, status, reset}` |
| GET | `/console/api/items/{id}/messages?limit=50` | The conversation's recent messages (max 100), each with `kind`, `embeds` and `attachments` |
| POST | `/console/api/items` | Write down work: `{title, note?, priority?, due_at?, parent_id?, project?, start?}`. With `start: true` it is handed to an agent in the same request; if that fails the item is kept and the response carries `start_error` |
| PATCH | `/console/api/items/{id}` | Change `title`, `note`, `priority` (0–3), `due_at`, `snoozed_until`, `project`, `parent_id`, `state` |
| POST | `/console/api/items/{id}/reply` | `{text}` — continues the session, queued behind a running turn |
| POST | `/console/api/items/{id}/done` | Mark done (also sets the ✅ marker on the thread) |
| POST | `/console/api/items/{id}/reopen` | Clear the done/someday verdict |
| GET | `/console/api/items/{id}/live` | A console conversation's running turn: status, tool activity, the answer so far, whether it can be stopped |
| POST | `/console/api/items/{id}/stop` | Stop the running turn of a console conversation (409 when nothing runs) |
| GET | `/console/api/files/{key}/{token}/{name}` | A file an agent delivered in a console conversation |
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
- While a turn runs, the detail view shows it live: the tool calls, the answer as it is written,
  and a **Stop** button. None of that is stored; when the turn ends the transcript has the answer.
  Only answers, questions, warnings, errors and file links are kept.
- Files the agent delivers (the usual `.ccdb-attachments-<id>` marker) are copied next to the
  session database (`console_files/`) and linked in the conversation. Downloading needs the same
  authentication as the API.

Threads that live in a chat platform still appear and can be replied to as before. Timestamps are ISO
8601 and are stored in UTC.

## Limits of this version

- Chat threads (work not started from the console) still live in the chat platform; the console
  reads and posts there.
- A console reply is posted by the bot, so it does not yet count in attention metering.
- The board polls every 8 seconds; there is no push channel yet.
- Archived threads come from the last 50 per watched channel, refreshed every two minutes.
