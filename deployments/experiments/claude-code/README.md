# Claude Code Agent — Experiment

Runs [Claude Code](https://github.com/anthropics/claude-code) headless in a container, backed by a LiteLLM proxy. Two deployment targets: Docker on a VM, or a Kind cluster.

Session state (`~/.claude`) is persisted to a volume so `claude --resume` works across container/pod restarts.

## Prerequisites

- Docker (both targets)
- `kind` + `kubectl` (Kind target only)
- A LiteLLM instance with a Claude model configured
- `LITELLM_BASE_URL` and `LITELLM_API_KEY`

> **Note:** Claude Code appends `/v1` to `ANTHROPIC_BASE_URL` internally — set your base URL **without** a trailing `/v1`.

---

## Option A: Docker on a VM

### 1. Build the image

Build locally and transfer to the VM (avoids Docker Hub rate limits on the VM).
If your VM is x86_64 and your local machine is ARM (Apple Silicon), cross-compile with `--platform linux/amd64`:

```bash
# Same architecture (e.g. both ARM or both x86_64)
docker build -t claude-code-agent:latest deployments/experiments/claude-code/
docker save claude-code-agent:latest | gzip > claude-code-agent.tar.gz

# Cross-compile for x86_64 VM from ARM Mac
docker buildx build \
  --platform linux/amd64 \
  --output type=docker,dest=claude-code-agent.tar \
  -t claude-code-agent:latest \
  deployments/experiments/claude-code/
gzip claude-code-agent.tar
```

Copy to VM and load:

```bash
scp claude-code-agent.tar.gz user@your-vm:~/
# On the VM:
docker load < ~/claude-code-agent.tar.gz
```

### 2. Configure credentials

```bash
cd deployments/experiments/claude-code/
cp .env.example .env
# Edit .env and fill in LITELLM_BASE_URL and LITELLM_API_KEY
source .env
```

### 3. Run

```bash
./run-claude-code-docker.sh
```

### 4. Run a prompt

```bash
docker exec -it claude-code-agent claude -p "hello" --output-format json

# With tools
docker exec -it claude-code-agent claude -p "list files in /home/agent" --allowedTools Bash

# Resume a previous session
docker exec -it claude-code-agent claude --resume <session-id> -p "continue the task"
```

### 5. Inspect state

```bash
# List all session and memory files
docker exec claude-code-agent find /home/agent/.claude -type f

# Check auto-memory
docker exec claude-code-agent cat /home/agent/.claude/MEMORY.md
```

### Stop / reset

```bash
docker stop claude-code-agent && docker rm claude-code-agent

# Full state reset (WARNING: destroys all sessions and memory)
docker volume rm claude-code-config
```

---

## Option B: Kind cluster

### 1. Build and load the image

```bash
docker build -t claude-code-agent:latest deployments/experiments/claude-code/

kind create cluster --config deployments/experiments/claude-code/kind-config.yaml --name claude-code
kind load docker-image claude-code-agent:latest --name claude-code
```

### 2. Create the namespace and secret

```bash
kubectl create namespace claude-code

# Option 1: kubectl (recommended — credentials never touch disk)
kubectl create secret generic claude-code-secret -n claude-code \
  --from-literal=litellm-base-url=https://your-litellm-host \
  --from-literal=litellm-api-key=your-token-here

# Option 2: from file (gitignored)
cp deployments/experiments/claude-code/claude-code-secret.example.yaml \
   deployments/experiments/claude-code/claude-code-secret.yaml
# Edit claude-code-secret.yaml and fill in real values
kubectl apply -f deployments/experiments/claude-code/claude-code-secret.yaml
```

### 3. Deploy

```bash
kubectl apply -f deployments/experiments/claude-code/claude-code-pvc.yaml
kubectl apply -f deployments/experiments/claude-code/claude-code-deployment.yaml
```

### 4. Wait and test

```bash
kubectl wait --for=condition=Ready pod \
  -l app.kubernetes.io/name=claude-code-agent \
  -n claude-code --timeout=60s

kubectl exec -it -n claude-code deploy/claude-code-agent -- \
  claude -p "hello" --output-format json
```

### 5. Inspect state

```bash
# List all session and memory files
kubectl exec -n claude-code deploy/claude-code-agent -- find /home/agent/.claude -type f

# Resume a session
kubectl exec -it -n claude-code deploy/claude-code-agent -- \
  claude --resume <session-id> -p "continue the task"
```

### Tear down

```bash
kind delete cluster --name claude-code
```

---

## Files

| File | Purpose |
|------|---------|
| `Dockerfile` | Builds the Claude Code agent image |
| `run-claude-code-docker.sh` | Deploy on a VM via Docker |
| `.env.example` | Credential template for Docker deployment |
| `kind-config.yaml` | Minimal single-node Kind cluster |
| `claude-code-pvc.yaml` | PVC for `~/.claude` persistence |
| `claude-code-deployment.yaml` | Kubernetes Deployment |
| `claude-code-secret.example.yaml` | Secret template for Kind deployment |
| `.gitignore` | Keeps `.env` and `*-secret.yaml` out of git |
