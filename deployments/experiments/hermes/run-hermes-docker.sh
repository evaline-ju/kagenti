#!/bin/bash
# Deploy Hermes agent on a VM using Docker with LiteLLM backend.
# Usage:
#   1. Set LITELLM_API_KEY in environment or .env file before running
#   2. For connecting to a Discord bot, set the bot token in DISCORD_BOT_TOKEN and allowed Discord user
#      IDs in DISCORD_ALLOWED_USERS (ref. https://hermes-agent.nousresearch.com/docs/user-guide/messaging/discord)
#   3. ./run-hermes-docker.sh
#
# To stop:  docker stop hermes-agent && docker rm hermes-agent
# To reset: rm -rf ~/hermes-data (WARNING: destroys all state)

set -euo pipefail

# --- Configuration -----------------------------------------------------------
HERMES_IMAGE="nousresearch/hermes-agent:v2026.4.30"
HERMES_DATA="$HOME/hermes-data"
CONTAINER_NAME="hermes-agent"

# LiteLLM proxy
LITELLM_BASE_URL="${LITELLM_BASE_URL:?ERROR: Set LITELLM_BASE_URL (e.g. https://your-litellm-host/v1)}"
LITELLM_API_KEY="${LITELLM_API_KEY:?ERROR: Set LITELLM_API_KEY before running (your LiteLLM auth token)}"

# Model — must match a model name configured in your LiteLLM instance
HERMES_MODEL="${HERMES_MODEL:-claude-sonnet-4-6}"

# Gateway API key (used to authenticate curl requests to Hermes)
API_SERVER_KEY="${API_SERVER_KEY:-hermes-experiment}"

# Discord (optional — leave empty to skip)
DISCORD_BOT_TOKEN="${DISCORD_BOT_TOKEN:-}"
DISCORD_ALLOWED_USERS="${DISCORD_ALLOWED_USERS:-}"

# --- Pull image --------------------------------------------------------------
echo "Pulling $HERMES_IMAGE ..."
docker pull "$HERMES_IMAGE"

# --- Prepare data directory ---------------------------------------------------
mkdir -p "$HERMES_DATA"

# --- Bootstrap config (equivalent to K8s init container) ----------------------
echo "Bootstrapping config in $HERMES_DATA ..."
docker run --rm \
  -v "$HERMES_DATA:/opt/data" \
  -e HERMES_MODEL="$HERMES_MODEL" \
  -e LITELLM_BASE_URL="$LITELLM_BASE_URL" \
  "$HERMES_IMAGE" \
  bash -c '
    HERMES_HOME=/opt/data
    INSTALL_DIR=/opt/hermes
    mkdir -p "$HERMES_HOME"/{cron,sessions,logs,hooks,memories,skills,skins,plans,workspace,home}
    [ ! -f "$HERMES_HOME/.env" ] && cp "$INSTALL_DIR/.env.example" "$HERMES_HOME/.env"
    [ ! -f "$HERMES_HOME/config.yaml" ] && cp "$INSTALL_DIR/cli-config.yaml.example" "$HERMES_HOME/config.yaml"
    [ ! -f "$HERMES_HOME/SOUL.md" ] && cp "$INSTALL_DIR/docker/SOUL.md" "$HERMES_HOME/SOUL.md"
    CONFIG="$HERMES_HOME/config.yaml"
    sed -i "s|provider: \"auto\"|provider: \"custom\"|" "$CONFIG"
    sed -i "s|default: \"anthropic/claude-opus-4.6\"|default: \"$HERMES_MODEL\"|" "$CONFIG"
    sed -i "s|base_url: \"https://openrouter.ai/api/v1\"|base_url: \"$LITELLM_BASE_URL\"|" "$CONFIG"
    echo "Config bootstrap complete"
  '

# --- Stop existing container if running ---------------------------------------
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
  echo "Stopping existing $CONTAINER_NAME ..."
  docker stop "$CONTAINER_NAME" 2>/dev/null || true
  docker rm "$CONTAINER_NAME" 2>/dev/null || true
fi

# --- Build env args -----------------------------------------------------------
DISCORD_ARGS=""
if [ -n "$DISCORD_BOT_TOKEN" ]; then
  DISCORD_ARGS="-e DISCORD_BOT_TOKEN=$DISCORD_BOT_TOKEN -e DISCORD_ALLOWED_USERS=$DISCORD_ALLOWED_USERS"
fi

# --- Run Hermes ---------------------------------------------------------------
echo "Starting Hermes agent ..."
docker run -d \
  --name "$CONTAINER_NAME" \
  --restart unless-stopped \
  -p 8642:8642 \
  -p 8644:8644 \
  -v "$HERMES_DATA:/opt/data" \
  -e HERMES_HOME=/opt/data \
  -e OPENAI_API_KEY="$LITELLM_API_KEY" \
  -e OPENAI_API_BASE="$LITELLM_BASE_URL" \
  -e HERMES_INFERENCE_PROVIDER=custom \
  -e HERMES_MODEL="$HERMES_MODEL" \
  -e API_SERVER_HOST=0.0.0.0 \
  -e API_SERVER_KEY="$API_SERVER_KEY" \
  -e GATEWAY_ALLOW_ALL_USERS=true \
  $DISCORD_ARGS \
  "$HERMES_IMAGE" \
  gateway run

# --- Wait for startup ---------------------------------------------------------
echo "Waiting for Hermes gateway to start ..."
for i in $(seq 1 30); do
  if curl -s -o /dev/null -w '' "http://localhost:8642/health" 2>/dev/null; then
    echo "Hermes is running!"
    echo ""
    echo "  Gateway API:  http://localhost:8642"
    echo "  API key:      $API_SERVER_KEY"
    echo "  Data dir:     $HERMES_DATA"
    echo ""
    echo "Test:"
    echo "  curl http://localhost:8642/health"
    echo "  curl -H "Authorization: Bearer $API_SERVER_KEY" http://localhost:8642/v1/models"
    echo ""
    echo "Dashboard (run separately):"
    echo "  docker run -d --name hermes-dashboard --restart unless-stopped -p 9119:9119 -v $HERMES_DATA:/opt/data:ro -e HERMES_HOME=/opt/data $HERMES_IMAGE dashboard --host 0.0.0.0 --no-open --insecure"
    echo ""
    echo "Logs:"
    echo "  docker logs -f $CONTAINER_NAME"
    exit 0
  fi
  sleep 2
done

echo "WARNING: Hermes did not become healthy within 60s"
echo "Check logs: docker logs $CONTAINER_NAME"
exit 1
