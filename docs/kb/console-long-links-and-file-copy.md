---
type: kb
title: Keep Console links and attachment contents intact
symptom: A long pasted URL is split across chat messages, or copying a preview loses its tail.
status: solved
date: 2026-10-14
updated: 2026-10-14
component: [console-client, discord-relay]
tags: [clipboard, attachments, long-text]
scope: context
context: ebi-agent-chat-relay
---

## Cause and resolution

Discord limits a message to 2,000 characters. The relay previously used the general display
chunker on user prompts, which cuts an uninterrupted URL. Keep long relayed prompts in a
single UTF-8 attachment instead; the agent still receives the original full prompt. Console
capture titles have their own 200-character limit, so keep an overlong first line in the note
instead of dropping the text beyond that title.

Previewing deliberately limits text to 2 MB and CSV tables to 1,000 rows. Copy must read the
original blob without those limits, not the rendered DOM or the preview's truncated text.
Use the same operation in the attachment actions and preview toolbar. Start the asynchronous
ClipboardItem write in the click gesture where supported, with writeText as the fallback.
Report permission/fetch failures and re-enable the action for retry.

## Verification

Synthetic long URLs are used in `tests/test_thread_relay.py` and `tests/console_long_text.cjs`.
Never put a real OAuth callback code into a test fixture. `tests/console_copy.browser.cjs`
checks Japanese text, a file larger than the preview limit, both attachment locations,
clipboard denial and retry. Local-file extraction ignores adjacent attachment button text.

- [Issue #1026](https://github.com/ebibibi/ebi-agent-chat-relay/issues/1026)
- [Issue #1027](https://github.com/ebibibi/ebi-agent-chat-relay/issues/1027)
