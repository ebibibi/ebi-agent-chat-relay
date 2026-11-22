---
type: adr
id: ADR-0021
title: Render document images with scoped attachment resources
decision: Use the vendored Markdown parser and sanitizer for file previews, with origin-scoped authentication and a viewport reading mode.
status: accepted
date: 2026-11-11
decided_by: ai
deciders: [Codex]
scope: repository
---

# ADR-0021: Render document images with scoped attachment resources

## Context

The user requested images in Markdown attachment previews and fullscreen reading.
The chat renderer intentionally supports only a small Markdown subset, excluding
images, while attachment previews also lack the source URL and sibling metadata.

## Options considered

- Extend the chat renderer's regular expressions: duplicates a Markdown grammar
  and misses reference images, escaped names and titles.
- Read arbitrary host paths referenced by a document: expands file access beyond
  what the agent delivered and risks exposing unrelated files.
- Fetch all web images through the backend: introduces server-side requests to
  document-controlled destinations.
- Reuse the vendored Markdown parser/sanitizer and keep loading in the browser:
  selected.

## Decision

Use markdown-it with raw HTML disabled and return a DOMPurify-sanitized fragment.
Keep image targets inert until checked. Relative references first match uniquely
named files from the same delivery/message, retaining their real signed URLs.
Otherwise resolve against the document URL. Authenticated same-origin image
requests are limited to the delivered-files route and converted to data images.
No arbitrary workspace or admin endpoint becomes an image source.

Allow HTTPS image origins in img-src. Script and connect policies stay restricted;
no backend proxy is added. External images receive no Console bearer token and
send no referrer. Unsupported and missing images retain a readable explanation.
The existing HTML preview iframe keeps its empty sandbox.

Fullscreen fills the browser viewport with CSS, with restore and Escape controls.
It does not replace the rendered document, and works without browser fullscreen
permission on mobile. Source, download and copy controls remain available.

These implementation choices are AI decisions, not separately approved by the
human; they may be revised while preserving the requested behavior.

## Consequences

Opening a document can contact the HTTPS image servers it names, as a document
viewer normally does. Local referenced images must be delivered with the document
or be reachable through its permitted URL context; host files are not migrated or
auto-collected. Ambiguous attachment names fail visibly rather than guessing.

## Related

- [File previews](../console.md#reading-file-previews)
- [Issue #1323](https://github.com/ebibibi/ebi-agent-chat-relay/issues/1323)
