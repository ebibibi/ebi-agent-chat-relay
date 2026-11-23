# Leave work at night, read one message in the morning

Issues #1476 and #1511. Everything here acts only when the person taps or sets something; quiet
hours stay off until set.

## 🌙 Night shift (capture box)

The 🌙 beside the capture box hands the night over in one tap. The relay picks the open threads
whose next move is the agent's own and, after a 2-minute grace period, sends each the night
instruction. Whatever the capture box holds goes along as the **top-priority line**.

Picked: status `idle` (the agent stopped without saying whose move it is), no open decision
request (those wait for the person and show under *decide* in the morning brief), not running,
queued, snoozed, archived or finished, no active wait (the wait resumes it), active within 30
days, not handed over in the last 12 hours. At most `shift_limit`
threads (default 8, 1–20), pinned and focused first. Everything is re-checked when sending.

The instruction says it is automatic and not the person's answer. Its scope is the labels of the
owner's decision-default scopes (`CCDB_DECISION_DEFAULTS_FILE`, see
[decision-defaults.md](decision-defaults.md)) when configured, else "research, implement,
verify, draft, open pull requests (do not merge)". It always forbids merging, sending,
publishing, paying, deleting and production changes, and tells the agent to send a decision card
and stop when the next step is the person's.

The panel under the capture box lists what was picked: ✕ takes one thread out, **Cancel all**
takes every planned one out before sending. Routes: `GET/POST/DELETE /console/api/night/shift`
(`POST {"note": "...", "lang": "ja"}`, `DELETE ?item=t123` or all).

## 🌙 Night-instruction template (reply box)

The 🌙 beside every reply box puts the night-instruction template into that box (the first
`［…］` blank is selected) and sends nothing. Replace the built-in text with your own:
`CCDB_NIGHT_TEMPLATE_FILE=/path/to/night.md` (read on every request, at most 4000 characters).

## ☀️ Morning brief

```bash
curl -s "$CCDB_API_URL/api/secretary/morning?format=markdown&hours=24&base=https://console.example"
```

One message built from the board with no model:

| Part | What |
|---|---|
| ✅ done | threads that finished or became completion candidates within `hours` (default 24, max 168), with links |
| ❓ decide | the top 3 open decision requests in the secretary home's order, with the recommended option, and how many more |
| 🔄 continuing | running threads with the first line of their situation panel's `doing` |
| 🌙 night shift | threads the night shift handed over in the window, with their status now (only when there are any) |

Without `format=markdown` the answer is JSON with the same parts plus `markdown`. `lang=en`
switches the labels. `base` (an http(s) URL) makes the links absolute; without it they are
Console-relative (`/#focus/<id>`). Reading the brief moves no cursor. The relay does not deliver
it by itself: run it from your scheduler at the time you chose and post it where you read things
in the morning.

## 🔕 Quiet hours and settings

```bash
curl -s -X PUT "$CCDB_API_URL/api/secretary/night" -H "Content-Type: application/json" \
  -d '{"quiet_start": "23:00", "quiet_end": "07:00", "urgent_statuses": ["error"], "shift_limit": 8}'
```

Inside the window the Console does not chime or raise desktop notifications, except for items
whose status is in `urgent_statuses` (`error`, `waiting`, `action`, `review`) or whose title
contains one of `urgent_keywords`. Everything still shows on the board. A field left out keeps
its value; `null` clears it. `morning_at` and `morning_place` only record when and where the
brief is delivered. Default: no window. Discord's own notifications follow Discord's settings.
