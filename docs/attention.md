# Attention metering — how much of *your* time the threads take

The relay lets one person run many agent sessions in parallel. Agent time is cheap and is
metered elsewhere (runs, sessions, rate limits). What runs out first is the operator's own
attention: reading a reply, deciding what to do, and typing the next instruction. Attention
metering estimates that time per day and per thread, so a weekly review can ask "where did my
time actually go?" instead of guessing.

It measures the **human**, not the agent.

## What is recorded

For every human message that reaches a session — on Discord, on Teams and from Ebi AI Console —
one row is written to the `human_activity` table in the deployment's session database:

| column | meaning |
|---|---|
| `frontend` | `discord`, `teams` or `console` (a Console reply into a chat thread is filed under that thread's own frontend) |
| `conversation_id` | the thread / channel / Teams conversation the message went to |
| `parent_id` | the parent channel, when there is one |
| `thread_title` | the thread's title at the time (newest title wins in reports) |
| `author_id` | Discord user id, Teams `from.id`, or the Console sign-in identity (`oidc:<email>`, `passkey:<name>`, `token`) unless `CCDB_ATTENTION_CONSOLE_AUTHOR` names one id for all of them |
| `occurred_at` | when the message was sent, UTC |
| `char_count`, `attachment_count` | how much was written / attached |
| `message_id` | the platform's message id (the `console_messages` id for Console conversations); `(frontend, conversation_id, message_id)` is unique, so recording is idempotent (Teams activity ids are only unique within a conversation) |
| `source` | `live` — recorded as it reached a session; `backfill` — read back from history |

**No message text is stored.** Only the metadata above. One caveat: when a message opens a new
Discord thread, the relay names that thread after the first 100 characters of the message, and
the row stores that exact name. So the title of a new thread can repeat the opening of its first
message. Every member of the channel already sees the same title in the sidebar. A later rename
reaches reports through the next message in the thread, because reports use the newest title.

What does **not** count:

- bot accounts, including the relay's own posts;
- webhooks (CI triggers, alert relays);
- everything the relay injects itself: `/api/spawn`, `/api/ingest`, thread-to-thread relays,
  scheduled tasks — all of them post as the bot or a webhook;
- Discord system messages (pins, renames, joins);
- messages that reach no session (an unlisted channel without an @mention, a user outside
  `allowed_user_ids`).

### Ebi AI Console

The rule above — "the author decides" — cannot work for the Console. A reply sent from the
Console into a Discord thread is posted *by the bot*, and the Console's own conversations store
the relay's turns under plain names (`wait`, `restart`, `thread <id>`) next to the person's.
So the Console meters at the entry point instead: only what a signed-in person sends counts.

Counted: starting an item (its title, note and extra prompt), a reply, and a skill picked in the
reply box. Not counted: wait and restart resumes, relays from other sessions, the Resume
button's canned prompt, the Stop button, and the Ebi Workspace brief the Console adds to a
prompt (it is stored as a message of its own). The list of attached files a reply carries counts
as attachments, not characters. A message too long for one reply is kept as `message.txt` and
counts as one attachment.

If you use both a chat frontend and the Console, set `CCDB_ATTENTION_CONSOLE_AUTHOR` to your chat
id (for Discord, the same value as `DISCORD_OWNER_ID`). Bursts are formed per author, so with one
id a Discord message and a Console reply a minute apart are one burst, not two lead-ins.

## The estimate

A message timestamp says when someone *finished* typing, not how long they spent. The estimate
fills that gap with three assumptions:

1. **Bursts.** One author's messages across *all* threads are sorted by time. A message sent
   within `idle_gap` of the previous one belongs to the same burst; a longer silence starts a new
   one. Switching between threads within a burst is still one stretch of attention.
2. **Lead-in.** A burst costs its span (first to last message) **plus a lead-in** for the
   reading and deciding that came before its first message. A lone one-line reply therefore costs
   the lead-in, not zero.
3. **Proportional split.** A burst's minutes are shared among its messages by weight:
   characters written plus `attachment_weight` (default 50) characters per attachment, so a
   pasted screenshot counts like a short sentence instead of nothing. When a whole burst has
   no weight at all, each message gets an equal share. Summed per thread, this splits the burst across the threads it
   touched by characters written; summed per day, a burst that crosses local midnight is split
   between the two days instead of landing whole on one side.

Example (defaults): replies at 10:00 in thread A (300 chars) and 10:08 in thread B (100 chars),
then nothing until 11:00. One burst: 8 min span + 2 min lead-in = 10 min, of which A gets 7.5
and B 2.5.

Known biases, by design:

- Reading a reply you never answer is invisible. So is thinking away from the keyboard.
- Long silences inside a stretch of reading (over `idle_gap`) start a new burst and add another
  lead-in rather than counting the silence.
- Days are local days in the configured timezone.

Every report says `"estimate": true` and lists the parameters it was produced with. Compare
numbers produced with the same parameters.

## Configuration

All optional; recording is on by default.

| variable | default | meaning |
|---|---|---|
| `CCDB_ATTENTION_ENABLED` | `true` | `false` stops recording. Existing rows stay readable. |
| `CCDB_ATTENTION_IDLE_GAP_MINUTES` | `10` | gap that ends a burst |
| `CCDB_ATTENTION_LEAD_IN_MINUTES` | `2` | minutes added before each burst |
| `CCDB_ATTENTION_ATTACHMENT_WEIGHT` | `50` | characters one attachment counts as in the split |
| `CCDB_ATTENTION_TIMEZONE` | host local zone | IANA zone for day buckets, e.g. `Asia/Tokyo` |
| `CCDB_ATTENTION_CONSOLE_AUTHOR` | the sign-in identity | author id Console messages are charged to |

The parameters apply when a report is computed, not when rows are recorded, so changing them
re-estimates all history. A malformed value stops startup with a message naming the variable.

To erase the data, delete the rows: `DELETE FROM human_activity;` in the session database.

## Reading it

### `/attention` (Discord slash command)

Ephemeral, and only about the person who runs it: today, the last 7 days (with a daily
average), and the top 5 threads of those 7 days.

### `GET /api/attention`

Same authentication as every other control-plane endpoint (`Authorization: Bearer
$CCDB_API_SECRET` when a secret is configured).

| parameter | default | meaning |
|---|---|---|
| `from`, `to` | last 7 days ending today | local days `YYYY-MM-DD`, inclusive, at most 366 days |
| `group_by` | `day` | `day` or `thread` |
| `author` | all authors | restrict to one author id |
| `include_backfill` | `true` | `false` estimates from live rows only |

Dates must fall between the years 1970 and 9998; anything else is a `400`.

```bash
curl -s -H "Authorization: Bearer $CCDB_API_SECRET" \
  "$CCDB_API_URL/api/attention?from=2026-09-28&to=2026-10-04&group_by=thread&author=123"
```

```json
{
  "estimate": true,
  "parameters": {"idle_gap_minutes": 10.0, "lead_in_minutes": 2.0,
                 "timezone": "Asia/Tokyo", "weighting": "characters (...)"},
  "from": "2026-09-28", "to": "2026-10-04", "group_by": "thread", "author": "123",
  "include_backfill": true, "sources": {"backfill": 40, "live": 120},
  "total_minutes": 412.5, "total_messages": 160,
  "rows": [
    {"frontend": "discord", "conversation_id": "1529...", "parent_id": "1528...",
     "title": "Fix the release pipeline", "minutes": 96.0, "messages": 31,
     "last_active": "2026-10-03T12:41:09.512+00:00"}
  ]
}
```

With `group_by=day`, each row is `{"day", "minutes", "messages", "threads"}`.

## Backfill

New rows only start when the feature is deployed. To seed history from Discord:

```bash
ccdb attention-backfill --guild <guild id> --since 2026-09-01
```

The command reads `DISCORD_BOT_TOKEN` and `DISCORD_OWNER_ID` from the environment or from
`--env` (default `.env`). It writes to the deployment's session database; use `--db` to write
somewhere else. It walks the guild's text channels, its active threads, and the public archived
threads of every channel. It also walks private archived threads where the bot may read them.
It records human messages since `--since`; use `--until` to set the last day.

- **Whose messages:** only `DISCORD_OWNER_ID` by default. `--author ID` (repeatable) picks other
  authors, and `--all-humans` records everyone. Other people's messages are not the operator's
  attention, so recording everyone needs that explicit flag.
- **Marking:** every backfilled row has `source = 'backfill'`.
- **No double counting:** a message whose id is already recorded is skipped, whether the live
  hook or an earlier backfill recorded it, and even under another conversation. Re-running is
  safe.
- **Overlap warning:** when live rows already exist in the range, the command warns. Backfill
  counts every message by the chosen authors, not only messages that reached a session, so the
  backfilled days and the live days are not quite the same measure. Use
  `include_backfill=false` when you want only the live measure.
- Discord rate limits are honoured (`429 retry_after` and exhausted buckets).

### Ebi AI Console history

The Console keeps every conversation's transcript in `console_messages`, so its history can be
recovered exactly:

```bash
ccdb attention-backfill-console --since 2026-10-01
```

It applies the same rules as live recording (only signed-in people; no waits, restarts, relays,
Stop or Resume) and keys rows by the transcript's message id, the same key live recording uses.
Re-running it, or running it over days already recorded live, adds nothing twice. Rows are
marked `source = 'backfill'`. `CCDB_ATTENTION_CONSOLE_AUTHOR` applies here as well. Console
replies into Discord threads before this was recorded live are not recovered: they are bot
posts in Discord, and `attention-backfill` records people only.

Backfill limitations:

- It cannot know which past messages reached a session. Rows are marked so that you can
  exclude them.
- Deleted threads are gone from the API. Their opening message still exists in the parent
  channel, and is counted under the channel rather than the vanished thread.
- Thread titles are the titles at backfill time.
