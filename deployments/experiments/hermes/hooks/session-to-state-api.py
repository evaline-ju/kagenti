#!/usr/bin/env python3
"""Hermes hook: post_llm_call → POST to state-query-api.

Posts both:
1. /hook/stop — session metadata (existing behavior)
2. /hook/turn — full turn (user + assistant messages) for recovery
"""
import json, sqlite3, sys, urllib.request, os

STATE_API_BASE = "http://state-query-api.platform.svc.cluster.local:8000"
AGENT_NAME = "hermes"
NAMESPACE = os.environ.get("POD_NAMESPACE", "hermes")

raw = sys.stdin.read()
data = {}
try:
    data = json.loads(raw) if raw.strip() else {}
except Exception:
    pass

session_id = data.get("session_id", "")
extra = data.get("extra", {})

if not session_id:
    sys.exit(0)

# Get token counts from SQLite
input_tokens = 0
output_tokens = 0
try:
    db = sqlite3.connect("/opt/data/state.db")
    row = db.execute(
        "SELECT input_tokens, output_tokens FROM sessions WHERE id = ?",
        (session_id,),
    ).fetchone()
    if row:
        input_tokens = row[0] or 0
        output_tokens = row[1] or 0
    db.close()
except Exception:
    pass

# 1. POST session metadata (existing behavior)
metadata_payload = json.dumps({
    "session_id": session_id,
    "agent_name": AGENT_NAME,
    "namespace": NAMESPACE,
    "usage": {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    },
    "last_assistant_message": extra.get("assistant_response", ""),
    "cwd": data.get("cwd", "/opt/data"),
    "transcript_path": "",
    "total_cost_usd": 0,
}).encode()

try:
    req = urllib.request.Request(
        f"{STATE_API_BASE}/hook/stop",
        data=metadata_payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    urllib.request.urlopen(req, timeout=5)
except Exception:
    pass

# 2. POST full turn for recovery
user_message = extra.get("user_message", "")
assistant_message = extra.get("assistant_response", "")

if user_message or assistant_message:
    turn_payload = json.dumps({
        "session_id": session_id,
        "agent_name": AGENT_NAME,
        "user_message": user_message,
        "assistant_message": assistant_message,
    }).encode()

    try:
        req = urllib.request.Request(
            f"{STATE_API_BASE}/hook/turn",
            data=turn_payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception:
        pass
