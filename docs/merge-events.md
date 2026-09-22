# Which merges fire workflow events

One GitHub behaviour decides the shape of most of this repository's CI automation:

> A merge attributed to `GITHUB_TOKEN` fires no `push` or `pull_request` event.
> A merge attributed to a real user does.

GitHub suppresses events caused by `GITHUB_TOKEN` to stop workflows triggering
themselves. `repository_dispatch` is explicitly exempt, which is why it is the
only signal `auto-approve.yml` can send to `auto-version-bump.yml`.

## What was measured

On 2026-09-22, across 16 merges into `main` in one day:

| merged by | linked issue closed | `push` event on `main` |
|-----------|---------------------|------------------------|
| `ebibibi` (7 merges) | yes, 1-2s after the merge | yes, 2-3s after the merge |
| `github-actions` (9 merges) | never | never |

Seven merges attributed to the repository owner produced exactly seven CI runs
on `main` with event `push`. Nine merges attributed to `github-actions` produced
none. The correlation is one-to-one, with no exceptions in the sample.

Two corollaries are easy to get wrong, and both were:

- **`issues: write` does not restore the closing of linked issues.** Auto-merge
  is completed by GitHub after the fact and does not carry the permissions of
  the job that enabled it. Measured on #743: the permission was present, the
  merge was completed by `github-actions`, and both linked issues were still
  open eight seconds later, until `auto-approve.yml` closed them itself.
- **A `GITHUB_TOKEN`-authored pull request is the same problem one step
  earlier.** It fires no `pull_request` event, so its required checks never run
  and it can never merge. Two attempts at a PR-based version bump were reverted
  for exactly this reason before #725 authored the PR as a real user instead.

## What depends on it

| Where | What it does about this |
|-------|-------------------------|
| `auto-approve.yml` | Polls for the merge, because nothing can react to it. Sends the upgrade and docs-sync webhooks, dispatches `pr-merged`, and closes the PR's linked issues itself |
| `auto-version-bump.yml` | Creates its branch, issue and pull request with `ADMIN_PAT`, so the bump PR's own checks actually run |
| `require-linked-issue.yml` | Exempts bot authors, because a bot cannot open the issue its PR is required to close |

The poll is the weak part: its bound is how busy the runners are, which the job
cannot know. See #752.

## Consequences worth knowing before changing this

Making automated merges user-attributed would fix the closing of linked issues
and remove the need to poll — and it would also start running `ci.yml` on every
merge, because that workflow triggers on `push` to `main` as well as on pull
requests. On a single-capacity self-hosted pool that roughly doubles the load
that the poll's timeouts were a symptom of in the first place.
