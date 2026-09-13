#!/usr/bin/env bash
# Report whether the running bot is loading code that is not on origin/main.
#
# `make dev-on` writes ~/.ccdb-dev-worktree, and the import hook installed by
# pre-start.sh then redirects claude_discord / claude_code_core to that
# worktree. It is the right tool for testing a change against real Discord
# traffic, but nothing expires it: pre-start.sh prints one line at boot and
# never mentions it again, so a forgotten `make dev-on` keeps a side branch in
# production indefinitely while every merged PR appears to deploy and does not.
#
# Main-tree mode has its own way of running stale code: the checkout only pulls
# in pre-start.sh, so a bot that has not restarted keeps serving whatever was
# merged before it booted. That is silent in exactly the same way -- the tree
# says main, `git pull` keeps succeeding, and nothing compares the *running*
# process against origin/main. So this script reports both kinds of staleness.
#
# Exit codes:
#   0  running the newest code, or dev mode on code already merged to origin/main
#   1  drift: dev mode on unmerged code, or merged commits undeployed too long
#   2  dev mode configured but unusable (marker points nowhere)
set -u

CCDB_HOME="${CCDB_HOME:-$HOME/claude-code-discord-bridge}"
MARKER="$HOME/.ccdb-dev-worktree"
SERVICE="${CCDB_SERVICE:-discord-bot}"
# A merged commit is not "undeployed" the moment it lands -- restarts are
# disruptive, so they are batched. Report from day one, alert after this many.
STALE_DAYS="${CCDB_STALE_DAYS:-3}"

# ── which commits actually need a restart? ──
#
# A restart kills every in-flight session, so the operator rightly refuses to do
# it for nothing. That makes a false alert expensive twice over: it asks for a
# disruptive action, and when the answer is "not worth it" the check stays red
# for days and stops being read at all. A docs-only commit, or a Dependabot bump
# of a development-only tool, cannot change one bit of what the bot does — those
# must not hold the alert open.
#
# Everything not proven inert counts. A commit whose file list cannot be read
# (an unresolved merge, an empty diff) counts too: silence here has to mean
# "nothing to deploy", never "the classifier gave up".
INERT_PATHS_RE='^(docs/|tests/|examples/|\.github/|LICENSE$|CHANGELOG\.md$|[^/]*\.md$)'

commit_needs_restart() {
    local sha="$1" files
    files="$(git -C "$CCDB_HOME" diff-tree --no-commit-id --name-only -r "$sha" 2>/dev/null)"
    [ -z "$files" ] && return 0

    local runtime_files
    runtime_files="$(printf '%s\n' "$files" | grep -Ev "$INERT_PATHS_RE" || true)"
    [ -z "$runtime_files" ] && return 1

    # uv.lock is synced on restart, so a lock change normally counts. The one
    # exception is a bump Dependabot itself labels development-only: the package
    # lands in the venv and nothing imports it. Trust that trailer only when it
    # is the *whole* story -- any production dependency in the same commit, or a
    # second changed file, and the commit counts again.
    if [ "$runtime_files" = "uv.lock" ]; then
        local message
        message="$(git -C "$CCDB_HOME" show --no-patch --format=%B "$sha" 2>/dev/null)"
        if printf '%s' "$message" | grep -q 'dependency-type: direct:development' &&
            ! printf '%s' "$message" | grep -q 'dependency-type: direct:production'; then
            return 1
        fi
    fi
    return 0
}

# ── main-tree mode: is the running process the newest code? ──
#
# The honest measure is not "is the checkout behind origin/main" (a pull without
# a restart changes nothing) but "which commits landed after the bot started".
# That single number covers both a stale checkout and a pulled-but-not-restarted
# one, and it stays quiet on a machine that restarts often.
report_undeployed() {
    git -C "$CCDB_HOME" fetch -q origin main 2>/dev/null || true

    local started
    started="$(systemctl show -p ActiveEnterTimestamp --value "$SERVICE" 2>/dev/null || true)"
    if [ -z "$started" ]; then
        echo "OK: main-tree mode (no $MARKER)"
        echo "    Cannot read $SERVICE start time; skipped the freshness check."
        return 0
    fi
    local started_epoch
    started_epoch="$(date -d "$started" +%s 2>/dev/null || echo 0)"
    if [ "$started_epoch" -eq 0 ]; then
        echo "OK: main-tree mode (no $MARKER)"
        echo "    Could not parse '$started'; skipped the freshness check."
        return 0
    fi

    # Oldest of the commits the running process is missing -- how long the wait
    # has actually lasted, not how long ago the newest one landed. Take the
    # minimum rather than the last line: git log orders by the commit graph, and
    # a merge can put an older commit anywhere in that list.
    local count=0 oldest_epoch="" sha commit_epoch
    while read -r sha; do
        [ -z "$sha" ] && continue
        commit_needs_restart "$sha" || continue
        count=$(( count + 1 ))
        commit_epoch="$(git -C "$CCDB_HOME" show --no-patch --format=%ct "$sha" 2>/dev/null || echo "")"
        if [ -n "$commit_epoch" ] &&
            { [ -z "$oldest_epoch" ] || [ "$commit_epoch" -lt "$oldest_epoch" ]; }; then
            oldest_epoch="$commit_epoch"
        fi
    done <<EOF
$(git -C "$CCDB_HOME" rev-list "origin/main" --since="@$started_epoch" 2>/dev/null || true)
EOF

    if [ "$count" -eq 0 ]; then
        echo "OK: main-tree mode, running the newest code on origin/main."
        return 0
    fi

    local age_days
    oldest_epoch="${oldest_epoch:-$started_epoch}"
    age_days=$(( ( $(date +%s) - oldest_epoch ) / 86400 ))

    if [ "$age_days" -lt "$STALE_DAYS" ]; then
        echo "OK: main-tree mode. $count commit(s) merged since the bot started"
        echo "    (oldest ${age_days}d old); they deploy on the next restart."
        return 0
    fi

    cat <<REPORT
DRIFT: merged code has been waiting ${age_days} days for a restart.

  service  : $SERVICE, up since $started
  missing  : $count commit(s) merged after that and therefore not running
  oldest   : ${age_days} day(s) old (threshold: ${STALE_DAYS}, \$CCDB_STALE_DAYS)

Restart when the bot is idle -- 'systemctl restart $SERVICE' pulls, syncs and
validates through pre-start.sh. An in-flight session does not survive it, so
check for running sessions first.
REPORT
    return 1
}

if [ ! -f "$MARKER" ]; then
    report_undeployed
    exit $?
fi

WORKTREE="$(cat "$MARKER" 2>/dev/null | tr -d '[:space:]')"
if [ -z "$WORKTREE" ] || [ ! -d "$WORKTREE/claude_discord" ]; then
    echo "BROKEN: $MARKER points to '$WORKTREE', which has no claude_discord/."
    echo "        The import hook silently falls back to the main tree."
    exit 2
fi

# Age of the marker is the honest measure of "how long has this been on".
MARKER_EPOCH="$(stat -c %Y "$MARKER" 2>/dev/null || echo 0)"
NOW_EPOCH="$(date +%s)"
DAYS=$(( (NOW_EPOCH - MARKER_EPOCH) / 86400 ))

BRANCH="$(git -C "$WORKTREE" rev-parse --abbrev-ref HEAD 2>/dev/null || echo '?')"
HEAD_SHA="$(git -C "$WORKTREE" rev-parse --short HEAD 2>/dev/null || echo '?')"

# Compare against the remote's idea of main, not a local ref that may itself be
# stale — the whole point is to detect "main moved and production did not".
git -C "$CCDB_HOME" fetch -q origin main 2>/dev/null || true
BEHIND="$(git -C "$CCDB_HOME" rev-list --count "$HEAD_SHA"..origin/main 2>/dev/null || echo '?')"

if git -C "$CCDB_HOME" merge-base --is-ancestor "$HEAD_SHA" origin/main 2>/dev/null; then
    echo "OK: dev mode on $BRANCH ($HEAD_SHA), already merged to origin/main."
    echo "    Behind origin/main by $BEHIND commit(s); on for ${DAYS}d."
    exit 0
fi

cat <<REPORT
DRIFT: the bot is running code that is not on origin/main.

  worktree : $WORKTREE
  branch   : $BRANCH ($HEAD_SHA)
  merged   : NO — $HEAD_SHA is not an ancestor of origin/main
  behind   : $BEHIND commit(s) of origin/main are NOT running
  dev mode : on for ${DAYS} day(s) (marker mtime)

Every PR merged in that window looks deployed and is not. Either finish the
branch (open a PR and merge it) or run 'make dev-off' to return to main.

Before switching, check .env for values only the dev branch understands — a
setting the main tree cannot parse falls back to a default, which changes
behaviour without erroring.
REPORT
exit 1
