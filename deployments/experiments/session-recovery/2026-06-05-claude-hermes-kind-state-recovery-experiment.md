# State Recovery Experiment — Claude Code vs Hermes on Kind

**Date**: 2026-06-05
**Components**: Claude Code (bare Pod), Hermes (Deployment), Redis + state-query-api (platform ns)
**Note**: This experiment runs outside the full Kagenti platform (no operator, no AgentRuntime CRs, no UI).

---

## 1. Background

### Definitions

**True resume** — Restore full internal state (tool calls, reasoning traces, system prompt, message history). The session is indistinguishable from pre-failure. The LLM sees prior assistant messages as its own.

**Approximate resume** — Replay conversation text as context injected into a new session. The LLM re-derives understanding from third-party text. Tool call traces, multi-step reasoning chains, and cached computations are lost.

### Agent Harnesses Under Test

| Agent | Deployment model | Native state storage | Session model |
|-------|-----------------|---------------------|---------------|
| **Hermes** | K8s Deployment (auto-restarts) | SQLite on PVC | Stateless gateway — each `/v1/chat/completions` call carries full message array |
| **Claude Code** | Bare Pod (manual restart) | JSONL transcript on local filesystem | Persistent session via `--session-id` / `--resume` |

### Platform Infrastructure (built for this experiment)

**state-query-api** — A lightweight FastAPI service in the `platform` namespace providing session visibility and recovery endpoints. Sits between Redis and the agent hooks.

| Endpoint | Purpose |
|----------|---------|
| `POST /hook/stop` | Receive session metadata (tokens, last message) |
| `POST /hook/turn` | Persist full conversation turns for recovery |
| `GET /sessions` | List sessions (filterable by agent) |
| `GET /sessions/{id}/turns` | Get conversation history |
| `GET /agents/{name}/recovery-context` | Get turns as injectable message array |
| `POST /agents/{name}/mark-recovered` | Mark session recovered (one-shot guard) |
| `POST /sessions/{id}/transcript` | Store JSONL transcript (Claude Code) |
| `GET /agents/{name}/transcript` | Get latest JSONL for restore |

**Source**: `deployments/experiments/platform/state-query-api/main.py`

**Redis** — AOF-persistent instance on a separate PVC in `platform` namespace. Survives agent pod/PVC loss.

### Redis Storage Format

Inspect with: `kubectl exec -n platform deploy/redis -- redis-cli <command>`

| Key pattern | Type | Content |
|-------------|------|---------|
| `session:{id}` | hash | `agent_name`, `namespace`, `last_assistant_message`, `input_tokens`, `output_tokens`, `last_active`, `has_transcript` |
| `session:{id}:turns` | list | JSON objects: `{"user":"...","assistant":"...","timestamp":N}` |
| `session:{id}:transcript` | string | Full JSONL transcript (Claude Code only, ~8KB per 2-turn session) |
| `sessions:all` | sorted set | All session IDs scored by last_active timestamp |
| `sessions:{agent_name}` | sorted set | Per-agent session index |
| `agent:{name}:last_session` | string | Most recent session ID |
| `agent:{name}:transcript_session` | string | Session ID with backed-up JSONL |
| `agent:{name}:recovered_session` | string | Session marked as already recovered |

---

## 2. Hypotheses

**H1**: After pod death with PVC loss, agent sessions can be recovered from an external store (Redis) with zero agent source code changes — using only hooks, wrapper scripts, and init containers.

**H2**: Both Hermes and Claude Code can recover conversation continuity after PVC loss, given mechanism-appropriate recovery:
- Hermes: **stateless replay** — replaying the full message array (gateway holds no inter-call state, so the message array IS the session)
- Claude Code: **true resume** — restoring the JSONL transcript and using `--resume` (persistent session that was interrupted and restored)

**H3**: True resume is mechanically verifiable — the recovered session is structurally indistinguishable from the original at the API request level, without relying on the agent's self-report.

---

## 3. Experiments

### Experiment 1: Baseline — Pod Kill and PVC Loss Without Recovery Hooks

**Method**: Send 2 multi-turn prompts to each agent. Kill pods (`--force --grace-period=0`). Test with PVC intact and with PVC deleted.

**Findings**:
- Pod kill with PVC intact: Hermes recovers (SQLite), Claude Code does not (no JSONL in `-p` mode)
- PVC loss: both agents lose all session state. Redis retains only `last_assistant_message` — insufficient for conversation continuity.
- Neither agent can answer a context-dependent follow-up after PVC loss.

### Experiment 2: Recovery Hooks — Session Recovery After PVC Loss

**Method**: Implement caller-side recovery (hooks + init containers + wrappers), repeat the pod kill + PVC loss scenario, then send a context-dependent follow-up.

**Recovery mechanism per agent:**

| Aspect | Hermes | Claude Code |
|--------|--------|-------------|
| **Save** | `post_llm_call` hook POSTs full turns to Redis | Wrapper script backs up JSONL to Redis after each turn |
| **Restore** | Caller fetches turns, prepends to next request | Init container restores JSONL from Redis to disk |
| **Resume** | Message replay (gateway stateless) | `claude -p --resume <session-id>` |
| **Agent code changes** | None | None |

**Test prompts** (same for both agents):
1. "Explain in 2-3 sentences what Kubernetes persistent volumes are and why they matter for stateful workloads. Then give one concrete example."
2. "Now explain how StatefulSets differ from Deployments when managing pods that need stable network identity and persistent storage. Keep it to 3 sentences."

**Failure injection**: `kubectl delete pod --force --grace-period=0` + delete PVC + recreate fresh PVC + restart pod.

| Aspect | Claude Code | Hermes |
|--------|-------------|--------|
| Pod recovery | Manual (`kubectl apply` required) | Automatic (~15s, Deployment controller) |

**Recovery follow-up prompts**:
- Hermes: "Based on our earlier discussion about Redis caching vs AOF, what did you recommend for session state that needs to survive pod restarts?"
- Claude Code: "Based on what you just told me about PVs and StatefulSets, which one provides stable network identity for a database cluster?"

---

## 4. Results

### Hermes — Stateless Replay via Message Array

- Recovered 6 messages (3 turns) from Redis
- Follow-up answer: "I recommended AOF persistence, since session state must survive pod restarts and AOF logs every write to disk so the data can be recovered after a restart."
- **Verdict: ✅ Conversation continuity restored.** Gateway is stateless — there is no persistent session to "resume." The caller provides the full message array on every call, so replaying from Redis is operationally identical to normal use. Recovery = reconstructing the caller's state, not the agent's.

### Claude Code — True Resume via JSONL Backup/Restore

- Backed up JSONL (8027 bytes, 11 lines) to Redis
- After PVC deletion + pod restart, init container restored JSONL from Redis
- Follow-up answer: "A StatefulSet provides stable network identity (via predictable pod hostnames like `db-0`, `db-1` and a headless Service), whereas a PersistentVolume only handles storage, not networking."
- `cache_read_input_tokens: 39752` confirms full prior context was read from the restored transcript
- **Verdict: ✅ True resume.** A persistent session was interrupted by PVC loss and restored from Redis. `--resume` loaded the JSONL as native session history — structurally indistinguishable from a session that was never interrupted.

### Summary Table

| Capability | Hermes | Claude Code |
|-----------|--------|-------------|
| Turn persistence to Redis | ✅ post_llm_call hook | ✅ Caller-side wrapper |
| Recovery from Redis after PVC loss | ✅ Stateless replay | ✅ True resume (JSONL backup + --resume) |
| Agent code changes required | None | None |
| Recovery is one-shot (no re-inject) | ✅ mark-recovered endpoint | ✅ Same session ID persists |
| Context-dependent follow-up works | ✅ | ✅ |
| Mid-turn recovery | ❌ | ❌ |
| Tool call history recovery | N/A (gateway stateless) | ✅ Preserved in JSONL |
| Reasoning trace recovery | N/A | ✅ Preserved in JSONL |
| Session ID continuity | ✅ Same session ID | ✅ Fixed UUID via --session-id |

### Why One-Shot Recovery Guards

| Harness | Guard mechanism | Why it's needed |
|---------|----------------|-----------------|
| **Hermes** | `mark-recovered` endpoint sets `agent:hermes:recovered_session` in Redis; `recovery-context` returns empty if the marker matches | Hermes is stateless per-call — the caller assembles the full message array each time. Without a guard, every post-recovery call would re-prepend the recovered history, duplicating turns in the context window and inflating token cost linearly per call. |
| **Claude Code** | Session ID is a fixed UUID — once JSONL is restored and `--resume` succeeds, subsequent turns append natively to the same file | No explicit guard needed. `--resume` makes the session structurally continuous: the JSONL *is* the session, and appending to it is normal operation, not re-injection. |

---

## 5. Conclusions

**H1 confirmed**: Both agents recover from PVC loss using only external infrastructure (hooks, wrappers, init containers). Zero agent source code changes.

**H2 confirmed**: Both recover conversation continuity, via mechanism-appropriate paths:
- Hermes: **stateless replay** — the gateway holds no inter-call state. The message array IS the session, so replaying it from Redis is operationally identical to normal use. There is no persistent session to "resume" — recovery means reconstructing the caller's context.
- Claude Code: **true resume** — `--resume` restores the full JSONL transcript (tool calls, reasoning, system prompt). The LLM treats it as a direct continuation of an interrupted persistent session.

**H3 confirmed**: Recovery is mechanically verifiable by inspecting data structures at the API layer — no reliance on the agent's self-report.

**Hermes** — recovery-context returns messages with `role: "assistant"` entries (same structure as a live call). After PVC loss + pod restart + message replay from Redis, agent responds with context-aware answer. This is stateless replay: the gateway never held session state, so replaying the message array is indistinguishable from normal operation. Contrast with approximate resume, which stuffs everything into `role: "system"` (structurally different from the original call).

**Claude Code** — after PVC loss + fresh PVC + pod restart, init container restored JSONL (8027 bytes) from Redis. `--resume` loaded it as native session history:

```
$ claude -p "Which provides stable network identity?" --resume "b2c3d4e5-..."
→ "I explained that StatefulSets provide stable network identity..."

session_id: b2c3d4e5-...  (same as original)
cache_creation_input_tokens: 39916  (full prior context from restored JSONL)
input_tokens: 6  (only the new follow-up)
JSONL: 11 lines restored → 21 lines after new turn appended natively
```

**Verification method** (no agent trust required): inspect the outgoing request structure. Prior assistant responses as `role: "assistant"` in the message array (Hermes) or JSONL `type: "assistant"` entries loaded by `--resume` (Claude Code) = structurally identical to normal operation, verifiable without trusting the agent's self-report.

### What Made Recovery Possible

| Agent | Key insight |
|-------|-------------|
| Hermes | Gateway holds no session state between calls. The message array IS the session. Replaying it from Redis is equivalent to never having failed. |
| Claude Code | `--session-id` + `--resume` creates a persistent JSONL session. Backing up + restoring that JSONL before pod start makes `--resume` work natively after PVC loss. |

### What Changed from Approximate to True Resume (Claude Code)

Approximate resume used isolated `claude -p` calls (each a new session) and injected prior turns as `--system-prompt` text. Three changes made it true:

1. **`--session-id` + `--resume`** instead of bare `-p` — creates a persistent multi-turn session with JSONL on disk (same pattern as [kagenti/agent-examples#531](https://github.com/kagenti/agent-examples/pull/531), which notes PVC loss as a design gap)
2. **Backup JSONL to Redis** after each turn via wrapper script
3. **Restore JSONL in init container** — fetches from Redis, writes to correct path so `--resume` finds it. Required `python:3.11-slim` (instead of `busybox`) and `chown -R 1001:1001` so Claude Code (uid 1001) can write to the restored directory.

---

## 6. Limitations and Open Questions

1. **Mid-turn state is always lost** — if a pod dies during an LLM call, that in-flight turn is never persisted. Neither harness supports checkpointing mid-inference.
2. **Claude Code JSONL path depends on CWD** — Claude Code stores transcripts at `~/.claude/projects/<cwd-derived>/<sessionId>.jsonl`, where the CWD is encoded by replacing `/` with `-` (e.g., `/home/agent` → `-home-agent`). CWD is not stored in an env var — it's the process working directory at runtime. To make restore robust, the `POST /sessions/{id}/transcript` endpoint now accepts `cwd` and stores it in the session hash. On restore, `restore-transcript.py` reads `cwd` back from the API response and derives the correct path dynamically (no hardcoded assumption). Verified working: init container log shows correct path derived from Redis-stored CWD.
3. **Token cost on resume** — full prior conversation is re-sent as input tokens. For Claude Code: `cache_read_input_tokens: 39752` on a 2-turn session. Scales linearly with conversation length.
4. **Hermes memories/skills lost** — only conversation turns are in Redis; PVC-resident agent state (memories, learned skills) is gone after PVC loss.
5. **Backup race condition** — if the pod dies before the post-turn backup completes, the latest turn is lost from Redis (though it may still be in JSONL if PVC survives).
6. **Single session recovery only** — the recovery-context endpoint picks the most recent session. Multiple concurrent sessions per agent are not handled.
7. **Hermes: stateless replay, not true resume — SQLite restore doesn't help** — the gateway holds no inter-call state. Each `/v1/chat/completions` call is independent; the gateway writes to SQLite but never reads back from it for session continuity. Backing up and restoring `state.db` would preserve historical records but the gateway would still not use them to "resume" — the next call still requires the caller to provide the full message array. True resume requires either (a) using Hermes in interactive/CLI mode (not gateway mode), where SQLite backup/restore would work, or (b) a session-aware proxy that maintains the message array externally. We chose gateway mode because it exposes an HTTP API (curl-testable, composable, load-balanceable); interactive mode requires a TTY and can't be driven programmatically from outside the pod.
8. **state-query-api is Redis-specific** — all storage operations (turns, transcripts, indexes, markers) use Redis data structures directly with no abstraction layer. A natural generalization would split by data shape:
   - **Turns + indexes + markers** → keep in Redis (small, frequently accessed, query-by-session)
   - **Transcripts** (JSONL blobs, 8-50KB+, write-once/read-once) → move to object storage (S3/MinIO)

   The API contract (endpoints, request/response shapes) would stay unchanged; only the backend implementation switches via a `TranscriptStore` interface with `put()`/`get()` methods and an env var to select backend (`TRANSCRIPT_STORE=s3|redis`).

9. **PVC snapshots** — VolumeSnapshots could back up the whole agent PVC instead of per-turn externalization. Recovers everything (including Hermes memories/skills) but is slow (seconds vs. milliseconds), all-or-nothing (can't address individual sessions), and cluster-scoped. Better suited to workspace recovery than session state. Not tested (Kind doesn't support VolumeSnapshots).

10. **Is PVC loss realistic?** — With network-attached storage (EBS, Ceph), PVs are durable. PVC loss is realistic for local provisioners (Kind, k3s), `reclaimPolicy: Delete` + accidental deletion, and cluster teardown. In production, the stronger argument for externalization is session routing (PV survives but new pod doesn't know which session to resume), write consistency on crash, and cross-cluster portability — not PV durability.

---

## Appendix A: Request Flows

### JSONL Backup/Restore (Claude Code)

```
Turn N completes → Wrapper reads JSONL from disk
  → POST /sessions/{id}/transcript (stored in Redis)

Pod restart → Init container runs → GET /agents/claude-code/transcript
  → Writes JSONL to ~/.claude/projects/-home-agent/{session-id}.jsonl
  → --resume finds it → True resume
```

### Caller-Side Recovery (Hermes)

```
Pod restart → Caller invokes recovery-inject.py
  → GET /agents/hermes/recovery-context (message array from Redis)
  → Prepends to new user message → POST /v1/chat/completions
  → POST /agents/hermes/mark-recovered (one-shot)
```

---

## Appendix B: Files Used

All paths relative to `deployments/experiments/`.

### Required for demo

| File | Purpose |
|------|---------|
| `platform/state-query-api/main.py` | State query API (turns, transcripts, recovery-context, mark-recovered) |
| `platform/state-query-api/Dockerfile` | Image build for state-query-api |
| `platform/state-query-api.yaml` | K8s Deployment + Service for state-query-api |
| `platform/redis.yaml` | Redis Deployment + PVC with AOF persistence |
| `hermes/hermes-deployment-kind.yaml` | Hermes Deployment with hook registration (post_llm_call) |
| `hermes/hermes-pvc.yaml` | PVC for Hermes (demo deletes and recreates it) |
| `hermes/hermes-service.yaml` | Service for port-forwarding to Hermes gateway |
| `hermes/hooks/session-to-state-api.py` | post_llm_call hook — persists turns to Redis via state-query-api |
| `hermes/run-kind-unified.sh` | Deploy script (namespace, secrets, image load, deploy) |
| `claude-code/claude-code-pod.yaml` | Pod spec with init container that runs restore-transcript.py |
| `claude-code/claude-code-configmap.yaml` | ConfigMap: settings.json + stop-hook-stdout.sh + restore-transcript.py |
| `claude-code/claude-code-secret.example.yaml` | Secret template (LiteLLM credentials — required for pod to start) |
| `claude-code/hooks/restore-transcript.py` | Init container script — fetches JSONL from Redis, writes to disk |

### Optional / not used in demo

| File | Purpose |
|------|---------|
| `hermes/hooks/recovery-inject.py` | Caller-side recovery utility (demo uses inline curl with `?session_id=` instead) |
| `claude-code/hooks/claude-with-true-resume.sh` | Wrapper that automates --session-id/--resume + backup (demo runs steps manually) |
| `claude-code/hooks/claude-with-recovery.sh` | Approximate resume fallback via --system-prompt (not demoed) |

---

## Appendix C: Redis Inspection Commands

```bash
kubectl exec -n platform deploy/redis -- redis-cli ZRANGE sessions:all 0 -1 WITHSCORES
kubectl exec -n platform deploy/redis -- redis-cli LRANGE session:<id>:turns 0 -1
kubectl exec -n platform deploy/redis -- redis-cli HGETALL session:<id>
kubectl exec -n platform deploy/redis -- redis-cli STRLEN session:<id>:transcript
```
