FROM node:22-slim

# Install system deps Claude Code needs to run tools (git, bash, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    bash \
    ca-certificates \
  && rm -rf /var/lib/apt/lists/*

# Create a non-root user; Claude Code writes state to ~/.claude
# Use UID 1001 — node:22-slim already has 'node' at UID 1000
RUN useradd -m -u 1001 -s /bin/bash agent

# Install Claude Code globally (pinned via CLAUDE_CODE_VERSION build arg)
#ARG CLAUDE_CODE_VERSION=latest
ARG CLAUDE_CODE_VERSION=2.1.133
RUN npm install -g @anthropic-ai/claude-code@${CLAUDE_CODE_VERSION}

USER agent
WORKDIR /home/agent

# ~/.claude will be mounted as a volume for persistent session state
VOLUME ["/home/agent/.claude"]

# Keep container alive so we can exec prompts into it
CMD ["sleep", "infinity"]
