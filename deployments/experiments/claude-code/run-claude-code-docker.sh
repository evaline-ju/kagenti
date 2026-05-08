#!/bin/bash
# Deploy Claude Code CLI in a Docker container backed by a LiteLLM proxy.
# Usage:
#   0. Build the claude-code-agent:latest (or update IMAGE_TAG for the claude-code-agent)
#   1. Set LITELLM_API_KEY in environment before running.
#   2. Optionally override LITELLM_BASE_URL and CLAUDE_MODEL.
#   3. ./run-claude-code-docker.sh
#   4. Exec into the container to run prompts:
#        docker exec -it claude-code-agent claude -p "your prompt" --allowedTools Bash
#
# To stop:  docker stop claude-code-agent && docker rm claude-code-agent
# To reset: docker volume rm claude-code-config (WARNING: destroys all state)

set -euo pipefail

# --- Configuration -----------------------------------------------------------
CONTAINER_NAME="claude-code-agent"
DATA_VOLUME="claude-code-config"

# LiteLLM proxy — set these in your environment or a .env file before running
# LITELLM_BASE_URL: base URL of your LiteLLM instance (no trailing /v1)
# LITELLM_API_KEY:  your LiteLLM auth token
LITELLM_BASE_URL="${LITELLM_BASE_URL:?ERROR: Set LITELLM_BASE_URL (e.g. https://your-litellm-host)}"
LITELLM_API_KEY="${LITELLM_API_KEY:?ERROR: Set LITELLM_API_KEY (your LiteLLM auth token)}"

# Model — must match a model name configured in your LiteLLM instance
CLAUDE_MODEL="${CLAUDE_MODEL:-claude-sonnet-4-6}"

# --- Build image if not present ----------------------------------------------
IMAGE_TAG="claude-code-agent:latest"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! docker image inspect "$IMAGE_TAG" &>/dev/null; then
  echo "Building $IMAGE_TAG ..."
  docker build -t "$IMAGE_TAG" "$SCRIPT_DIR"
else
  echo "Image $IMAGE_TAG already exists, skipping build."
  echo "  (To force rebuild: docker rmi $IMAGE_TAG)"
fi

# --- Create persistent volume -------------------------------------------------
if ! docker volume inspect "$DATA_VOLUME" &>/dev/null; then
  echo "Creating volume $DATA_VOLUME ..."
  docker volume create "$DATA_VOLUME"
fi

# --- Stop existing container if running --------------------------------------
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
  echo "Stopping existing $CONTAINER_NAME ..."
  docker stop "$CONTAINER_NAME" 2>/dev/null || true
  docker rm "$CONTAINER_NAME" 2>/dev/null || true
fi

# --- Run container -----------------------------------------------------------
echo "Starting Claude Code agent container ..."
docker run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  -e ANTHROPIC_BASE_URL="$LITELLM_BASE_URL" \
  -e ANTHROPIC_API_KEY="$LITELLM_API_KEY" \
  -e ANTHROPIC_MODEL="$CLAUDE_MODEL" \
  -e DISABLE_AUTOUPDATER=1 \
  -e DISABLE_TELEMETRY=1 \
  -v "$DATA_VOLUME:/home/agent/.claude" \
  "$IMAGE_TAG"

# --- Wait for container to be running ----------------------------------------
echo "Waiting for container to be ready ..."
for i in $(seq 1 10); do
  if docker inspect --format '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null | grep -q "true"; then
    echo ""
    echo "Claude Code agent is running!"
    echo ""
    echo "  Container:    $CONTAINER_NAME"
    echo "  State volume: $DATA_VOLUME (mounted at /home/agent/.claude)"
    echo "  Model:        $CLAUDE_MODEL"
    echo "  LiteLLM URL:  $LITELLM_BASE_URL"
    echo ""
    echo "Run a one-shot prompt:"
    echo "  docker exec -it $CONTAINER_NAME claude -p 'echo hello world' --allowedTools Bash"
    echo ""
    echo "Interactive shell:"
    echo "  docker exec -it $CONTAINER_NAME bash"
    echo ""
    echo "Logs:"
    echo "  docker logs -f $CONTAINER_NAME"
    echo ""
    echo "Stop:"
    echo "  docker stop $CONTAINER_NAME && docker rm $CONTAINER_NAME"
    exit 0
  fi
  sleep 2
done

echo "ERROR: Container did not start within 20s"
echo "Check logs: docker logs $CONTAINER_NAME"
exit 1
