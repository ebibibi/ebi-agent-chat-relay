# Codex live follow-ups (experimental)

Set the deployment environment variable `CCDB_CODEX_TRANSPORT=app-server` to
run cloud Codex threads through `codex app-server --listen stdio://` instead of
`codex exec`. Restart the relay after changing its deployment environment.
The default remains `exec`. This is an experimental protocol, measured on
**codex-cli 0.162.1**; use a compatible installed CLI.

With the shared live-input delivery path (#1129), a follow-up sent while a Codex
turn is running can be steered into that same turn. The runner returns `True`
only after `turn/steer` acknowledges the active turn ID. Explicit rejection or
an inactive turn returns `False`, leaving the existing follow-up queue available.
An RPC timeout or broken connection raises: delivery is uncertain, and callers
must not silently interpret that as confirmed rejection or blindly retry it.
The relay cannot guarantee exactly-once delivery across a broken transport.

The `local` backend **always uses exec**, even when this setting is enabled.
Its isolated, verified `CODEX_HOME` and vendor-egress guarantees (AGENTS.md,
decision 13) are unchanged. App-server must be independently measured for local
vendor egress before enabling it for local models.

## Launch and safety

The existing sanitized environment, account-pool `CODEX_HOME`, model, reasoning
effort, developer instructions, and execution-environment preparation are reused.
The app-server inherits the CLI's configured sandbox unless the operator sets
`CCDB_CODEX_SANDBOX_OVERRIDE` or explicitly enables the existing bypass setting.
The native execution environment forces `workspace-write` (retaining an explicit
`read-only` override) and refuses a `danger-full-access` environment override.
Its sandbox override takes precedence over the runner's bypass setting.

Approval policy is `never`, matching unattended exec. Server requests requiring
an interactive UI receive a JSON-RPC error; they never receive automatic approval.
Prompts are JSON values sent over stdin, never command arguments or shell text.
Thread start/resume resolves `cwd: "."` inside the server, so an execution
wrapper's remote working directory is respected rather than overridden with a
host-only path. Thread IDs are validated before resuming.

One reader handles notifications and responses independently of rendering.
Command, file-change and MCP tool events retain start/result pairing; complete
assistant messages and per-turn usage become ordinary relay stream events.
A separate task drains stderr so it cannot block stdout. Completion closes stdin
and waits for clean exit; timeout/cancellation interrupts an active turn before
bounded process cleanup. Missing rollout history starts a replacement thread
only before `turn/start`; errors after a turn begins never rerun the prompt.

## Measurement and decision

A real app-server run executed `sleep 6`. While that command was active,
`turn/steer` accepted additional text and returned the original `turnId`. The
command finished and the final answer included both the original and injected
markers. There was one completed turn, with no interruption or restart.
Steering after completion returned `-32600: no active turn to steer`. Closing
stdin after completion exited with status 0. The implementation was also tested
against the installed CLI: accepted follow-up, final answer containing the new
instruction, and resumption of the same thread retaining the injected text.

Protocol sequence:

1. `initialize` with `clientInfo`, then `initialized` notification.
2. `thread/start` or `thread/resume` → `result.thread.id`.
3. `turn/start` with text input → `result.turn.id`.
4. `turn/steer` with `threadId`, `expectedTurnId`, and text input → `result.turnId`.
5. Observe `item/started`, `item/completed`, `thread/tokenUsage/updated`, and
   `turn/completed` (including failed/interrupted status).
6. Close stdin and wait for the server to exit.

Decision: keep this a deployment opt-in while the protocol and its execution
behavior gain operational coverage. Existing consumers get the previous exec
behavior without configuration changes. A cross-engine design review identified
response/request ID separation, ambiguous steer delivery, native sandbox
precedence, and process cleanup as the main risks; the implementation and
protocol-peer regression tests cover those boundaries.

See [issue #1130](https://github.com/ebibibi/ebi-agent-chat-relay/issues/1130)
for the measured protocol report and [execution environments](execution-environments.md)
for deployment boundaries.
