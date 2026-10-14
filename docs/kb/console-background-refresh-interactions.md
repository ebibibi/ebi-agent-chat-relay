---
type: kb
title: Console background refresh interrupts scrolling and Stop feedback
symptom: The thread list jumps to the selected row; Stop appears unresponsive or loses pending feedback.
status: solved
date: 2026-10-14
updated: 2026-10-14
component: [console-client]
tags: [scroll, polling, stop]
scope: context
context: ebi-agent-chat-relay
---

## Symptoms and cause

The thread list used `scrollIntoView()` after every render, including the eight-second board poll. A selected thread outside the viewport therefore pulled the reader back. Keeping only the pixel scroll offset would also move the content being read when items are inserted or removed above it.

The list and detail Stop controls awaited the server before displaying feedback. The live panel changed its own button immediately, but its two-second poll recreated that button without retaining the pending state.

## Resolution

List rendering retains the first visible row, its occurrence within the list, and its viewport offset, falling back to the previous scroll offset if that row disappears. At the top, it stays at the top so new items remain visible. Only explicit navigation (keyboard movement, opening a thread, switching views, or capturing an item) reveals the selection.

A pinned thread can also appear in its tree position. Restoring by thread ID alone selects the first occurrence and jumps back to the pinned section; include the occurrence when restoring the anchor. Selected rows use an outline and explicit label distinct from the unread dot.

All Stop entry points use the same per-item pending state. Buttons immediately disable and display a stopping label or hourglass, with `aria-busy` and an accessible label. Repeated clicks do not create another request. A failed request shows the error and enables retry; an accepted request remains pending until the board reports that the item is no longer running or queued.

## Regression checks

With Playwright and its Chromium installed:

```sh
node --test tests/console_scroll.browser.cjs tests/console_stop.browser.cjs
```

The tests load the actual static client with intercepted API fixtures; they do not stop a real agent. They cover desktop/mobile wheel scrolling, repeated polling, insertions/removals, keyboard navigation, draft/focus retention, and delayed/failing Stop requests from all three entry points. Allow one CSS pixel when checking visibility because Chromium rounds scroll positions.

## Related

- [Issue #1023](https://github.com/ebibibi/ebi-agent-chat-relay/issues/1023)
- [Issue #1024](https://github.com/ebibibi/ebi-agent-chat-relay/issues/1024)
