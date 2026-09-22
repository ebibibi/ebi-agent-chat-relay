# Choose an agent backend

Ebi Agent Chat Relay separates the **frontend** where a person talks from the **backend** that
does the work. Discord and Microsoft Teams use the same session ledger, coordination layer, and
backend factory. Selecting a backend therefore does not require a different bot or a different
Teams app.

## Supported combinations

| Backend | Transport | Authentication | Best fit |
|---|---|---|---|
| Claude Code | local `claude` CLI | the CLI's existing login | Claude-native coding workflows |
| OpenAI Codex | local `codex` CLI | the CLI's existing login | Codex coding and review workflows |
| Local | local `codex` CLI to an OpenAI-compatible `/v1/responses` endpoint | none by default | data that should stay on a controlled network |
| AG-UI | HTTP request plus JSON server-sent events | optional bearer token | custom and hosted agents that implement AG-UI |
| pi | local `pi` CLI | the CLI's own login or an API key | one agent over many providers, including subscriptions and local servers |

All five work from both Discord and Microsoft Teams. The frontend controls message rendering,
buttons, files, and rate limits; the backend controls model execution and streamed events.

## Select a backend

Set the default before startup:

```dotenv
CCDB_BACKEND=codex
```

On Discord, switch an individual conversation without restarting:

```text
/backend claude
/backend codex
/backend local
/backend agui
/backend pi
```

These are Discord slash commands, and a conversation override is persisted in SQLite so it survives
a process restart. The normal Teams queue integration in v4 does not dispatch the text-command
router yet; Teams conversations use the configured/global backend. Set `CCDB_BACKEND` before
startup or change the global setting from the Discord administration surface when both frontends
run together.

Use `/model` and `/effort` to inspect or change backend-specific choices. Each backend remembers
its own model and reasoning setting.

## Claude Code and Codex

Install and authenticate the official CLI on the private session host before starting ccdb. The
relay reuses that CLI login; it does not copy a subscription token into Discord or Teams.

```bash
claude --version
codex --version
ccdb start
```

The default backend remains `claude`, so upgrading an existing deployment does not change which
agent receives the next turn.

## Local

The local backend drives an OpenAI-compatible `/v1/responses` endpoint through a ccdb-owned Codex
configuration. It disables the measured startup update check and analytics rather than assuming
that “logged out” means “offline.”

```dotenv
CCDB_LOCAL_BASE_URL=http://127.0.0.1:11434/v1
```

The model is not an environment setting: choose it at runtime with `/ollama use` (or `/model`), and
`/ollama list` marks the selection with `▶`.

Read [Local-model backend](local-backend.md) before using this for sensitive data. The guard is a
configuration control, not an operating-system egress firewall, and should be re-measured after
Codex CLI upgrades.

## AG-UI

Install the optional HTTP dependency and configure the exact run endpoint:

```bash
uv sync --extra agui
```

```dotenv
CCDB_AGUI_URL=https://agent.example.com/run
CCDB_AGUI_TOKEN=replace-with-a-dedicated-token
```

Then set `CCDB_BACKEND=agui`, or enter `/backend agui` in Discord. Whichever frontend supplies the
turn also supplies a stable AG-UI `threadId`, so the remote endpoint can preserve its own
conversation state.

See [AG-UI backend](agui-backend.md) for the event mapping, security boundary, and intentionally
unsupported protocol features.

## pi

[pi](https://github.com/earendil-works/pi) is a terminal coding agent that normalises Anthropic,
OpenAI, Google, GitHub Copilot and OpenAI-compatible local servers behind one CLI, and reads
`AGENTS.md` and skills on its own.

```bash
npm install -g @earendil-works/pi-coding-agent
pi          # then /login, once, to authorise a provider
```

```dotenv
CCDB_PI_ALLOW_UNSANDBOXED=1
```

That opt-in is required, and ccdb refuses to spawn `pi` without it. pi documents that it has no
built-in sandbox and shows no trust prompt in its non-interactive modes, so there is no setting
that corresponds to the approval loop Claude Code offers: the tool allowlist passed through
`CCDB_ALLOWED_TOOLS` is the only restriction that applies. Isolation has to come from the host —
a container, a VM, or the worktree discipline every session prompt already carries.

Project-local `.pi/` settings, skills and extensions are ignored unless
`CCDB_PI_APPROVE_PROJECT=1` is also set, so a repository ccdb checks out cannot reconfigure the
agent about to run inside it.

Select a provider and model together, because pi resolves both from one value:

```text
/model anthropic/claude-sonnet
/effort high
```

`/effort` maps to pi's thinking level, which includes an explicit `off`.

See [pi backend](pi-backend.md) for the measured CLI contract and the event mapping.

## Mixing frontends and backends

Backend resolution belongs to the shared session layer, not the platform implementation. In v4,
Discord can set per-conversation overrides; Teams consumes the configured/global choice. A
deployment can therefore run, for example:

- a Discord thread on Claude Code;
- another Discord thread on a local model;
- Teams conversations on the configured Codex default; or
- Teams conversations on an internal AG-UI agent when AG-UI is the configured/global backend.

The same AI Lounge, claims, collision detection, worktree rules, and session persistence cover all
of them. Frontend identity is stored with each session, so a Teams result cannot accidentally be
posted into a Discord thread with a numerically similar key.

## Security boundary

Every selected backend receives that conversation's prompts and attachments. Treat a remote AG-UI
endpoint or local model host as part of the data-processing path. A customer-tenant Teams app does
not by itself keep data inside that tenant: messages still travel through Bot Framework, the public
receiver, the queue, the private session host, and the selected backend. Deploy the complete stack
inside the required boundary when the contract requires that literal property.
