# Proceeding when nobody answers a decision card

A decision card ([ADR-0024](adr/0024-add-a-secretary-mode-built-around-decisions.md)) waits for
the person. For some kinds of work that wait costs more than it protects: the agent recommends a
reversible choice ("write the draft, publish nothing") and the card sits for days. This feature
lets the **owner** list such kinds of work in advance. A card that names one of them proceeds
with its recommended option once its time has passed (#1478).

It is **off by default**. Without the scope file every card waits for a person, exactly as
before, and agents are not told about it.

## Turning it on

Point `CCDB_DECISION_DEFAULTS_FILE` at a JSON file:

```json
{
  "scopes": {
    "drafts": {"label": "Write drafts and notes in the repository; publish or send nothing"},
    "readonly_checks": {"label": "Read-only investigation: no change to any system"}
  }
}
```

The file is re-read on every pass and every request, so editing the list takes effect without a
restart; setting or unsetting the variable itself needs one.

## What agents send

With scopes configured, agents learn an extra field on a ❓ or 👀 request:

```json
"if_no_answer": {"after": "2026-11-23T09:00:00+09:00", "scope": "drafts"}
```

The request is refused (400) when the scope is not in the list, the time has no offset, no
option is marked recommended, or the card is a 📋 action (a task only the person can do).
Without scopes configured, a request carrying the field is refused too, so no agent stores a
promise nothing will keep.

## What happens at the time

A Console loop (every 60 seconds) looks at open cards whose time has passed. For each one whose
scope is still listed, that nobody moved to **Later**, and whose thread is idle (not running,
queued, done, a completion candidate or archived), it posts this into the thread:

> ⏱️ Nobody answered the card "…" by …. Proceeding with the recommended option **…** — …
> This is an automatic default, not the person's answer …

and closes the card with reason `defaulted`. The notice resumes the agent like a reply would,
but it is not attributed to the person and is not counted as their attention. At most one card
per thread and three cards in total proceed per pass.

## What silence never approves

A scope key that names publishing, sending, mail, posting, payment, purchase, billing,
deletion, removal or production (`publish`, `send`, `mail`, `post`, `pay`, `delete`, `prod`, …)
is refused when the file is loaded, and a file that fails validation turns the whole feature
**off**, never on. The harm of a broken guard here is work proceeding without consent, so the
guard fails closed.

## Related

- Finishing an item in the Console (**Done**) now closes its open cards, as 🏁/✅ always did.
  Before, a card outlived the finished thread and came back as soon as anything touched it.
- Agents are told that an open card does not stop independent work in the same thread.
- Decisions of one family are already gathered under the root thread (**Decide together** in
  [console.md](console.md)).
