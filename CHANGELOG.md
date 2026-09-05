# Changelog

Last updated: 2026-08-11

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **GPT-6 is selectable, and the Codex model list stops going stale** — `/model`'s Codex suggestions
  were a hardcoded quartet (`gpt-5.6-sol`, `gpt-5.5`, `gpt-5.5-codex`, `o4-mini`), two of which no
  longer exist, and the newest generation was not among them: picking GPT-6 meant knowing the slug
  `gpt-6-astra` by heart and typing it blind. Codex now gets the same treatment Claude got in
  decision 11, without ccdb calling a vendor: the Codex CLI already fetches its own catalog and
  writes it to `$CODEX_HOME/models_cache.json`, so `codex_model_choices()` reads that file, drops
  the entries the CLI marks `hide` (auto-review, reserve capacity), and orders the rest by the
  catalog's own `priority` — GPT-6 leads the dropdown as soon as the Codex CLI has seen it, with no
  ccdb release. A host that has never run the Codex CLI, an unreadable catalog, or
  `CCDB_MODEL_DISCOVERY=0` all degrade to a short static list (now GPT-6-first). Relatedly,
  `/effort` accepted `minimal/low/medium/high/xhigh` only, so the two levels GPT-5.6 and GPT-6
  actually added — `max` and `ultra` — were rejected by ccdb before the CLI ever saw them, capping
  the newest models below their own ceiling; `VALID_CODEX_EFFORTS` is now the union across models
  (the CLI still rejects a level its selected model does not support, and that error reaches the
  thread).

- **The deploy-drift check now also catches a bot that simply never restarted** — it answered one
  question ("is a forgotten `make dev-on` worktree in production?") and returned `OK: main-tree
  mode` for everything else, which is a guard that reports on the case it was written for and waves
  the rest through. Main-tree mode has its own silent staleness: the checkout only pulls in
  `pre-start.sh`, so a bot that has not restarted keeps serving whatever was merged before it
  booted, while the tree says `main` and every `git pull` keeps succeeding. The check now compares
  the *running process* against `origin/main` — commits merged after the service's
  `ActiveEnterTimestamp` are the ones not running — and reports how long the oldest of them has
  been waiting. Because restarts are batched deliberately (an in-flight session does not survive
  one), it reports from day one but only exits 1 past `CCDB_STALE_DAYS` (default 3). A machine
  without systemd, or an unreadable unit, says so instead of silently passing. `CCDB_SERVICE`
  overrides the unit name.

- **`scripts/check-deploy-drift.sh` (also `make drift`)** — reports when the bot is loading code
  that is not on `origin/main`. `make dev-on` is the right tool for testing a change against real
  Discord traffic, but nothing expires it: `pre-start.sh` printed one line at boot and never
  mentioned it again, so a forgotten dev worktree can keep a side branch in production for days
  while every merged PR appears to deploy and does not. The check names the worktree and branch,
  states whether that commit is an ancestor of `origin/main`, counts how many merged commits are
  therefore not running, and reports how long dev mode has been on. `pre-start.sh` now prints the
  full report instead of the single line. Exit codes: 0 clean, 1 drift, 2 marker points nowhere.

### Fixed

- **`auto_start=false` seeds are no longer truncated to their first message** — a spawn seed longer
  than Discord's 2,000-character limit is posted as several messages by `spawn_session`, but
  `_fetch_seed_context` read only message #1. The Claude session that started on the human's reply
  therefore woke up with a seed cut off mid-sentence, invisibly, and worse the longer the seed was
  (a daily news digest lost most of its items). It now reads the leading run of bot messages,
  stopping at the first human message, bounded by `SEED_CONTEXT_MESSAGE_LIMIT`.

- **`POST /api/spawn` honours `user_id`** — the field was already being sent by callers and silently
  dropped, so a spawned thread never appeared in the requester's joined list and the miss looked
  like success. The user is now added as a thread member before the seed message is posted, matching
  what `/api/ingest` already did for the bot owner. A malformed `user_id` is a caller bug and is
  rejected with 400; a Discord-side failure to add the member is only a visibility miss and is
  suppressed, because a spawn that already created the thread and started Claude must not be
  reported as failed.

- **`.gitignore` covers `.env.*`, not just `.env`** — dated backups of the environment file
  (`.env.bak-…`) sat untracked-but-not-ignored beside the real one, each holding a live bot token,
  so a single `git add -A` staged a working credential. GitHub push protection caught one such
  commit before it left the machine; the ignore rule removes the trap rather than relying on the
  catch. `.env.example` is re-included explicitly so the template stays tracked.

### Changed

- **Thread-completion recording is opt-in, off by default** — deleting a thread is an everyday,
  destructive act, and having it silently start a Claude session is a surprise. `/thread-completion
  on|off` throws the switch and the answer is stored, so it survives a restart; the environment
  variables now decide only whether the switch exists. The state is re-checked after the debounce
  window, so turning it off during the wait drops the pending batch. Anything other than a stored
  "on" — no settings repo, no value, a failed read — is off, because the absence of an answer is
  not permission.

- **`ThreadCompletionCog`'s prompt is external** (`THREAD_COMPLETION_PROMPT_FILE`) — where a
  completion record goes is one person's note-taking convention, and this repository is public. The
  Cog keeps the generic half (batching, session/transcript resolution, the manifest) and reads the
  instance-specific instructions from a file outside the repo. An unreadable path falls back to a
  generic prompt rather than dropping the batch.

### Added

- **A test that fails when shipped source names a real person** — `examples/ebibot` is a real
  instance's configuration, so a docstring explaining why a Cog exists is exactly where personal
  detail leaks in. The check is narrow on purpose: names, not topics.

### Added

- **`find_transcript(session_id, root)`** in `claude_code_core.transcript_search` — resolves one
  session's transcript without knowing its working directory. A thread can be deleted; its
  transcript can't, so anything that wants to say something about a finished conversation after the
  thread is gone needs this. The id is validated before it reaches a filesystem glob.
- **`ThreadCompletionCog`** (EbiBot example) — treats "the user deleted the thread" as "that work is
  finished". Deletions are batched over a quiet period, resolved against the session rows and
  on-disk transcripts, written to a manifest, and handed to one Claude session that files the
  records. Threads that never held a session (notification threads) are dropped, and the Cog's own
  record threads don't re-trigger it. Disabled unless `THREAD_COMPLETION_CHANNEL_ID` is set.

### Fixed

- **EbiBot's own Cogs kept the 24-hour thread window** — `alert_responder` and `job_failure_triage`
  passed `auto_archive_duration=1440` literally. They now use the shared constant, and the
  architecture test scans `examples/ebibot/cogs` too.

- **Threads stay in the channel's thread list for a week instead of an hour** — every
  `create_thread()` call site now asks for Discord's maximum auto-archive window (7 days) via the
  shared `THREAD_AUTO_ARCHIVE_MINUTES` constant. Chat threads had been created with a 60-minute
  window, so a conversation dropped out of the sidebar an hour after the last reply and looked
  deleted; the other call sites silently inherited discord.py's 24-hour default. An architecture
  test fails when a new call site forgets the keyword.

- **Teams session cards now leave the running state when a turn ends** — teardown removes and
  unregisters the Stop action, then flushes the final card repaint so the completed or error status
  is visible immediately instead of leaving a stale working card behind.

## [4.0.0] - 2026-08-11

### Added

- **Microsoft Teams as a production frontend** — the normal launcher can run Discord and Teams in
  one process with `CCDB_FRONTENDS=discord,teams`. A public receiver verifies Bot Framework
  activities and enqueues them without holding the bot client secret; a private `ActivityPuller`
  polls outbound, invokes the same real session runner Discord uses, and posts results back to Teams.
- **A complete Teams operator guide** — documents Entra registration, Azure Bot, separate queue
  credentials, public receiver deployment, private host configuration, generated app packaging,
  tenant consent, staged validation, security boundaries, and troubleshooting.
- **A unified backend guide** — explains Claude Code, OpenAI Codex, guarded local, and AG-UI choices
  independently from Discord or Teams.
- **Curated v4 release notes** — make the product boundary and 3.x compatibility position explicit.

### Changed

- **The public product contract is frontend × backend** — any supported chat frontend can select any
  supported backend per conversation while sharing persistence and coordination.
- **Release version is 4.0.0** — the major version communicates the combined multi-frontend product
  and Teams deployment boundary. Existing package names, commands, settings, data, APIs, default
  backend, and Discord-only startup remain compatible.

## [3.4.0] - 2026-08-11

### Fixed

- **Teams rejected every genuine inbound request — the token claim is `serviceurl`, not `serviceUrl`** — found by pointing a real Teams tenant at the endpoint for the first time. The signature, issuer, audience and expiry all verified; the request then died on the last check with "token has no serviceUrl claim". Measured on a live Bot Framework token, the claim set is exactly `['aud', 'exp', 'iss', 'nbf', 'serviceurl']` — the camelCase spelling that appears throughout the activity *body* does not exist in the token. **The whole suite was green the entire time**, because the fixtures were built with the same wrong name the implementation read: a test that constructs its own input cannot catch a wrong assumption about that input. Both spellings are accepted now, and `TestTheClaimNameIsLowerCase` pins the real one along with the reason it went unseen.

### Added

- **AG-UI agents as an optional backend** — `AgUiBackend` sends standard `RunAgentInput` requests to a configured HTTP(S) endpoint and maps JSON SSE lifecycle, text, reasoning, and tool events into the relay's existing `StreamEvent` contract. Select it with `/backend agui` or `CCDB_BACKEND=agui`; Discord thread identity is preserved as the AG-UI `threadId`, and images are sent as inline base64 input. The transport is isolated behind the `agui` optional dependency and treats the endpoint as a security boundary: URL credentials and redirects are rejected, bearer tokens are stripped from child CLI environments, response details are bounded, and oversized SSE frames fail closed. Durable HITL resume, state/activity rendering, protobuf, and client tools remain explicit follow-ups. (#605, #607)

- **Teams can run without putting the session host on the internet — `claude_teams.relay`** — Discord was safe in a way that had nothing to do with the code: the transport was outbound only, so the machine running coding-agent sessions never appeared on the internet's attack surface. Teams requires inbound HTTPS, but *where* that inbound lands is a choice. A disposable receiver takes the request, verifies the token, and puts the activity on a queue; the session host **polls that queue outbound** and replies straight to the Bot Connector, also outbound. The host opens no listening port — the Discord shape, restored on a platform that does not offer it. The receiver holds the bot's *public* application id and a write credential for one queue, and **no client secret**, because it never replies: owning it yields the traffic passing through from that moment on, not the ability to speak as the bot, read past conversations, or reach the host. It verifies rather than forwarding, because forwarding would make the queue the trust boundary and put the Bot Connector's keys on the private machine; the envelope records what was checked, so the host's trust is auditable rather than assumed, and carries the **token's** `serviceurl` rather than the body's copy — the distinction that stops a replayed token redirecting the host's authenticated calls, and one that moving machines must not quietly lose.
- **The failure modes that come with a queue, handled rather than discovered** — acknowledgement is separate from delivery, so a host that dies mid-handler gets the message again instead of losing it (a user's message that vanishes because a process restarted is indistinguishable from a bot that ignored them). That makes duplicates possible, so the puller filters by activity id: running a session twice for one message is worse than the crash that caused the redelivery. A message that fails every time is dropped after a bounded number of deliveries, loudly and with its id, because the alternative blocks every message behind it. An unreadable one is dropped immediately — unreadable now is unreadable next time. And a poll that returns nothing pauses, because a queue with no long poll would otherwise turn the loop into a spin against it.
- **`python -m claude_teams relay`** — the receiver as a one-command process, plus an Azure Queue Storage client written against the REST API rather than the SDK: four calls, and smaller than the import would be. Queue Storage speaks XML alone among these APIs and is parsed with `defusedxml`, because the document arrives over a network as bytes this process did not write whoever is nominally at the other end. Pop receipts are encoded with `safe=""` — the default leaves `/` alone, and a half-encoded receipt makes the delete silently target something else, so the message comes back forever. See `docs/teams-relay.md`, which states the cost too: a card press is acknowledged before anyone knows whether the prompt is still live, so feedback precision is traded for keeping the host off the internet.
- **`python -m claude_teams serve` — an endpoint that only echoes** — bringing a Teams app up the first time means checking a chain (Entra app, service principal, Azure Bot, messaging endpoint, manifest, resource-specific consent, token validation) where any broken link produces the same symptom: silence. A process that *only* echoes turns that into one question with one answer, and while it runs, the thing reachable from the internet cannot start a session even if it is compromised. It binds to loopback by default, and its health check identifies nothing about the deployment — a health endpoint is the most-scanned URL on any host, and one that names what it fronts is free reconnaissance.
- **`TeamsFrontend` — Teams becomes reachable by everything ccdb already does** — the scheduler, the webhook trigger and the REST API all reach a conversation through this seam without knowing which platform it lives on, and it passes `check_frontend`, the same contract `DiscordFrontend` passes. The awkward part is that a Teams address is two things: the conversation id *and* the regional Bot Connector that owns it, and the `frontend_threads` ledger records only the first. So the frontend learns `serviceUrl` from inbound traffic and a deployment can configure its tenant's (`CCDB_TEAMS_SERVICE_URL`) — which is what lets a scheduled follow-up post after a restart. With neither, a conversation resolves to **`None`**: knowing a conversation exists and not where to post to it is not a surface, and inventing a host would send a session's output somewhere nobody is reading. Creating one without a host raises instead, because that is a configuration error rather than an ordinary fact like a deleted thread. A key minted by another frontend resolves to `None` too — one ledger holds every platform's conversations, and handing back a Teams surface for a Discord key would post into the wrong platform entirely. Creating a conversation starts a new reply chain in the channel, which is what keeps Thread=Session intact on a platform whose threads are a property of a message rather than objects of their own. Every surface it hands out shares one prompt registry and one file registry, because the endpoint routes an inbound press to a single place and surfaces with registries of their own would each be unanswerable from it.
- **Text commands, because Teams does not have slash commands for bots** — Discord registers `/model` with the platform and gets autocomplete, validation and a UI; Teams offers a bot none of that, and its `commandLists` only pre-fills the compose box. So a command arrives as an ordinary message starting with a slash, and `claude_teams/commands.py` is the whole command surface. Only *registered* names count: `/tmp/build.log is missing` is a sentence about a path, and a router that parsed first and dispatched later would silently swallow it — here an unrecognised name is not a command and the text reaches the session unchanged. The caller can also tell "not a command" from "the command produced no output", which are the two things that must not be confused. The manifest's menu is generated from the same registry that dispatches, so a documented command that answers to nothing cannot happen; `build_manifest(config, commands=router.menu())` advertises exactly what a custom router handles.
- **Mentions are resolved by id and stripped from the prompt** — a channel message addressed to the bot arrives as `<at>Relay</at> fix the parser` with the mention repeated in `entities`. Whether the bot was addressed is decided from the entities and matched on **id**, not display name: names are neither unique nor stable, and a tenant can hold two apps that share one. The markup then comes out of the text before the model sees it — every mention, not only the bot's, because the tags are markup rather than content. Leaving them in makes a session learn to strip `<at>…</at>` itself, badly, and puts a typed `/model opus` somewhere no parser will find it. `raw_text` keeps what Teams delivered and `clean_text` is what the model should see, which is the distinction `InboundMessage` already draws.
- **Files reach a Teams personal chat, and a channel is told they cannot** — a bot cannot attach a file to a Teams message; what it can do in a personal chat is offer one. `deliver_files` sends a consent card per file, and on accept Teams returns a one-time upload URL to PUT the bytes to. A file that cannot be read or is over the capability's size limit is named and refused rather than dropped or truncated — a truncated file looks complete and is not. In a channel the files are named and the message says the contents were **not** sent, because consent cards are personal-scope only and writing into a channel's folder is a Graph permission this deployment does not hold. The conformance contract is therefore run **twice**: a personal chat passes all 18 checks, a channel fails exactly one, and the test asserts *which* one — an assertion that breaks in both directions, so the gap cannot quietly widen or quietly close.
- **The upload URL is checked before a byte moves** — the accept invoke carries it, which makes it the one place something off the wire decides where the contents of a local file are written. The host is matched against the domains Microsoft hands upload sessions out on, on the parsed hostname rather than by substring: `https://contoso.sharepoint.com@evil.example.com/` and `https://evil.example.com/?x=.sharepoint.com` both contain the suffix and neither is SharePoint. The invoke is authenticated, so this is defence in depth — and it is the difference between a file transfer and an exfiltration primitive if anything upstream is ever wrong. No `Authorization` header is attached to the upload, because the URL Teams returns is itself the credential and sending this deployment's token to a host it did not choose is the wrong instinct. A transfer is claimed once and bound to its conversation, exactly like a prompt.
- **A card press in Teams can now answer a prompt — and mostly, it cannot** — `prompt_choice` posts an Adaptive Card (a button per choice for a short list, a dropdown for a long or multi-select one), `prompt_form` posts one input per field, and pressing a control resolves the caller waiting on it. The interesting half is the refusals. An action arrives carrying whatever `data` the client sent: the Bot Connector proves *a Teams user sent it* and proves nothing about the payload matching a card this process posted, so `claude_teams/interactions.py` treats all of it as untrusted. **The conversation must match** — without that, someone who learns a prompt id can approve a tool run in a conversation they are not part of, and the session sees an ordinary approval with nothing odd about it. **The value must have been offered**, or a crafted action returns any string as "what the user chose". **Once only**, so a replay cannot answer the next prompt and a re-pressed Stop cannot interrupt the session after this one. **Only declared keys come back from a form**, because a card submit merges every input into the payload. Every refusal looks identical to the caller — one sentence, no reason — since "wrong conversation" and "expired" are both free information to whoever is probing. Prompt ids are unguessable rather than sequential; the conversation binding is the real control, but an id nobody can enumerate removes the class of attack that starts with guessing one.
- **Fail-closed, including the case where the prompt never arrived** — a timed-out `prompt_choice` applies `default_on_timeout`, which a tool-permission request sets to the denying choice, and the *same* fallback runs when the card could not be posted at all: a prompt nobody could see must not be safer to ignore than one nobody answered. The shared contract deliberately cannot check this — from outside, denying on timeout and inventing a denial return the same value — so it is proved in `tests/test_teams_prompts.py::TestFailClosed` by withholding the answer, which is the only place it can be.
- **Stop lives on the card** — Discord re-posts its Stop button to keep it in view because messages scroll away from it; in Teams the card is already the one message being kept current, so the control goes there. Disabling it removes the control *and* stops honouring its id, so a press that lands afterwards cannot interrupt whatever ran next.

### Changed

- **An invoke is answered in the HTTP response body, not with a bare 200** — a card press does not arrive like a message: Teams reads the response body as the answer, so the endpoint's usual `{"status": "ok"}` would show the user an error even though the press worked. Invokes now return an `InvokeResponse`; messages are unchanged. Unknown invoke names (Teams sends several ccdb does not implement, and more over time) are answered successfully rather than with a failure, which would surface as a broken bot.
- **Teams gets a surface, and the shared contract is pointed at it — `TeamsSurface`** — the same `check_surface` that keeps Discord honest now runs against the Teams class that ships, with the *transport* faked rather than the surface, so what is checked is the real implementation's own decisions. It reports **17 checks passed and 1 failed**, and the failure is pinned by name rather than skipped: a bot cannot attach a file to a Teams channel message, so `deliver_files` names the files and states that their contents were **not** sent. A run that went green while the bytes went nowhere would be exactly the kind of green worth distrusting, so closing the gap is what makes `tests/test_teams_conformance.py` pass and nothing else is. Prompts follow the same rule — they post the question and return `None`, which the contract defines as "unanswered" and which callers already handle by applying their own default; returning a *choice* would leave the caller unable to distinguish "the user allowed it" from "the surface invented allow". No Stop button is rendered either, because an `Action.Execute` nothing routes shows the user an error when they press it.
- **One card instead of a column of embeds** — Discord posts an embed per tool call and edits it, which is right where editing is cheap and there is no hourly ceiling. Teams allows 1,800 operations per hour per conversation, so porting that design would spend a long session's whole budget on scrollback and then go quiet. A tool starting, the status changing and a tool finishing are three events and one operation here — repaint — and three inside one interval cost one request. The card bounds its own activity list and truncates long text, because Teams refuses a payload over 28 KB and the refusal is invisible from the sending side: the update fails, the card freezes on its last good state, and the session looks stuck.
- **`UpdatePacer` — coalescing, not throttling** — throttling drops updates and queueing delivers them all late; coalescing delivers the *current* state on the next slot, which is the only state anyone wants to see. It coalesces **per target**, because the card and a streaming reply are different messages and the budget belongs to the conversation: one key would let a card repaint silently swallow a pending stream edit, and the answer would stop growing with nothing to see. The first update is immediate (a budget nobody is competing for should not cost latency), a failed update clears the slot rather than wedging the rest of the session's display, close drops what is pending rather than repainting a card the session has moved past, and waiting targets take turns so a fast-changing card cannot starve a stream.
- **A conversation now has an address — `ConversationRef`** — Teams has no single API host; each activity names the regional Bot Connector that owns its conversation. Most of what ccdb does is not a reply, so a scheduled task, a webhook and the REST API need that address without an inbound activity to hold. `BotConnector` gained `update_activity`, which is what makes the card and the streaming reply possible at all.
- **The Teams frontend gets its skeleton — `claude_teams`** — a sibling package to `claude_discord`, not a layer under it: both implement `claude_code_core.frontend`, neither imports the other, and the conformance suite now runs Teams against the numbers the *frontend* ships rather than a copy the test owned. `claude_teams/capabilities.py` is that single column — 80,000 characters per message, no bot reactions, files as links, and 1,800 updates per hour per conversation, which is what actually governs streaming (`min_update_interval` resolves to 2.0 s, so a once-a-second live timer would exhaust a long session's budget partway through). `supports_tables` / `supports_headings` / `supports_inline_images` stay off deliberately: Teams can render all three, this surface does not emit the markup yet, and a capability is a promise about the implementation rather than the platform's brochure. `TeamsConfig` turns the ways a Teams deployment silently receives nothing into named exceptions — a `public_host` carrying a scheme or path is the most common one, because `validDomains` takes a bare host — and derives the Teams app id from the bot's application id, since a regenerated manifest id installs as a *different app* and orphans every existing conversation. `python -m claude_teams manifest` writes the installable package, generated rather than checked in so no tenant's ids live in the repository; it declares `ChannelMessage.Read.Group`, without which a channel-installed bot only sees messages that @mention it, and `webApplicationInfo`, because adding SSO later is a fresh consent prompt for every tenant that already installed the app. Icons are valid placeholder PNGs written without an imaging dependency, so a first run produces something installable.
- **The inbound endpoint, and the boundary it is** — Discord's transport was outbound-only; Teams needs a public HTTPS URL with coding-agent sessions behind it, so verification is the whole perimeter rather than an authentication nicety. Signature algorithms are pinned by this package instead of read from the token, because the token header is attacker-controlled and the app id — the obvious HMAC key to try — is printed in the manifest. The token's `serviceUrl` claim must match the activity body's: the body says where the reply goes and the reply carries this deployment's credentials, so an unbound genuine token would aim authenticated outbound calls at a host the caller picked. Signing keys refresh when an unknown `kid` appears rather than on a timer — a rotation would otherwise reject every request until the cache expired, an outage that heals itself and is invisible afterwards — and no more often than every five minutes, because that trigger is reachable by anyone. Bodies are size-capped before parsing, every rejection answers a bare 401, and a failure *after* acceptance is logged and answered 200, since Teams redelivers on 5xx and a 500 would have one user message reprocessed on every retry. The endpoint echoes by default, which is what makes the skeleton provable end to end before a surface exists.
- **A ThreadKey can now be turned back into a place to post — the `frontend_threads` ledger** — every table in the database stores a conversation as a bare integer. For Discord that integer is the thread's snowflake, so "which conversation is 1535820929958027334" answers itself; for a frontend whose ids are strings (Teams uses `19:...@thread.tacv2;messageid=...`) it does not, because the key is a hash and **a hash does not run backwards**. Without this table a deployment could look up a session, learn its key, and have no way to reply to it. The ledger records `(frontend, external_id)` and the parent channel or team, so a conversation can be reopened and a sibling opened beside it. `issue_thread_key()` joins `derive_thread_key()` and answers the question a frontend actually has — not "what does this id hash to" but "what key may I *use*". The difference is collisions: `ThreadKey` is the primary key of the sessions table, so two conversations sharing one does not raise, it lets the second session quietly overwrite the first and leaves a thread showing somebody else's history. Colliding keys are re-derived rather than incremented, because a linear walk marches a whole cluster of collided keys through the same occupied stretch; an exhausted probe budget raises instead of reusing. Discord ids are passed through verbatim rather than hashed, since the snowflake *is* the id every Discord API call needs. `DiscordFrontend` records every conversation it creates or resolves — Discord does not need the ledger to work, but a table that knows half a deployment's conversations answers "where does this key live" wrongly rather than not at all — and a ledger write that fails is logged and ignored, because bookkeeping must not be able to kill a session. Existing deployments are adopted on startup by an idempotent backfill, so months of existing threads are not left unaddressable. Fully Zero-Config: `DiscordFrontend(ledger=...)` defaults to `None` and behaves exactly as before without one.

### Changed

- **A scheduled run reaches its conversation through the frontend, not through Discord — `SessionFrontend` and `DiscordFrontend`** — `ConversationSurface` covered one thread, but the object that *hands threads out* was still `bot.get_channel(...)`, open-coded at every call site. Each site re-decided the same three things — fall back to `fetch_channel` on a cold cache, accept or reject a non-thread channel, and what a missing thread means — and they did not all decide the same way. `claude_discord/frontend.py` gathers them: `resolve_surface(thread_key)` finds an existing conversation and answers **`None`, never an exception**, for one that is gone, because a deleted thread is ordinary and must not take an unattended scheduler loop down with it; `create_surface(parent_id, title)` opens a new one and *is* loud about an unknown channel, which is a configuration error rather than a fact of life. `SchedulerCog` now uses it for both its follow-up and new-conversation paths, so scheduled tasks are the first feature that would work on a second frontend unchanged. A new `check_frontend()` contract — the companion to `check_surface()` — pins the obligations that types cannot: a conversation resolves to the key it was created with, two conversations never share a key, and every surface reports the frontend that minted it. Both `DiscordFrontend` and the new `MemoryFrontend` reference implementation pass it, and a Teams frontend will have to. `BridgeComponents.frontend` exposes the seam so a custom Cog can stop hard-coding Discord into its own logic, and `SchedulerCog(frontend=...)` defaults to Discord so no existing deployment changes.
- **`setup_bridge` no longer opens the database in the middle of wiring cogs** — the ten repositories every deployment needs are built by `build_session_stores()` in the new `claude_discord/stores.py`, which knows nothing about threads or channels. The two jobs were unrelated, and a Teams deployment needs the stores with none of the Discord wiring around them. Behaviour is unchanged; the one shared SQLite path is now visible in one place, which is also where two deployments would silently start sharing sessions.
- One visible difference: a scheduled task's starter message in the channel now reads `🔄 [Scheduled] <name>` in plain text and matches the thread's own title, where it previously used bold and code formatting that the title did not share.

### Changed

- **Approving a tool, a plan or an MCP elicitation now goes through the conversation
  surface — `claude_code_core/approvals.py`** — streaming, tool activity and
  attachments already reached the user through the frontend-neutral protocol, but the
  three moments where a session *stops and waits for a person* still built Discord
  views directly. A second frontend would therefore have produced a session that
  streams text perfectly and then dies at the first permission request, which is the
  first thing any real session hits. Permission, plan approval and elicitation are now
  expressed as `ChoicePrompt` / `FormPrompt` / `prompt_url` — the vocabulary a surface
  already has to implement — so a Teams or Slack surface inherits all three without
  writing any approval logic. Each request's prompt builder and its answer reader live
  side by side in one module, because the choice values a prompt offers are the same
  strings the payload reader matches on; split apart, renaming one would silently turn
  every approval into a denial. The readers treat anything they do not recognise as a
  refusal, so that failure mode fails closed rather than open. **A prompt that cannot
  be posted no longer hangs the session**: previously, if the message carrying the
  buttons failed to send, discord.py's view timer never started, nothing ever timed
  out, and the CLI waited forever on an approval nobody could see — now the surface's
  own clock applies and an unpostable request is injected as denied. Prompts are also
  dispatched off the event loop, so a two-minute approval no longer holds up every
  event queued behind it. `EventProcessor` gains `wait_for_prompts()` and
  `cancel_prompts()` for callers that need to settle or abandon outstanding questions;
  `finalize()` deliberately does neither, leaving an approval the user is mid-way
  through answering free to complete. Discord's rendering is unchanged apart from
  URL-mode elicitation, where the link and the "did it work?" confirmation are now two
  messages instead of one, so that a surface without link buttons can still show a
  usable URL. The superseded `permission_view.py`, `plan_view.py` and
  `elicitation_view.py` are removed; they had no callers left and no public exports.
  AskUserQuestion is untouched and still uses its own persisted view.

- **The project is now called Ebi Agent Chat Relay** — phase 1 of the rename in
  [ADR-0001](docs/adr/0001-adopt-ebi-agent-chat-relay.md), covering brand text only. The
  README and the distribution description carry the new name with a "formerly" note; the
  repository, the `claude-code-discord-bridge` distribution, the `ccdb` command, every
  `CCDB_*` variable, all REST routes, persisted data paths and Python import names are
  **unchanged**, and none of them may change without a separate accepted ADR and a major
  release. Existing installations keep starting exactly as before, and `ccdb` stays the
  short name used throughout the documentation. Translated READMEs are refreshed by the
  existing translation workflow on the next release.

### Fixed

- Report persisted sessions without an in-flight turn as `history` instead of `idle`,
  avoiding the false impression that saved conversations are agents waiting for work
  or user input. `running` remains reserved for turns currently in flight.
- Make Teams sync retries remove obsolete pending-attachment warnings after a client corrects a message's attachment inventory.

### Added

- Add an opt-in owner PR completion gate (`CCDB_PR_COMPLETION_OWNER`) that resumes a
  Discord session once when its `session/<thread_id>` branch still has a non-draft
  open PR. The continuation requires the agent to wait for checks, fix in-scope
  failures, merge, and verify post-merge consumers instead of treating PR creation as
  completion. GitHub lookup failures remain visible but fail open. (#577)

- **Synced Teams threads are filed one folder per company — `thread.org` + `orgs.json`** — every mirrored thread landed directly under the sync root, so a vault that had run for a few months was a flat list of hundreds of folders from every customer at once, with no way to see whose conversation was whose. A sync request may now carry `thread.org` (a free-text company label) and threads are written to `{root}/{company}/{title}--{root_mid}/`. The label is **not** part of the identity — the primary key is still `{team}/{root_mid}` — so relabelling a company moves nothing, re-uploads nothing, and costs nothing. Authority lives in `orgs.json` at the sync root, a hand-editable `team GUID → company` map that **wins over whatever a client sends**: correcting a mistyped name there sticks instead of being overwritten on the next sync. A team the file does not know yet is recorded from the client's label on first use, so labelling one conversation files every later thread of that company automatically. `find_thread_dir` now looks at the root *and* one level in, which is what makes filing an existing vault by hand a safe migration: a thread that has been dragged into a company folder is still recognised, instead of being re-created empty and re-uploading its whole history silently. A thread whose company is unknown stays at the root — no invented `_unfiled` bucket for deployments that never label anything — and an existing folder is never moved on its own, because a folder that walks around the vault would break every wikilink pointing into it. `thread.json` and the generated `README.md` both record the company.
- **An upstream Teams thread can now be mirrored into a folder as raw files, one per message — `POST /api/teams/sync/plan` and `/api/teams/sync/push`** — the running-summary linkage (`/api/ingest/summary`) asked a client to remember how far it had got (a `marker`) and kept only a distilled summary on this side, so the sync state lived in two places and the raw messages lived in neither. Nothing could tell the difference between "there was nothing new" and "the new part failed to travel"; an attachment that never arrived left no trace to notice, and a wrong answer could not be checked against what was actually said, because what was actually said had not been kept. These two endpoints replace that with a have/want negotiation in which **the client keeps no state at all**. `plan` receives the message ids and content hashes the client can see (no bodies — a 1000-reply thread costs tens of KB) and answers with the subset this side is missing or holds at a different hash; `push` stores exactly that subset. Because a *changed* hash and a *never-seen* id are the same question, following an upstream edit is not a separate feature — it is the mechanism, and the superseded version is kept under `_history/` rather than overwritten. Each message becomes `messages/{mid}.md` with YAML frontmatter (author, timestamp, `prev`, hash, `edited`, `deleted`), its attachments land in `messages/{mid}/`, and order is recorded in an append-only `chain.jsonl`; `next` is deliberately **not** stored, since writing it would mean rewriting an existing file on every new reply. The identity is Teams' own: `mid` (the Unix-ms message id, `chatMessage.id` in Microsoft Graph) scoped by the team GUID and the thread's root mid — so the same folders remain valid if a client ever switches from DOM scraping to Graph. The vault directory is the single source of truth: `plan` is answered by reading the files, so deleting a message file makes it come back on the next sync, an interrupted push simply completes on the next one, and pressing the button twice is a no-op. An attachment that could not be stored is never reported as success — it is listed in `thread.json`, in the folder's generated `README.md`, in the push response, and it keeps appearing in `want_attachments` until its bytes actually arrive. Threads live under `{working_dir}/teams` by default, beside the `ingest/` tree (`CCDB_TEAMS_VAULT_ROOT` or `teams_vault_root=` to keep them somewhere else, such as a notes vault); both routes are gated by the existing ingest bearer token, are exposed on the external listener alongside `/api/ingest`, spawn nothing, and re-check path containment at every write. Zero-Config and additive: `/api/ingest` and the summary routes are untouched, so an existing client keeps working unchanged.

## [3.3.0] - 2026-07-27

### Fixed
- **An ingest can no longer lose an attachment quietly — `attachments_manifest` and the delivery verdict** — `POST /api/ingest` saved whatever bytes it was handed and reported `attachments_saved: N`; nothing anywhere knew what `N` *should* have been. A client that dropped a file on the way (a download that 403'd, a size cap, a screenshot captured before it finished loading) produced an ingest **indistinguishable from a complete one**, and the session answered confidently on evidence it never had. For an exported Teams thread the missing file is routinely the one the whole export was for — the log or screenshot on the newest message. ccdb cannot recover bytes a client never sent, so the fix is to make their absence impossible to miss. Clients may now send `attachments_manifest`: one entry per attachment found upstream (`name`, optional `sha256`/`size`/`kind`/`url`/`message`) with a status of `embedded` (bytes are in this request), `linked`, `skipped` or `failed`. ccdb reconciles that declaration against the files that actually landed after zip expansion (`ext/ingest_manifest.py`) — matching on **sha256 first**, then exact name, then the `4_image.png` index-prefix a bundler adds for collisions, then size — and consumes each file at most once, so two attachments named `image.png` can no longer both "match" the single file that arrived. Any shortfall is reported four ways: a ⚠️ block at the **top** of the session prompt naming each missing file and instructing the session not to invent its contents (with a separate callout when the loss is on the newest message), an `ATTACHMENTS-REPORT.md` ledger written beside the files, an `attachments` verdict in the 201 response so the *sending* client — the only party that can re-send — learns of the gap at send time, and a `WARNING` in the log. Set `CCDB_INGEST_REQUIRE_COMPLETE=1` (or `ingest_require_complete=True`) to refuse a lossy ingest with `409` instead of starting a session on partial evidence. Fully Zero-Config and backward compatible: a client that sends no manifest behaves exactly as before and is reported as `verified: false` — never as verified-complete.
- **Two attachments with the same filename no longer overwrite each other** — Teams names every pasted screenshot `image.png`, and both the per-request save path and the zip expander wrote colliding names straight to the same path. One file ended up on disk where two were sent, while `attachments_saved` still said 2 — a loss that looked exactly like success. Colliding names are now disambiguated (`image.png`, `image_2.png`), in the request payload and inside a bundled zip alike.
- **The prompt now groups attachments by the message they came from** — the flat path list gave a session no way to tell which file belonged to the message being replied to, so the newest message's evidence was just one line among 70. When a manifest supplies `message`, paths are grouped under their upstream message and the newest group is marked as the one to read first. Without a manifest the flat list is unchanged.
- **An @mention is answered where it was written — no thread, no lingering session** — the inverted listening policy routed mentions into the existing *new conversation* flow, so a mention in an unlisted channel opened a **thread** and started a session there; worse, that thread was bot-owned, and bot-owned threads were exempt from the mention gate, so everything said in it afterwards kept waking Claude. Both halves are gone. A mention is now handled by its own path (`_handle_mention`): ccdb reads the recent history of that **exact channel or thread**, answers **in place**, and goes quiet until the next mention. A mention in a channel is answered in the channel; a mention in a thread is answered in that thread; nothing creates a thread. The `Thread.owner_id` exemption is removed entirely — outside the no-mention channels *every* run is summoned by name, including in threads ccdb opened itself, because people keep talking to each other in those threads and a run nobody asked for is noise. Threads under a listed no-mention channel are unaffected (that is where the session flow lives). An existing session for that channel/thread is still resumed, so a follow-up mention continues the same work. `build_thread_transcript` is now `build_recent_transcript` and takes any channel, since the mention may land in a channel rather than a thread.

### Changed
- **The listening policy is inverted: configure where ccdb needs *no* mention, not where it exists** — `claude_channel_ids` used to be the exhaustive list of places the bot could ever respond, with `MENTION_ONLY_CHANNEL_IDS` as an exception list carved back out. That is backwards for the common deployment: every new channel or thread is loud by default until someone remembers to list it as an exception, and a mention in an unlisted channel does nothing at all. `claude_channel_ids` now means "channels that need **no @mention**" — everything there, and in threads under them, still runs Claude — while **everywhere else in the guild the bot answers when, and only when, someone @mentions it** (`mention_anywhere`, on by default, `CCDB_MENTION_ANYWHERE=false` to opt out). A channel nobody thought about is quiet by construction, and the bot stays reachable by name from anywhere without a config change. Threads ccdb created itself (`Thread.owner_id` — new conversations, `/fork`, `/api/spawn`) remain conversational without a mention; DMs are never picked up by the mention path; and `allowed_user_ids` is still applied first, everywhere. `mention_only_channel_ids` keeps working but is now largely redundant — *not listing* a channel has the same effect. The routing lives in two small predicates (`_is_no_mention_scope`, `_should_respond`) instead of the previous branchy `on_message`.
- **The AI Lounge Discord mirror is now an explicit, self-healing on/off setting** — the lounge has two layers: the DB-backed messages injected into every session's prompt (the AI-to-AI coordination) and an *optional* mirror that echoes them into a Discord channel for a human to watch. These are now documented as a clean on/off switch: set `COORDINATION_CHANNEL_ID`/`lounge_channel_id` for mirror-on, leave it unset for mirror-off (DB-only) — coordination is identical either way, so a deployment whose humans don't read the channel can turn the mirror off and lose nothing. `_send_lounge_to_discord` also self-heals: if the configured channel is deleted or becomes inaccessible (`discord.NotFound`/`Forbidden`), the mirror disables itself for the rest of the process after logging once, instead of warning on every post. The DB record is always saved first, so disabling the mirror never drops a message. Documented in `README.md` and `.env.example`.
- **Lounge prompt now draws the line between the lounge and the coordination APIs** — with `/api/sessions`, `/api/threads/{id}/messages` and `/api/claims` in place, the lounge was being used for jobs those endpoints do better (discovering who is running, reading another thread, locking a resource). The session-start lounge prompt now states the division explicitly: use the APIs to *discover* and *lock*; use the lounge only for **broadcast announcements with no single target** and **intent announced before acting** — the narrative no structured call carries. The README gains the same guidance, and notes that the Discord-channel mirror is purely human-facing (the AI-to-AI layer is the DB-backed prompt injection, which works with `lounge_channel_id` unset), so a deployment whose humans don't read the channel can drop the mirror without affecting coordination. Prompt/docs only — no behaviour change.

### Added
- **`/model` suggestions are discovered live instead of hardcoded — `CCDB_MODEL_DISCOVERY`** — the autocomplete shipped a constant list, so it went stale on every model launch: it was still offering `opus — Opus 4.8 (powerful, deep reasoning)` the week Opus 5 shipped, and the only fix was a ccdb release. `model_catalog.py` now asks `GET /v1/models` which models the local credentials can see and derives the suggestions from the answer, so a model released this morning appears in the dropdown today and each alias is labelled with what it currently resolves to (`opus — Claude Opus 5 (alias → claude-opus-5)`), followed by the full model ids. The alias→model mapping is computed from the payload (newest model per family), not from a table someone has to remember to edit. This is the one place ccdb talks to the Anthropic API rather than the CLI — the CLI has no way to enumerate models — so it is fenced in accordingly: it reuses the CLI's own auth (`ANTHROPIC_API_KEY` → `ANTHROPIC_AUTH_TOKEN` → `~/.claude/.credentials.json`, expired OAuth ignored), never logs the token, and is strictly non-essential — no credentials, no network, a third-party provider (Bedrock/Vertex/Foundry), an empty result, or `CCDB_MODEL_DISCOVERY=0` all fall back to the static `SUGGESTED_MODELS` list, which is itself now version-free so it cannot go stale in turn. Results are cached for 6h (failures for 5min) because autocomplete fires on every keystroke, and the lookup runs on a worker thread with a 5s timeout using stdlib `urllib` — no new dependencies. Codex suggestions stay static; the Codex CLI exposes no model listing.
- **A mention now carries the thread's recent history — `CCDB_THREAD_CONTEXT_DAYS`** — being summoned into a thread mid-discussion meant answering with no idea what was being discussed: the session either did not exist yet, or it only remembered its own turns and had seen none of the human chatter since. Before replying in a thread it does **not** own, ccdb now prepends a transcript of that thread's last **7 days** (`discord_ui/thread_context.py`). Reading the whole thread would be the obvious move and the wrong one — a months-old thread is a large, mostly irrelevant token bill — so the window is bounded three ways: by age (`days`), by message count (200), and by total size (12,000 chars, trimmed from the *oldest* end so the turns actually being replied to always survive). Each message is truncated at 1,000 chars so one pasted log cannot eat the budget, empty/embed-only messages are skipped, and the triggering message is excluded (it is the prompt). Threads the bot created itself are skipped entirely — it saw every turn there. A thread ccdb cannot read degrades to "no context" rather than failing the run. Set `CCDB_THREAD_CONTEXT_DAYS=0` to disable.
- **Running per-thread summaries for long ingest threads — `GET`/`POST`/`DELETE /api/ingest/summary`** — an ingest client (notably the Teams browser extension) that keeps replying in one upstream thread for months would have to re-export the entire history on every run, because ccdb's "Thread = Session" model spawns a *fresh* Discord thread + Claude session per ingest and remembers nothing about the upstream thread. ccdb now keeps a compact running summary keyed by a client-supplied stable `summary_key` (e.g. the Teams thread's root message id), stored in the new `thread_summaries` table (`database/summary_repo.py`), so the client can send only the **diff** (messages newer than the stored `marker`) while the session still gets full historical context. Flow: (1) before exporting, the client calls `GET /api/ingest/summary?key=…` (external, token-gated) to read the stored `summary` + `marker` and bounds its export to the diff; (2) `POST /api/ingest` accepts `summary_key` + `latest_marker`, and ccdb **injects the stored summary into the prompt** and asks the session to save an updated one; (3) the session calls `POST /api/ingest/summary` (internal control plane, localhost — same trust model as `/api/tasks`) with its own `result_id`, and ccdb resolves the key + advances the `marker` from the ingest row so the read position only moves forward when a summary is actually saved (a failed session re-exports the same diff, never skips messages). `DELETE …/summary?key=` forces a full re-summary. The marker is opaque to ccdb and never handled by the session, so it cannot drift. Fully backward-compatible and Zero-Config: omit `summary_key` and ingest behaves exactly as before. The external listener exposes only the `GET` (read) route; writing a summary is a localhost-only action. `ingest_results` gains `summary_key`/`pending_marker` columns (auto-migrated on existing DBs).
- **Find a past thread by keyword — `/search` slash command and `GET /api/search`** — Discord threads drop out of the sidebar once they auto-archive (they are never deleted, just hidden), and their titles are often vague, so a conversation you remember having becomes impossible to relocate. ccdb already stores a persistent per-thread `summary` (the opening prompt) for every session, so search needs no new storage, no re-indexing, and — crucially — no AI tokens: it is a `LIKE` query over `summary` and `working_dir` (the existing `SessionRepository.search()`), returning each hit with a **Discord deep-link** (`https://discord.com/channels/{guild}/{thread}`) that reopens even an archived thread with one click. The `/search <query>` slash command renders the hits as a scannable embed (origin icon, truncated summary, last-used time, working dir, jump link) and takes an optional `origin` filter (Discord/CLI); `GET /api/search?q=&origin=&limit=` exposes the same lookup on the localhost control plane for other Claude sessions and skills (JSON results with `deep_link`). Both cap results (embed 15, API max 50) and reject a blank query. Zero-Config: auto-wired, works against data ccdb already keeps.
- **Body search over local transcripts — `/search body:True` and `GET /api/search?body=1`** — the follow-up "tier 1" that finds keywords appearing *mid-conversation*, not just in the opening summary, still without spending an AI token. Every Claude Code session already writes its full conversation to `~/.claude/projects/<cwd>/<session_id>.jsonl`; body search `grep`s those transcripts (fast C scan via `create_subprocess_exec`, never `shell=True`, keyword passed as a `-e` fixed-string argument after `--` so it can't be a flag or a regex), then parses only the matched files to pull a readable snippet. A hit is mapped back to its Discord thread via `session_id` (surfaced with the same deep-link and a `💬` badge); a transcript with no thread — a CLI run or an older resume-chain fragment — is shown with a `claude --resume <id>` hint instead. If `grep` is absent it falls back to a bounded pure-Python scan of the most-recent transcripts. The transcript root defaults to the standard `~/.claude/projects` (override via `transcripts_path` / `CLI_SESSIONS_PATH`), so it is Zero-Config wherever Claude Code has run. Shared orchestration lives in `claude_code_core/thread_search.py` (summary + body merge, dedupe by thread) and `claude_code_core/transcript_search.py` (the grep/scan + snippet); the slash command defers before the disk scan to stay within Discord's 3s ACK window. Verified against a real 709 MB / 5,400-file corpus: sub-2s per query, Japanese and English both hit.

## [3.2.0] - 2026-07-22

### Added
- **Cross-session coordination suite — sessions can now see, avoid, talk to, and be warned about each other** — concurrent Claude sessions already announced themselves in the AI Lounge, but a lounge note only carried a `thread_id`; there was no way to act on it. Four new, composable layers close the loop, all Zero-Config (auto-wired via `setup_bridge`, dormant until sessions actually overlap), all on the localhost control plane, and all taught to sessions through the lounge prompt so they get used:
  - **See — `GET /api/sessions` and `GET /api/threads/{thread_id}/messages`** (`session_view.py`). `/api/sessions` merges three sources of truth — the `sessions` table (created-at, working dir, backend), the in-memory `SessionRegistry` (what each live session is doing *right now*), and each thread's latest lounge note — into one ordered view with a `state` of `running` or `idle`. A session with no DB row yet (its ID is minted after the first turn) still appears, because that is exactly the peer most likely to collide. `/api/threads/{id}/messages` reads another thread's conversation oldest-first, per-message truncated. Supports `state=running`, `exclude_thread`, `limit`. Sessions hold no Discord token, so the bot performs the reads.
  - **Avoid — `POST` / `GET` / `DELETE /api/claims`** (`database/claims_repo.py`). An advisory lock a session takes on a free-form resource (`repo:ccdb#issue-123`, `file:...`) *before* working: 201 when acquired, **409 carrying the holder** (thread, note, thread name, and whether it is still `running`) so the refusal is actionable. Every claim has a TTL (default 2h, max 24h, pruned lazily) so a dead session cannot pin a resource forever; names are normalized (case/whitespace); `acquire()` runs in `BEGIN IMMEDIATE` so two racing sessions cannot both win; `force=true` releases a claim held by a peer that died. No LLM round trip — the cheap half of coordination.
  - **Talk — `POST /api/threads/{thread_id}/message`** (`relay.py`). One session delivers a message into another live session (`ClaudeChatCog.deliver_relayed_message`, the same `on_message`-bypass `/api/spawn` uses). `mode=queue` (default) waits for the receiver's turn to finish; `mode=interrupt` SIGINTs it for "stop now" (it can cost uncommitted work, so it is not the default). The text is posted into the thread first (humans see the whole exchange — never a back channel) and wrapped in a marker naming the sender and stating it is not from the human. `RelayGuard` bounds every chain against loops — 2 hops, 60 s per-pair cooldown, 5 messages per sender per 10 min, no self-sends — returning 429 with the reason. The lounge prompt carries a deterministic tie-break rule (commits/PR beat investigation → earlier session → lower thread id) so a negotiation converges instead of ending in mutual politeness; whoever stands down pushes its branch first.
  - **Be warned — automatic collision detection** (`collision.py`, `cogs/collision_watch.py`). The layers above need a session to *say* something; this catches the overlaps nobody announced. `EventProcessor` records the path of every write-type tool call (`Write`, `Edit`, `MultiEdit`, `NotebookEdit`) into a per-thread `FileActivityTracker`, and `CollisionWatchCog` compares those sets across live sessions once a minute. Two live threads writing the same file within 15 minutes is the signal — file paths, not working directories (on a single-user host every session shares `$HOME`, so directory equality flags every pair and means nothing); reads are ignored. It announces via a lounge line (free, no interruption) plus a message in each colliding thread, never relaying into a running session (preempting a turn on a suspicion costs more than the collision), and at most once per 30 min per pair.
- **Codex model & reasoning effort are now configurable (and the model default follows the CLI)** — when the Codex backend is active, ccdb pinned a hard-coded `gpt-5.4` and passed `--model` on every spawn, so it lagged the Codex console default (now `gpt-5.5`) and offered no way to pick a reasoning-effort level. ccdb now (1) **omits `--model` when no model is configured**, deferring to the Codex CLI's own default (`model` in `~/.codex/config.toml`) so it never goes stale again; (2) supports a **per-backend reasoning effort** — `CodexRunner` accepts `effort` and injects `-c model_reasoning_effort=<level>` (validated against `minimal/low/medium/high/xhigh` before it reaches the CLI, defence-in-depth against config injection); and (3) adds a backend-aware **`/effort`** slash command (show/set, thread or global scope) that validates against the active backend's level set (Claude: `low/medium/high/max`; Codex: `minimal/low/medium/high/xhigh`) and persists to `SettingsRepository` via `BackendSettings.current_effort()/set_effort()`. The existing per-backend `/model` command already covers model selection for Codex; effort resolution at spawn time is thread > global, with the legacy Claude-only `/effort-set` honoured as a fallback for Claude. The Claude-oriented env `effort` is no longer forwarded to Codex builds (its levels differ — "max" is not a Codex level). Zero-Config: with nothing set, Codex uses its own `config.toml` model and effort exactly as the console does. `DEFAULT_MODEL["codex"]` is now `None`; `BackendFactory.default_model_for()` may return `None` meaning "defer to the CLI".
- **`/api/spawn` can post file attachments into the new thread** — `POST /api/spawn` now accepts an optional `attachments` array of `{filename, data}` where `data` is base64-encoded file bytes. After the seed prompt is posted, ccdb decodes them and posts them into the freshly created thread as real Discord file attachments (batched at Discord's 10-files-per-message limit, 8 MB/file skip), so a programmatic caller can surface the original files alongside the prompt. The motivating case: a Forgejo Issue/PR watcher that spawns a Discord thread and wants the issue's attachments viewable in-thread. Filenames are reduced to a safe basename (path-traversal guarded, shared with the ingest path); attachments are capped at 10 files / 25 MB total per request and validated (a bad base64 payload returns 400) before any thread is created. Backward-compatible: omit `attachments` and behaviour is unchanged (Zero-Config). Adds `attachments` to `ClaudeChatCog.spawn_session()` and `send_file_blobs()`/`collect_discord_files_from_blobs()` in `discord_ui/file_sender.py`. The body limit raised in v3.1.0 (#446) already covers these base64 payloads.

### Fixed
- **Mention-only threads stay mention-only after the first summon (security)** — the previous fix gated *starting* a session in a human's thread, but kept an escape hatch that let any thread ccdb had a `SessionRepository` record for continue without a mention. That record is permanent, so summoning Claude once with a single @mention into a human conversation thread silently converted it into a Claude thread forever: every later message from an authorised user — including ones addressed to the other people in the thread — started a new run. `_is_thread_reply_allowed()` now decides from Discord's own `Thread.owner_id` instead of the session table: in a mention-only channel, a thread reply runs Claude only when the bot is **@mentioned in that message**, or when **the bot created the thread itself** (`_handle_new_conversation`, `/fork`, `/api/spawn` — all bot-owned). The signal is structural rather than stateful, so it cannot drift as the DB accumulates history, and genuine session threads are unaffected. Behaviour change for consumers relying on the old hatch: replies in a *human-created* thread now need a mention each time.
- **Mention-only channels no longer leak Claude into human-created threads (security)** — `MENTION_ONLY_CHANNEL_IDS` was enforced only for messages posted **directly** in the channel. Because Discord threads are children of a channel, a thread created by a human in a mention-only channel matched the `is_target_thread` branch instead, which routes straight to `_handle_thread_reply` — and that path *starts a fresh session* when no session record exists (the `/api/spawn auto_start=false` path). Result: opening a thread in a "listen only when @mentioned" channel silently spawned a Claude Code session from the thread's starter message, defeating the whole point of the setting. Thread messages now inherit the parent channel's mention-only policy via `ClaudeChatCog._is_thread_reply_allowed()`, with two deliberate escape hatches so nothing existing breaks: the bot is explicitly **@mentioned**, or ccdb **already owns the thread** (a `SessionRepository` record exists, covering bot-created session threads and `/api/spawn` threads). Channels not listed in `MENTION_ONLY_CHANNEL_IDS` are untouched and skip the session lookup entirely. Note that the per-user gate (`DISCORD_OWNER_ID` → `allowed_user_ids`) was always applied first, so this was a channel-policy bypass rather than an unauthenticated-user bypass.
- **Codex sessions receive AI Lounge and concurrency context again** — the shared run helper built ephemeral coordination instructions for every backend, but `CodexRunner.clone()` silently discarded `append_system_prompt`. Codex sessions therefore never saw the required AI Lounge start/end announcements, recent activity from other threads, the active-session registry, or file-delivery instructions. `CodexRunner` now preserves the context across clones and passes it to Codex CLI as a TOML-encoded `developer_instructions` override for both new and resumed sessions. User prompts remain unchanged on stdin, and the coordination context remains outside the persisted conversation history (#480).
- **Codex backend tool embeds no longer get stuck on "Running … Ns elapsed"** — with the Codex backend, every tool execution accumulated in the thread as an in-progress embed whose live elapsed-timer never stopped. Two parser bugs in `parse_codex_line`/`CodexRunner` caused this: (1) Codex's `command_execution` **completion** (`item.completed`) was tagged `MessageType.ASSISTANT`, but `EventProcessor` only cancels a tool's live timer and finalizes its embed on `MessageType.USER` events (`_on_tool_result`) — so the completion (which already carries `tool_result_id` + `output`) was routed to `_on_assistant` and silently dropped, leaving the timer running forever; it is now tagged `MessageType.USER` so the embed updates with the command output and the timer stops. (2) Codex emits `file_changes` as a single atomic `item.completed` with **no** preceding `item.started`; ccdb opened a tool embed + timer for it but no matching result ever arrived, so it also accumulated. `CodexRunner._read_stream` now pairs each atomic tool item (via the new `_atomic_tool_completion` helper, keyed on `_ATOMIC_ITEM_TYPES`) with a synthetic `USER` tool-result so its timer is cancelled immediately. Claude-backend behaviour is unchanged (its tool results already arrive as `user` messages).

## [3.1.0] - 2026-06-18

### Added
- **External ingest listener — reach `/api/ingest` from other LAN hosts without exposing the control plane** — the REST API binds to `127.0.0.1` so the trusted local Claude subprocess can call the full control plane (`/api/spawn`, `/api/tasks`, …) unauthenticated. Reaching it from another machine previously required a separate reverse-proxy process that allow-listed individual paths. ccdb now starts a **second, in-process listener** (`CCDB_INGEST_HOST` + `CCDB_INGEST_PORT`, e.g. `0.0.0.0:8100`) that serves **only** the token-gated ingest surface — `POST /api/ingest`, `GET /api/ingest/{result_id}`, and an open `/api/health` — and nothing else. RCE-capable routes never leave localhost. The ingest handlers enforce the dedicated `ingest_token` (`hmac.compare_digest`) themselves, so no global middleware is involved; and if the host/port are set **without** an `ingest_token` the external listener is refused (logged warning, not a silent unauthenticated exposure). Disabled by default (Zero-Config: unset host/port → only the localhost listener runs, exactly as before). This removes the need for the bolt-on proxy entirely. Adds `ApiServer.external_app` and the `ingest_host`/`ingest_port` constructor params, wired from env in `main.py`.
- **Ingested sessions notify the bot owner (async delivery for long runs)** — an `/api/ingest` session runs unattended and can take many minutes, but the spawned Discord thread was easy to miss (you had to be a member or search for it). The owner (`DISCORD_OWNER_ID`) is now **auto-added to the thread and @mentioned on start**, and **@mentioned again on completion** (success or error) via the result sink. Discord becomes the async inbox: post the work, close everything, and get pinged when the answer is ready — no foreground poller required. The reply is also in the thread and retrievable by `result_id` as before. No-op when no owner is configured; all sends are best-effort (suppressed on failure).
- **ScheduleWakeup support — harness-style `/loop` self-pacing now works over Discord** — Models with the dynamic-pacing harness (e.g. Claude Fable 5) may call a `ScheduleWakeup` tool ("Next wakeup scheduled for ...") expecting the harness to re-invoke them after a delay. In `claude -p` mode no such harness exists, so the session simply ended and the loop silently died. ccdb now detects the `ScheduleWakeup` tool call in the event stream and registers a **one-shot task in the existing SQLite scheduler** (same thread, session resume, `delaySeconds` clamped to the harness range 60–3600 s) so the session wakes up and continues exactly as the model requested. Last call wins (one pending wakeup per thread, name `wakeup-thread-{id}`); the `<<autonomous-loop-dynamic>>` sentinel is rewritten to a meaningful continuation instruction; a `⏰ Wakeup scheduled in Ns` notice is posted to the thread. Auto-wired via `setup_bridge()` when the scheduler is enabled (Zero-Config); when the scheduler is disabled the call is logged and ignored. Adds `TaskRepository.delete_by_name()` and `EventProcessor.pending_wakeup`.
- **Claude Fable 5 model option** — added `fable` to the selectable Claude models (`/model-set`, `/model-show`, and the `ccdb setup` model prompt). Fable 5 is Anthropic's state-of-the-art, token-efficient model and is passed through to the CLI as `claude --model fable`, so no API changes are needed. Like Opus, it is registered as a "slow" model in the statusline stall detector (`_SLOW_MODEL_KEYWORDS`) so its longer autonomous/thinking pauses don't trigger false stall warnings. The stale Opus label was corrected to "Opus 4.8". (Mythos 5 is intentionally not exposed — it is restricted to trusted-access programs.)
- **`/api/ingest` result retrieval — get an interactive session's final answer back** — `POST /api/ingest` previously spawned a Discord thread and ran a Claude session but gave the external caller no way to read the session's final reply (it lived only inside Discord). It now optionally returns a `result_id`, and `GET /api/ingest/{result_id}` polls for `{status, result, error, thread_id, thread_name}` once the session completes. This closes the loop for "round-trip" integrations (e.g. a Teams browser extension that posts a thread + attachments, waits for the answer, and writes it back to Teams) while keeping the Discord thread as a full, observable history of what happened. Implemented with a small dedicated `IngestResultRepository` (status/result/error keyed by `result_id`, capped at 200 rows; the request body is never persisted) and a new `RunConfig.result_sink` callback that `run_claude_with_config` fires exactly once at the session's terminal state — propagated across the internal compact/AskUserQuestion reruns so it never double-fires. Shares the dedicated `ingest_token` auth (independent of `api_secret`). Auto-wired via `BridgeComponents.apply_to_api_server()` (Zero-Config); when `ingest_repo` is absent the endpoint behaves exactly as before (no `result_id`, `GET` returns 503). `EventProcessor.final_assistant_text` exposes the captured reply.
- **Automated dependency maintenance** — added `.github/dependabot.yml` (weekly grouped version updates for the `uv` Python ecosystem and GitHub Actions) plus an `auto-merge-dependabot` job in `auto-approve.yml`. The job auto-approves and enables auto-merge for patch/minor Dependabot PRs once CI passes, and flags major bumps for manual review. Dependabot merges intentionally skip the EbiBot-restart webhook (the lock change reaches the running bot on its next natural restart via `pre-start.sh`), so routine dependency upkeep never disrupts active sessions. The job auto-approves and enables auto-merge for patch/minor Dependabot PRs once CI passes, and flags major bumps for manual review. Dependabot merges intentionally skip the EbiBot-restart webhook (the lock change reaches the running bot on its next natural restart via `pre-start.sh`), so routine dependency upkeep never disrupts active sessions.
- **API provider indicator** — After each session, the statusline footer shows a `🔗 API: <provider>` line indicating which API endpoint the Claude Code CLI is actually using: `Anthropic API (direct)`, `AWS Bedrock`, `Google Vertex AI`, `Azure AI Foundry`, or a custom base URL. The label is derived from the final subprocess environment (`_build_env()`), so CLI env overlays (`CCDB_CLI_ENV_FILE`) and systemd-provided variables are reflected accurately. It is shown even when no `statusLine` is configured, so "which API am I using right now" stays visible after every turn. Adds the `detect_api_provider()` helper (exported from `claude_code_core`) and `SessionBackend.describe_api()` on both `ClaudeRunner` and `CodexRunner`.

### Fixed
- **`/api/ingest` no longer 413s on real payloads (request body limit raised)** — aiohttp's `web.Application` defaults to a 1 MiB request-body limit. A real ingest carries a full conversation thread plus base64-encoded attachments (base64 inflates ~4/3), so the very first realistic Teams thread returned `413 Payload Too Large` against the new external listener. Both the localhost app and the external ingest app now set `client_max_size` to a default derived from the decoded attachment cap (`_MAX_INGEST_TOTAL_BYTES * 4/3 + 1 MiB`, ≈ 67 MiB) so a payload up to the attachment limit actually fits, and the limit is overridable via `CCDB_MAX_BODY_BYTES` / the `max_body_bytes` constructor param. The aiohttp default was the only thing capping ingest size; the documented 50 MiB attachment ceiling is now the real, reachable limit.
- **`spawn_session` no longer crashes on long prompts (Discord 4000-char limit / error 50035)** — `/api/ingest` and `/api/spawn` post the prompt as a Discord *seed message* so `StatusManager` has something to react to. A long prompt (e.g. an ingested Teams thread) exceeded Discord's per-message limit and `thread.send(prompt)` failed with HTTP 400 (code 50035), surfacing to the caller as a 500. The seed is now chunked with the existing fence-aware `chunk_message()` across multiple messages; the **full, unmodified prompt** is still passed to the Claude CLI (which has no such limit), so chunking only affects what's shown in the thread, never what Claude receives.
- **Sessions no longer hang "running" forever when the model asks for confirmation (plan approval / thinking display restored)** — The Claude Code CLI stopped populating `stop_reason` on `assistant`-type stream-json messages; the real completion signal moved to the trailing `message_delta` `stream_event` (which ccdb ignores). The parser still derived `is_partial = (stop_reason is None)`, so **every** assembled content block was mis-flagged as a streaming partial. That silently disabled all `not is_partial`-gated handlers in the event processor — extended-thinking display and, critically, **plan approval (`ExitPlanMode`)**. With the Approve/Cancel UI suppressed, the CLI blocked indefinitely waiting for an answer it could never receive, so the Discord thread sat at "⏺ Session running" with no visible prompt. This surfaced most often with reasoning-heavy models such as **Fable 5** that present plans for confirmation. Each `assistant` message ccdb receives is now correctly treated as a complete content block (token-level partials arrive only as `stream_event`s, which we don't act on). As a side effect, completed text blocks post directly instead of via repeated in-place edits, easing Discord edit rate-limit (HTTP 429) pressure. An explicit `stop_reason` is still honoured when present.
- **Interactive input prompts now mention the requester** — Plan approval, permission
  requests, MCP elicitation, and AskUserQuestion messages mention the user who started
  the session, making it clear that Claude is waiting for button/form input instead of
  still running. Passive controls such as Stop and tool-result expand buttons remain
  silent. Mentions use a restricted `allowed_mentions` payload so only the target user
  can be notified (#419).
- **`pre-start.sh` skipped `git pull` when untracked files were present** — the local-dev-mode guard treated *any* `git status --porcelain` output as a signal to skip the pull. The bot-generated `logs/` directory (from the optional rotating file log handler) showed up as an untracked entry, so the guard silently skipped every pull and a stale checkout persisted across restarts. The guard now considers only *tracked* changes (`--untracked-files=no`), and `logs/` is gitignored.

### Security
- **Cleared all open Dependabot alerts (9)** — bumped `pillow` 12.1.1 → 12.2.0 (2 high-severity advisories), `pytest` 9.0.2 → 9.0.3, `python-dotenv` 1.2.1 → 1.2.2, `idna` 3.11 → 3.18, and `Pygments` 2.19.2 → 2.20.0 in `uv.lock`. Minimum-version floors in `pyproject.toml` were raised (`pillow>=12.2.0`, `python-dotenv>=1.2.2`, `pytest>=9.0.3`) so the vulnerable ranges stay out of future dependency resolutions.

## [3.0.0] - 2026-05-15

### Added
- **OpenAI Codex backend** — `SessionBackend` Protocol with `ClaudeRunner` and `CodexRunner` implementations. Select via `CCDB_BACKEND=claude|codex` (default: `claude`). Both runners satisfy the same async-streaming contract.
- **Backend identification in embeds** — `session_start_embed` title prefix and accent color reflect the active backend (Claude = blurple, Codex = OpenAI teal). `session_complete_embed` prepends a `🧠 <backend> · <model>` chip.
- **`/backend [name] [scope]`** — show or switch backend at runtime. Persists via `SettingsRepository` so the choice survives bot restart. `scope` is `thread`-aware: invoked inside a thread defaults to thread scope, otherwise global.
- **`/model [name] [scope]`** — show or switch the per-backend model. Same scope semantics as `/backend`. Stored separately for each backend so switching backend automatically falls back to the previously-set model for that backend.
- **`CCDB_*` env-var namespace** — unified config under `CCDB_BACKEND`, `CCDB_MODEL`, `CCDB_COMMAND`, `CCDB_CLAUDE_COMMAND`, `CCDB_CODEX_COMMAND`, `CCDB_PERMISSION_MODE`, `CCDB_WORKING_DIR`, `CCDB_ALLOWED_TOOLS`, `CCDB_EFFORT`, `CCDB_CHANNEL_IDS`, `CCDB_MONITOR_ALL_CHANNELS`, `CCDB_DANGEROUSLY_SKIP_PERMISSIONS`. Original `CLAUDE_*` variables continue to work as fallback.
- **`/goal` slash command** — autonomous goal-driven sessions that loop until a user-supplied completion condition is met.
- **`BackendFactory`** — runtime authority for constructing `ClaudeRunner` / `CodexRunner` on demand from static config. Used by `/backend` to swap the active runner without restarting the bot.
- **`BackendSettings`** — thin layer over `SettingsRepository` that resolves the active backend/model with thread > global > env precedence and persists writes from the slash commands. 8 unit tests cover resolution + mutation.

### Changed
- **Project display name** — "Claude Code Discord Bridge" → "Claude & Codex Discord Bridge". Repo, package, and Python module names (`claude-code-discord-bridge`, `claude_discord`, `claude_code_core`) are unchanged to preserve install / import compatibility.
- **`/help` embed title** — now "🤖 Claude & Codex Bot — Help".
- **`SessionBackend` Protocol** — extended to declare `command`, `api_port`, `timeout_seconds`, `dangerously_skip_permissions`, `allowed_tools`, `_build_env()`, and a non-`async` `run()` signature (matches how async generator functions are typed). All consumers (`claude_chat`, `skill_command`, `scheduler`, `webhook_trigger`, `cog_loader`, `discord_ui/views.StopView`, `cogs/run_config.RunConfig`) now type their `runner` parameter as `SessionBackend` instead of `ClaudeRunner`.
- **`CodexRunner.__init__`** — accepts (and stores) `allowed_tools` for Protocol conformance; the field is currently a no-op in argv construction.

### Notes
- Thread-scoped backend/model overrides are persisted by `BackendSettings` and the `/backend` / `/model` commands set them correctly, but `ClaudeChatCog` still spawns sessions via `self.runner.clone(...)` and therefore does not yet consume them. A follow-up will swap that for `BackendFactory.build(...)` so per-thread overrides take effect at session start. Global switches are already in effect immediately.

## [2.2.0] - 2026-04-26

### Added
- **Session search** — `SessionRepository.search()` with keyword search across summary and working_dir, origin filtering, and thread ID include/exclude support
- **Enhanced `/resume` command** — optional `query` parameter for keyword search and `filter` parameter to show only orphaned (deleted thread) sessions
- **Working directory tracking** — Discord chat sessions now save `working_dir` to the database (previously only CLI-synced sessions saved this)

### Changed
- **ResumeSelectView** — shows full `working_dir` path in session descriptions instead of just the last directory component

## [2.1.24] - 2026-04-02

### Added
- **CLI env overlay** (`CCDB_CLI_ENV_FILE`) — inject environment variables into CLI subprocesses via an external `KEY=VALUE` file, read on every CLI invocation without bot restart. Useful for temporary API routing (e.g., Azure Foundry switching) (#359)
- **Job failure triage** (`JobFailureTriageCog`) — auto-investigates scheduler job failures (#357)

## [2.1.0] - 2026-03-15

### Added
- **Chat-only mode** (`chat_only_channel_ids` / `CHAT_ONLY_CHANNEL_IDS`) — when a channel is configured as chat-only, only text responses are shown to Discord; tool embeds, thinking blocks, session start/complete embeds, todo lists, and other technical details are hidden. Permission requests and AskUserQuestion are always shown regardless. Useful for public channels where non-technical users are watching (#315)
- **Poll parameter for `/api/notify`** — REST API notification endpoint now accepts a `poll` parameter to create Discord polls alongside notification messages (#312)
- **Plain text format for `/api/notify`** — `format: "text"` option sends notifications as plain text instead of embeds, useful for simple messages (#311)
- **Thread ID in lounge messages** — lounge messages now include `thread_id` for self-identification after context compaction (#305)
- **Dev worktree mode** — `make dev-on` / `make dev-off` for testing code changes on EbiBot before merging PRs; uses `sys.meta_path` hook to redirect imports to worktree (#294)
- **StatusLine display** — configured `statusLine` shown in Discord after each session (#296)
- **AlertResponderCog** — generic alert-monitoring Cog for custom deployments (#289)
- **Plugin skill auto-discovery** — `/skill` command now auto-discovers skills from installed plugins (#292)

### Changed
- **Dev workflow documentation** — clarified that local testing with `make dev-on` must happen before PR merge, not after

### Fixed
- **Context compaction interrupt** — interrupt cloned runner on `compact_boundary`, not original (#306, #307)
- **Error embed detail** — show exception detail in error embed and handle non-dict todo items (#310)
- **CLI session import** — wire `CLI_SESSIONS_PATH` env, UTF-8 session files, `working_dir` on resume (#302)
- **CI YAML parse error** — resolve YAML parse error in `ci.yml` broken since cc9e2d8 (#298)
- **Auto-approve polling** — extend polling to 15min and cancel stale runs (#297)
- **Thread title cleaning** — harden against noisy model output (#291)
- **`/rewind` implementation** — true rewind with JSONL truncation and turn-selection UI (#293)
- **Auto-merge filter** — exclude external-issue labeled PRs from auto-merge (#286)
- **SkillCommandCog clone** — pass `thread_id` to `runner.clone()` (#284)

## [2.0.0] - 2026-03-06

### Added
- **`/context` slash command** — shows context window usage % with a visual progress bar and autocompact warning when nearing the 83.5% threshold (#265)
- **`/usage` slash command** — shows Claude API rate limit utilization with percentage bar and time-until-reset countdown (#267)
- **`rate_limit_event` parser** — stream-json `rate_limit_event` messages are now parsed and persisted to a new `usage_stats` DB table (upserted per `rate_limit_type`); consumers get up-to-date usage data with zero config (#266)
- **Context stats persistence** — `context_window` and `context_used` are saved to the `sessions` table after each session completes; powers the `/context` command and `/rewind` confirmation

### Changed
- **`/rewind` confirmation now shows context % at reset time** — when context stats are available, the reset message includes "Context was X% full at reset." to help users understand why the session was rewound (#242)
- **`/fork` uses `--fork-session` CLI flag** — forks now create a truly independent session copy with a new session ID; eliminates the `UNIQUE INDEX` violation that crashed the bot when both threads tried to save the same session ID (#243)
- **DB migration: `idx_sessions_session_id` is no longer UNIQUE** — allows forked sessions to coexist without constraint violations; existing databases are upgraded automatically on startup

### Fixed
- **Scheduler sessions are now resumable** — `SchedulerCog` passes `session_repo` to `RunConfig` so sessions created by scheduled tasks are persisted to the DB and can be resumed with follow-up messages (#264)

### Database
- New `usage_stats` table (one row per `rate_limit_type`; upserted on every `rate_limit_event`)
- New columns `context_window` and `context_used` on `sessions` table
- `idx_sessions_session_id` changed from UNIQUE to non-unique index

## [1.9.0] - 2026-03-05

### Added
- **`/tools` slash commands** — `/tools-show`, `/tools-set`, `/tools-reset` for runtime tool permission management from Discord; no bot restart required (#235)
- **`/rewind` and `/fork` slash commands** — rewind a session to an earlier checkpoint, or fork the conversation into a new thread (#239)
- **Thread inbox with auto-classification** — incoming messages in inbox mode are automatically categorized by Claude (`claude -p`) for smarter routing (#249)
- **`THREAD_AUTO_RENAME`** — optionally let Claude AI rename thread titles based on session content; keeps threads self-documenting without manual effort (#257)
- **`CLAUDE_ALLOWED_TOOLS` usage guide** — comprehensive documentation with real-world examples showing how to lock down or expand tool access per deployment (#233)

### Changed
- **All user-facing and Claude-facing strings translated to English** — system prompts, embed labels, and UI text unified in English for broader contributor accessibility (#255)

### Fixed
- **Log injection prevention** — `api_server.py` now sanitizes log messages; ANSI escape sequences and control characters in user-supplied data can no longer corrupt log output (#254)
- **Safer bot-restart resume prompt** — after a bot restart, Claude reports its current state and asks for confirmation before continuing, rather than silently auto-resuming mid-task (#252)
- **Duplicate user mention removed** — session-complete message no longer mentions the requester twice when both `requester_id` and the session owner are set (#244)
- **ebibot-upgrade webhook suppressed** for docs-sync and auto-bump PRs, preventing spurious upgrade cycles that re-triggered on their own merges (#246, #247)
- **Redundant `notify-upgrade` workflow removed** — consolidates upgrade notification into a single path (#251)
- **`asyncio.TimeoutError` on Python 3.10** — now caught correctly everywhere; Python 3.10 raises a different exception class than 3.11+ (#260)
- **`/tools` Ask modal feedback** — "Other" free-text modal submission now updates the prompt message immediately for visual confirmation (#238)

### Security
- **CodeQL scanning** — GitHub Actions workflow added for static analysis of Python source and Actions workflow injection vulnerabilities
- **Command injection prevention in CI** — `notify-failure` workflow now uses `${{ env.VAR }}` instead of direct `${{ github.event.* }}` interpolation in `run:` steps, closing a shell-injection path

### CI / Testing
- **Branch coverage enforced** — `--cov-branch` flag added to CI pytest run; 75% branch coverage required as a merge gate, catching logic paths that line coverage misses (#260)
- **Auto version bump reliability** — switched to PR-based flow with `ADMIN_PAT` to reliably bypass branch protection rulesets on direct pushes (#227, #230)

## [1.8.0] - 2026-03-02

### Added
- **Custom Cog loader** — load external Cog files from any directory via `CUSTOM_COGS_DIR` env or `--cogs-dir` CLI flag; each `.py` file exposes `async def setup(bot, runner, components)`; fault-isolated (one Cog failure doesn't block others) (#220)
- **EbiBot example** (`examples/ebibot/`) — real-world reference implementation with 4 self-contained custom Cogs: ReminderCog, WatchdogCog, AutoUpgradeCog, DocsSyncCog (#220)
- **`CLAUDE_DANGEROUSLY_SKIP_PERMISSIONS` env var** — skip all CLI permission checks without code changes; recommended for ccdb deployments where access is already gated via `allowed_user_ids` (#215)
- **Permission Modes documentation** — README section explaining how `-p` mode interacts with permission modes and why `DANGEROUSLY_SKIP_PERMISSIONS` is the practical choice for ccdb (#218, #219)
- **systemd service file** — production-ready `discord-bot.service` and `scripts/pre-start.sh` for deploying ccdb as a systemd service with auto-update on restart

### Changed
- **`main.py` rewritten** — now uses `setup_bridge()` for full Cog auto-setup instead of manual registration; supports all env vars including `CUSTOM_COGS_DIR`, `CLAUDE_CHANNEL_IDS`, `API_HOST`, `API_PORT`, `CLAUDE_ALLOWED_TOOLS`
- **`cli.py` updated** — `ccdb start` accepts `--cogs-dir` argument

### Fixed
- **Silent exception suppression** — replaced `contextlib.suppress(Exception)` with proper logging + narrowed exception types so errors are visible in logs (#216)
- **Pyright type errors in `main.py`** — `load_config()` return type was `dict[str, str]` but `dangerously_skip_permissions` was a `bool`; moved bool conversion to call site (#223)
- **Branch protection**: added required CI status checks (`test (ubuntu-latest, 3.10/3.11/3.12)`) so PRs with failing CI can no longer be merged

## [1.7.5] - 2026-03-02

### Added
- **File attachment delivery** — when Claude writes files during a session, listing their absolute paths in `.ccdb-attachments` (one per line) causes the bot to upload them to Discord on session complete; opt-in, zero config for consumers (#195, #196)
- **`/help` slash command** — lists all registered slash commands dynamically; CI guard prevents stale command lists from being merged (#199, #200)
- **Mention requester after significant work** — when `requester_id` is set and Claude uses ≥ 3 tool calls, the requesting user is mentioned in the session-complete message so they notice the result in busy servers (#198)
- **Multi-channel support (`claude_channel_ids`)** — accepts a comma-separated list of channel IDs so one bot instance can serve multiple channels (#204)
- **Mention-only channel mode** — a channel can be configured to only respond when the bot is directly @-mentioned, leaving other messages alone (#204)
- **Inline-reply channel mode** — a channel can be configured to reply inline (no thread created), suitable for simple one-off commands (#204)
- **Real-time tool timer** — in-progress tool embeds now show elapsed seconds updated every 5 s so long-running commands are visually trackable (#194)
- **CI failure Discord notification** — GitHub Actions posts a Discord message when any CI job fails, with branch name and run URL (#208)
- **Weekly stale branch cleanup** — a scheduled GitHub Actions workflow deletes branches from closed PRs using the GitHub API (handles squash-merge branches correctly) (#208, #209)

### Changed
- **Tool result collapse threshold** — single-line tool outputs are now shown flat (no expand button); multi-line results (2+ lines) collapse behind an expand button. Previously, only outputs with 4+ lines were collapsed.
- **UpgradeApprovalView re-post** — the upgrade approval button is deleted and re-sent after each upgrade step so it stays at the bottom of the channel and remains visible (#201)
- **Text attachment size limit raised** — per-file limit increased from 50 KB to 200 KB; total limit from 100 KB to 500 KB, matching Discord's auto-conversion of long pastes (#213)

### Fixed
- **Empty tool output stuck embed** — tool calls that complete with no output (e.g. a command that exits silently) now properly clear the in-progress indicator on the embed instead of leaving it stuck.
- **Coordination channel session-end message** — now uses thread ID instead of title to identify sessions, preventing confusion when threads are renamed.
- **Streaming message truncation** — long streaming messages are no longer cut off with `...`; the full content is always forwarded (#203).
- **Pyright type errors for `Thread | TextChannel`** — inline-reply mode introduced `TextChannel` as a valid thread target; type annotations in six internal modules updated to reflect this (#206).
- **Text attachments with missing `content_type`** — Discord auto-converts long pastes to `.txt` files with `content_type=None`; the bot now falls back to file-extension detection so these attachments are read correctly (#211).
- **Large text attachments silently dropped** — text attachments exceeding the old 50 KB limit were skipped without notifying Claude; they are now truncated with a visible notice so Claude always sees the content (#213).

## [1.6.0] - 2026-02-26

### Added
- **Cross-platform CI** — test matrix now covers Linux, Windows, and macOS × Python 3.10/3.11/3.12 (9 parallel jobs); `fail-fast: false` so all OS results are visible in one run (#192)
- **`_resolve_windows_cmd` unit tests** — 7 new tests covering npm wrapper parsing, fallback heuristic, OSError, missing node, and `_build_args` integration; all tests pass on every OS via `tmp_path` fixtures and `sys.platform` mocking (#192)

### Fixed
- **Windows compatibility** — resolved Windows npm `.cmd`/`.bat` Claude CLI wrapper to the underlying Node.js script so `create_subprocess_exec` can launch it; `add_signal_handler` (unsupported on Windows) now skipped on `win32` (#176)
- **Windows CI: UnicodeDecodeError in test_architecture** — `read_text()` calls now specify `encoding="utf-8"` explicitly; previously failed on Windows where the default encoding is locale-dependent (e.g. cp932)

## [1.5.0] - 2026-02-26

### Added
- **Collapsible tool results** — long tool outputs now collapse behind an expand button to keep threads readable (#171)
- **Todo embed pinned at bottom** — TodoWrite embed is delete-reposted so it always stays at the bottom of the thread (#170)

### Changed
- **Refactor: extract prompt_builder and session_sync modules** — split oversized files per project conventions; `claude_chat.py` (601→513 lines) with new `prompt_builder.py`, `session_manage.py` (702→577 lines) with new `session_sync.py` (#188)
- **Dead code cleanup** — removed 7 unused backward-compat re-exports from `_run_helper.py`, fixed duplicate exports in `discord_ui/__init__.py`, removed unused `_build_prompt` wrapper (#188)

### Fixed
- **Image-only messages** — sending a Discord message with only an image (no text) no longer crashes the bot; empty prompt with image URLs is now valid (#186, #187)
- **Image attachment support via stream-json** — images now passed as url-type blocks in `--input-format stream-json` mode instead of the removed `--image` flag (#178, #181, #182)
- **StopView runner reference** — Stop button now correctly targets the active runner after system-context clone (#175)
- **Discord system messages ignored** — thread renames, pins, and other system messages no longer trigger Claude (#172)
- **`is_error:true` result events** — error results from Claude CLI are now surfaced as error embeds in Discord (#184)
- **`stream_event` debug noise** — suppressed noisy debug logs for `stream_event` message type (#185)
- **CI: auto-version-bump** — release PRs with `[release]` tag no longer trigger spurious patch bumps; branch protection respected (#164, #167, #169, #173)

## [1.4.1] - 2026-02-24

### Fixed
- **Critical: CLI subprocess hang on Claude >=2.1.50** — `ClaudeRunner` spawned Claude CLI with `stdin=asyncio.subprocess.PIPE`, which causes Claude CLI >=2.1.50 to block indefinitely even in non-interactive (`-p`) mode. Switched to `stdin=asyncio.subprocess.DEVNULL`. This was causing all Bot-spawned sessions to create threads but never respond. `inject_tool_result()` already handles the missing stdin gracefully (logs a warning and returns) (#162)

### Changed
- Improved debug logging in `ClaudeRunner`: logs cwd at startup, PID after process creation, first 3 stdout lines, and EOF line count for easier troubleshooting (#162)
- README: reorganized Interactive Chat features from flat 23-item list into 5 scannable sub-sections with emoji headers (#160)

## [1.4.0] - 2026-02-22

### Added
- **TodoWrite live progress** — when Claude calls `TodoWrite`, a single Discord embed is posted to the thread and edited in-place on every subsequent update; shows ✅ completed, 🔄 active (with `activeForm` label), ⬜ pending; avoids thread flooding (#46)
- **Image attachments** — Discord image attachments are downloaded to temp files and passed to Claude via `--image`; up to 4 images per message, up to 5 MB each; temp files cleaned up after session (#43)
- **Bidirectional runner** — `ClaudeRunner` subprocess now opened with `stdin=PIPE`; new `inject_tool_result(request_id, data)` method writes JSON to stdin, enabling interactive tool-result injection (#50)
- **Plan Mode** — when Claude calls `ExitPlanMode`, the plan text is sent to Discord as an embed with Approve/Cancel buttons (`PlanApprovalView`); Claude's execution resumes only after approval; 5-minute timeout auto-cancels (#44)
- **Tool permission requests** — when Claude needs permission to execute a tool, Discord shows an embed with Allow/Deny buttons (`PermissionView`) showing tool name and JSON input; 2-minute timeout auto-denies (#47)
- **MCP Elicitation** — MCP server `elicitation` requests surfaced in Discord: form-mode generates a Modal with up to 5 fields from the JSON schema; url-mode shows a URL button with Done/Cancel; 5-minute timeout (#48)

### Changed
- `RunConfig` gains `image_paths: list[str] | None` field for per-invocation image passing
- `ClaudeRunner.__init__` accepts optional `image_paths` parameter; `_build_args()` appends `--image <path>` for each

## [1.3.0] - 2026-02-22

### Added
- **AI Lounge** (`LoungeChannel`) — shared Discord channel where concurrent Claude Code sessions announce themselves; hooks and concurrency notice injected automatically into every session's system prompt (#102, #107)
- **Startup resume** — bot restart auto-resumes interrupted sessions via `on_ready`; `pending_resumes` DB table tracks sessions that need resumption (#115)
- **`POST /api/spawn`** — programmatic Claude Code session creation from external callers (GitHub Actions, schedulers, other Claude sessions) without a Discord message trigger (#113)
- **`DISCORD_THREAD_ID` env injection** — subprocess env includes `DISCORD_THREAD_ID` so Claude can self-register for resume via `mark-resume` endpoint without knowing its session ID (#116)
- **Auto-mark on upgrade restart** — `AutoUpgradeCog` marks active sessions for resume before applying a package upgrade restart, so sessions survive bot upgrades (#126)
- **Auto-mark on any shutdown** — `cog_unload()` marks active sessions for resume on any bot shutdown (not just upgrades), ensuring no session is lost on `systemctl restart` (#128)
- **Automatic worktree cleanup** — `WorktreeCleanupCog` removes stale git worktrees left by finished sessions on a configurable interval (#124)
- **Stop button always at bottom** — Stop button is re-posted to the thread after each assistant message so it stays reachable without scrolling (#119)
- **`BridgeComponents.apply_to_api_server()`** — convenience method to wire `CoordinationChannel` and `SessionRegistry` into the REST API server; also auto-wired in `setup_bridge()` (#103)
- **`session_registry` in scheduler tasks** — `SchedulerCog` passes `session_registry` into spawned tasks so Claude can detect concurrent sessions before starting (#99)

### Changed
- **Layered architecture refactor** — large-scale internal refactor introducing `RunConfig` (immutable per-run config) and `EventProcessor` (stateful stream processor), replacing ad-hoc kwargs threading through the runner stack (#110)
- **Dead code removal** — eliminated unreachable branches and unused symbols identified by vulture, ruff, and coverage analysis (#104)
- **README rewrite** — README now leads with the concurrent multi-session development use case as the primary value proposition (#100)

### Fixed
- `session_start_embed` sent exactly once regardless of how many `SYSTEM` events arrive (#105)
- docs-sync webhook sent from `auto-approve.yml` after PR merge (was missing) (#106)
- Duplicate result text guarded by flag instead of fragile string comparison (#109)
- `spawn_session` made non-blocking via `asyncio.create_task` to avoid blocking the event loop (#117)
- `ServerDisconnectedError` from aiohttp on bot shutdown now handled gracefully (#120)
- Pre-commit hook exits with a clear error message when `uv` is not installed (#121)
- `asyncio.TimeoutError` in `auto_upgrade` now caught correctly on Python 3.10 (#123)
- `asyncio.TimeoutError` in `runner` and `ask_handler` now caught correctly on Python 3.10 (#130)

## [1.2.0] - 2026-02-20

### Added
- **Scheduled Task Executor** (`SchedulerCog`) — register periodic Claude Code tasks via Discord chat or REST API. Tasks are stored in SQLite and executed by a single 30-second master loop. No code changes needed to add new tasks (#90)
- **`/api/tasks` REST endpoints** — `POST`, `GET`, `DELETE`, `PATCH` for managing scheduled tasks. Claude Code calls these via Bash tool using `CCDB_API_URL` env var (#90)
- **`TaskRepository`** (`database/task_repo.py`) — CRUD for `scheduled_tasks` table with `get_due()`, `update_next_run()`, enable/disable support (#90)
- **`ClaudeRunner.api_port` / `api_secret` params** — when set, `CCDB_API_URL` (and optionally `CCDB_API_SECRET`) are injected into Claude subprocess env, enabling Claude to self-register tasks (#90)
- **`setup_bridge()` auto-discovery** — convenience factory that auto-wires `ClaudeRunner`, `SessionStore`, and `CoordinationChannel` from env vars; consumer smoke test in CI (#92)
- **Zero-config coordination** — `CoordinationChannel` auto-creates its channel from `CCDB_COORDINATION_CHANNEL_NAME` env var with no consumer wiring needed (#89)
- **Session Sync** — sync existing Claude Code CLI sessions into Discord threads with `/sync-sessions` command; backfills recent conversation messages into the thread (#30, #31, #36)
- **Session sync filters** — `since_days` / `since_hours` + `min_results` two-tier filtering, configurable thread style, origin filter for `/sessions` (#37, #38, #39)
- **LiveToolTimer** — live elapsed-time updates on long-running tool call embeds (#84, #85)
- **Coordination channel** — cross-session awareness so concurrent Claude Code sessions can see each other (#78)
- **Persistent AskView buttons** — bus routing and restart recovery for interactive Discord buttons (#81, #86)
- **AskUserQuestion integration** — `AskUserQuestion` tool calls render as Discord Buttons and Select Menus (#45, #66)
- **Thread status dashboard** — status embed with owner mention when session is waiting for input (#67, #68)
- **⏹ Stop button** — inline stop button in tool embeds for graceful `SIGINT` interrupt without clearing the session (#56, #61)
- **Token usage display** — cache hit rate and token counts shown in session-complete embed (#41, #63)
- **Redacted thinking placeholder** — embed shown for `redacted_thinking` blocks instead of silent skip (#49, #64)
- **Auto-discover registry** — bot auto-discovers cog registry; zero-config for consumers (#54)
- **Concurrency awareness** — multiple simultaneous sessions detected and surfaced in Discord (#53)
- **`upgrade_approval` flag** — gate `AutoUpgradeCog` restart behind explicit approval before applying updates (#60)
- **`restart_approval` mode** — `AutoUpgradeCog` can require approval before restarting the bot (#28)
- **DrainAware protocol** — cogs implementing `DrainAware` are auto-discovered and drained before bot restart (#26)
- **Pyright** — strict type checking added to CI pipeline (#22)
- **Auto-format on commit** — Python files are auto-formatted by ruff before every commit to prevent CI failures (#16)

### Changed
- **Test coverage**: 152 → 473 tests
- Removed `/skills` command; `/skill` with autocomplete is the sole entry point (#40)
- Tool result embeds show elapsed time in description rather than title field (#84, #88)

### Fixed
- Persistent AskView buttons survive bot restarts via bus routing (#81)
- SchedulerCog posts starter message before creating thread (#93, #94)
- GFM tables wrapped in code fences for consistent Discord rendering (#73, #76)
- Table header prepended to continuation chunks for Discord rendering (#73, #74)
- Markdown tables kept intact when chunking for Discord (#55, #57)
- Concurrency notice strengthened with diagnostic logging (#52, #62)
- Active Claude sessions drained before bot restart (#13, #15)
- `raw` field added to `StreamEvent` dataclass (#20)
- Extended thinking embed rendered as plain code block (#18, #19)
- `notify-upgrade` workflow triggered on PR close rather than push (#17)
- Auto-approve workflow waits for active webhook triggers before merging (#24)

## [1.1.0] - 2026-02-19

### Added
- **`/stop` command** — Stop a running Claude Code session without clearing the session ID, so users can resume by sending a new message (unlike `/clear` which deletes the session)
- **Attachment support** — Text-type file attachments (plain text, Markdown, CSV, JSON, XML, etc.) are automatically appended to the prompt; up to 5 files × 50 KB per file, 100 KB total
- **Timeout notifications** — Dedicated timeout embed with elapsed seconds and actionable guidance replaces the generic error embed for `SESSION_TIMEOUT_SECONDS` timeouts

### Changed
- **Test coverage**: 131 → 152 tests

## [1.0.0] - 2026-02-19

### Added
- **CI/CD Automation**: WebhookTriggerCog — trigger Claude Code tasks from GitHub Actions via Discord webhooks
- **Auto-Upgrade**: AutoUpgradeCog — automatically update bot when upstream packages are released
- **REST API**: Optional notification API server with scheduling support (requires aiohttp)
- **Rich Discord Experience**: Streaming text, tool result embeds, extended thinking spoilers
- **Bilingual Documentation**: Full docs in English, Japanese, Chinese, Korean, Spanish, Portuguese, and French
- **Auto-Approve Workflow**: GitHub Actions workflow to auto-approve and auto-merge owner PRs
- **Docs-Sync Workflow**: Automated documentation sync with infinite loop prevention (3-layer guard)
- **Docs-Sync Failure Notification**: Discord notification when docs-sync CI fails

### Changed
- **Architecture**: Evolved from mobile-only Discord frontend to full CI/CD automation framework
- **Test coverage**: 71 → 131 tests covering all new features
- **Codebase**: ~800 LOC → ~2500 LOC
- **README**: Complete rewrite reflecting GitHub + CI/CD automation capabilities

### Fixed
- Duplicate docs-sync PRs caused by merge conflict resolution triggering re-runs

## [0.1.0] - 2026-02-18

### Added
- Initial release — interactive Claude Code chat via Discord threads
- Thread = Session model with `--resume` support
- Real-time emoji status reactions (debounced)
- Fence-aware message chunking
- `/skill` slash command with autocomplete
- Session persistence via SQLite
- Security: subprocess exec only, session ID validation, secret isolation
- CI pipeline: Python 3.10/3.11/3.12, ruff, pytest
- Branch protection and PR workflow

[Unreleased]: https://github.com/ebibibi/ebi-agent-chat-relay/compare/v2.1.0...HEAD
[2.1.0]: https://github.com/ebibibi/ebi-agent-chat-relay/compare/v2.0.5...v2.1.0
[2.0.0]: https://github.com/ebibibi/ebi-agent-chat-relay/compare/v1.9.0...v2.0.5
[1.9.0]: https://github.com/ebibibi/ebi-agent-chat-relay/compare/v1.8.0...v1.9.0
[1.8.0]: https://github.com/ebibibi/ebi-agent-chat-relay/compare/v1.7.5...v1.8.0
[1.7.5]: https://github.com/ebibibi/ebi-agent-chat-relay/compare/v1.6.0...v1.7.5
[1.6.0]: https://github.com/ebibibi/ebi-agent-chat-relay/compare/v1.5.0...v1.6.0
[1.5.0]: https://github.com/ebibibi/ebi-agent-chat-relay/compare/v1.4.1...v1.5.0
[1.4.1]: https://github.com/ebibibi/ebi-agent-chat-relay/compare/v1.4.0...v1.4.1
[1.4.0]: https://github.com/ebibibi/ebi-agent-chat-relay/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/ebibibi/ebi-agent-chat-relay/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/ebibibi/ebi-agent-chat-relay/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/ebibibi/ebi-agent-chat-relay/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/ebibibi/ebi-agent-chat-relay/compare/v0.1.0...v1.0.0
[0.1.0]: https://github.com/ebibibi/ebi-agent-chat-relay/releases/tag/v0.1.0
