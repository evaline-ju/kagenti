# Demo: Session Recovery After PVC Loss

**Prerequisites**: Kind cluster running with Redis, state-query-api, Hermes, and Claude Code deployed.

---

## Hermes

```bash
# Port-forwards
kubectl port-forward -n platform svc/state-query-api 8000:8000 &
kubectl port-forward -n hermes svc/hermes-agent 8642:8642 &

# API key for Hermes gateway
API_SERVER_KEY=$(kubectl get secret -n hermes hermes-secrets -o jsonpath='{.data.API_SERVER_KEY}' | base64 -d)
```

### Step 0: Reset (optional, for a clean slate)

```bash
# Not strictly needed since Step 4 pins by session ID, but useful for a clean demo
kubectl exec -n platform deploy/redis -- redis-cli DEL agent:hermes:recovered_session
```

### Step 1: Establish conversation

Ask something with a unique, non-obvious detail the model can't hallucinate:

```bash
curl -s -X POST http://localhost:8642/v1/chat/completions \
  -H "Authorization: Bearer $API_SERVER_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "claude-sonnet-4-6",
    "messages": [{"role":"user","content":"I have exactly 47 agent pods running across 3 namespaces and need their session state to survive pod restarts. What persistence approach do you recommend? One sentence."}]
  }' | jq -r '.choices[0].message.content'
```

### Step 2: Capture session ID and verify turn in Redis

```bash
# Capture the session ID (pinning avoids fragility if later calls create new sessions)
HERMES_SESSION=$(curl -s http://localhost:8000/agents/hermes/last-session | jq -r '.last_session')
echo "HERMES_SESSION=$HERMES_SESSION"

# Verify the turn is stored
kubectl exec -n platform deploy/redis -- redis-cli LRANGE "session:${HERMES_SESSION}:turns" 0 -1
```

### Step 3: Kill pod + delete PVC

```bash
kubectl scale deploy hermes-agent -n hermes --replicas=0
kubectl delete pvc hermes-data -n hermes
kubectl apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata: {name: hermes-data, namespace: hermes}
spec: {accessModes: [ReadWriteOnce], resources: {requests: {storage: 2Gi}}}
EOF
kubectl scale deploy hermes-agent -n hermes --replicas=1
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=hermes-agent -n hermes --timeout=60s
```

### Step 4: Recover from Redis and verify

```bash
# Re-establish port-forward (pod changed)
kill %2; kubectl port-forward -n hermes svc/hermes-agent 8642:8642 &

# Fetch recovered messages using the pinned session ID (immune to any intervening calls)
curl -s "http://localhost:8000/agents/hermes/recovery-context?session_id=${HERMES_SESSION}" | jq .
# Shows: {"messages": [{"role":"user",...}, {"role":"assistant",...}], "session_id": "..."}

# Build the recovered message array and send a follow-up that proves recovery
MESSAGES=$(curl -s "http://localhost:8000/agents/hermes/recovery-context?session_id=${HERMES_SESSION}" | jq '.messages')

curl -s -X POST http://localhost:8642/v1/chat/completions \
  -H "Authorization: Bearer $API_SERVER_KEY" \
  -H "Content-Type: application/json" \
  --data "$(echo "$MESSAGES" | jq '{model: "claude-sonnet-4-6", messages: (. + [{"role":"user","content":"How many agent pods did I say I have? Just the number."}])}')" \
  | jq -r '.choices[0].message.content'
```

**Expected**: "47"

Why this proves recovery: the model cannot hallucinate "47" — that number only exists in the recovered conversation from Redis. If it answers correctly, the messages were fetched from Redis and actually sent to the LLM.

### What to point out

- PVC is gone — `kubectl get pvc -n hermes` shows the fresh one
- Redis still has the turns — `redis-cli LRANGE` shows history survived
- The message array has `role: "assistant"` — the LLM treats prior responses as its own, not third-party context
- This is **stateless replay**, not session continuity — Hermes creates a new session on every call. There is no persistent session to resume; recovery means carrying forward the message context from Redis into a fresh call

---

## Claude Code

### Step 1: Establish conversation (2 turns)

Use a unique detail (project codename "ZEPHYR-9") that the model can't hallucinate:

```bash
SESSION_ID=$(python3 -c "import uuid; print(uuid.uuid4())")

kubectl exec -n claude-code claude-code-agent -- claude -p \
  "Our project codename is ZEPHYR-9. Remember that codename. Now, we need persistent storage for it on Kubernetes. What is a PersistentVolume? One sentence." \
  --session-id "$SESSION_ID" --output-format json | jq -r '.result'

kubectl exec -n claude-code claude-code-agent -- claude -p \
  "And what does a StatefulSet add on top of that for ZEPHYR-9? One sentence." \
  --resume "$SESSION_ID" --output-format json | jq -r '.result'
```

Verify the variable is set before continuing (all subsequent steps depend on it):

```bash
echo "SESSION_ID=$SESSION_ID"
# Must print a UUID like: SESSION_ID=a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

### Step 2: Backup JSONL to Redis

```bash
JSONL=$(kubectl exec -n claude-code claude-code-agent -- \
  cat /claude-home/.claude/projects/-home-agent/${SESSION_ID}.jsonl)

curl -s -X POST "http://localhost:8000/sessions/${SESSION_ID}/transcript" \
  -H "Content-Type: application/json" \
  -d "{\"agent_name\":\"claude-code\",\"jsonl\":$(echo "$JSONL" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')}"

# Verify backup size
kubectl exec -n platform deploy/redis -- redis-cli STRLEN session:${SESSION_ID}:transcript
```

### Step 3: Kill pod + delete PVC

```bash
kubectl delete pod -n claude-code claude-code-agent --force --grace-period=0
kubectl delete pvc -n claude-code claude-code-home

kubectl apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata: {name: claude-code-home, namespace: claude-code}
spec: {accessModes: [ReadWriteOnce], resources: {requests: {storage: 1Gi}}}
EOF
```

### Step 4: Recreate pod (init container restores JSONL)

```bash
kubectl apply -f deployments/experiments/claude-code/claude-code-pod.yaml
kubectl wait --for=condition=Ready pod/claude-code-agent -n claude-code --timeout=60s
```

### Step 5: Verify restore

```bash
# Init container log confirms restore
kubectl logs -n claude-code claude-code-agent -c init-config
# EXPECTED: "Transcript restore: restored session <id> (XXXX bytes) to ..."

# File exists on the fresh PVC
kubectl exec -n claude-code claude-code-agent -- \
  ls -la /claude-home/.claude/projects/-home-agent/${SESSION_ID}.jsonl
```

### Step 6: Resume and verify

Ask about the unique detail that only exists in the recovered session:

```bash
kubectl exec -n claude-code claude-code-agent -- claude -p \
  "What is our project codename? Just the codename, nothing else." \
  --resume "$SESSION_ID" --output-format json \
  | jq '{result: .result, session_id: .session_id, cache_creation_input_tokens: .usage.cache_creation_input_tokens, input_tokens: .usage.input_tokens}'
```

**Expected**:
```json
{
  "result": "ZEPHYR-9",
  "session_id": "<same as $SESSION_ID>",
  "cache_creation_input_tokens": 39000,
  "input_tokens": 6
}
```

Why this proves recovery: "ZEPHYR-9" is a made-up codename that only exists in the JSONL restored from Redis. The model cannot hallucinate it.

### What to point out

- PVC was deleted and recreated fresh — all local state gone
- Init container log: JSONL fetched from Redis and written to disk
- `session_id` matches original — same session, not a new one
- `cache_creation_input_tokens: ~39000` — full prior transcript loaded
- `input_tokens: 6` — only the new question is "new"
- Agent says "I explained..." — owns prior statements

---

## Key Demo Moments

| Action | What audience sees |
|--------|-------------------|
| `kubectl delete pvc` | "All local state is now gone" |
| `redis-cli LRANGE/STRLEN` | "But Redis still has it" |
| Init container log | "Restored from Redis on fresh pod start" |
| Agent answers follow-up | "It remembers — and owns the prior statements" |
| `cache_creation_input_tokens` | "The full transcript was loaded, not a summary" |
| `role: "assistant"` in array | "Structurally identical to a live session" |

The single strongest moment: delete the PVC on screen, then immediately ask a follow-up that only makes sense with prior context.

---

## Cleanup: Restore PVCs After Demo

If you need working PVCs back after the demo (the demo leaves fresh empty PVCs in place, but if you deleted them as a final flourish):

```bash
# Hermes — recreate PVC, deployment controller handles the rest
kubectl apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata: {name: hermes-data, namespace: hermes}
spec: {accessModes: [ReadWriteOnce], resources: {requests: {storage: 2Gi}}}
EOF
kubectl rollout restart deploy/hermes-agent -n hermes
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=hermes-agent -n hermes --timeout=60s

# Claude Code — recreate PVC, then recreate pod (bare pod, no controller)
kubectl apply -f - <<EOF
apiVersion: v1
kind: PersistentVolumeClaim
metadata: {name: claude-code-home, namespace: claude-code}
spec: {accessModes: [ReadWriteOnce], resources: {requests: {storage: 1Gi}}}
EOF
kubectl delete pod -n claude-code claude-code-agent --ignore-not-found
kubectl apply -f deployments/experiments/claude-code/claude-code-pod.yaml
kubectl wait --for=condition=Ready pod/claude-code-agent -n claude-code --timeout=60s
```

Both pods will start fresh with empty PVCs. The init containers will restore config (and JSONL for Claude Code if a transcript exists in Redis).
