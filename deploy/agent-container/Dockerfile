# Sample image for the `container` execution environment (CCDB_EXECUTION_MODE=container).
#
# The relay runs `docker run --rm -i ... <image> claude|codex <args>`, so the image only
# has to put the agent CLIs on PATH. It runs as the relay's own uid/gid with the working
# directory and the agent's state (~/.claude, ~/.claude.json, ~/.codex) bind-mounted at
# their host paths, so no credentials are baked in here.
#
#   docker build -t ccdb-agent:latest deploy/agent-container
#   CCDB_EXECUTION_MODE=container CCDB_CONTAINER_IMAGE=ccdb-agent:latest
#
# Add whatever toolchain your agents need (python, go, ...) below.
FROM node:22-bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        bash ca-certificates curl git openssh-client procps ripgrep \
    && rm -rf /var/lib/apt/lists/*

ARG CLAUDE_CODE_VERSION=latest
ARG CODEX_VERSION=latest
RUN npm install -g "@anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}" "@openai/codex@${CODEX_VERSION}" \
    && npm cache clean --force

# The relay passes --user <uid>:<gid>; a writable HOME parent lets the CLI create
# files beside the mounted state when the uid has no passwd entry.
RUN chmod 1777 /home
