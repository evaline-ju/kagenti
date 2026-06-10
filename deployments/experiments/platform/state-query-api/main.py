"""
State Query API — platform-level session visibility across agent harnesses.

Queries Redis for session metadata written by agent hooks (Hermes, Claude Code).
Provides a unified view of all agent sessions regardless of harness type.
Also receives Stop hook callbacks from Claude Code and writes session state to Redis.
"""

import os
import time
from typing import Any, Optional

import redis
from fastapi import FastAPI, Query, Request

app = FastAPI(title="Agent State Query API")

REDIS_HOST = os.environ.get("REDIS_HOST", "redis.platform.svc.cluster.local")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))

r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


@app.post("/hook/stop")
async def hook_stop(request: Request):
    """Receive Claude Code Stop hook payload and write session metadata to Redis."""
    payload = await request.json()

    session_id = payload.get("session_id", "")
    if not session_id:
        return {"status": "ignored", "reason": "no session_id"}

    usage = payload.get("usage", {})
    agent_name = payload.get("agent_name", "claude-code")
    namespace = payload.get("namespace", "claude-code")
    last_active = int(time.time())

    r.hset(f"session:{session_id}", mapping={
        "agent_name":           agent_name,
        "namespace":            namespace,
        "session_id":           session_id,
        "last_assistant_message": payload.get("last_assistant_message", ""),
        "transcript_path":      payload.get("transcript_path", ""),
        "cwd":                  payload.get("cwd", ""),
        "input_tokens":         usage.get("input_tokens", 0),
        "output_tokens":        usage.get("output_tokens", 0),
        "total_cost_usd":       payload.get("total_cost_usd", 0),
        "last_active":          last_active,
    })
    r.zadd(f"sessions:{agent_name}", {session_id: last_active})
    r.zadd("sessions:all", {session_id: last_active})

    return {"status": "ok", "session_id": session_id}


@app.post("/hook/turn")
async def hook_turn(request: Request):
    """Receive a full conversation turn (user + assistant) and persist to Redis list."""
    payload = await request.json()

    session_id = payload.get("session_id", "")
    if not session_id:
        return {"status": "ignored", "reason": "no session_id"}

    agent_name = payload.get("agent_name", "unknown")
    user_message = payload.get("user_message", "")
    assistant_message = payload.get("assistant_message", "")

    if not user_message and not assistant_message:
        return {"status": "ignored", "reason": "no messages"}

    import json as _json
    turn = _json.dumps({
        "user": user_message,
        "assistant": assistant_message,
        "timestamp": int(time.time()),
    })
    r.rpush(f"session:{session_id}:turns", turn)
    r.hset(f"session:{session_id}", "agent_name", agent_name)
    r.hset(f"session:{session_id}", "has_turns", "true")

    # Track last active session per agent
    r.set(f"agent:{agent_name}:last_session", session_id)

    return {"status": "ok", "session_id": session_id, "turn_count": r.llen(f"session:{session_id}:turns")}


@app.get("/sessions/{session_id}/turns")
def get_session_turns(session_id: str):
    """Retrieve full conversation history for a session."""
    import json as _json
    raw_turns = r.lrange(f"session:{session_id}:turns", 0, -1)
    if not raw_turns:
        return {"session_id": session_id, "turns": [], "count": 0}

    turns = [_json.loads(t) for t in raw_turns]
    return {"session_id": session_id, "turns": turns, "count": len(turns)}


@app.get("/agents/{agent_name}/last-session")
def get_last_session(agent_name: str):
    """Get the last active session ID for an agent."""
    session_id = r.get(f"agent:{agent_name}:last_session")
    if not session_id:
        return {"agent_name": agent_name, "last_session": None}
    return {"agent_name": agent_name, "last_session": session_id}


@app.get("/agents/{agent_name}/recovery-context")
def get_recovery_context(agent_name: str, session_id: Optional[str] = Query(None)):
    """Return turns as a message array for injection after pod death.

    If session_id is provided, use that specific session.
    Otherwise:
    - For agents with multi-turn sessions (e.g. Hermes): pick session with most turns.
    - For agents with single-turn sessions (e.g. Claude Code -p mode): aggregate recent
      turns across all sessions chronologically.
    """
    import json as _json

    recovered = r.get(f"agent:{agent_name}:recovered_session")

    if session_id:
        if recovered == session_id:
            return {"messages": [], "session_id": session_id, "turn_count": 0, "already_recovered": True}
        raw_turns = r.lrange(f"session:{session_id}:turns", 0, -1)
        messages = []
        for t in raw_turns:
            turn = _json.loads(t)
            if turn.get("user"):
                messages.append({"role": "user", "content": turn["user"]})
            if turn.get("assistant"):
                messages.append({"role": "assistant", "content": turn["assistant"]})
        return {"messages": messages, "session_id": session_id, "turn_count": len(raw_turns)}

    # Default: use the most recent session (last one the hook wrote to)
    last_session = r.get(f"agent:{agent_name}:last_session")
    if not last_session:
        return {"messages": [], "session_id": None, "turn_count": 0}

    if recovered == last_session:
        return {"messages": [], "session_id": last_session, "turn_count": 0, "already_recovered": True}

    raw_turns = r.lrange(f"session:{last_session}:turns", 0, -1)
    messages = []
    for t in raw_turns:
        turn = _json.loads(t)
        if turn.get("user"):
            messages.append({"role": "user", "content": turn["user"]})
        if turn.get("assistant"):
            messages.append({"role": "assistant", "content": turn["assistant"]})

    return {"messages": messages, "session_id": last_session, "turn_count": len(raw_turns)}


@app.post("/agents/{agent_name}/mark-recovered")
async def mark_recovered(agent_name: str, request: Request):
    """Mark a session as recovered so recovery-context won't re-inject it."""
    payload = await request.json()
    session_id = payload.get("session_id", "")
    if not session_id:
        session_id = r.get(f"agent:{agent_name}:last_session") or ""
    if session_id:
        r.set(f"agent:{agent_name}:recovered_session", session_id)
        return {"status": "ok", "agent_name": agent_name, "recovered_session": session_id}
    return {"status": "ignored", "reason": "no session_id"}


@app.post("/sessions/{session_id}/transcript")
async def store_transcript(session_id: str, request: Request):
    """Store a JSONL transcript for a session (for true resume after PVC loss)."""
    payload = await request.json()
    agent_name = payload.get("agent_name", "unknown")
    jsonl_content = payload.get("jsonl", "")
    cwd = payload.get("cwd", "")
    if not jsonl_content:
        return {"status": "ignored", "reason": "no jsonl content"}

    r.set(f"session:{session_id}:transcript", jsonl_content)
    r.hset(f"session:{session_id}", "has_transcript", "true")
    if cwd:
        r.hset(f"session:{session_id}", "cwd", cwd)
    r.set(f"agent:{agent_name}:transcript_session", session_id)

    return {"status": "ok", "session_id": session_id, "size_bytes": len(jsonl_content)}


@app.get("/sessions/{session_id}/transcript")
def get_transcript(session_id: str):
    """Retrieve a stored JSONL transcript for true resume."""
    jsonl_content = r.get(f"session:{session_id}:transcript")
    if not jsonl_content:
        return {"status": "not_found", "session_id": session_id}

    return {"status": "ok", "session_id": session_id, "jsonl": jsonl_content, "size_bytes": len(jsonl_content)}


@app.get("/agents/{agent_name}/transcript")
def get_agent_transcript(agent_name: str):
    """Get the latest transcript session ID for an agent."""
    session_id = r.get(f"agent:{agent_name}:transcript_session")
    if not session_id:
        return {"agent_name": agent_name, "session_id": None}

    jsonl_content = r.get(f"session:{session_id}:transcript")
    if not jsonl_content:
        return {"agent_name": agent_name, "session_id": session_id, "status": "missing"}

    session_meta = r.hgetall(f"session:{session_id}")
    cwd = session_meta.get("cwd", "")

    return {"agent_name": agent_name, "session_id": session_id, "jsonl": jsonl_content, "size_bytes": len(jsonl_content), "cwd": cwd}


@app.post("/agents/{agent_name}/sqlite-backup")
async def store_sqlite(agent_name: str, request: Request):
    """Store a base64-encoded SQLite database backup for an agent."""
    payload = await request.json()
    db_b64 = payload.get("db", "")
    if not db_b64:
        return {"status": "ignored", "reason": "no db content"}
    r.set(f"agent:{agent_name}:sqlite_backup", db_b64)
    return {"status": "ok", "agent_name": agent_name, "size_bytes": len(db_b64)}


@app.get("/agents/{agent_name}/sqlite-backup")
def get_sqlite(agent_name: str):
    """Retrieve the SQLite database backup for an agent."""
    db_b64 = r.get(f"agent:{agent_name}:sqlite_backup")
    if not db_b64:
        return {"status": "not_found", "agent_name": agent_name}
    return {"status": "ok", "agent_name": agent_name, "db": db_b64, "size_bytes": len(db_b64)}


@app.get("/health")
def health():
    try:
        r.ping()
        return {"status": "ok", "redis": "connected"}
    except redis.ConnectionError:
        return {"status": "degraded", "redis": "disconnected"}


@app.get("/sessions")
def list_sessions(
    agent: Optional[str] = Query(None, description="Filter by agent name"),
    limit: int = Query(50, ge=1, le=500),
):
    key = f"sessions:{agent}" if agent else "sessions:all"
    session_ids = r.zrevrange(key, 0, limit - 1)

    sessions = []
    for sid in session_ids:
        data = r.hgetall(f"session:{sid}")
        if data:
            data["session_id"] = sid
            sessions.append(data)

    return {"sessions": sessions, "count": len(sessions)}


@app.get("/sessions/{session_id}")
def get_session(session_id: str):
    data = r.hgetall(f"session:{session_id}")
    if not data:
        return {"error": "session not found"}, 404
    data["session_id"] = session_id
    return data


@app.get("/stats")
def stats():
    total = r.zcard("sessions:all")

    agent_stats = {}
    for key in r.scan_iter("sessions:*"):
        if key == "sessions:all":
            continue
        agent_name = key.split(":", 1)[1]
        agent_stats[agent_name] = r.zcard(key)

    total_input_tokens = 0
    total_output_tokens = 0
    for sid in r.zrange("sessions:all", 0, -1):
        data = r.hgetall(f"session:{sid}")
        total_input_tokens += int(data.get("input_tokens", 0))
        total_output_tokens += int(data.get("output_tokens", 0))

    return {
        "total_sessions": total,
        "by_agent": agent_stats,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "timestamp": int(time.time()),
    }
