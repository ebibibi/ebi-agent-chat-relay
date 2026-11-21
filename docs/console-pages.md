# Console pages

A page is a screen in the Console's menu that a session, a script or a scheduled job adds by
registering data. It lists things only a person can finish: a post to send by hand on a network
with no affordable write API, a form on a portal, a message to approve.

No code runs in the browser and nothing needs a redeploy. The Console draws every field as text and
every link as a link that opens in a new tab.

## Add a screen

```bash
curl -s -X PUT "$CCDB_API_URL/api/pages/to-post" -H "Content-Type: application/json" \
  -d '{"title": "📣 To post", "open_label": "Compose", "done_label": "Posted"}'

curl -s -X POST "$CCDB_API_URL/api/pages/to-post/items" -H "Content-Type: application/json" \
  -d '{"key": "launch-1", "title": "Launch note", "body": "We shipped it.",
       "url": "https://example.com/compose?text=We%20shipped%20it.", "source": "release job"}'
```

The page appears in the menu at the next board refresh, with the number of open items.

| Page field | Meaning |
|---|---|
| `title` | Required, up to 60 characters. Shown as given, so choose the language yourself |
| `open_label`, `done_label`, `skip_label` | Optional button labels; the Console's own words otherwise |
| `position` | Optional order in the menu |

The slug in the path is 1-40 lowercase letters, digits and hyphens. A deployment has at most 20 pages.

| Item field | Meaning |
|---|---|
| `key` | Required and stable, 1-128 of letters, digits, `_`, `.`, `:`, `-`. A hash of the text works well |
| `title`, `body` | At least one. `body` keeps its line breaks and has a copy button |
| `url` | Optional link the person opens. `http` or `https` only |
| `source` | Optional note on where the item came from |
| `created_at` | Optional ISO 8601 time with a timezone, for importing earlier items |

Open items are listed oldest first, so working from the top clears them in order.

**Registering a key again never changes its state.** It refreshes the text and link and answers
`200` with `"status": "exists"` instead of `201`, so a generator that runs twice cannot bring back
what a person already finished.

## Resolve an item

A person presses a button in the Console, and the record says `person`. Automation that can observe
the outcome resolves the item itself and says who it is:

```bash
curl -s "$CCDB_API_URL/api/pages/to-post/items?state=open"

curl -s -X PATCH "$CCDB_API_URL/api/pages/to-post/items/launch-1" -H "Content-Type: application/json" \
  -d '{"state": "done", "resolved_by": "post-watcher", "result_url": "https://example.com/posts/1"}'
```

`state` is `open`, `done` or `skipped`. Going back to `open` clears who resolved the item and the
result. Finished items stay on the page's other tabs, where a person can bring them back.

`DELETE /api/pages/{slug}` removes a page and its items.

## What a page is not

A page is one list with these actions. It does not lay out arbitrary content. Why a screen is data
and not a plugin: [ADR-0018](adr/0018-add-console-screens-as-registered-data.md).
