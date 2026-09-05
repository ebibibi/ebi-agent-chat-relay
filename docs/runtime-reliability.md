# Runtime recovery and control-plane boundaries

`SESSION_TIMEOUT_SECONDS` is an **idle** timeout. Claude, Codex and the local
Codex backend reset it when a stdout line arrives. AG-UI resets its socket
read deadline when data arrives. Active streams may outlive the configured
duration; a silent tool still needs an appropriately generous idle budget.
Set the value to `0` to disable the deadline explicitly. Timeout handling
terminates the runner using its existing cleanup path.

Relay-owned inbound credentials are stripped from CLI environments, including
after a Claude environment overlay. Provider API credentials remain available;
the local control-plane secret is injected explicitly. This limits accidental
credential exposure through environment dumps; it is not a filesystem sandbox.

## Attachment delivery

The existing `.ccdb-attachments-<thread_id>` marker remains the request format.
The relay moves its contents into a sibling `.pending` outbox before sending
and checkpoints each successful file. If a send fails, the user receives a
warning and pending files retry at the next successful session completion.
New markers merge with pending paths. Missing and oversized Discord files
remain pending rather than being acknowledged as delivered.

Already acknowledged files are not replayed when a later file fails. A crash
or ambiguous network failure after Discord accepts a file but before the local
checkpoint can still duplicate that final file; this is at-least-once delivery,
not an exactly-once guarantee. Teams retains its existing consent-card delivery
semantics. Files in temporary worktrees must remain available until delivered.

## Control plane

The standalone server accepts an optional `CCDB_API_SECRET`. When configured,
callers send `Authorization: Bearer <secret>`; session runners receive the same
secret as `CCDB_API_SECRET`. Existing direct loopback clients keep working
without authentication when the optional setting is absent.

The privileged app rejects external Host/Origin values and forwarding headers.
The separate token-gated ingest app is unaffected. A non-loopback control-plane
bind requires a secret. An operator deliberately using an authenticated proxy
can set `CCDB_CONTROL_PLANE_HOST_GUARD=0`, while enforcing authentication and
access restrictions at that proxy. Never expose the control plane as a public
ingest endpoint: a proxy that rewrites Host and strips forwarding headers can
hide from this defense-in-depth check.

## Startup rollback

Automatic rollback handles **import-validation failures**. Dependency sync
failure stops startup immediately and is retried on the next start; it does
not enter the import rollback path.

The pre-start script records import-validated main commits in the repository's
Git metadata. If a later import fails, it selects a clean detached worktree of
the recorded version as the runtime import source and synchronizes that
version's dependencies into the service environment. The main checkout and
branch history stay intact; no automatic revert commits are created.

On the next start the runtime selector is cleared and main is updated and
validated again. Failure selects the saved version again; success records the
new working commit. The runtime hook is reinstalled after dependency sync and
covers the core, Discord and Teams packages. User development overrides take
precedence and do not become verified main checkpoints. A missing checkpoint,
modified saved worktree or local tracked edits causes rollback to refuse.

Saved fallback worktrees live under Git metadata in `ccdb-rollback-checkouts/`;
the active selection is `ccdb-runtime-root`. They are retained for recovery.
The fallback version's dependencies are synchronized before import validation;
this does not verify a live Discord connection or every runtime failure.

Startup worktree cleanup is **report-only** (`--dry-run`), including pruning.
Actual cleanup requires an explicit operator request after confirming that the
target worktrees have no active writers. This matters because even Git's normal
removal can discard an ignored file created after a clean-status check.

The cleanup script retains dirty, untracked and ignored files and all closed
but unmerged PR branches. Removal additionally requires a merged PR and ancestry
or patch equivalence with main. Normal Git safety checks remain enabled; a
refusal never triggers forced deletion.
