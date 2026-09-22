# pi backend

[pi](https://github.com/earendil-works/pi) is a terminal coding agent that normalises Anthropic,
OpenAI, Google, GitHub Copilot, OpenRouter and OpenAI-compatible local servers behind one CLI. ccdb
spawns it the same way it spawns Claude Code and Codex: one process per turn, JSON events on
stdout, the prompt on stdin.

Start from [Choose an agent backend](backends.md) for setup. This page is the contract.

## Measured CLI contract

Everything below was measured against **pi 0.85.1**, not read off the published event list. Three
of the behaviours contradict what the documentation implies, and each one would produce a
plausible-looking wrong result if taken on trust. Re-measure after a pi upgrade; the recorded
fixtures in `tests/fixtures/pi_turn_*.jsonl` are what make that cheap.

```
pi --mode json [--session-id <uuid>] [--model <provider/id>] [--thinking <level>]
   [--append-system-prompt <text>] [--tools <list>] (--approve|--no-approve) --
```

| Property | Behaviour |
|---|---|
| Session resume | `--session-id` takes an exact id and **creates the session when it is missing**. ccdb stores the id from the first turn's `session` event and passes it back afterwards. |
| Terminal event | `agent_settled`, emitted after `agent_end`. Undocumented. |
| Usage | Reported per assistant `message_end` as `{input, output, cacheRead, cacheWrite}`, not once per turn. |
| Failures | **Not** an `error` event: an assistant `message_end` with `stopReason: "error"` and `errorMessage`, and the process still exits 0. |
| Interrupt | SIGINT exits immediately with no terminal event, and the session file for that turn is never written. |
| Working directory | Process cwd. There is no `--cd`. |
| Prompt | Piped stdin is merged into the prompt, so it stays out of argv. |

The resume property is the one that simplifies the implementation: `codex_runner` carries a
recovery path for a rollout that no longer exists, and `pi_runner` needs none, because a lost
session file degrades into a fresh session under the same id.

The interrupt property is the one that costs something: an interrupted turn is lost rather than
resumable, so the thread continues from the last completed turn.

## Event mapping

| pi event | ccdb `StreamEvent` |
|---|---|
| `session` | `SYSTEM` carrying `session_id` |
| `message_end` (role `assistant`) | `ASSISTANT` with `text`, `thinking`, and that message's usage |
| `message_end` (role `assistant`, `stopReason: "error"`) | `RESULT`, complete, with `error` |
| `message_end` (role `user` / `toolResult`) | dropped — echoing them would repost the prompt and duplicate tool results |
| `tool_execution_start` | `ASSISTANT` with a `ToolUseEvent` |
| `tool_execution_end` | **`USER`** with `tool_result_id` / `tool_result_content` |
| `agent_settled` | `RESULT`, complete, with the turn's accumulated usage |
| `agent_start`, `turn_start`, `turn_end`, `agent_end` | `SYSTEM` |
| `message_update` | dropped — deltas, superseded by the `message_end` that follows |

`tool_execution_end` is a `USER` event because `EventProcessor` only cancels a tool embed's live
elapsed timer on `_on_tool_result`. Tagging it `ASSISTANT` leaves every pi tool timer running for
the rest of the session.

Tool names are translated to the names ccdb renders and categorises (`read` → `Read`, `bash` →
`Bash`, `find` → `Glob`, and so on), and pi's `path` argument is renamed to `file_path` so a tool
embed reads `Reading: /x/y.txt` rather than `Reading: unknown`. Unmapped names — extension tools —
pass through unchanged.

Usage is summarised onto the terminal event as the **largest** single `input` seen plus the **sum**
of `output`. Summing input would multiply the same context, which pi re-sends on every step of a
multi-tool turn.

## Choosing a model

pi has no single provider, so `/model set` matters more here than on the other backends. Values are
**fully qualified** — `anthropic/claude-opus-5`, `openai-codex/gpt-6-astra`, `ollama/gpt-oss:120b`.
A bare id also works at the CLI, but it is a fuzzy *pattern*: `opus` resolves to whichever opus pi
ranks first, so ccdb never offers one.

The autocomplete is read from pi's own catalog on disk rather than from any vendor API:

| File | Written by | Contains |
|---|---|---|
| `~/.pi/agent/models-store.json` | pi, on `pi update` | the providers pi fetched |
| `~/.pi/agent/models.json` | the operator | hand-declared providers (a local Ollama endpoint, a gateway) |

Both are read and merged, because the second is where the only model some installs can actually run
appears — pi does not copy it into the store. Neither file being present degrades to a short static
list. Point ccdb at a non-default location with `CCDB_PI_HOME`, or turn discovery off entirely with
`CCDB_MODEL_DISCOVERY=0`.

The store is what pi last fetched, so it can trail `pi --list-models`, which also draws on the
catalog bundled with the npm package. Run `pi update` to refresh it. ccdb deliberately does not read
the bundled catalog: it lives inside the package's `node_modules`, and it lists every provider pi
*could* speak to rather than the ones this install is set up for. The field is free text either way
— autocomplete narrows the common case, it does not restrict what can be entered.

**Leaving the model unset is not neutral.** With no `--model`, pi resolves its own default from its
catalog ranking, which is not necessarily a model the account's credentials can call — on pi 0.85.1
that is `anthropic/claude-opus-4-8`, and an account without that entitlement gets a `400` for every
turn with no way to change it from Discord except `/model set`. `CCDB_PI_MODEL` pins a deployment
default so a fresh thread starts on a model that works; `/model set` still overrides it per thread
or globally.

## Security

pi states plainly that it has no built-in sandbox, that built-in tools run with the permissions of
the process, and that its non-interactive modes show no trust prompt. There is consequently no flag
equivalent to `dangerously_skip_permissions`, because unsandboxed is the only mode pi has.

ccdb does not paper over that. `CCDB_PI_ALLOW_UNSANDBOXED=1` is required before the backend will
spawn anything; without it, a turn returns the refusal and no process starts. This is the same
stance as the local backend's phone-home check: a property ccdb cannot provide is surfaced once, to
the operator, rather than discovered later from a thread that already ran.

The only restriction that does apply is the tool allowlist, so `CCDB_ALLOWED_TOOLS` reaches pi as
`--tools`. Use pi's own tool names (`read`, `write`, `edit`, `bash`, `grep`, `find`, `ls`).

Project trust is declined by default (`--no-approve`). Set `CCDB_PI_APPROVE_PROJECT=1` to pass
`--approve` and let pi load project-local `.pi/` settings, skills and extensions. Leaving it off
means a repository ccdb checks out cannot reconfigure the agent that is about to run inside it —
worth keeping even for repositories the operator owns, since an agent's own worktree is exactly
where such a file would appear.

## Not implemented

pi's `--mode rpc` keeps one process alive and accepts `prompt`, `steer`, `follow_up`, `abort`,
`set_model` and image input over stdin. That would give ccdb mid-turn steering, a protocol-level
abort instead of a signal, and image attachments — none of which json mode can offer. It needs a
persistent-process supervisor that ccdb does not currently have, so it is deliberately a separate
piece of work rather than a flag on this backend.

One detail to carry into it: RPC framing is strict LF-delimited JSONL. A reader that also splits on
U+2028/U+2029 is not protocol-compliant, because those characters are legal inside JSON strings.
