# Architecture

Ebi Agent Chat Relay has two independent adapter axes around one session core:

- a **frontend** turns a Discord thread or Teams conversation into a `ConversationSurface`; and
- a **backend** turns Claude Code, Codex, local, or AG-UI output into neutral `StreamEvent` values.

The core owns session persistence, approvals, streaming, concurrency, worktrees, AI Lounge,
claims, and collision detection. It contains no model of its own.

## v4 overview

```text
Discord Gateway                       Teams / Bot Framework
      │                                       │ signed HTTPS
      │                                       ▼
      │                              public relay receiver
      │                                       │ verified envelope
      │                                       ▼
      │                              Azure Storage Queue
      │                                       ▲ outbound poll
      ▼                                       │
DiscordFrontend ◀──────── FrontendRouter ── TeamsFrontend
      │                                       │
      └──────────── ConversationSurface ──────┘
                              │
                              ▼
                 shared session execution core
        persistence · prompts · streams · Lounge · claims
                              │
                              ▼
                        BackendFactory
                ┌────────┬────────┬────────┬────────┐
                ▼        ▼        ▼        ▼
             Claude    Codex    Local    AG-UI
              CLI       CLI      CLI     HTTP/SSE
```

Discord remains the primary frontend for creating scheduled conversations and exposes the richest
administration command surface. Teams inbound activities reach the same session runner through the
private `ActivityPuller`. The public receiver can neither authenticate as the bot nor run an agent.

## Frontend contract

`claude_code_core.frontend` defines two protocols:

- `SessionFrontend` creates or resolves conversations; and
- `ConversationSurface` renders one conversation's text, status, activity, prompts, interrupt
  control, and file delivery.

`DiscordFrontend` and `TeamsFrontend` are siblings. Neither is implemented in terms of the other.
The same executable conformance suites run against both, which pins behavior that Python protocols
alone cannot express.

Platform differences live in `SurfaceCapabilities` rather than scattered name checks:

| Capability | Discord | Teams |
|---|---:|---:|
| message size | 2,000 chars | 80,000 chars |
| bot reactions | yes | no |
| live update budget | roughly every 1.5 s | 1,800/hour/conversation |
| files | attachment | personal-chat consent upload at the surface layer |
| native bot slash commands | yes | no |

The `FrontendRouter` resolves a stored thread key through every registered frontend and creates new
surfaces through the primary frontend. `frontend_threads` maps Teams string conversation IDs into
the integer keyspace used by the existing session tables, while preserving the frontend origin so
results cannot cross platforms.

## Teams transport boundary

Teams requires inbound HTTPS; the agent host should not. The recommended transport is split:

1. The public receiver verifies Bot Framework signature, issuer, audience, expiry, and the signed
   `serviceurl` claim before parsing an activity into a bounded envelope.
2. It writes that envelope to a dedicated queue using an add-only credential.
3. `ActivityPuller` on the private host reads outbound, deduplicates activity IDs, and dispatches
   the activity to `TeamsSessionHost`.
4. The session host resolves the Teams surface, selects the backend through the shared settings and
   factory, and calls the same `run_claude_with_config` execution path used by Discord.
5. `BotConnector` posts the response directly to Microsoft's regional service URL using the client
   credential that exists only on the private host.

Delivery is at least once. The puller deduplicates successful activity IDs, retries transient
failures, and drops a poison message after a bounded attempt count rather than blocking the queue.
See [Teams relay](teams-relay.md) and [Teams setup](teams-setup.md).

## Backend contract

`SessionBackend` exposes one asynchronous event stream regardless of transport. `BackendFactory`
constructs the selected implementation at the start of each turn:

- `ClaudeRunner` executes Claude Code stream-json;
- `CodexRunner` executes Codex JSONL;
- the local backend executes Codex against a ccdb-owned OpenAI-compatible configuration; and
- `AgUiBackend` sends `RunAgentInput` over HTTP and parses JSON server-sent events.

`BackendSettings` resolves values in thread → global → environment order. Session IDs are only
resumed when the stored backend matches the selected backend; native Claude, Codex, and remote
AG-UI identifiers are not interchangeable.

See [Choose an agent backend](backends.md).

## Shared execution flow

```text
inbound user turn
   │
   ├─ resolve frontend conversation and stable ThreadKey
   ├─ load session record and backend settings
   ├─ build one backend runner for this turn
   ├─ construct RunConfig with the platform-neutral surface
   ▼
run_claude_with_config
   ├─ enforce shared concurrency
   ├─ inject coordination/worktree context
   ├─ consume StreamEvent values
   ├─ render through ConversationSurface
   ├─ fail closed on unanswered approvals
   └─ persist session id, backend, working directory, and origin
```

The execution helper retains its historical name for compatibility; it is backend-neutral.

## Persistence and isolation

One `DataLayout` root contains the session database and related stores. Sessions, settings,
approval state, Lounge entries, claims, usage, summaries, ingestion, and frontend mappings share
the deployment boundary deliberately so concurrent agents can see and coordinate with one another.

That same property means unrelated customers must not share a data root. Use a separate process,
bot identity, queue, and `CCDB_DATA_ROOT` for each isolation boundary.

## Main modules

| Module | Responsibility |
|---|---|
| `claude_code_core/frontend.py` | frontend/surface protocols, capabilities, stable thread keys |
| `claude_code_core/backend.py` | backend protocol and common construction vocabulary |
| `claude_discord/frontend.py` | Discord conversation adapter |
| `claude_teams/frontend.py` | Teams conversation adapter |
| `claude_discord/teams_integration.py` | normal-process Teams runtime and `ActivityPuller` dispatch |
| `claude_teams/relay/` | verified envelope, receiver, queue client, and poller |
| `claude_discord/backend_factory.py` | Claude, Codex, local, and AG-UI construction |
| `claude_discord/cogs/event_processor.py` | neutral stream-event rendering and prompt dispatch |
| `claude_discord/stores.py` | construction of the shared SQLite repositories |
| `claude_discord/deployment.py` | one configurable data-root layout |
| `claude_code_core/attention.py` | pure estimate of the operator's own attention ([attention](attention.md)) |
| `claude_code_core/attention_repo.py` | `human_activity` storage and the frontend-neutral recording hook |

## Extension points

### Add a frontend

Implement `SessionFrontend` and `ConversationSurface`, declare accurate capabilities, then run both
frontend and surface conformance suites against the real adapter with only its transport faked.
Do not import Discord components into the new frontend.

### Add a backend

Implement `SessionBackend`, emit neutral `StreamEvent` values, register it in `BackendFactory` and
`BackendSettings`, and make credentials explicit. A remote backend is a data boundary; do not echo
untrusted response bodies or leak its credentials into child processes.

### Embed the relay

`setup_bridge()` wires the standard Discord deployment and exposes `BridgeComponents` for custom
cogs. Public names remain compatibility constrained even though the product name changed; see
[the rename plan](RENAME_PLAN.md).
