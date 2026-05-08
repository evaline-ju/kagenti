# Hermes Agent — Experiment

Runs [Hermes](https://hermes-agent.nousresearch.com) headless in a container, backed by a LiteLLM proxy. Two deployment targets: Docker on a VM, or a Kind cluster.

Session state (`/opt/data`) is persisted to a volume — sessions, memories, skills, and the SQLite state database all survive container/pod restarts.

## Prerequisites

- Docker (both targets)
- `kind` + `kubectl` (Kind target only)
- A LiteLLM instance with a Claude model configured
- `LITELLM_BASE_URL` and `LITELLM_API_KEY`

---

## Option A: Docker on a VM

### 1. Configure credentials

```bash
cd deployments/experiments/hermes/
cp .env.example .env
# Edit .env and fill in LITELLM_BASE_URL and LITELLM_API_KEY
source .env
```

### 2. Run

The script pulls the image from Docker Hub, bootstraps config, and starts the gateway:

```bash
./run-hermes-docker.sh
```

### 3. Test

```bash
# Health check
curl http://localhost:8642/health

# List models
curl -H "Authorization: Bearer $API_SERVER_KEY" http://localhost:8642/v1/models

# Send a message
curl -s -X POST http://localhost:8642/v1/chat/completions \
  -H "Authorization: Bearer $API_SERVER_KEY" \
  -H "Content-Type: application/json" \
  -d '{"model": "hermes-agent", "messages": [{"role": "user", "content": "Hello"}]}'
```

### 4. Dashboard (optional)

```bash
docker run -d --name hermes-dashboard --restart unless-stopped \
  -p 9119:9119 \
  -v ~/hermes-data:/opt/data:ro \
  -e HERMES_HOME=/opt/data \
  nousresearch/hermes-agent:latest \
  dashboard --host 0.0.0.0 --no-open --insecure
# Open http://localhost:9119
```

### 5. Inspect state

```bash
# Session list
docker exec hermes-agent /opt/hermes/.venv/bin/hermes sessions list

# Memory files
docker exec hermes-agent cat /opt/data/MEMORY.md
docker exec hermes-agent cat /opt/data/USER.md

# SQLite state
docker exec hermes-agent python3 -c "
import sqlite3
db = sqlite3.connect('/opt/data/state.db')
print('Sessions:', db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0])
print('Messages:', db.execute('SELECT COUNT(*) FROM messages').fetchone()[0])
db.close()
"
```

### Discord (optional)

Set before running the script:

```bash
export DISCORD_BOT_TOKEN=your-bot-token
export DISCORD_ALLOWED_USERS=your-user-id
./run-hermes-docker.sh
```

### Stop / reset

```bash
docker stop hermes-agent && docker rm hermes-agent

# Full state reset (WARNING: destroys all sessions and memory)
rm -rf ~/hermes-data
```

---

## Option B: Kind cluster

### 1. Create the cluster

```bash
kind create cluster --config deployments/experiments/hermes/kind-config.yaml --name hermes
```

### 2. Create the namespace and secret

```bash
kubectl create namespace hermes

# Option 1: kubectl (recommended — credentials never touch disk)
kubectl create secret generic hermes-secrets -n hermes \
  --from-literal=LITELLM_API_KEY=your-token \
  --from-literal=API_SERVER_KEY=hermes-experiment \
  --from-literal=DISCORD_BOT_TOKEN=your-discord-bot-token   # optional

# Option 2: from file (gitignored)
cp deployments/experiments/hermes/hermes-secret.example.yaml \
   deployments/experiments/hermes/hermes-secret.yaml
# Edit hermes-secret.yaml and fill in real values
kubectl apply -f deployments/experiments/hermes/hermes-secret.yaml
```

### 3. Deploy

```bash
kubectl apply -f deployments/experiments/hermes/hermes-pvc.yaml
kubectl apply -f deployments/experiments/hermes/hermes-deployment-vm.yaml
kubectl apply -f deployments/experiments/hermes/hermes-service.yaml
```

### 4. Wait and test

```bash
kubectl wait --for=condition=Ready pod \
  -l app.kubernetes.io/name=hermes-agent \
  -n hermes --timeout=120s

# Port-forward and test
kubectl port-forward -n hermes svc/hermes-agent 8642:8642 &
curl http://localhost:8642/health
```

### 5. Inspect state

```bash
# Session list
kubectl exec -n hermes deploy/hermes-agent -- \
  /opt/hermes/.venv/bin/hermes sessions list

# Memory files
kubectl exec -n hermes deploy/hermes-agent -- cat /opt/data/MEMORY.md

# SQLite state
kubectl exec -n hermes deploy/hermes-agent -- python3 -c "
import sqlite3
db = sqlite3.connect('/opt/data/state.db')
print('Sessions:', db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0])
print('Messages:', db.execute('SELECT COUNT(*) FROM messages').fetchone()[0])
db.close()
"
```

### Tear down

```bash
kind delete cluster --name hermes
```

---

## Files

| File | Purpose |
|------|---------|
| `run-hermes-docker.sh` | Deploy on a VM via Docker |
| `.env.example` | Credential template for Docker deployment |
| `kind-config.yaml` | Minimal single-node Kind cluster |
| `hermes-pvc.yaml` | PVC for `/opt/data` persistence |
| `hermes-deployment-vm.yaml` | Kind/VM deployment backed by LiteLLM |
| `hermes-dashboard-deployment.yaml` | Optional dashboard deployment |
| `hermes-service.yaml` | ClusterIP service for gateway + webhook ports |
| `hermes-secret.example.yaml` | Secret template for Kind deployment |
| `test-commands.sh` | State and memory test commands (Kind) |
| `test-commands-vm.sh` | State and memory test commands (Docker) |
| `check-sessions.sh` | Session inspection helpers |
| `.gitignore` | Keeps `.env` and `hermes-secret.yaml` out of git |
