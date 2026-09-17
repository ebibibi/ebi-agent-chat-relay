# Explicit escalation — `/ask`

The companion to the [local-model backend](local-backend.md). A thread does its
work on a model you control; when it needs research, a second opinion or a
plan, **one** anonymized question goes out and one answer comes back.

```
/ask <question>
   │
   ▼
anonymization gateway ── identifying terms → stable aliases
   │
   ▼
external CLI, isolated ── no project context, no files, no tools
   │
   ▼
answer, restored to real names, in the thread
```

## Why this is stronger than anonymizing a normal chat turn

An ordinary session gives the external model your repository and a shell. The
[gateway](anonymization.md) can only anonymize the message text; everything the
agent then *reads* goes to the API on its own.

A consult has none of that. The external model receives one string and nothing
else, so **the audit log is a complete record of what left the machine** — which
is the property that makes the claim checkable rather than reassuring.

## The isolation, and why it is verified rather than documented

Every consult is checked immediately before the process starts. If any of these
does not hold, nothing is sent:

| | Why |
|---|---|
| `--setting-sources ""` | Otherwise CLAUDE.md, skills and memory are sent |
| `--tools ""` | An allow list. Naming tools to forbid cannot keep up |
| `--strict-mcp-config` | Otherwise the operator's MCP servers come along |
| Empty temporary directory as cwd | Nothing local to read |
| No tool named anywhere in the argv | An override can only widen the empty list |
| `--` before the prompt | Without it the variadic tool list eats the prompt |

The first row is the one that surprises people. Measured with `cwd=/home/ebi`
and every tool already disabled, changing **only** `--setting-sources`: asked
whether a term unique to the project's CLAUDE.md was present in its context,
the model answered "not present" with the flag and "present" without it. A
naive escalation ships the whole file — customer names included — no matter how
carefully the question itself was anonymized.

The tool flags are the second surprise. Measured 2026-08-17 with the deny
list alone, the consult still had `ToolSearch` — the entry point to every
configured MCP tool, Gmail and Calendar included — plus `Skill` and
`Workflow`; the external model duly described them and read the anonymized
aliases as record IDs to look up. Extending the deny list by hand then left
`CronCreate`, `RemoteTrigger` and `DesignSync`. A deny list has to be
rewritten for every tool the CLI adds, so it is the wrong shape: `--tools ""`
allows nothing by default and was measured to stop `Bash` from running.

The deny list was kept beside it as a second layer, and that turned out to be
the wrong call for the mirror-image reason: it goes stale in the *other*
direction too. Measured on CLI 2.1.273, the entry for a tool the CLI has since
dropped makes it report `Permission deny rule "…" matches no known tool` on
stderr before answering, so the list has to track renames and removals that the
empty allow list is immune to. A layer that must be re-checked against every
CLI release is not defence in depth; it is a second thing to get wrong. There
is now exactly one tool-selection flag, and `verify_isolation()` refuses any
argv that adds another — in either direction, since `--allowedTools` would
widen the empty list and `--disallowedTools` would bring the hand-written names
back.

This is a *route*, not a procedure. `verify_isolation()` inspects the argv about
to be used, so a future refactor that drops a flag fails loudly instead of
quietly sending more than intended.

## Using it

`/ask` appears only when an anonymization rules file exists. Without rules
nothing would be replaced, and a command that silently forwards real names is
worse than no command.

```
/ask question: How do I debug a conditional access policy that blocks one host?
```

The reply shows the exact text that was sent (`show_sent`, on by default), then
the answer with your real names restored. Both are in the audit log.

## Is the question still a question?

Replacement can succeed and destroy the request at the same time:

```
/ask question: 胡田昌彦の所属会社はJBSです。この会社の良い点と悪い点を挙げてください。
→ sent: person-002の所属会社はorg-002です。この会社の良い点と悪い点を挙げてください。
```

Nothing leaked and nothing is broken — and the answer is worthless, because the
thing being asked about *is* the thing that was hidden. Observed in practice, the
external model treats the aliases as record IDs and reports that it cannot look
them up.

So before the external call, a local model judges one narrow thing about the
already-anonymized text: **does answering require knowing what the placeholders
stand for?** If it does, nothing is sent and the reply says so.

| | Leak inspector | Answerability judge |
|---|---|---|
| Guards | a safety property | a quality property |
| Sees | the anonymized text | the anonymized text |
| Costs, when wrong | a real name leaves the machine | one wasted call |
| **Unavailable means** | **block** (fail closed) | **send** (fail open) |

That last row is the load-bearing one. Copying the inspector's fail-closed
stance here would take `/ask` down whenever the local model is busy, to save an
API call — so `AnswerabilityVerdict.blocks` is true only for a judgement that
actually ran.

Two more properties keep it cheap and overridable:

- **No substitutions, no judgement.** A question the rules did not touch cannot
  have been broken by them, so the usual technical `/ask` pays nothing. Measured
  on qwen3.5:35b, a judged question costs ~1s warm (8s cold).
- **`force: true` skips the check.** A local model will sometimes be wrong, and
  a guard on question *quality* must never be the last word.

```
/ask question: org-002 の良い点と悪い点  force: true
```

Set `CCDB_ASK_ANSWERABILITY=0` to turn the check off entirely. It needs no
endpoint of its own — it rides on the inspector's local model, so it works the
moment `/ask` does.

Measured 2026-08-17 on qwen3.5:35b, 6/6 correct: the reputation, evaluation and
alias-comparison questions were withheld; conditional-access troubleshooting, a
Kerberos error and an MFA rollout plan all went out even though they carried
`org-001` and `host-002`. The false-positive direction is the one to re-measure
after changing the model — a judge that refuses real technical questions is
worse than no judge.

## Limits

- **One question, no memory.** A consult does not resume; each one starts clean.
  That is deliberate — a growing conversation would accumulate context nobody
  audited.
- **Nothing is retried.** If the external CLI fails, you get the error.
- **The body still goes out.** Identifiers are replaced; the sentences are not.
  If the text itself is the secret, do not escalate it.
