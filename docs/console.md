# Ebi AI Console

The Ebi AI Console is a triage screen for the agent threads ccdb runs. It shows every thread
sorted by whose move it is, with priority, due date, snooze, project and parent/child tree, and
lets you reply, mark things done, or write down new work and hand it to an agent. It works on
desktop and phone (add it to the home screen).

It was called the Relay Console until v5. Only the name changed: the URLs (`/console/…`), the
`CCDB_CONSOLE_*` settings, the `[console]` extra and the code keep the word `console`, so an
existing setup keeps working as it is.

The JSON API under `/console/api` is the contract. The bundled web client is one consumer of it.
See [ADR-0010](adr/0010-add-an-api-first-console-on-its-own-listener.md) and, for sign-in,
[ADR-0012](adr/0012-sign-in-to-the-console-with-passkeys-by-default.md).

## On a phone

The conversation keeps its composer at the bottom while earlier messages scroll independently.
Its height follows the visible viewport when the software keyboard opens. Long drafts scroll
inside the input, leaving room to read the conversation.

The header keeps Back, Menu, a one-tap Stop while running, and **•••**. Open **•••** for
completion, AI settings, links, pinning and task details. Child-task summaries and live activity
start collapsed on mobile; tap their headings to expand them. Replying directly to a child
still requires the explicit child-reply action.

Changing lists from Menu returns to the list. Drafts and pending uploads survive returning to
a conversation and rotating the screen. Bulk completion controls appear after selecting a row.
Desktop controls retain their existing layout.

## Views

| View | Shows |
|---|---|
| **Focus** (default) | What you can do now, across every list. Importance (priority first, P0 above P1 whatever else is true; then how close the deadline is; then whose move it is) and readiness are separate axes: only work you can act on right now ranks under "Do this first" and "Then"; anything waiting for a time, a person or a job goes under "⏳ Waiting", soonest first, and moves up once the time it named has come. Each row shows the next step and why it ranks there. Work bound to a place or device (📍 home, office, work PC) is tagged; pick where you are in the bar above the list — one place or several, matched with OR — and work bound to none of them moves to "📍 Somewhere else". Set an item's place with the 📍 button in its header (pick one, Anywhere, or type a new one); add, rename and delete places under "⚙ Manage places" — a rename or delete reaches every item. Work an agent is doing, finished, parked and snoozed items are left out. Reachable with `g f` |
| **Inbox** | The ball is on you: ❓ waiting for an answer, 👀 something to review, 📋 a task only you can do, ⚠️ a failed turn |
| **Done?** | 🏁 Completion candidates: the agent believes the work is finished. Only you close an item — confirm it with Done (`e`) and it moves to Done; until then it stays listed and is never hidden by "✅ Done hidden". Reachable with `g c` |
| **Running** | A turn is in flight (or queued for a slot) |
| **To do** | Work you wrote down that no agent has yet |
| **Idle** | The agent stopped without saying whose move it is |
| **Tree** | Everything open, grouped by project, children under their parent |
| **Recent** | Every item, finished ones included, newest activity first, grouped by day. Each row names the list it lives in now, so a thread that moved from Running to Done is still easy to find |
| **Snoozed / Done** | Hidden until the snooze ends / finished — by you: an agent's marker never moves an item here |

The status chips above every list narrow it to the statuses you pick — ❓ your reply, 👀 to
review, 📋 your task, 🏁 completion candidates, and so on; several at once match with OR, "All"
clears them. Each chip counts what the current list holds, and the choice is remembered per browser.

Each thread also shows how full its context window was after its last turn — 🧠 and a percentage
on the row (amber from 70%, red from 90%), used / window tokens in the detail header — so a
thread close to compaction is visible before it happens.

## The Focus guide

A card at the top of Focus turns the ranking into advice. It is computed from data the board
already has — status, priority, deadline, lineage and the triage guesses — with no extra
model call, so it costs nothing per poll and gives the same answer twice
(`claude_discord/console/guide.py`).

- **Your next move** — the top item that fits where you are, with the reasons it ranks
  there (overdue, due within 24 h, high priority, the agent asked you, a deliverable to
  review, a step only you can do, the last turn failed), the next step and, when an agent
  stopped to hand it over, a note that answering lets the agent carry on.
- **Nothing needs you** — when no item is yours to act on now, the card says so and shows
  how much is running, waiting and awaiting a "Done?" confirmation, instead of an empty list.
- **Decide together** — two or more decisions in one family are gathered under the root
  thread, so one sitting settles them all.
- **Records out of step** (only with an [Ebi Workspace](#optional-ebi-workspace)) — a
  finished or candidate thread from the last three days whose Workspace task with the same
  text is still open, or whose project records (`status.md` / `single-actions.md` and
  `log.md`) were last changed before the conversation ended. The second check needs a
  binding a person chose; a binding guessed from the working directory is too loose to
  accuse a record of being stale.

Everything except the last part works without a Workspace; without one the board sends
`guide.drift: null` and the card leaves that section out.

## Automatic triage

Nobody fills in priority and due date for every thread, so the console asks a fast model
to: the cheapest model of the backend chosen with `/backend` — Haiku on Claude, the cheapest
"fast and affordable" model in Codex's own catalog on Codex (falling back to whichever CLI is
installed). The choice is made per call, so switching the backend switches triage too. A background pass every minute picks up to six open items with new
activity, gives the model the title and the latest human and agent messages, and stores its
answer: a priority (P0–P3), a deadline if the conversation names one, the reason, your next
step, whether you can take that step now or must wait (and for what, and until when), and
whether it can only be done in one place or on one device. The model is shown the place labels
already in use so it reuses them. Each call is one-shot and tool-less: `claude -p` with tools, hooks
and settings off, or `codex exec` ephemeral and read-only without the user's config or rules,
both in a scratch directory and without relay or console credentials in their environment.

A guess never overrides a person:

- A priority, due date or place you set wins. Setting one from the console pins it, so later
  guesses leave it alone (clearing a place means "anywhere", not "guess again"). A priority other than P2 that existed before triage counts as yours.
- A filled-in value is drawn outlined (priority) or in italics (due date), and the detail
  drawer shows the model's full guess under "🤖 AI guess", including fields you overrode.
- Whether you can act now is yours to correct too. The **⏳ Waiting / ▶ Can do now** button in
  the detail header shows what Focus believes (dashed while it is the model's guess) and flips
  it with one click; a row under "⏳ Waiting" has its own **▶ Can do now**, and the drawer has
  **🤖 Back to the AI's call**. Unlike a priority, readiness belongs to the moment: on a thread
  your call holds until the conversation moves again (a reply, a finished turn), then the
  model's fresh guess takes over, so a stale "can do now" never hides a wait that came later.
  Written-down work keeps it until you clear it. API: `PATCH /console/api/items/{id}` with
  `readiness: "now" | "waiting" | null`; the board reports `readiness`, `readiness_by: "you"`
  and the resulting `actionable`.

| Variable | Default | Meaning |
|---|---|---|
| `CCDB_CONSOLE_TRIAGE` | `1` | `0` turns automatic triage off |
| `CCDB_CONSOLE_TRIAGE_MODEL` | `haiku` | Model for Claude triage, passed to `claude --model` |
| `CCDB_CONSOLE_TRIAGE_CODEX_MODEL` | *(cheapest in the catalog)* | Model for Codex triage, passed to `codex exec -m` |
| `CCDB_CONSOLE_TRIAGE_COMMAND` | `claude` | The Claude CLI |
| `CCDB_CODEX_COMMAND` | `codex` | The Codex CLI. Triage stays off only when neither CLI is on `PATH` |

Each triage is one model call, billed like any other `claude -p` call on that machine.
On Windows a Claude CLI installed by npm (`claude.cmd`) is started as `node` + its script, never
through `cmd.exe`; a wrapper that cannot be resolved that way leaves triage idle and logs why.

## Reading a thread

Messages are rendered as Markdown (Discord flavour: headings, lists, tables, code blocks, quotes,
links, spoilers). The renderer builds DOM nodes and never parses HTML, so a message can format
itself but cannot inject markup.

Each message carries a `kind`:

| Kind | What it is | Shown |
|---|---|---|
| `human` | What a person wrote in the chat (console replies are posted by the bot, so they show as `agent` with their footer) | Always |
| `agent` | Answers, questions, errors and files from the agent | Always |
| `activity` | The relay's own bookkeeping: tool calls, thinking, session start/finish embeds, `-#` status notes, the usage footer, completion pings, rename notices and automatic continuation prompts. A console conversation records one `-#` line per finished tool call | Only with the "⚙ Activity" button in the detail header |

Scheduled reminders and API notifications using the standard notification color appear in the
normal conversation view, including cards delivered before the console was updated. A scheduled
notification posts a reminder; it does not by itself start an agent turn.

### Attaching files

Paste a file, drop it on the reply box, or pick it with 📎 next to Send — up to 10 per reply.
Each uploads at once (`POST /console/api/uploads`, the raw body, `X-Filename` the
percent-encoded name) and waits above the box: images as thumbnails, other files as a named chip.
Images (PNG, JPEG, GIF, WebP) are checked by their bytes and capped at 10 MB; any other file is
kept as it came under a bare, sanitised version of its name, up to 50 MB. The reply names the
uploads (`files`), and the agent receives each one by local path. Uploads are served back only
as downloads (`application/octet-stream`, `Content-Disposition: attachment`).

The box for writing down new work takes attachments the same way; they travel in the item's
note (`POST /console/api/items` with `files`), so an agent started from it gets the paths.

Replies and new work longer than 4,000 characters automatically upload the full text as
`message.txt`. The agent receives a short instruction to read that file, and the conversation
keeps a downloadable link. Text is never truncated. This happens before sending JSON, so large
pastes also avoid the JSON request size limit. The generated file uses one attachment slot and
the same 50 MB file limit; a failed upload keeps the text in the input box for retry.

### Previewing files

Files an agent delivers and files attached in chat threads open in place with 👁 Preview
instead of downloading, when they are text: Markdown is rendered (by the console's DOM-building
renderer, never as HTML), HTML is rendered in an iframe with an empty `sandbox` (no scripts,
forms, popups or same-origin access), CSV/TSV shows as a table, JSON pretty-printed, and other
text (logs, code, YAML, …) as wrapped monospace. Source and Download are always one click away;
a file over 2 MB shows its first 2 MB. Chat attachments are read from Discord's CDN, which the
CSP allows for `connect-src` (read-only, CORS `*`).

The open thread reloads with the board while a turn is running. Background refreshes keep
the list anchored to the row you are reading; they do not scroll back to the selected thread.

In Recent, "activity" means the thread's last message (or, for work you wrote down, its last
edit); changing priority or snooze does not move an item. Recent shows what the board already
loads (open threads plus the newest 50 archived threads per channel), so it needs no paging.

Within the other views items sort by priority (P0 first), then overdue, then due date, then whoever has
waited longest.

Status is derived from what ccdb already knows: the outcome marker on the thread title, running
turns, the slot queue and the spawn lineage. Agents do not have to do anything new. What the
console stores itself (`work_items` in the session database) is only what nothing else knows:
priority, due date, snooze, project, a parent override, a title override, and captures.

### Buttons

A bot cannot click a Discord button, so the console does not read Discord's components. Flows
that wait on a button also register the same answer in an in-process registry
(`claude_discord/message_actions.py`), and the console shows those as buttons under the message:

- restart / upgrade approval (`✅ Approve`, also offered on every "still waiting" reminder)
- choice prompts: tool permission, plan approval and other single-choice questions
- AskUserQuestion with a single answer

Multi-select questions and "Other" free text are still answered by replying. Whichever side
answers first wins; the buttons disappear on both. Registrations live in memory, so after a
restart there is nothing to press — exactly as nothing is waiting any more.

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


### Backend, model and effort

The "🤖 backend · model · effort" line above the reply box opens a picker for that conversation:
backend (Claude, Codex, local, AG-UI, pi), the models of the chosen backend (or a typed one) and
effort levels. Each choice applies from the next turn; picking a backend redraws its own models and
levels. The nav's "🤖 Default: …" opens the same picker for the setting new conversations start
with (`GET /console/api/defaults`, `POST /console/api/defaults` with `{name: backend|model|effort,
arg}`), validated like `/backend`, `/model` and `/effort`. A conversation's own choice wins over
the default.

### Running work

The nav shows "Running 2 / limit 4 · 1 queued". − / + change the limit (1–32) at once: raising
it starts waiting runs, lowering it lets running ones finish first. A limit changed here is kept
across restarts (`POST /console/api/slots` with `max`; stored in the settings table and applied
on start).

Each thread carries one-click controls for its state, on its row and in the detail header:

| State | Controls |
|---|---|
| Running | ⏸ Pause — stop this turn and mark the thread paused; ■ Stop — stop this turn |
| Paused | ▶ Resume — ask the agent to carry on from where it stopped, and clear the mark |
| Waiting for a slot | ⏫ First — start before the others waiting; ⏬ Later — let them go first |

Stop and Pause work for chat threads and console conversations alike: every run registers an
interrupt with the slot it holds (`POST /console/api/items/{id}/stop`, `/pause`, `/resume`,
`/queue` with `action: first|later`). The pause mark is stored on the item (`paused_at`).

📌 in the detail header (or `p`) pins a thread as what you are working on now. Pinned threads
open every list but Done under a highlighted "📌 Working on now" strip, newest pin first, and
bring their family with them rather than duplicating it in the tree. The pin is stored with the item
(`PATCH /console/api/items/{id}` with `pinned: true|false`), so every device sees it.

"✅ Done hidden" in the list header (or `h`) leaves finished items out of every list — Recent and
the tree included, and filtering does not bring them back. The Done list always shows them. The
choice is remembered per browser.

`j`/`k` move, `Enter` opens, `Esc` closes, `e` done, `1`–`4` priority P0–P3, `s` snooze until
tomorrow 09:00, `c` write down work (the first line is the title, further lines become a note; `Ctrl+Enter` adds it — in every text box `Enter` is a newline and `Ctrl+Enter` sends), `r` reply, `/` filter, `h` hide/show done, `p` pin, `g` then `f`/`i`/`c`/`a`/`u`/`t`/`w`/`d` switches
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
   `Ebi AI Console setup code: K7QPM-3XW9D` (single use, valid 15 minutes).
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
passkey signs out every session it opened. Only a passkey session (or the token) can add, list or remove
passkeys. Someone let in by Cloudflare Access or an identity provider cannot give themselves a
passkey that would outlive their place on the allowlist.

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

### Optional: sign in with Google, Entra ID or any OpenID Connect provider

The console can send you to your identity provider and back, with no Cloudflare in front.
Register a **web** client with the provider and use this redirect URI:
`<your origin>/console/api/auth/oidc/callback`.

```dotenv
CCDB_CONSOLE_ORIGIN=https://console.example.com      # required: the redirect URI is built from it
CCDB_CONSOLE_OIDC_ISSUER=https://accounts.google.com
CCDB_CONSOLE_OIDC_CLIENT_ID=1234-abc.apps.googleusercontent.com
CCDB_CONSOLE_OIDC_CLIENT_SECRET=...
CCDB_CONSOLE_ALLOWED_EMAILS=me@example.com           # required: who may come in
# CCDB_CONSOLE_OIDC_NAME=Google                       # button label (guessed for Google/Microsoft)
```

| Provider | Issuer | Where to create the client |
|---|---|---|
| Google | `https://accounts.google.com` | Google Cloud console → APIs & Services → Credentials → OAuth client ID → *Web application* |
| Microsoft Entra ID | `https://login.microsoftonline.com/<tenant-id>/v2.0` | Entra admin center → App registrations → *Web* platform, plus a client secret. Add the optional ID-token claim `email` |
| Okta, Auth0, Keycloak, … | the issuer shown in the provider's discovery document (copy it exactly, including any trailing `/`) | the provider's admin console |

The sign-in screen then shows **Sign in with Google** (or your label) next to the passkey
button. Set `CCDB_CONSOLE_PASSKEYS=0` if you want the provider to be the only way in.

What is checked: the authorization code flow with PKCE; `state` bound to the browser by a
short-lived cookie (a sign-in started elsewhere cannot be completed in your browser); the ID
token's signature against the issuer's published keys, its issuer, audience (and `azp` when
there are several), expiry and `nonce`; and the email against the allowlist. The provider must
also say `email_verified: true`. An email claim alone proves nothing, because on some providers
a user can set it to any address. Entra ID does not send that claim. For a **single-tenant**
Entra ID, where your admins control every address, you can waive it with
`CCDB_CONSOLE_OIDC_TRUST_UNVERIFIED_EMAIL=1`. Never waive it for a provider where users choose
their own email.

Taking an email off the allowlist, or switching to another issuer, ends its sessions at once.
A Google or Entra ID session cannot add or remove passkeys; sign in with a passkey for that. Whether you are asked for a second
factor is the provider's policy: turn on 2-step verification or passkeys for the accounts on the
allowlist.

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
| GET | `/console/api/auth/oidc/start` | *(no sign-in)* Redirect to the OIDC provider (only when configured) |
| GET | `/console/api/auth/oidc/callback` | *(no sign-in)* Where the provider sends you back; signs in and redirects to `/` |
| POST | `/console/api/auth/setup-code` | *(no sign-in)* Log a new setup code; only while no passkey exists |
| POST | `/console/api/auth/logout` | End this session |
| GET | `/console/api/passkeys` | Registered passkeys (names and dates, never key material) |
| POST | `/console/api/passkeys/invite` | A single-use code for adding another device |
| DELETE | `/console/api/passkeys/{id}` | Remove a passkey and its sessions |
| GET | `/console/api/board` | Every item, sorted for triage, plus slot usage and the Focus `guide` |
| GET | `/console/api/usage` | Per backend (or pool profile): `available`, `unavailable_until`, and `windows[]` of `{type, utilization, resets_at, status, reset}` |
| GET | `/console/api/items/{id}/messages?limit=50` | The conversation's recent messages (max 100), each with `kind`, `embeds` and `attachments` |
| POST | `/console/api/items` | Write down work: `{title, note?, priority?, due_at?, parent_id?, project?, start?}`. With `start: true` it is handed to an agent in the same request; if that fails the item is kept and the response carries `start_error` |
| PATCH | `/console/api/items/{id}` | Change `title`, `note`, `priority` (0–3), `due_at`, `snoozed_until`, `project`, `parent_id`, `state`, `readiness` (`now`, `waiting`, `null` = the AI's guess) |
| POST | `/console/api/items/{id}/reply` | `{text}` — continues the session, queued behind a running turn |
| POST | `/console/api/items/{id}/done` | Mark done (also sets the ✅ marker on the thread) |
| POST | `/console/api/items/{id}/reopen` | Clear the done/someday verdict |
| GET / POST / PATCH / DELETE | `/console/api/places` | The managed list of places with open-item counts; add `{name}`, rename `{from, name}` (every item follows), delete `{name}` (items become doable anywhere) |
| GET | `/console/api/items/{id}/live` | A console conversation's running turn: status, tool activity, the answer so far, whether it can be stopped |
| POST | `/console/api/items/{id}/messages/{message_id}/actions/{action_id}` | Press a button listed in a message's `actions` (404 once it is answered) |
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
- The agent marks the outcome with the usual `/api/threads/$DISCORD_THREAD_ID/candidate` (and
  `waiting`, `review`, `action`); the board shows it like a thread's marker. A candidate is only
  a proposal: the item reaches Done when you mark it done.
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

### Parent conversations and delegated work

Clicking either a parent or child in a list opens the root parent conversation.
Every view groups matching family members under their parent, including ancestors for context.
Each family stays at its first matching position; filters still limit which children appear.
Families have a shared rounded border and a **Group** heading with the child-thread count.
Parent/child labels and connecting lines distinguish the roles; deeper descendants also name
their immediate parent. When filtering or folding hides children, the heading shows how many
are visible out of the total. Standalone threads remain outside these group cards.
The detail view shows a collapsible **Delegated work** panel above the conversation. Child
and grandchild cards show status and a short latest agent report, including finished tasks.
The totals distinguish running work, work needing the parent's attention, and completed work.
AI-suggested next steps are labelled as suggestions when no recent report is available.

Click a child card to inspect its conversation. **Back to parent** returns to its immediate
parent; **Main conversation** opens the root. Child replies are hidden by default: use
**Reply directly to this child** when direct intervention is intended. Normal requests and
decisions belong in the parent conversation, which coordinates its children. Direct child reply
mode is clearly labelled and resets when leaving that child; its draft remains intact.

Finishing a parent from the Console completes its descendants first, including grandchildren.
The button shows the full completion count. A failed descendant leaves its ancestors open.
The immediate Undo reopens only the items completed by that action. This is the web client's
family action; the individual `/items/{id}/done` API still changes only that item.

### Selecting and completing multiple threads

Use a row's checkbox (or Ctrl/Cmd-click) to toggle an individual selection. Click a row and
Shift-click another to select the inclusive range in the current displayed order; Shift-click
also works on checkboxes. The toolbar shows selected and total completion counts, including
all descendants of selected parents. Click **Complete selected** to finish that snapshot.

Selections use thread IDs, so polling and reordering do not substitute other targets. A changed
row order resets the range anchor. Filtering away selected rows clears those selections with
a notice. **Clear selection** starts over. Failures remain listed by thread with a retry
button; successful threads are removed from the selection. Failed targets remain explicit in
the error list even if a filter hides their rows. Completion runs once per ID, descendants
before parents, and does not affect unrelated families.

## Finding earlier work

The instant filter searches board metadata. **Search messages** next to it also searches
all saved Console messages (including messages older than the open thread's history window)
and available local Claude transcripts. Results show a matching excerpt; selecting one opens
the original conversation, including completed work. This search uses no AI calls.

Transcript results must resolve to a Console conversation or a Discord thread under a watched
channel. An older archived thread found this way is kept in the board's temporary discovery
cache (up to 50 threads), so opening it survives the next refresh. Unrelated CLI transcripts
are not exposed. Discord messages without a local Claude transcript, such as unrecorded chat
or another backend's Discord history, are not covered; Console-owned conversations are covered
regardless of backend. Results are capped at 50; use a more specific phrase for broad searches.
A timed-out transcript scan shows a partial-results notice. `GET /console/api/search?q=…`
is authenticated and accepts a literal query of 1–500 characters.

Threads deleted from Discord can still be found when they were exported first as
`<thread_id>.jsonl` (a `{"_meta": {"id", "name"}}` line followed by raw Discord message
objects). Point `CCDB_CONSOLE_THREAD_ARCHIVE` at that directory: search then also matches
export titles and messages, and a transcript or summary hit on a deleted thread falls back
to its export. Such results are marked as deleted; selecting one shows the conversation read
only inside the search dialog (`GET /console/api/archive/<thread_id>`, authenticated, last 500
messages, bookkeeping hidden). Nothing can be resumed from an export.

Selected rows have a blue outline and a **Selected** marker. Unread rows use a green dot and
bold title. Pinning keeps its pin symbol while selection keeps its own outline. Background
refreshes preserve the visible row and its offset, including a thread repeated in a tree and
its pinned section; only deliberate navigation reveals the selected row.

## Copying file contents

Text attachments have **Copy contents to clipboard** beside Preview/Download and in the
preview toolbar. Copy uses the complete original source text, including everything past the
2 MB preview cutoff; it does not copy the rendered HTML, formatted JSON or only the visible
rows of a CSV. Copy shows immediate progress and a success or error message and remains
retryable. Browser clipboard permission and a secure browser context are required. Binary
files retain their existing download actions.

Console replies relayed into Discord which exceed Discord's 2,000-character message limit
are posted as one UTF-8 `message.txt` attachment. The agent receives the original complete
prompt. A long URL is never split across two Discord messages. A long first line in a new
capture is kept in its note even when its title is shortened.

## Optional Ebi Workspace

When Ebi Workspace has been explicitly configured and contains `02_Contexts`, Console shows
an **Ebi Workspace** navigation entry and a context/project picker. No guessed default vault
or installed plugin cache enables this integration. Detection order:

1. `EBI_WORKSPACE_NOTES_FOLDER` or `CLAUDE_WORKSPACE_NOTES_FOLDER` environment variable.
2. `${XDG_CONFIG_HOME:-~/.config}/ebi-workspace/notes-folder` (absolute path in the file).
3. Those same variables in `~/.claude/settings.local.json` under `env`.

The Workspace view reads project `status.md` next actions and context `single-actions.md`,
including nested contexts. Add, rename or check a task to update its original Markdown;
Console stores only the association (`workspace_path`), never a second task list. Updates
preserve unrelated sections, check a content revision to refuse stale edits, replace files
atomically, and record the change in the project's/context's log plus a link in the daily note.
Archived, someday, hidden and symlinked directories are excluded.

**Run with AI** starts a Console conversation associated with the task's context/project.
Starting work does not mark the task complete. The agent receives the paths of the applicable
Workspace instructions, source tasks and log and is instructed to read them and record the
outcome. Replies and child work retain that association. **Start work here** chooses the
context for the next capture; an existing conversation's picker can set its association too.

**Finishing follows into the notes.** When a human marks an associated item done (the
✅ button, `e`, or bulk complete), Console checks off the one open task whose text equals the
item's note or title — the task a **Run with AI** conversation came from — and records it in
the log and daily note. With no such task it records the finished conversation instead; a task
the agent already checked is left alone, and an ambiguous match checks nothing. Reopen (or
Undo) unchecks a task finished this way. The response's `workspace` field reports the result,
and a failed update never blocks the finish. Finishing the open conversation also returns to
the list, as the back button would, so a phone needs one tap per task.

**Workspace work in Focus.** With the Ebi Workspace plugin installed, the 🎯 Focus view also
shows what the Workspace says to do now, ranked by the plugin's own goodmorning scoring
(priority × due date, stale projects, recurrence, follow-up dates): the first next action of each
active project, due follow-ups, and single actions. Each row can be checked off, re-dated (the
`📅` date is written into the line, before any `🔁` rule) or run with AI, with the same
revision checks and log/daily-note records as the Workspace view. Today's `## 今日のプラン`
from the daily note appears as a folded card, and waiting-for entries whose follow-up date has
not come are listed beside the waiting threads. Plain `- item` bullets under a project's next
actions count as open tasks, as they do for goodmorning; checking one off writes `- [x] item`.

Console does not copy the plugin's scoring. It runs the installed plugin's
`scripts/workspace_scanner.py` in a separate isolated Python process (`python -I`) at most once
a minute and after every Console write. The plugin is found through
`EBI_WORKSPACE_PLUGIN_DIR` or Claude Code's `~/.claude/plugins/installed_plugins.json`;
without it this part of Focus is simply absent (`GET /console/api/workspace/plan` answers
`{"available": false, "reason": "plugin"}`).

**Where a thread belongs is worked out, not asked for.** Choosing a context is not a step
the human has to take: a new thread starts unresolved, the Console binds it in the background,
and a person only corrects it. Every binding records how it was decided (`workspace_source`)
and why (`workspace_reason`), and a weaker source never replaces a stronger one:

| Source | Badge | Meaning |
|---|---|---|
| `pinned` | 👤 | A person chose it — in the picker, by confirming a guess, by starting from a Workspace task, or by choosing "No Workspace" (pinned without a path, which stops all guesses). |
| `recorded` | ✍️ | The session wrote that project's records. |
| `inferred` | 🤖 | Guessed from the conversation. |
| `heuristic` | 🧭 | An unambiguous working directory inside the Workspace (`cwd`), an explicitly labelled repository in `overview.md` (`repository`), a unique matching project name (`project`), or the parent's binding (`parent`). |

List rows show the badge with its reason on hover; the detail drawer adds ❔ for unresolved
threads, **✓ Confirm** to pin a guess, and **Change** — a searchable picker with recently used
entries, "No Workspace" and "Automatic" (which clears the binding so evidence decides again).
The capture box's picker applies to the next capture only and is never remembered.

`recorded` comes from the session transcript the CLI keeps on disk (Claude Code
`projects/*/<session>.jsonl`, Codex `sessions/**/rollout-*-<session>.jsonl` in the default
home): once a minute the Console reads only what sessions used in the last 14 days appended
since the last pass, and binds a thread to the entry whose `log.md`, `status.md`, `overview.md`
or `single-actions.md` it last wrote — with a write tool, or a shell command that names the
file with a write such as `>>`, `tee` or `sed -i`. Reading a record does not count.

`inferred` costs no extra model call: when the background triage looks at a thread that no
person decided and whose session recorded nothing, its prompt also lists the Workspace
entries (path and the first prose line of each `overview.md` / `AGENTS.md`) and asks which
one the conversation is about. Only an answer that names a listed entry with confidence of at
least 0.7 binds the thread; small talk and one-off questions stay unresolved. A labelled
repository is written in `overview.md` as `repository: /absolute/path`,
`working_dir: /absolute/path`, or `- リポジトリ: ~/path`. Overview is available alongside
tasks; task changes are immediately visible in Obsidian because both use the same files.

**The project beside the conversation.** A bound thread's detail shows a folded 🗂️ section
with the entry's 📍 current state (`## 現在地`), open next actions to check off in place,
`## 連絡待ち`, the latest `log.md` entries and the other threads bound to the same entry. It reads
the Markdown through `GET /console/api/workspace/context?path=…` and refreshes every 30 seconds,
so an agent's edit to `status.md` appears while you watch. The `[Ebi Workspace context]` block the
Console appends to a prompt is folded in the conversation under a one-line note naming the
entry, so it is visible which context each message carried, including after a re-binding.

**Filter and group by context.** With a Workspace, every list starts with 🗂️ chips for the
top-level contexts it holds (plus ❔ for unresolved threads), combined with OR and remembered per
browser. **≡ By project** lists a bucket view under one heading per bound entry, unresolved last.

Through the API, `PATCH /console/api/items/{id}` with `workspace_path` pins it; with
`workspace_path: null` it returns to automatic, unless `workspace_source: "pinned"` is sent too,
which means "No Workspace". Clients cannot set any other source.

**Notes beyond the four Workspace files.** `GET /console/api/workspace/note?path=…` returns
any Markdown note under the vault root (text, revision, modified time) and
`GET /console/api/workspace/file?path=…` an image or PDF a note embeds (served with
`nosniff` and a sandboxing CSP; SVG is never served). Paths are vault-relative; `..`,
backslashes, NUL, symbolic links and hidden folders (`.obsidian`, `.git`, `.trash` …) are refused.

The owner decides what the Console must never show in
`${XDG_CONFIG_HOME:-~/.config}/ebi-workspace/console-exclude.txt` — editable by hand or from the
Workspace panel (**🚫 Hidden notes**, `GET`/`PUT /console/api/workspace/exclude`). One pattern per
line: `folder/` matches a folder anywhere, `02_Contexts/x/` a folder from the vault root,
`*.private.md` a file name anywhere, `01_Daily/2025-*` a path from the root. An excluded note
answers exactly like a missing one, and is never listed, searched or linked.

**Note viewer.** `#note/<vault path>` opens any visible note full screen, rendered the way
Obsidian shows it: front matter as a folded properties table, `[[wikilinks]]` (with `#heading`
and `|alias`), `![[image]]` and one level of `![[note]]` embeds, `> [!type]` callouts, task
checkboxes, tables and code. Links resolve like Obsidian — a path from the vault root, then
relative to the note, then a unique file name, then the same-name file closest to the note —
on the server (`links` in the note response); unresolved links are shown dashed. Below the note,
**🔗 Linked from** lists backlinks (`GET /console/api/workspace/backlinks`, cached per file
modification time). Browser back/forward walk the notes you followed; Escape or **Close**
returns to the list. The Workspace panel's **Overview** and **status.md** open here.

**Finding notes.** `Ctrl+O` (⌘O) searches note names and `Ctrl+Shift+F` (⌘⇧F) searches inside
notes (every term required, first matching line shown); Tab switches between the two, ↑/↓ and
Enter open the hit, and **📝 Find a note** in the navigation does the same on a phone
(`GET /console/api/workspace/notes?q=…`, `GET /console/api/workspace/search?q=…`).

**Notes named in conversations.** In every message, vault paths (recognised by the numbered
top-level folders of an Ebi Workspace vault, e.g. `…/02_Contexts/x/log.md`, also as inline
code with spaces) and `[[wikilinks]]` become links into the viewer; wikilinks resolve through
`GET /console/api/workspace/resolve?link=…`. Code blocks are left alone, and a hidden or missing
note opens as "not found".

**Editing a note.** **✏️ Edit** in the viewer shows the Markdown source beside a live preview
(tabs on a phone); `Ctrl+S` / ⌘S or **💾 Save** writes it with `PUT /console/api/workspace/note`
(`path`, `revision`, `text`, optional `log`). The write is atomic and moves `最終更新:` and a
front-matter `updated:` to today. If an agent or Obsidian changed the file since the edit began,
the server answers 409 with the note as it is now and the viewer shows a line diff: load the
version on disk, overwrite on purpose, or go back to editing. **Also record in the work log** adds a
line to the enclosing Workspace entry's `log.md` and the daily note. Leaving with unsaved changes
asks first.

**Notes a thread changed.** Under a conversation, **📝 Notes this thread changed** (folded, read
when opened) lists the vault notes its session wrote — found in the session transcript like the
✍️ binding, widened to any note under a numbered top-level vault folder — each with a diff from
the vault's last recorded revision before the thread started to the file on disk now (so it also
includes other edits made meanwhile, which the panel says). New notes show in full; excluded,
hidden and unchanged notes are left out. `GET /console/api/items/{id}/notes`.

The renderer is markdown-it (HTML in notes disabled) with small Obsidian plugins, sanitised by
DOMPurify into a DOM fragment. Both are vendored single files (`vendor-*.min.js`, licenses in
`static/THIRD_PARTY_LICENSES.md`) and are loaded only when the first note is opened, so the
Console itself still has no build step and no runtime dependency.

Authenticated API routes:

- `GET /console/api/workspace` — availability and contexts/projects/tasks with revisions.
- `GET /console/api/workspace/document?path=…&name=…` — a supported Workspace document.
- `POST /console/api/workspace/tasks` — add a task (`path`, `revision`, `text`).
- `PATCH /console/api/workspace/tasks` — edit a task (`path`, `revision`, `line`, `text`/`done`).
- `POST /console/api/workspace/run` — start the current open task (`path`, `revision`, `line`).

Mutations use the same authentication and CSRF checks as other Console actions. Workspace
paths are relative to `02_Contexts`; arbitrary files and traversal are refused. A stale
revision returns 409 so an Obsidian edit cannot be silently overwritten.

### Customer reply drafts stay pending

A drafted customer answer is still human work: reviewing and sending it are separate from
creating the text or attachment. The agent's finishing instructions require review/action
until delivery is evidenced and prohibit autonomous completion of Teams Channel Exporter
threads, including later turns. Humans can explicitly complete them from the Console.

The inbox classifier receives the current request as well as the final reply. Recognized
Teams Channel Exporter request envelopes bypass model classification and stay waiting;
other customer drafts are classified with an explicit instruction not to infer delivery.
This preserves the exporter's final-reply-body-only format. Existing done markers are not
changed retroactively, and the request-envelope check does not persist provenance across
turns; the agent instruction covers that case.

### Discovering Workspace

When no Workspace is configured, Console offers a dismissible introduction with an official
purchase link. The menu entry remains available after dismissal. Configured installations
show their tasks instead. The introduction makes no external requests until the user opens
the sales link; it does not install or buy anything automatically.

### Thread links

Use **Copy thread link** in the conversation header to copy a shareable Console URL.
The URL contains the work-item ID, without authentication parameters. Paste it into
a message, or write `[thread title](/#focus/t123)` using the actual thread ID.
Same-instance links open inside the current Console tab; browser Back/Forward work.
A child link opens that child's family view with direct replies still opt-in.
External links keep opening in a new tab. Missing or inaccessible targets show a
notice rather than silently opening a different conversation. Links do not grant
access: the recipient still needs access to this Console instance.

Agents receive the link format and the existing `/api/search?q=…&body=1` lookup
in their run context, with instructions to use verified IDs only. That search
can find stored session summaries and local Claude transcripts; it does not make
all external message histories searchable.
