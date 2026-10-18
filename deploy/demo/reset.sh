#!/bin/bash
# Reset a demo instance to its scenario. Run as root (it stops and starts the service).
#
#   DEMO_USER     OS user that owns the demo instance        (default: ccdb-demo)
#   DEMO_SERVICE  systemd unit of the demo relay             (default: ccdb-demo.service)
#   DEMO_BRANCH   branch the demo checkout follows           (default: main)
#   DEMO_SCENARIO scenario folder, relative to the checkout  (default: examples/demo/ja-it-admin)
#
# Layout under the demo user's home: relay/ (a checkout of this repository), demo.env,
# vault/, data/, and an optional reset.d/ whose executables run as the demo user
# after the wipe — the place for instance-specific clean-up such as deleting the
# cloud resources the agents created during the last take.
set -euo pipefail

# The checkout this script lives in is reset below; a function is parsed whole
# before it runs, so rewriting the file mid-run cannot change what executes.
main() {
  DEMO_USER=${DEMO_USER:-ccdb-demo}
  DEMO_SERVICE=${DEMO_SERVICE:-ccdb-demo.service}
  DEMO_BRANCH=${DEMO_BRANCH:-main}
  DEMO_SCENARIO=${DEMO_SCENARIO:-examples/demo/ja-it-admin}
  HOME_DIR=$(getent passwd "$DEMO_USER" | cut -d: -f6)
  RELAY="$HOME_DIR/relay"

  as_demo() {
    runuser -u "$DEMO_USER" -- env HOME="$HOME_DIR" \
      PATH="$HOME_DIR/.local/bin:/usr/local/bin:/usr/bin:/bin" bash -c "$1"
  }

  systemctl stop "$DEMO_SERVICE"
  # Follow the branch: a new release brings its new demo items with it.
  as_demo "cd '$RELAY' && git fetch -q origin '$DEMO_BRANCH' && git checkout -q '$DEMO_BRANCH' \
    && git reset -q --hard 'origin/$DEMO_BRANCH' && uv sync -q --extra console"
  as_demo "cd '$RELAY' && .venv/bin/python -m claude_discord.demo reset \
    --scenario '$DEMO_SCENARIO' --vault '$HOME_DIR/vault' --data-root '$HOME_DIR/data'"
  # Conversations of the last take: transcripts, todos and agent worktrees.
  as_demo "rm -rf ~/.claude/projects ~/.claude/todos ~/.claude/shell-snapshots ~/worktrees"
  if [ -d "$HOME_DIR/reset.d" ]; then
    for hook in "$HOME_DIR"/reset.d/*; do
      [ -x "$hook" ] || continue
      echo "reset hook: $hook"
      as_demo "'$hook'" || echo "reset hook failed: $hook" >&2
    done
  fi
  systemctl start "$DEMO_SERVICE"
  as_demo "set -a; . '$HOME_DIR/demo.env'; set +a; cd '$RELAY' && .venv/bin/python -m claude_discord.demo seed \
    --scenario '$DEMO_SCENARIO' --url \"http://127.0.0.1:\$CCDB_CONSOLE_PORT\""
}

main "$@"
