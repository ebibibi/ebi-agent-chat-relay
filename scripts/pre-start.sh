#!/bin/bash
# ccdb pre-start script — runs as ExecStartPre before bot process.
# Ensures code is at latest and dependencies are synced.
set -e

# Resolve the repository root dynamically so this script works for any user,
# regardless of where they cloned the repo.  Using readlink -f handles the case
# where the script itself is a symlink.
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
CCDB_HOME="$(dirname "$SCRIPT_DIR")"
cd "$CCDB_HOME"
source "$SCRIPT_DIR/deploy-checkout.sh"
ccdb_resume_updates

# Locate uv: prefer the local installation, fall back to whatever is on PATH.
UV="${CCDB_UV_BIN:-}"
if [ -z "$UV" ]; then
    UV="$(command -v uv 2>/dev/null || true)"
fi
if [ -z "$UV" ]; then
    echo "[pre-start] ERROR: uv not found. Install it or set CCDB_UV_BIN." >&2
    exit 1
fi

# ── Webhook helper ──
DISCORD_WEBHOOK_URL=""
if [ -f .env ]; then
    DISCORD_WEBHOOK_URL=$(grep -E '^DISCORD_WEBHOOK_URL=' .env | cut -d'=' -f2- | tr -d '"' || true)
fi
send_webhook() {
    local message="$1"
    if [ -n "$DISCORD_WEBHOOK_URL" ]; then
        local escaped
        escaped=$(printf '%s' "$message" | python3 -c 'import sys,json; print(json.dumps(sys.stdin.read()))')
        curl -s -o /dev/null -X POST "$DISCORD_WEBHOOK_URL" \
            -H "Content-Type: application/json" \
            -d "{\"content\": $escaped}" || echo "[pre-start] WARNING: webhook failed" >&2
    fi
}

# ── Step 1: Pull latest code (skip in local dev mode) ──
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
LAST_GOOD=$(git rev-parse --git-path ccdb-last-good)
# Bootstrap the record only from a checkout that actually imports before an
# update. Once present, failed versions must never overwrite this checkpoint.
if [ "$CURRENT_BRANCH" = main ] && [ ! -f "$LAST_GOOD" ] && \
   [ ! -f "$HOME/.ccdb-dev-worktree" ] && \
   [ -x .venv/bin/python ] && .venv/bin/python -c "from claude_discord.main import main" >/dev/null 2>&1; then
    ccdb_record_good
fi
# Only *tracked* changes signal local dev mode. Untracked files (e.g. the
# bot-generated logs/ directory) must never block the pull, otherwise a stale
# checkout silently persists across every restart. uv.lock is a derived file
# that uv sync regenerates, so it is filtered out too.
LOCAL_CHANGES=$(git status --porcelain --untracked-files=no 2>/dev/null | grep -v '^ M uv.lock$' | grep -v '^M  uv.lock$' || true)

if [ "$CURRENT_BRANCH" != "main" ] || [ -n "$LOCAL_CHANGES" ]; then
    echo "[pre-start] Local dev mode (branch: $CURRENT_BRANCH) — skipping git pull" >&2
else
    # Discard uv.lock changes before pull — uv sync regenerates it afterwards.
    git checkout -- uv.lock 2>/dev/null || true
    echo "[pre-start] Pulling latest code..." >&2
    set +e
    git pull --ff-only origin main 2>&1
    PULL_EXIT=$?
    set -e
    if [ $PULL_EXIT -ne 0 ]; then
        echo "[pre-start] WARNING: git pull failed (exit $PULL_EXIT), continuing with current code" >&2
    fi
fi

# ── Step 2: Sync dependencies ──
echo "[pre-start] Syncing dependencies..." >&2
"$UV" sync 2>&1

COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
echo "[pre-start] Code at: ${COMMIT}" >&2

# ── Step 2b: Install dev worktree import hook ──
# The hook intercepts claude_discord imports via sys.meta_path and redirects them
# to ~/.ccdb-dev-worktree when that file exists. Uses sys.meta_path (not sys.path)
# to override python -m's CWD-first resolution.
# The hook files are created here so they survive venv recreation (uv sync --reinstall).
install_runtime_hook() {
for SITE_PKG in "$CCDB_HOME"/.venv/lib/python*/site-packages; do
    # Install the import hook module
    cat > "$SITE_PKG/_ccdb_dev_hook.py" << 'HOOK_EOF'
"""Dev worktree import hook — redirects claude_discord & claude_code_core to ~/.ccdb-dev-worktree."""
import sys, os, importlib.util
from pathlib import Path

_TARGETS = ("claude_discord", "claude_code_core", "claude_teams")

def _install():
    dev_file = os.path.expanduser("~/.ccdb-dev-worktree")
    if not os.path.exists(dev_file):
        runtime_pointer = Path(__file__).with_name("_ccdb_runtime_pointer")
        if not runtime_pointer.exists():
            return
        dev_file = runtime_pointer.read_text().strip()
        if not os.path.exists(dev_file):
            return
    with open(dev_file) as f:
        worktree = f.read().strip()
    if not any(os.path.isdir(os.path.join(worktree, t)) for t in _TARGETS):
        return
    class _Finder:
        def find_spec(self, fullname, path, target=None):
            root = fullname.split(".", 1)[0]
            if root not in _TARGETS:
                return None
            parts = fullname.split(".")
            pkg = os.path.join(worktree, *parts)
            if os.path.isdir(pkg):
                init = os.path.join(pkg, "__init__.py")
                if os.path.exists(init):
                    return importlib.util.spec_from_file_location(fullname, init, submodule_search_locations=[pkg])
            mod = pkg + ".py"
            if os.path.exists(mod):
                return importlib.util.spec_from_file_location(fullname, mod)
            return None
    sys.meta_path.insert(0, _Finder())

_install()
HOOK_EOF
    # Activate the hook via .pth (runs on Python startup, before any user code)
    echo "import _ccdb_dev_hook" > "$SITE_PKG/_ccdb_dev_hook.pth"
    git rev-parse --path-format=absolute --git-path ccdb-runtime-root > "$SITE_PKG/_ccdb_runtime_pointer"
done
}
install_runtime_hook

if [ -f "$HOME/.ccdb-dev-worktree" ]; then
    # One line saying "dev mode" reads like a setting, not a warning, so a
    # forgotten `make dev-on` can keep a side branch in production for days
    # while merged PRs appear to deploy. Print the full drift report instead.
    echo "[pre-start] Dev worktree mode: $(cat "$HOME/.ccdb-dev-worktree")" >&2
    if [ -x "$CCDB_HOME/scripts/check-deploy-drift.sh" ]; then
        "$CCDB_HOME/scripts/check-deploy-drift.sh" 2>&1 | sed 's/^/[pre-start] /' >&2 || true
    fi
fi

# ── Step 3: Validate imports ──
echo "[pre-start] Validating imports..." >&2
set +e
IMPORT_ERROR=$(.venv/bin/python -c "from claude_discord.main import main" 2>&1)
IMPORT_EXIT=$?
set -e

if [ $IMPORT_EXIT -ne 0 ]; then
    echo "[pre-start] ERROR: Import validation failed:" >&2
    echo "$IMPORT_ERROR" >&2
    send_webhook "⚠️ **ccdb pre-start failed**: Import error.\n\`\`\`\n${IMPORT_ERROR}\n\`\`\`\nAttempting rollback..."

    echo "[pre-start] Rolling back..." >&2
    if [ "$CURRENT_BRANCH" != main ] || [ -n "$LOCAL_CHANGES" ] || \
       [ -f "$HOME/.ccdb-dev-worktree" ]; then
        echo '[pre-start] Development checkout failed validation; preserving local work' >&2
        exit 1
    fi
    # uv.lock is generated by sync; do not discard any other local changes.
    git restore -- uv.lock
    ccdb_rollback_checkout || exit 1
    UV_PROJECT_ENVIRONMENT="$CCDB_HOME/.venv" "$UV" sync --project "$CCDB_ROLLBACK_ROOT" 2>&1
    install_runtime_hook

    set +e
    ROLLBACK_ERROR=$(.venv/bin/python -c "from claude_discord.main import main" 2>&1)
    ROLLBACK_EXIT=$?
    set -e

    if [ $ROLLBACK_EXIT -ne 0 ]; then
        echo "[pre-start] FATAL: Import still fails after rollback" >&2
        send_webhook "🔴 **ccdb rollback also failed**.\n\`\`\`\n${ROLLBACK_ERROR}\n\`\`\`\nManual intervention required."
        exit 1
    fi

    send_webhook "✅ **ccdb rollback succeeded**: running on $(git -C "$CCDB_ROLLBACK_ROOT" rev-parse --short HEAD)."
fi

if [ "$(git branch --show-current)" = main ] && \
   [ ! -f "$HOME/.ccdb-dev-worktree" ] && \
   [ ! -f "$(git rev-parse --git-path ccdb-runtime-root)" ]; then
    ccdb_record_good
fi
COMMIT=$(git rev-parse --short HEAD)
if [ -n "${CCDB_ROLLBACK_ROOT:-}" ]; then
    COMMIT=$(git -C "$CCDB_ROLLBACK_ROOT" rev-parse --short HEAD)
fi

# ── Step 4: Report stale worktrees (never remove files during startup) ──
CLEANUP_SCRIPT="$CCDB_HOME/scripts/cleanup_worktrees.sh"
if [ -x "$CLEANUP_SCRIPT" ]; then
    "$CLEANUP_SCRIPT" --dry-run 2>&1 || true
fi

echo "[pre-start] All checks passed. Starting bot (${COMMIT})." >&2
