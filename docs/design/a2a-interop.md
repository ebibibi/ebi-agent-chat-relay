# A2A interoperability — design

Design only. Nothing in this document is implemented. It exists to turn
[Issue #664](https://github.com/ebibibi/ebi-agent-chat-relay/issues/664) from a large
undefined feature into phases that can each be picked up, merged, and judged on their own.

Issue #664 defers the work explicitly ("Future work. Do not displace the current automatic
local/external routing roadmap"). The purpose here is that when it *is* picked up, the first day
is spent writing code rather than reading a specification.

## Scope

Build, eventually:

- **ccdb as an A2A client** — a remote A2A agent selected with `/backend a2a`, exactly as AG-UI is
  today.
- **ccdb as an A2A server** — an Agent Card plus a task endpoint, so an external agent can hand
  work to a ccdb session.

Not in scope, in either direction:

- an agent registry, discovery service, or multi-agent orchestration inside ccdb;
- the gRPC and protobuf transport bindings;
- replacing the AG-UI backend. A2A and AG-UI solve different problems (agent-to-agent delegation
  versus agent-to-user event streaming) and both stay optional.

## The protocol facts that affect ccdb

Measured against the specification and PyPI on 2026-09-17.

- A2A is at **1.0.0**, stewarded by the Linux Foundation (`a2aproject/A2A`).
- The Agent Card is served at **`/.well-known/agent-card.json`** (RFC 8615). It is a public
  document. An *authenticated extended card* exists for anything that must not be.
- Three transport bindings — JSON-RPC 2.0, gRPC, HTTP+JSON — share one canonical data model.
  Implementing one is conformant.
- **Tasks** move through `submitted` to `working` to (`input-required` or `auth-required`) to
  `completed`, `failed`, `canceled`, or `rejected`. A terminal task **cannot restart**; follow-up
  work is a new task inside the same `contextId`.
- **Streaming** is SSE whose `data` field carries a JSON-RPC response wrapping a `Task`,
  `TaskStatusUpdateEvent`, or `TaskArtifactUpdateEvent`. The stream closes on any terminal state
  *and on `input-required`*. A dropped stream is resumed with a separate subscribe call.
- **Push notifications** are HTTP POSTs authenticated by a JWT the agent signs; the header carries
  `kid`, the public keys come from the agent's JWKS endpoint, and the claims include `iss`, `aud`,
  `iat`, `exp`, `jti`, and `taskId`. The config may also carry a client-chosen `token`.
- The official **Python SDK** is `a2a-sdk`, currently 1.1.2 (2026-07-22), Apache-2.0, actively
  maintained. Its *base* dependencies are `protobuf`, `google-api-core`,
  `googleapis-common-protos`, `pydantic`, `httpx`, and `json-rpc` — a materially larger footprint
  than the single optional `aiohttp` that AG-UI cost us.

## Contract mapping

| A2A | ccdb today | Fit |
|---|---|---|
| Agent Card | — | New. `SurfaceCapabilities` and the backend names are the raw material for `skills` and `defaultInputModes`. |
| `contextId` | `ThreadKey` plus the session row in `SessionRepository` | Good. This is the durable conversation identity in both models. |
| `taskId` | — | **Gap.** One turn is one `SessionBackend.run()` call and has no persisted identity. `ingest_results.result_id` is the closest thing that exists. |
| `Message` / `TextPart` | `InboundMessage` in, `ConversationSurface.send_text` and `StreamEvent.text` out | Good. |
| `FilePart` | `InboundAttachment` / `OutboundFile`; `ImageData` on the backend side | Partial — the backend path carries images only. |
| `DataPart` | — | Gap. No structured-payload part in the `StreamEvent` vocabulary. |
| `TaskStatusUpdateEvent` | `StatusKind` / `set_status`, and `Notice` | Good. |
| `TaskArtifactUpdateEvent` (`append`, `lastChunk`) | `TextStream.append` / `finalize`, `OutboundFile` | Good — chunked artifacts are what the streaming manager already debounces. |
| `input-required` | `ChoicePrompt` / `FormPrompt`, `AskQuestion`, `ElicitationRequest` | Semantically good, operationally blocked — see below. |
| `auth-required` | — | Gap. No vocabulary for "the backend needs the user to log in somewhere". |
| `CancelTask` | `SessionBackend.interrupt()` / `kill()`, `offer_interrupt` / `InterruptHandle` | Good, both directions. |
| `GetTask` / `ListTasks` | `GET /api/sessions`, `GET /api/ingest/{result_id}` | Close. See the note below. |
| Push notification config | `POST /api/notify` and `NotificationRepository` outbound | Outbound exists. Inbound JWT verification is new code — but `claude_teams/jwks.py` and `claude_teams/auth.py` already do `kid`-triggered JWKS refresh with pinned algorithms for Bot Framework, and that is the same job. |
| `securitySchemes` bearer | the `ingest_token` pattern in `ext/api_server.py` | Good. |

Two things follow from that table.

**`/api/ingest` is already a non-standard A2A task API.** An external program posts work, gets an
id back, and polls `GET /api/ingest/{result_id}` for the answer while a real session runs in a
human-visible thread. The server direction is largely "give that surface a standard shape and a
discoverable card", not a new subsystem.

**The one structural gap is turn identity.** A2A tasks are addressable and immutable once
terminal; ccdb's ledger is keyed by thread and a session is resumed indefinitely. The natural
mapping is `contextId` = session, `taskId` = turn, and that requires a durable per-turn id that
does not exist yet.

### What does not map

- **`input-required` is the same blocker AG-UI already hit.** ADR-0004 decided that
  interrupt/resume outcomes fail visibly rather than reporting success, because there is no
  durable interaction store that survives a restart. A2A gives that problem a standard name and a
  second caller; it does not solve it. Any phase that claims `input-required` support must ship
  that store.
- **A stream that ends at `input-required`** breaks the "one SSE response per turn" shape the
  `AgUiBackend` assumes. An A2A client has to be prepared to re-subscribe mid-turn.
- **ccdb's permission prompts and plan approvals** have no A2A equivalent. They would have to ride
  inside `input-required` as `DataPart` payloads that only a ccdb-aware peer could render, so a
  generic peer will see them as an unanswerable question. Do not advertise them as a skill.

## Security posture

The existing posture is localhost-first with explicit, opt-in authentication. A2A must not relax
it. **A design that opens an unauthenticated port is out of bounds**, including the tempting
version where "discovery is public, so the endpoint may as well be".

Server direction:

- The A2A endpoint runs on **its own listener**, never as a route on the control-plane app. That
  app reaches `/api/spawn`, which starts arbitrary Claude sessions. `ext/api_server.py` already
  splits a token-gated external surface onto a second app for exactly this reason, and mounting a
  subtree exposes everything under it.
- Opt-in like `ingest_token`: with no token configured the endpoint answers `503`, so it can never
  come up unauthenticated by accident.
- The Agent Card is public. It therefore carries static capability data only — no thread titles,
  channel ids, working directories, operator names, or the installed skill list. Anything
  role-specific belongs in the authenticated extended card.
- Verify the bearer token before doing any work, as `claude_teams/endpoint.py` does.

Client direction — reuse the AG-UI outbound guards rather than reinventing them: reject
credentials in the URL, do not follow redirects (which would forward the bearer token), strip the
token from child CLI environments via `child_env.py`, bound each SSE frame, and keep non-success
bodies out of Discord and Teams.

Push notifications, if implemented:

- Accept a notification only for a config **we** created, and treat it as a signal to re-fetch the
  task — never as the content itself.
- Verify the JWT: resolve `kid` against the peer's JWKS with pinned algorithms (`none` rejected),
  check `iss` against an allowlist, check `aud`, reject stale `iat`/`exp`, keep a `jti` replay
  window, and confirm `taskId` is one we own. Check the `token` we chose, if we set one.
- The webhook is a public listener, so the same rate-limited JWKS refresh discipline as
  `claude_teams/jwks.py` applies — an unknown `kid` is a public trigger.

## Open questions, and the spike that closes each

| # | Question | Spike | Size |
|---|---|---|---|
| 1 | Does a real peer interoperate with a JSON-RPC-only server, or does it assume gRPC? | Serve a static card plus one `message/send` from a throwaway aiohttp stub; point Hermes Agent and an `a2a-sdk` sample client at it. | half a day |
| 2 | What does `input-required` look like on the wire from a generic peer — is free text enough, or is a `DataPart` schema required to answer? | Drive Hermes into `input-required` and record the frames. | half a day |
| 3 | Must a task survive a ccdb restart? Do clients tolerate `GetTask` returning not-found, or must it become `failed`? | Kill the stub mid-task and observe both clients. | half a day |
| 4 | Is `a2a-sdk` acceptable behind an optional extra? | Resolve it as an optional dependency in a scratch worktree; count resolved wheels and check the `protobuf` and `pydantic` pins against ours. | an hour |

Question 4 decides whether phase 1 writes a wire client or wraps the SDK. Do not decide it from
the dependency list alone.

## Phased plan

Each phase is independently mergeable and useful on its own.

1. **A2A backend, non-streaming.** `A2aBackend(SessionBackend)` selected with `/backend a2a`:
   `message/send`, poll `tasks/get`, map into `StreamEvent`. Any A2A agent becomes reachable from
   Discord and Teams. Shaped like `agui_backend.py` (roughly 550 lines plus tests) and reusing its
   guards.
2. **Backend streaming.** `message/stream` over SSE, with re-subscribe on a dropped connection.
   `input-required` and `auth-required` fail visibly, as ADR-0004 requires.
3. **Durable turn identity.** A persisted per-turn id in the session ledger. Valuable on its own:
   it gives `/api/ingest` a real task id and is the prerequisite for restart-safe interrupt
   resume, which AG-UI already needs. **Do this whether or not A2A ships.**
4. **A2A server, card and polling.** `/.well-known/agent-card.json` plus `message/send` and
   `tasks/get` on a separate opt-in listener, spawning a session through the existing ingest path.
   An external agent can hand work to ccdb.
5. **Server streaming and cancel.** SSE responses; `CancelTask` routed to `interrupt()`.
6. **Push notifications, both directions**, with the verification described above.

Phases 1 and 4 are each done only when validated against Hermes Agent *and* one independent
implementation, as Issue #664 asks. A passing unit test against our own fixture proves nothing
about interoperability.

## Rejected alternatives

**Implement A2A as a `SessionFrontend` / `ConversationSurface`.** Issue #664 says "as a frontend
and backend", but the frontend contract is built for a human surface: status kinds, pinned
dashboards, rename, file consent, edit pacing against a platform quota. An A2A client is a
program, and `check_surface` would pass on stubs that mean nothing. The inbound direction belongs
next to the ingest surface in `ext/api_server.py`, which already models "a program asked for work
and will collect the result". A thin surface can be added later if peer-facing streaming turns out
to need the chunker's pacing; it should not be the entry point.

**Depend on `a2a-sdk` in the core package.** Same reasoning as ADR-0004 for AG-UI: a mandatory
framework dependency would land on every Claude/Codex-only install. Unlike AG-UI, though, the SDK
is not pre-rejected *behind an optional extra* — A2A's canonical model is protobuf and its
security surface is non-trivial. Spike 4 decides it with numbers.

**Implement the gRPC binding first.** Rejected. JSON-RPC over HTTP is conformant, reaches the
named peers, and keeps the dependency surface at the HTTP client we already have.

**Mount the A2A routes on the existing control-plane app.** Rejected — that app reaches
`/api/spawn`.

**Serve the task endpoint unauthenticated because discovery is public.** Rejected. The card may be
public; the endpoint that starts a session on the operator's machine may not.

## Related

- [Issue #664](https://github.com/ebibibi/ebi-agent-chat-relay/issues/664)
- [ADR-0004: Add AG-UI as an optional backend transport](../adr/0004-add-ag-ui-as-an-optional-backend.md)
- [AG-UI backend](../agui-backend.md) — the closest existing precedent
- [A2A specification](https://a2a-protocol.org/latest/specification/)
- [a2aproject/a2a-python](https://github.com/a2aproject/a2a-python)
