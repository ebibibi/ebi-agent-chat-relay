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
| `max_chars` | Optional default character limit for the items' `body` (0 or absent: none) |
| `count` | How `body` is counted against the limit: `chars` (default) or `weighted` (see below) |

The slug in the path is 1-40 lowercase letters, digits and hyphens. A deployment has at most 20 pages.

| Item field | Meaning |
|---|---|
| `key` | Required and stable, 1-128 of letters, digits, `_`, `.`, `:`, `-`. A hash of the text works well |
| `title`, `body` | At least one. `body` keeps its line breaks and has a copy button |
| `url` | Optional link the person opens. `http` or `https` only |
| `link` | Optional link to share with the post, shown with its own copy button next to the body's. `http` or `https` only. Keep it out of `body` when the person should decide each time whether to add it (a network that limits posts with links) |
| `source` | Optional note on where the item came from |
| `created_at` | Optional ISO 8601 time with a timezone, for importing earlier items |
| `target` | Optional free label for where the item goes, up to 60 characters (`Instagram · @shop`) |
| `attachments` | Optional list of up to 10 files: an `http(s)` URL, or a file [uploaded to the page](#a-posting-queue) |
| `max_chars`, `count` | Optional limit and counting rule for this item, overriding the page's |

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

## A posting queue

A page works as a queue of posts a person sends by hand, for any network: nothing in the relay
knows a network by name. Pick a page per network or one page for all of them; either way each
item says where it goes in `target`, and the Console offers a filter by target as soon as a list
holds more than one.

For each open item the Console shows the text with a copy button, the attachments (a stored
image as a thumbnail, a stored video on request; both can be saved, and on a phone the share
button hands text and files to the posting app), the link that opens the compose screen, and the
character count against the limit. **Posted** takes an optional link to the post the person just
made; **Skip** sets it aside. Both are one tap, as before.

### Upload an image or video

```bash
curl -s -X POST "$CCDB_API_URL/api/pages/to-post/files" \
  -H "Content-Type: image/png" -H "X-Filename: launch.png" --data-binary @launch.png
# 201 {"url": "/console/api/pages/to-post/files/<token>/launch.png", "name": "launch.png",
#      "type": "image/png", "kind": "image", "size": 48213}
```

The body is the raw file and `X-Filename` its name, percent-encoded if it is not ASCII. Accepted
types, recognised by the file's bytes and not by the header alone:

| Kind | Types | Up to |
|---|---|---|
| image | `image/png`, `image/jpeg`, `image/gif`, `image/webp` | 10 MB |
| video | `video/mp4`, `video/quicktime`, `video/webm` | 200 MB |

Anything else is refused with `415`, a file whose bytes are not the type it claims with `400`, a
larger one with `413`. The page must exist (`404` otherwise). The extension comes from the bytes.
Files are kept under `page_files/<slug>/` next to the database and served only to a signed-in
Console user, inert (`nosniff`, a sandboxing CSP). Deleting the page deletes its files.

### Register a post

```bash
curl -s -X PUT "$CCDB_API_URL/api/pages/to-post" -H "Content-Type: application/json" \
  -d '{"title": "📣 To post", "open_label": "Compose", "done_label": "Posted"}'

curl -s -X POST "$CCDB_API_URL/api/pages/to-post/items" -H "Content-Type: application/json" \
  -d '{"key": "launch-ig", "target": "Instagram · @shop", "title": "Launch",
       "body": "We shipped it. Link in bio.", "max_chars": 2200,
       "attachments": [{"url": "/console/api/pages/to-post/files/<token>/launch.png"},
                       {"url": "https://cdn.example.com/teaser.mp4", "name": "Teaser"}],
       "source": "release job"}'
```

An attachment is a URL string or `{"url", "name"}`. A stored file must have been uploaded to the
same page; an outside URL is shown as a link only, never fetched by the Console. The response
lists each attachment with its `kind` (`image`, `video` or `file`), and every item carries the
limit and rule in force and the measured length: `limit` (0 = none), `count` and `length`.

`GET /api/pages/{slug}/items?target=Instagram%20%C2%B7%20%40shop` lists one target's items, for
a job that watches one network and resolves what it finds there.

### Counting rules

- `chars` counts Unicode code points.
- `weighted` counts the way networks that budget by display width do: a link is 23 whatever its
  length, a character in the basic Latin-to-Hangul-Jamo range and some punctuation is 1, any other
  (CJK, emoji) is 2. It approximates such a network's count; the network's own composer has the
  final word.

## What a page is not

A page is one list with these actions. It does not lay out arbitrary content, and it does not post
anywhere itself. Why a screen is data and not a plugin:
[ADR-0018](adr/0018-add-console-screens-as-registered-data.md); why posting fields are generic
labels and attachments are stored by the relay: [ADR-0028](adr/0028-make-pages-a-posting-queue-for-any-network.md).
