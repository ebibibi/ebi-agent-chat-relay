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
| `fade_days`, `expire_days` | Optional days before an open item is dimmed, then skipped; 0 = never ([Fade and expire](#fade-and-expire)) |
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
image as a thumbnail, a stored video on request; both can be saved with a normal download), the
link that opens the compose screen, and the
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
| video | `video/mp4`, `video/quicktime`, `video/webm` | 4 GB (`CCDB_PAGE_VIDEO_MAX_MB`) |

Anything else is refused with `415`, a file whose bytes are not the type it claims with `400`, a
larger one with `413`, and one the disk cannot take with `507`. The page must exist (`404`
otherwise). The extension comes from the bytes. Files are kept under `page_files/<slug>/` next to
the database and served only to a signed-in Console user, inert (`nosniff`, a sandboxing CSP).

#### Long videos

The video limit is large enough for a 20-minute 1080p screen recording (about 1.2 GB) kept at a
bitrate where slide text stays sharp. Uploads are streamed to disk, never held in memory, so the
limit costs disk space only. Three settings, read when the relay starts, each with a default:

| Variable | Default | Meaning |
|---|---|---|
| `CCDB_PAGE_VIDEO_MAX_MB` | `4096` | Largest video accepted, in MB. Images stay at 10 MB. |
| `CCDB_PAGE_FILES_MIN_FREE_MB` | `1024` | Free disk space an upload must leave. Checked against `Content-Length` before the first byte, and every 256 MB for an upload of unknown length; refused with `507`. |
| `CCDB_PAGE_VIDEO_RETAIN_DAYS` | `30` | Days a video is kept after the last item listing it was resolved. `0` keeps every video. |

A value that is not a whole number in range is logged and replaced by the default.

Stored files are served with `Accept-Ranges: bytes` and answer `Range` requests with `206`. The
Console fetches a video only when ▶ is pressed; signed in with a session cookie (passkey, sign-in,
Access or a proxy in front of it), the browser then streams it in ranges, so it starts at once,
can seek, and "Save" is the browser's own download, resumable and never held in memory. A
reverse proxy that cuts long responses cuts one range, which the browser asks for again. Signed
in with a bearer token only, which neither a `<video>` element nor a plain link can carry, the
Console still fetches the whole file first, as before; prefer a session sign-in for long videos.
The Console never reads a file into memory to hand it to a share sheet (#1589): a person saves it
and picks the saved file in the posting app.

#### Disk space and clean-up

- Deleting a page deletes all of its files.
- Each upload first clears videos whose items were all resolved (done or skipped) more than
  `CCDB_PAGE_VIDEO_RETAIN_DAYS` ago. A video any open item, or any item resolved within the
  period, still lists is kept; images are never cleared this way (they are small and the done tab
  shows them). A cleared video's item keeps its text and post URL; only the file is gone.
- An uploaded file no item ever lists is kept until its page is deleted, so a generator that
  uploads and then fails to register its item should retry the registration with the same URL
  rather than upload again.
- With the defaults, one 1.2 GB video a day settles at about 36 GB plus the reserve.

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

## One card per piece of content

When the same video or article goes to several networks, register one item per network with the
same `group`. The Console shows each group as **one card** with a row per target, in the order the
rows were registered: what an automation already posted (with a link to the post), what it is
still posting, and what a person posts by hand. A page that never sets `group` lists exactly what
it listed before.

```bash
curl -s -X PUT "$CCDB_API_URL/api/pages/out" -H "Content-Type: application/json" \
  -d '{"title": "📣 Out", "done_label": "Posted", "fade_days": 3, "expire_days": 7}'

# A row an automation posts: nobody is asked to act on it while it is pending.
curl -s -X POST "$CCDB_API_URL/api/pages/out/items" -H "Content-Type: application/json" \
  -d '{"key": "vid-42:net-a", "group": "vid-42", "group_title": "Regions explained (11/25)",
       "target": "Net A", "mode": "auto", "body": "..."}'

# A row a person posts, with the video to hand over.
curl -s -X POST "$CCDB_API_URL/api/pages/out/items" -H "Content-Type: application/json" \
  -d '{"key": "vid-42:net-b", "group": "vid-42", "group_title": "Regions explained (11/25)",
       "target": "Net B", "body": "...", "url": "https://net-b.example/compose",
       "attachments": ["/console/api/pages/out/files/<token>/clip.mp4"]}'
```

| Item field | Meaning |
|---|---|
| `group` | Optional content key, the same characters as `key`. Items sharing it are one card |
| `group_title` | Optional card title, up to 200 characters; else the first item's `title` |
| `mode` | `manual` (default): a person finishes it. `auto`: an automation posts it and reports the outcome |

`mode` is kept when the key is registered again, like `state`: a generator that runs twice does not
send a row that fell back to a person back to the automation.

### Report an automatic post

The automation that posted reports on the same item, so the card shows it beside the rows left:

```bash
# Posted: the row shows "posted automatically" and links the post.
curl -s -X PATCH "$CCDB_API_URL/api/pages/out/items/vid-42:net-a" -H "Content-Type: application/json" \
  -d '{"state": "done", "resolved_by": "net-a-poster", "result_url": "https://net-a.example/p/1"}'

# Failed: the row stays on the card for a person, with the reason.
curl -s -X PATCH "$CCDB_API_URL/api/pages/out/items/vid-42:net-a" -H "Content-Type: application/json" \
  -d '{"state": "open", "mode": "manual", "error": "token expired"}'
```

`error` (up to 500 characters) is accepted only with `state: open` and is cleared when the row is
finished. An `auto` row with no outcome after 6 hours is treated as a row for a person (`stalled`
in the response, and counted in the menu), so a crashed automation never hides a post. A link
found later can be added with another `PATCH` carrying `state: done` and the `result_url`.

### Tabs, the menu count and the filter

A card is on the **open** tab while any of its rows is open, and its finished rows stay on it; on
**done** once nothing is open; on **skipped** when every row was skipped. The menu counts rows a
person should finish (manual rows and stalled `auto` rows). The target filter counts, on the open
tab, the rows still open, and shows only the chosen target's rows.

`GET /api/pages/{slug}/cards?state=open&limit=50` lists cards as the Console shows them: each with
`group` (empty for an item without one), `title`, `state`, `created_at` and every row in `items`.
`GET /api/pages/{slug}/items?group=vid-42` lists one card's rows in one state.

### Buttons on a row

A row a person finishes shows a few plain buttons, usable on any row and in any order (#1589).
No step-by-step walk: the person picks the network they want to post on now.

1. **⬇ Save the video (23 MB)** — one per file, at the top of the card: a normal download of that file, with its
   size (`size` on the attachment, filled when the item is registered). A file of 500 MB or more
   also gets a note that a phone is the wrong place to save it: save it on a computer, or open the
   composer in a browser and attach it there.
2. **📋 Copy text** — the body to the clipboard.
3. **↗ Open the composer** — when the row has a `url` (new tab).
4. **✅ Posted** — marks the row done in one tap; **Skip** sets it aside.

**One copy per content (#1601).** A card's rows often carry the very same video (Instagram and
TikTok usually do). An upload whose bytes (sha256) are already on the page answers the stored
file's URL instead of keeping a second copy, and at start-up copies kept before this were merged
into one, rewriting the items that listed them (a copy no item lists any more is deleted). The
card lists each file once at its top — "⬇ ① Save the video (23 MB) — used on Instagram・TikTok"
— and each row only says which one to use ("🎬 uses video ①"), so a phone downloads it once. A
file saved on this device is marked "saved on this device" (`localStorage`, `page-saved:<url>`).

A card with rows to post says how once, above its rows ("save → copy → pick the saved video in
the posting app and paste → Posted"), instead of under every button.

Why not the share sheet: handing a file to the share sheet means reading the whole file into the
page first, because a phone browser opens the sheet only right after a tap. A long video (a
gigabyte) never finished "Preparing", and a walk that started at the first row made people post
on a network they did not mean to (#1589 replaced the walk of #1564 and #1574).

### Fade and expire

`fade_days` and `expire_days` on the page (0-365, 0 = never, the default) keep the queue from
turning into debt. An open row a person should finish is dimmed (`faded`) once it is older than
`fade_days`, and every open row older than `expire_days` is skipped with `resolved_by: "expired"`.
Expiring runs when the board lists pages, at most once a minute. The age is `created_at`, so an
item imported with an old `created_at` may be skipped at once.

## What a page is not

A page is one list with these actions. It does not lay out arbitrary content, and it does not post
anywhere itself. Why a screen is data and not a plugin:
[ADR-0018](adr/0018-add-console-screens-as-registered-data.md); why posting fields are generic
labels and attachments are stored by the relay, and why a piece of content is one card:
[ADR-0028](adr/0028-make-pages-a-posting-queue-for-any-network.md).
