#!/usr/bin/env python3
"""Restore JSONL transcript from Redis via state-query-api.

Runs in the init container before Claude Code starts.
If a transcript exists in Redis for this agent, writes it to disk
so that --resume can find it. Enables true resume after PVC loss.
"""
import json, os, sys, urllib.request

STATE_API = os.environ.get("STATE_API", "http://state-query-api.platform.svc.cluster.local:8000")
AGENT_NAME = "claude-code"
CLAUDE_HOME = os.environ.get("CLAUDE_HOME", "/claude-home")

try:
    resp = urllib.request.urlopen(f"{STATE_API}/agents/{AGENT_NAME}/transcript", timeout=5)
    data = json.loads(resp.read())
except Exception as e:
    print(f"Transcript restore: no connection ({e})")
    sys.exit(0)

if not data.get("jsonl") or not data.get("session_id"):
    print("Transcript restore: no transcript in Redis")
    sys.exit(0)

session_id = data["session_id"]
jsonl_content = data["jsonl"]

# Claude Code stores transcripts under ~/.claude/projects/<project-key>/
# The project key is derived from the CWD: /home/agent → -home-agent
# Write to the correct path so --resume can find it.
cwd = "/home/agent"
project_key = cwd.replace("/", "-")
project_dir = os.path.join(CLAUDE_HOME, ".claude", "projects", project_key)
os.makedirs(project_dir, exist_ok=True)

transcript_path = os.path.join(project_dir, f"{session_id}.jsonl")
with open(transcript_path, "w") as f:
    f.write(jsonl_content)

print(f"Transcript restore: restored session {session_id} ({len(jsonl_content)} bytes) to {transcript_path}")
