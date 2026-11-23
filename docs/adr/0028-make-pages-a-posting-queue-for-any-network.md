---
type: adr
id: ADR-0028
title: Make Console pages a posting queue for any network
decision: Page items gain a free-form target label, attachments (outside links or files uploaded to the page and kept by the relay) and a character limit with a named counting rule. No network is named in code, and nothing is posted by the relay.
status: accepted
date: 2026-11-22
decided_by: ai
deciders: [Claude]
scope: repository
extends: ADR-0018
---

# ADR-0028: Make Console pages a posting queue for any network

## Context

A page (ADR-0018) worked well as a queue of posts to send by hand for one network: copy the text,
open the compose link, press "posted", and a job watching the network marks what it finds. Posts
for other networks were still handed over one thread at a time, because an item could not say
where it goes, carry an image or a video, or show whether its text fits.

The pull was to build that for the networks in use today. Their APIs differ and cost money, and
the list of networks a deployment posts to is its own business, not the relay's.

## Decision

Extend the page item, not the page kind, and keep every network out of the code.

- **The target is a label, not an enum.** `target` is free text (up to 60 characters) such as
  `Instagram · @shop`. The Console builds its filter from the labels it sees. A new network or a
  second account needs no release.
- **Attachments are kept by the relay, checked by their bytes.** A generator uploads a file to
  `/api/pages/{slug}/files` and lists the returned URL; an outside `http(s)` URL is accepted too,
  but shown only as a link and never fetched. Only image and video types are kept, recognised by
  magic bytes, served with the type the bytes prove, `nosniff` and a sandboxing CSP, to a signed-in
  person only. Considered and rejected: listing local file paths (the REST API would then read any
  file on the host) and storing files in the conversation file store (it exists only when the
  Console can run sessions, and pages do not need that).
- **Files live with the page.** They sit under `page_files/<slug>/` next to the database, an item
  may list only a file of its own page, and deleting the page deletes them. They are not deleted
  when an item is resolved: the done tab keeps showing what was posted.
- **A limit names its counting rule, not a network.** `max_chars` with `count: chars` (code points)
  or `count: weighted` (wide characters 2, every link 23) covers networks that count either way.
  The relay measures the body once and sends `length` and `limit`, so every client shows the same
  number and the browser keeps no second implementation.
- **The person may say what they posted.** The Console's "done" carries an optional post URL,
  checked like every other link. Who resolved the item is still recorded by the route, never taken
  from the browser.
- **Same number of taps.** Copy, open, done and skip stay one tap each; the URL field is optional.
- **Videos load on request.** A phone opening the list must not download every clip. The Console
  plays a fetched video from a `blob:` URL, the one addition to its CSP (`media-src 'self' blob:`).

## Consequences

- Existing pages, items and REST callers are unchanged: the new columns are added on start with
  defaults, and the new fields are optional in both directions.
- Posting itself stays manual. A network's API can be added later as a job that lists a target's
  open items (`?target=`), posts them and resolves them like any other observer, without changing
  the page.
- Stored videos take disk space until their page is deleted. If that becomes a problem, pruning
  files of items resolved long ago is the next step; it is not built before it is needed.
- The weighted rule approximates networks that count by display width. The network's own composer
  remains the final word, which the docs say.

## Addendum (2026-11-22): the link to share is a field of its own

Some networks limit how many posts may carry a link, so whether to add the link is the person's
call, post by post. With the link inside `body`, they had to delete it by hand every time.

- An item may carry `link` (http/https, checked like `url`). The Console shows it and offers
  "Copy link" beside "Copy text", one tap each, side by side on a phone.
- `length` measures the body only. The link is not counted, because it may not be pasted.
- The generator decides per target whether the link also goes into the body; nothing in the relay
  names a network. Registering a key again refreshes `link` like the other fields, so an open item
  can move its link out of the body without losing its state.
- The share sheet still hands over the body only: the person adds the link there when they choose.

## Addendum (2026-11-23): long videos (#1538)

Some networks take the same long video as YouTube (20 minutes at 1080p, about 1.2 GB). At the
200 MB limit such a video had to be re-encoded near 1.1 Mbps, which blurs the slide text the video
exists to show.

- **The video limit is a setting, 4 GB by default.** `CCDB_PAGE_VIDEO_MAX_MB`; images stay at
  10 MB. A default that fits the long video means nothing has to be configured to post it.
- **The disk decides before the network does.** Uploads were already streamed to disk; now an
  upload that would leave less than `CCDB_PAGE_FILES_MIN_FREE_MB` (1 GB) free is refused with
  `507`, before the first byte when the length is declared and periodically when it is not. A
  full disk under the database is worse than one refused upload.
- **Ranges, not blobs.** The file route already answered `Range` (aiohttp's `FileResponse`); the
  Console did not use it, loading every video whole into a `blob:` first. With a session cookie
  it now points `<video>` and "Save" at the file URL, so the browser streams, seeks and resumes,
  and an authenticating proxy that cuts long responses cuts only one range. A bearer token cannot
  ride on those requests, so token sign-in keeps the blob path. A short-lived signed URL would
  remove that gap but adds a second way to authorise a read; not built until token sign-in is
  used for long videos.
- **Resolved videos are cleared, automatically.** This replaces "not built before it is needed"
  above: at one 1.2 GB video a day the disk fills within weeks. Each upload first deletes videos
  whose items were all resolved over `CCDB_PAGE_VIDEO_RETAIN_DAYS` (30) ago. Clearing on upload,
  not on a timer, needs no scheduler and runs exactly when space is about to be used. A video an
  open or recently resolved item lists is kept, images are kept, and `0` turns it off. A cleared
  item keeps its text and post URL. Orphaned uploads (never listed) are left alone: telling an
  orphan from a file whose item is about to be registered would need a grace period for little
  gain.

