# Ebi Agent Chat Relay

[![CI](https://github.com/ebibibi/ebi-agent-chat-relay/actions/workflows/ci.yml/badge.svg)](https://github.com/ebibibi/ebi-agent-chat-relay/actions/workflows/ci.yml)
[![CodeQL](https://github.com/ebibibi/ebi-agent-chat-relay/actions/workflows/codeql.yml/badge.svg)](https://github.com/ebibibi/ebi-agent-chat-relay/actions/workflows/codeql.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![GitHub stars](https://img.shields.io/github/stars/ebibibi/ebi-agent-chat-relay?style=social)](https://github.com/ebibibi/ebi-agent-chat-relay/stargazers)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**One cockpit for all your coding agents.** Run Claude Code, OpenAI Codex, local models, pi and
AG-UI agents side by side — and talk to them wherever you like: a dedicated web console on your
desktop or phone (with Entra ID sign-in), Discord, or Microsoft Teams. Let a strong model manage,
hand the legwork to cheaper ones, and step in only when a decision is actually yours.

**[日本語](docs/ja/README.md)** | **[简体中文](docs/zh-CN/README.md)** | **[한국어](docs/ko/README.md)** | **[Español](docs/es/README.md)** | **[Português](docs/pt-BR/README.md)** | **[Français](docs/fr/README.md)**

![Ebi AI Console: a manager agent on Claude Opus coordinating child agents on Codex and a local model](docs/images/readme/console-family.png)

<sub>Every screenshot in this README uses fictional sample data. Regenerate them with
[`scripts/readme_screenshots.cjs`](scripts/readme_screenshots.cjs).</sub>

## Why people use it

| | |
|---|---|
| 🖥️ **A GUI built for supervising agents** | The Ebi AI Console shows every agent thread on one screen, sorted by *whose move it is*. It tells you what to do next and lets you reply, attach files, switch models, pause or stop — without opening a terminal. |
| 📱 **Multi-frontend** | Discord, Microsoft Teams or the dedicated web console — use one, or all of them at once from one process. Bring the relay to your team's Teams tenant and sign in to the console with Entra ID, Google, any OIDC provider or passkeys. |
| 🔀 **Multi-harness** | Claude Code, OpenAI Codex, a local model, the pi CLI or any AG-UI agent — pick one per conversation and switch mid-thread. The conversation is handed over, not lost. |
| 💸 **Multi-agent, cost-aware** | A manager agent on your best model delegates to child agents on cheaper or flat-rate models. Triage runs on the cheapest model you have. Subscriptions you already pay for do the work. |
| 🛡️ **Parallel without collisions** | Every session works in its own git worktree, sees what the others are doing, and claims what it touches. Ten agents, one repository, no clobbered work. |

---

<a id="whats-new-in-v5"></a>

## 🖥️ The Ebi AI Console — supervise agents, don't babysit terminals

![Focus view: the console picks your next move and shows the agent's drafted reply](docs/images/readme/console-focus.png)

Running several agents is easy; keeping track of them is the hard part. The console answers one
question first: **"what do I do now?"**

- **Focus view** — a cheap, tool-less model reads each thread with new activity and fills in
  priority, due date, the next step and whether it needs a particular place (📍 office, home).
  Your next move is pinned at the top. Anything a person sets by hand always wins.
- **Sorted by whose move it is** — ❓ waiting for your answer, 👀 to review, 📋 your task,
  ⚠️ failed, running, idle, done. Agents report outcomes through the relay, so the board stays
  accurate without extra prompting.
- **Act in place** — reply with files attached; preview images, Markdown, HTML, CSV and logs inline;
  autocomplete slash commands and skills; ⏸ pause, ■ stop and ▶ resume running work.
- **See the cost before it bites** — 5-hour and weekly quota meters for Claude and Codex in the
  header, and every thread shows how full its context window is (🧠 41%).
- **Capture work instantly** — write down a task and an agent starts on it right away, in a
  conversation the console owns (no chat app needed).
- **Search, share, finish in bulk** — search inside conversations, copy thread links, and complete
  several threads at once (a parent completes its children too).

<img src="docs/images/readme/console-mobile.png" alt="The console on a phone" width="260" align="right">

**On your phone too.** Install the console to your home screen; the mobile layout keeps the
composer within reach. Check on your agents from the train, answer the one question that blocks
them, and let them carry on.

**Secure by default.** The console never runs unauthenticated: passkeys (WebAuthn) out of the box,
plus optional sign-in with Microsoft Entra ID, Google or any OIDC provider, Cloudflare Access and an
email allowlist. The JSON API under
`/console/api` is the contract; the bundled web client is one consumer.

The console is optional — nothing listens unless `CCDB_CONSOLE_PORT` is set — and it can also be your
only frontend: no Discord account needed (see the [Quick start](#-quick-start--the-console-in-2-minutes)). Full guide: [docs/console.md](docs/console.md).

<br clear="right">

---

<a id="whats-new-in-v4"></a>

## 📱 Multi-frontend — talk to your agents where you already are

Where you talk and which agent works are two independent choices. Any frontend works with any
agent, and one relay process can serve several frontends at once
(`CCDB_FRONTENDS=discord,teams,console`) over the same sessions, scheduler and API.

| Frontend × backend | Claude Code | OpenAI Codex | Local (Ollama etc.) | pi | AG-UI |
|---|:---:|:---:|:---:|:---:|:---:|
| **Ebi AI Console** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Discord** | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Microsoft Teams** | ✅ | ✅ | ✅ | ✅ | ✅ |

- **Ebi AI Console** — the dedicated GUI shown above, for desktop and phone. It can be your only
  frontend: no chat account needed.
- **Discord** — one thread per session, emoji status reactions, slash commands. The fastest way to
  get going on a personal server.
- **Microsoft Teams** — bring agents into your organization's Microsoft 365 tenant. A small public
  receiver accepts Bot Framework activities, and the private session host pulls them outbound-only,
  so the machine that runs your agents never opens an inbound port.
  [Setup guide](docs/teams-setup.md) · [security model](docs/teams-relay.md).
- **Entra ID sign-in** — the console signs in with Microsoft Entra ID (or Google, any OpenID Connect
  provider, Cloudflare Access) on top of passkeys, limited to an allowlist of addresses.
  [Console authentication](docs/console.md#optional-sign-in-with-google-entra-id-or-any-openid-connect-provider).

---

## 🔀 Multi-harness — one surface, every agent CLI

The relay drives the **official CLIs** as subprocesses, so every agent keeps its own tools, skills,
memory and project instructions. You bring your existing logins; the relay never sees an API key.

- **Switch per conversation** — `/backend`, `/model` and `/effort` (or the picker above the reply
  box) change the agent from the next turn. Model lists are discovered live, so a model released
  this morning is selectable without upgrading the relay.
- **Hand over, don't restart** — session IDs are not portable between CLIs, so when you switch,
  the relay hands the conversation transcript to the new agent and carries on.
- **Local when it matters** — the `local` backend runs Codex against a model on your own hardware
  and refuses to start if the CLI would still phone home. `/ollama` manages the models.
- **Ask outside, safely** — `/ask` sends one anonymized, isolated question to an external model.

Details: [backend guide](docs/backends.md) · [local backend](docs/local-backend.md) ·
[pi](docs/pi-backend.md) · [AG-UI](docs/agui-backend.md) · [escalation](docs/escalation.md).

---

## 💸 Multi-agent, cost-aware

Not every task needs your most expensive model. The relay lets you decide **which model thinks and
which model types**:

- **Manager and children are configured separately.** A parent conversation on, say, Claude Opus
  breaks the work down and delegates it to child threads on Codex, a smaller Claude model or a
  local model. Set child defaults globally or per parent, right from the console.
- **Delegated work stays visible.** The parent shows a *Delegated work* panel with each child's
  status and latest report. You talk to the manager; it coordinates the children.
- **Spread the load across subscriptions.** Claude and Codex have separate quotas. Running the
  manager on one and the workers on the other uses both flat-rate plans in parallel instead of
  exhausting one. [Account pools](docs/account-pools.md) rotate several logins per backend on top.
- **Housekeeping on the cheapest model.** Console triage uses the cheapest model of your backend
  (Haiku on Claude, the cheapest catalog model on Codex), one-shot and without tools.
- **Pin scheduled jobs to a model.** Nightly audits and digests can run on a small model while
  interactive work keeps the big one.
- **A concurrency limit you can change live** keeps the number of running agents — and the bill —
  where you want it.

```text
Ship storefront v2.0              Claude Opus 5.5     manager: plans, reviews, asks you
 ├─ Implement checkout flow       Codex gpt-6.1-sol   running
 ├─ Write E2E tests               Codex gpt-6.1-sol   running
 ├─ Security review of PR #128    Codex gpt-6.1-sol   done
 └─ Update API reference          Local gpt-oss:120b  waiting for your review
```

---

## 🛡️ Parallel sessions without fear

Each conversation is an isolated, persistent agent session. The relay injects coordination into
every one of them, regardless of backend:

1. **Worktree isolation** — every session is told to create a git worktree and never touch the
   main working directory.
2. **Active session registry** — sessions can see who else is running and where.
3. **AI Lounge** — a shared breakroom where sessions announce intent before acting, and which they
   check before disruptive operations such as restarts or force pushes.
4. **Resource claims** — a session claims the repo, issue or file it is about to touch; a second
   session is turned away instead of duplicating the work.

```
Thread A (feature)    ──→  Claude Code  (worktree-A)  ─┐
Thread B (PR review)  ──→  OpenAI Codex (worktree-B)   ├─→  #ai-lounge
Thread C (docs)       ──→  local model  (worktree-C)  ─┘    "A: auth refactor in progress"
                                                             "B: PR #42 review done (codex)"
                                                             "C: updating README"
```

Plus: a **scheduler** for recurring agent jobs, **CI waits** that free the session slot until a
pipeline finishes, **startup resume** after restarts, a **REST API** for spawning and messaging
sessions from scripts and CI, and **execution environments** (bubblewrap, containers, SSH) that put
an OS boundary around the agent. See [What You Can Do](docs/guide/features.md).

---

## ⚡ Quick start — the console in 2 minutes

No Discord, no Teams, no bot token: just the web console. You need Python 3.12+,
[uv](https://docs.astral.sh/uv/) and at least one agent CLI you are already logged in to
(`claude`, `codex`, …).

```bash
git clone https://github.com/ebibibi/ebi-agent-chat-relay.git && cd ebi-agent-chat-relay
uv sync --extra console
mkdir -p data
printf 'CCDB_FRONTENDS=console\nAPI_PORT=8099\nCCDB_CONSOLE_PORT=8100\n' > .env
uv run ccdb start
```

Open <http://localhost:8100>, enter the one-time setup code printed in the log, register a passkey,
and write down your first task. Want Discord too? Run `uv run ccdb setup` — the wizard lists your
channels and writes `.env` for you. Full walkthrough: **[Getting Started](docs/guide/getting-started.md)**.

---

## 📚 Documentation

| Guide | What's inside |
|---|---|
| **[Getting Started](docs/guide/getting-started.md)** | Discord bot setup, the setup wizard, systemd, custom Cogs, configuration, permission modes, Microsoft Teams |
| **[What You Can Do](docs/guide/features.md)** | Parallel sessions, AI Lounge, claims, session-to-session relay, ingest API, backend switching, account pools, sandboxes |
| **[Feature Reference](docs/guide/reference.md)** | Every feature at a glance — chat, coordination, scheduler, CI/CD, sessions, security |
| **[Automation, Scheduler & REST API](docs/guide/automation.md)** | GitHub Actions recipes, scheduled tasks, auto-upgrade, the REST API |
| **[Architecture & Background](docs/guide/architecture.md)** | How it fits together, design philosophy, testing, how this project was built |
| [Ebi AI Console](docs/console.md) · [Backends](docs/backends.md) · [Teams setup](docs/teams-setup.md) | Deep dives |
| [Security](docs/SECURITY.md) · [Execution environments](docs/execution-environments.md) · [ADRs](docs/adr/) | Why things are the way they are |

---

## 🤝 Contributing — humans and agents welcome

This project is **written by AI agents and directed by a human** — the whole codebase, tests and docs came from Claude Code ([how](docs/guide/architecture.md#how-this-project-was-built)) — so it is a good place to try
contributing with your own Claude Code or Codex. PRs from humans and from agents are equally welcome.

- 🌱 **First PR?** Pick an issue labelled
  [`good first issue`](https://github.com/ebibibi/ebi-agent-chat-relay/labels/good%20first%20issue)
  or [`help wanted`](https://github.com/ebibibi/ebi-agent-chat-relay/labels/help%20wanted).
- 🌍 **Translations** of the README and guides (Chinese, Korean, Spanish, Portuguese, French) are
  always appreciated.
- 🐛 **Run it on macOS or Windows?** Production runs on Linux; your
  [bug reports](https://github.com/ebibibi/ebi-agent-chat-relay/issues/new?template=bug_report.md) are gold.
- 💡 **Ideas** go in a [feature request](https://github.com/ebibibi/ebi-agent-chat-relay/issues/new?template=feature_request.md).

Setup is one command (`uv sync --dev`). Read
**[CONTRIBUTING.md](CONTRIBUTING.md)** for the workflow.

---

## 👤 About the author

Built by **Masahiko Ebisuda** ([@ebibibi](https://github.com/ebibibi)) — a Microsoft MVP for
Azure Hybrid & Windows Server for 14 consecutive years, who works by delegating to a team of AI
agents instead of managing people. This relay is the tool he uses every day to do it.

- 📺 YouTube: [胡田昌彦 / Windows, Azure, M365, 生成AI](https://www.youtube.com/@ebibibi) (Japanese, 11k+ subscribers)
- 🌐 Website: [ebisuda.net](https://ebisuda.net)

---

## ⭐ Star history

If the relay saves you time, a star helps other people find it.

[![Star History Chart](https://api.star-history.com/svg?repos=ebibibi/ebi-agent-chat-relay&type=Date)](https://star-history.com/#ebibibi/ebi-agent-chat-relay&Date)

*Formerly Claude Code Discord Bridge, then Claude & Codex Discord Bridge — see
[ADR-0001](docs/adr/0001-adopt-ebi-agent-chat-relay.md). Every existing identifier still works:
the package is `claude-code-discord-bridge` and the command is `ccdb`.
Release notes: [v5.1.0](docs/releases/v5.1.0.md) · [v5.0.0](docs/releases/v5.0.0.md) ·
[v4.0.0](docs/releases/v4.0.0.md) · [CHANGELOG](CHANGELOG.md).*

> **Disclaimer:** This project is not affiliated with, endorsed by, or officially connected to Anthropic or OpenAI. "Claude" and "Claude Code" are trademarks of Anthropic, PBC; "OpenAI", "Codex", and "ChatGPT" are trademarks of OpenAI. This is an independent open-source tool that interfaces with the Claude Code CLI and the OpenAI Codex CLI.

## License

MIT
