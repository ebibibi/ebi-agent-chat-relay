#!/bin/bash
# Sourced by pre-start.sh. Never rewrite main to undo a failed deployment.

ccdb_record_good() {
    local record branch changes
    branch=$(git branch --show-current) || return 1
    changes=$(git status --porcelain --untracked-files=normal) || return 1
    if [ "$branch" != main ] || [ -n "$changes" ]; then
        echo '[pre-start] Local development checkout does not replace verified main' >&2
        return 0
    fi
    record=$(git rev-parse --git-path ccdb-last-good) || return 1
    git rev-parse HEAD > "${record}.tmp" || return 1
    mv "${record}.tmp" "$record"
}

ccdb_resume_updates() {
    local marker
    marker=$(git rev-parse --git-path ccdb-runtime-root) || return 1
    # Only remove our runtime selector. Main and user branches never moved.
    if [ -f "$marker" ]; then
        rm -- "$marker"
        echo '[pre-start] Retrying current checkout after a runtime rollback' >&2
    fi
}

ccdb_rollback_checkout() {
    local record marker commit gitdir branch changes fallback_head fallback_changes
    record=$(git rev-parse --git-path ccdb-last-good) || return 1
    marker=$(git rev-parse --git-path ccdb-runtime-root) || return 1
    branch=$(git branch --show-current) || return 1
    if [ "$branch" != main ] || [ ! -f "$record" ]; then
        echo '[pre-start] No verified main checkout available for automatic rollback' >&2
        return 1
    fi
    changes=$(git status --porcelain --untracked-files=no) || return 1
    if [ -n "$changes" ]; then
        echo '[pre-start] Local edits prevent automatic rollback' >&2
        return 1
    fi
    read -r commit < "$record" || return 1
    git cat-file -e "${commit}^{commit}" || return 1
    git merge-base --is-ancestor "$commit" main || return 1
    gitdir=$(git rev-parse --absolute-git-dir) || return 1
    CCDB_ROLLBACK_ROOT="$gitdir/ccdb-rollback-checkouts/$commit"
    if [ ! -d "$CCDB_ROLLBACK_ROOT" ]; then
        git worktree add --detach "$CCDB_ROLLBACK_ROOT" "$commit" || return 1
    fi
    fallback_head=$(git -C "$CCDB_ROLLBACK_ROOT" rev-parse HEAD) || return 1
    fallback_changes=$(git -C "$CCDB_ROLLBACK_ROOT" status --porcelain --untracked-files=no) || return 1
    if [ "$fallback_head" != "$commit" ] || [ -n "$fallback_changes" ]; then
        echo '[pre-start] Saved rollback checkout is modified; refusing to use it' >&2
        return 1
    fi
    printf '%s\n' "$CCDB_ROLLBACK_ROOT" > "${marker}.tmp" || return 1
    mv "${marker}.tmp" "$marker" || return 1
    echo '[pre-start] Selected verified runtime checkout; main remains updateable' >&2
}
