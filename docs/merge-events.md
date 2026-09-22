# Which merges fire workflow events

One GitHub behaviour decides the shape of most of this repository's CI automation:

> A merge attributed to `GITHUB_TOKEN` fires no `push` or `pull_request` event.
> A merge attributed to a real user does.

GitHub suppresses events caused by `GITHUB_TOKEN` to stop workflows triggering
themselves. A fine-grained or classic personal access token acts as its owner,
so normal events are emitted.

## What was measured

On 2026-09-22, across 16 merges into `main` in one day:

| merged by | linked issue closed | `push` event on `main` |
|-----------|---------------------|------------------------|
| `ebibibi` (7 merges) | yes, 1-2s after the merge | yes, 2-3s after the merge |
| `github-actions` (9 merges) | never | never |

Seven merges attributed to the repository owner produced exactly seven CI runs
on `main` with event `push`. Nine merges attributed to `github-actions` produced
none. The correlation was one-to-one, with no exceptions in the sample.

PR #758 then tested the exact auto-merge path rather than a manual merge. The
automation first enabled auto-merge with `GITHUB_TOKEN`; the owner subsequently
ran the same `gh pr merge --auto --squash` command with the owner token. When the
required checks completed, GitHub attributed the merge to `ebibibi`, closed
#757 one second later, and emitted the `main` push workflows three seconds
later. The token that last enables auto-merge determines the eventual actor.

Two corollaries are easy to get wrong, and both were:

- **`issues: write` does not restore the closing of linked issues for a bot
  merge.** Auto-merge is completed by GitHub after the fact and does not carry
  the permissions of the job that enabled it. Measured on #743: the permission
  was present, the merge was completed by `github-actions`, and both linked
  issues stayed open until the workflow explicitly closed them.
- **A `GITHUB_TOKEN`-authored pull request is the same problem one step
  earlier.** It fires no `pull_request` event, so its required checks never run
  and it can never merge. Two attempts at a PR-based version bump were reverted
  for exactly this reason before #725 authored the PR as a real user instead.

## Current design

| Where | How it uses this rule |
|-------|-----------------------|
| `auto-approve.yml` | Approves with `GITHUB_TOKEN`, then enables owner PR auto-merge with `ADMIN_PAT`. It does not wait for completion |
| `post-merge.yml` | Reacts to the resulting `main` push, resolves the merged PR from the commit, sends the upgrade and docs-sync webhooks, and dispatches `pr-merged` |
| `auto-version-bump.yml` | Creates its branch, issue and pull request with `ADMIN_PAT`, so the bump PR's own checks run |
| `ci.yml`, `codeql.yml` | Run on the PR (and on their schedules), not again for the resulting `main` push |
| GitHub itself | Closes the issues named by `Closes #N` because the merge is user-attributed |

Dependabot remains intentionally bot-attributed. Its lock-file updates do not
restart the bot or create another version bump; they take effect on the next
natural restart.

## Why event-driven post-merge work replaced polling

A polling job has to guess how long required checks will remain queued. The old
job first waited 15 minutes, then 40; PR #749 still merged about a minute after
the longer window expired. When the wait ended, every operation behind it was
lost. A `push` event has no duration guess: it exists only after the merge and
starts the consumers immediately.

User-attributed merges would normally also repeat CI and CodeQL on `main`,
doubling load on the single-capacity self-hosted pool. Those expensive checks
now run on pull requests and schedules only. The lightweight post-merge control
plane runs on `ubuntu-latest`.
