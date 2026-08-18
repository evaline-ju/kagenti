"""Fixtures for the transparent-inbound E2E suite.

Deploys two agents into a scratch namespace — one with
``inboundInterception: transparent``, one with the default ``reverse-proxy`` —
so the bypass property can be asserted against a control rather than in
isolation. A single-shape run cannot distinguish "validation works" from
"nothing reached the agent at all".
"""

import json
import os
import subprocess
import time

import pytest

NAMESPACE = os.environ.get("TI_NAMESPACE", "ti-e2e")
AGENT_IMAGE = os.environ.get(
    "TI_AGENT_IMAGE", "ghcr.io/rossoctl/cortex/demos/echo-upstream:latest"
)
# The port the agent binds. Under transparent interception it must stay this;
# under reverse-proxy the operator relocates the agent off it.
AGENT_PORT = int(os.environ.get("TI_AGENT_PORT", "8000"))
READY_TIMEOUT = int(os.environ.get("TI_READY_TIMEOUT", "180"))


def kubectl(*args, check=True, stdin=None):
    """Run kubectl and return stdout. Raises on non-zero unless check=False."""
    proc = subprocess.run(
        ["kubectl", *args],
        capture_output=True,
        text=True,
        input=stdin,
        timeout=120,
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"kubectl {' '.join(args)} failed ({proc.returncode}):\n"
            f"stdout: {proc.stdout}\nstderr: {proc.stderr}"
        )
    return proc.stdout


def kubectl_json(*args):
    return json.loads(kubectl(*args, "-o", "json"))


def _namespace_config(mechanism: str) -> str:
    """authbridge-runtime-config for a namespace.

    inboundInterception is a namespace-scoped switch (the pod mutator has no
    access to the AgentRuntime CR), so the mechanism is selected here rather
    than per-agent. That is why each mechanism needs its own namespace.
    """
    return (
        "mode: proxy-sidecar\n"
        f"inboundInterception: {mechanism}\n"
        "egressEnforcement: enforce-redirect\n"
    )


def _wait_ready(namespace: str, name: str, timeout: int):
    """Block until the deployment reports all replicas ready."""
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        out = kubectl(
            "get", "deployment", name, "-n", namespace,
            "-o", "jsonpath={.status.readyReplicas}/{.status.replicas}",
            check=False,
        )
        last = out
        if out.strip() in ("1/1",):
            return
        time.sleep(3)
    # Dump enough to triage without re-running: pod status is where an
    # iptables/init failure or a port collision shows up.
    pods = kubectl("get", "pods", "-n", namespace, "-o", "wide", check=False)
    events = kubectl(
        "get", "events", "-n", namespace, "--sort-by=.lastTimestamp", check=False
    )
    raise AssertionError(
        f"deployment {namespace}/{name} not ready within {timeout}s "
        f"(readyReplicas={last})\npods:\n{pods}\nevents (tail):\n{events[-2000:]}"
    )


def _agent_manifest(namespace: str, name: str) -> str:
    """A minimal HTTP agent Deployment + Service.

    Declares containerPort explicitly: the reverse-proxy mechanism relocates
    Ports[0], so the declared value is what the test inspects to tell the two
    mechanisms apart.
    """
    return f"""
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {name}
  namespace: {namespace}
  labels:
    rossoctl.io/type: agent
spec:
  replicas: 1
  selector:
    matchLabels:
      app: {name}
  template:
    metadata:
      labels:
        app: {name}
        rossoctl.io/type: agent
    spec:
      containers:
        - name: agent
          image: {AGENT_IMAGE}
          imagePullPolicy: IfNotPresent
          ports:
            - containerPort: {AGENT_PORT}
---
apiVersion: v1
kind: Service
metadata:
  name: {name}
  namespace: {namespace}
spec:
  selector:
    app: {name}
  ports:
    - port: {AGENT_PORT}
      targetPort: {AGENT_PORT}
"""


def _ensure_namespace(namespace: str, mechanism: str):
    kubectl("create", "namespace", namespace, check=False)
    # Render then apply via stdin so a re-run updates rather than conflicts.
    cm = kubectl(
        "create", "configmap", "authbridge-runtime-config",
        "-n", namespace,
        f"--from-literal=config.yaml={_namespace_config(mechanism)}",
        "--dry-run=client", "-o", "yaml",
    )
    kubectl("apply", "-f", "-", stdin=cm)


@pytest.fixture(scope="session")
def linux_nodes():
    """Skip the suite where iptables interception cannot work."""
    nodes = kubectl_json("get", "nodes")
    for node in nodes.get("items", []):
        if node["status"]["nodeInfo"]["operatingSystem"] != "linux":
            pytest.skip("transparent inbound is Linux/iptables only")
    return True


@pytest.fixture(scope="session")
def transparent_agent(linux_nodes):
    """Agent deployed with inboundInterception: transparent."""
    ns = f"{NAMESPACE}-transparent"
    name = "ti-agent"
    _ensure_namespace(ns, "transparent")
    kubectl("apply", "-f", "-", stdin=_agent_manifest(ns, name))
    _wait_ready(ns, name, READY_TIMEOUT)
    yield {"namespace": ns, "name": name}
    if os.environ.get("TI_KEEP_NAMESPACE") != "1":
        kubectl("delete", "namespace", ns, "--wait=false", check=False)


@pytest.fixture(scope="session")
def reverse_proxy_agent(linux_nodes):
    """Control: agent deployed with the default port-stealing mechanism."""
    ns = f"{NAMESPACE}-reverse"
    name = "rp-agent"
    _ensure_namespace(ns, "reverse-proxy")
    kubectl("apply", "-f", "-", stdin=_agent_manifest(ns, name))
    _wait_ready(ns, name, READY_TIMEOUT)
    yield {"namespace": ns, "name": name}
    if os.environ.get("TI_KEEP_NAMESPACE") != "1":
        kubectl("delete", "namespace", ns, "--wait=false", check=False)


def agent_pod(namespace: str, name: str) -> dict:
    pods = kubectl_json("get", "pods", "-n", namespace, "-l", f"app={name}")
    running = [p for p in pods["items"] if p["status"]["phase"] == "Running"]
    assert running, f"no Running pod for {namespace}/{name}"
    return running[0]


def container(pod: dict, name: str) -> dict:
    for c in pod["spec"]["containers"] + pod["spec"].get("initContainers", []):
        if c["name"] == name:
            return c
    raise AssertionError(
        f"container {name} not found in pod {pod['metadata']['name']}; "
        f"have {[c['name'] for c in pod['spec']['containers']]}"
    )


def env_of(c: dict, key: str):
    for e in c.get("env", []):
        if e["name"] == key:
            return e
    return None


def curl_from_probe(namespace: str, url: str) -> int:
    """Dial url from a throwaway pod in `namespace` and return the HTTP status.

    A separate pod is essential: the whole point is to exercise the pod-to-pod
    path. Dialing from inside the agent pod would traverse loopback, which is
    inside the trust boundary and deliberately not intercepted.
    """
    out = kubectl(
        "run", f"ti-probe-{int(time.time() * 1000) % 100000}",
        "-n", namespace, "--rm", "-i", "--restart=Never",
        "--image=curlimages/curl:8.11.1", "--quiet", "--",
        "curl", "-s", "-o", "/dev/null", "-w", "%{http_code}",
        "--max-time", "15", url,
        check=False,
    )
    digits = "".join(ch for ch in out if ch.isdigit())
    assert digits, f"probe produced no HTTP status for {url}; raw output: {out!r}"
    return int(digits[-3:])
