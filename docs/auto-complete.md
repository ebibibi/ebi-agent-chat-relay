# Evidence-based auto-completion

An agent normally only *proposes* that a thread is finished (`/candidate`, the 🏁 marker) and the
thread waits for a human to confirm it. When the work is demonstrably over — a deliverable exists,
the checks passed, nothing is left waiting — that confirmation is pure queueing. With
auto-completion on, such a thread closes itself.

It is **off by default**; nothing changes until the operator sets `CCDB_AUTO_COMPLETE`.

## Rules

- **Evidence, never time.** No candidate is ever completed because it sat unanswered. Silence is
  not approval.
- **Measured where ccdb can measure.** ccdb checks its own tables, not the agent's account:
  - no active wait (`/api/waits`) on the thread;
  - no open decision request on the thread (a deferred one still counts);
  - every child thread is finished (🏁 / ✅ / Done), not running, with no wait or open request.
- **The agent's word only for what ccdb cannot see:** what it delivered, how it verified it, and
  that it stayed within what it was asked to do.
- **External effects are never approved here.** Anything listed in `external_effects` (a send, a
  publication, a payment, a production deletion) keeps the thread a candidate.
- **Teams reply-draft threads** (Teams Channel Exporter) are never completed automatically.

## How an agent asks

The same `/candidate` call, with a body:

```bash
curl -s -X POST "$CCDB_API_URL/api/threads/$DISCORD_THREAD_ID/candidate" \
  -H "Content-Type: application/json" \
  -d '{"evidence": {"deliverables": ["https://github.com/o/r/pull/12"],
       "verification": ["PR #12: all checks green, merged, deploy run 123 succeeded"],
       "within_scope": true, "external_effects": []}}'
```

The response carries the verdict:

```json
{"status": "marked", "thread_name": "🏁 …",
 "auto_complete": {"mode": "on", "eligible": true, "complete": true, "reasons": [], "scheduled": true}}
```

`reasons` lists every failed check, so an agent (or a person reading the log) sees why a thread
stayed a candidate. Malformed evidence is refused with 400 before any marker is set. Agents are told
about the body only while the feature is enabled.

## When it completes

Not during the call. The call comes mid-turn and the agent's own final message follows it; a Done
recorded before that message would look stale to the board and be undone. ccdb therefore waits for
the turn to end, checks that the 🏁 is still there (a human reply removes it), measures everything
again, posts a note with the deliverables, the verification and "reply to reopen", and then marks
the thread done exactly like the Console's Done button.

A human reply afterwards reopens the thread as usual. A restart while a completion is pending drops
it: the thread simply stays a candidate.

## Configuration

| Variable | Values | Default |
|---|---|---|
| `CCDB_AUTO_COMPLETE` | `off`, `dry-run` (report the verdict, never complete), `on` | `off` |
| `CCDB_AUTO_COMPLETE_SCOPE` | `children` (threads spawned by another thread), `all` | `children` |
| `CCDB_AUTO_COMPLETE_REQUIRE_VERIFICATION` | `1`, `0` | `1` |

An unknown value turns the feature off: a typo must never widen what closes on its own.

Code: `claude_discord/auto_complete.py` (rules), `claude_discord/ext/auto_complete_api.py`
(measuring and finishing). Issue #1477.
