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

# Pre-onboarded namespaces rather than fresh ones. A new namespace lacks the
# platform's per-namespace plumbing (authbridge-config, and the Keycloak client
# registration that produces each agent's credentials Secret), so an injected pod
# there never starts — for reasons that have nothing to do with this feature. The
# installer creates team1/team2 already onboarded; use those.
NS_TRANSPARENT = os.environ.get("TI_NS_TRANSPARENT", "team1")
NS_CONTROL = os.environ.get("TI_NS_CONTROL", "team2")
# A stand-in agent, not a real one: the suite tests the interception boundary,
# not agent behavior. nginx-unprivileged is Alpine-based (so the same image
# doubles as the probe, via busybox wget) and is already cached on Kind nodes.
AGENT_IMAGE = os.environ.get(
    "TI_AGENT_IMAGE", "docker.io/nginxinc/nginx-unprivileged:1.29.1-alpine"
)
PROBE_IMAGE = os.environ.get("TI_PROBE_IMAGE", AGENT_IMAGE)
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


def _stub_credentials(namespace: str, name: str) -> bool:
    """Create a placeholder for the per-agent Keycloak credentials Secret.

    Opt-in via TI_STUB_CREDENTIALS=1, and deliberately NOT the default: stubbing
    by default would let this suite pass on a cluster whose Keycloak client
    registration is broken, hiding a real platform failure behind a green run.

    The stub is sound for what this suite asserts. The Secret is mounted for
    OUTBOUND token-exchange; inbound validation rejects a request with no
    Authorization header before any IdP contact, so its contents cannot influence
    the 401 assertions. It would NOT be sound for a test that needs a valid token.
    """
    if os.environ.get("TI_STUB_CREDENTIALS") != "1":
        return False
    secret = kubectl(
        "get", "pods", "-n", namespace, "-l", f"app={name}",
        "-o", r"jsonpath={.items[*].metadata.annotations.rossoctl\.io/keycloak-client-credentials-secret-name}",
        check=False,
    ).split()
    created = False
    for sn in {s for s in secret if s}:
        out = kubectl(
            "create", "secret", "generic", sn, "-n", namespace,
            "--from-literal=client-id.txt=" + name,
            "--from-literal=client-secret.txt=stub-not-used-for-inbound-denial",
            check=False,
        )
        if "created" in out or "already exists" in out:
            created = True
    return created


def _wait_ready(namespace: str, name: str, timeout: int):
    """Block until the deployment reports all replicas ready."""
    deadline = time.monotonic() + timeout
    last = ""
    stubbed = False
    while time.monotonic() < deadline:
        out = kubectl(
            "get", "deployment", name, "-n", namespace,
            "-o", "jsonpath={.status.readyReplicas}/{.status.replicas}",
            check=False,
        )
        last = out
        if out.strip() == "1/1":
            # Ready alone is not enough: the AgentRuntime controller labels the
            # pod template after the Deployment exists, so the FIRST ready pod
            # may predate injection. Require the sidecar to be present.
            pods = kubectl(
                "get", "pods", "-n", namespace, "-l", f"app={name}",
                "-o", "jsonpath={.items[*].spec.containers[*].name}",
                check=False,
            )
            if "authbridge-proxy" in pods:
                return
        if not stubbed:
            stubbed = _stub_credentials(namespace, name)
        time.sleep(3)
    pods = kubectl("get", "pods", "-n", namespace, "-o", "wide", check=False)
    events = kubectl(
        "get", "events", "-n", namespace, "--sort-by=.lastTimestamp", check=False
    )
    # A missing per-agent Keycloak credentials Secret blocks EVERY injected pod,
    # including default reverse-proxy ones, so it says nothing about this feature.
    # Skip with the cause named rather than fail and point suspicion at the
    # interception rules.
    if "rossoctl-keycloak-client-credentials" in events and "not found" in events:
        pytest.skip(
            "(set TI_STUB_CREDENTIALS=1 to stub the Secret and test the "
            "interception boundary anyway) "
            "platform gap, not a feature failure: the per-agent Keycloak "
            "client-credentials Secret was never created, so the injected pod "
            "cannot mount it. Verify the operator's Keycloak client registration "
            "works on this cluster (it needs the k8s.keycloak.org CRD)."
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
spec:
  replicas: 1
  selector:
    matchLabels:
      app: {name}
  template:
    metadata:
      labels:
        app: {name}
    spec:
      containers:
        - name: agent
          image: {AGENT_IMAGE}
          imagePullPolicy: IfNotPresent
          # Rewrite nginx's listen directive from PORT so this stand-in behaves
          # like a PORT-honoring agent framework. Without that the reverse-proxy
          # control could not be exercised: an agent that ignores PORT collides
          # with AuthBridge on the stolen port and the pod never starts (which is
          # itself one of the failure modes transparent interception removes).
          command: ["/bin/sh", "-c"]
          args:
            - |
              sed -i "s/listen  *8080/listen ${{PORT:-{AGENT_PORT}}}/" /etc/nginx/conf.d/default.conf
              exec nginx -g 'daemon off;'
          env:
            - name: PORT
              value: "{AGENT_PORT}"
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
---
# The AgentRuntime is what marks this workload as an agent. The platform's
# agent-label-protection ValidatingAdmissionPolicy rejects a hand-applied
# rossoctl.io/type label, so injection must be driven through the CR — which is
# also how agents are deployed for real.
apiVersion: agent.rossoctl.dev/v1alpha1
kind: AgentRuntime
metadata:
  name: {name}
  namespace: {namespace}
spec:
  type: agent
  mtlsMode: disabled
  tlsBridgeMode: disabled
  targetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: {name}
"""


def _read_namespace_config(namespace: str) -> str:
    return kubectl(
        "get", "configmap", "authbridge-runtime-config", "-n", namespace,
        "-o", r"jsonpath={.data.config\.yaml}", check=False,
    )


def _write_namespace_config(namespace: str, body: str):
    cm = kubectl(
        "create", "configmap", "authbridge-runtime-config", "-n", namespace,
        f"--from-literal=config.yaml={body}",
        "--dry-run=client", "-o", "yaml",
    )
    kubectl("apply", "-f", "-", stdin=cm)


def _set_mechanism(namespace: str, mechanism: str) -> str:
    """Prepend the inbound mechanism to the namespace config; return the original.

    Edits in place rather than replacing the ConfigMap: the existing body carries
    the namespace's real jwt-validation issuer and token-exchange settings, and a
    synthetic replacement would exercise a pipeline no real deployment runs.
    """
    original = _read_namespace_config(namespace)
    if not original.strip():
        pytest.skip(
            f"namespace {namespace} has no authbridge-runtime-config — not an "
            "onboarded rossoctl namespace (set TI_NS_TRANSPARENT / TI_NS_CONTROL)"
        )
    body = (
        f"inboundInterception: {mechanism}\n"
        "egressEnforcement: enforce-redirect\n" + original.lstrip("\n")
    )
    _write_namespace_config(namespace, body)
    return original


@pytest.fixture(scope="session")
def linux_nodes():
    """Skip the suite where iptables interception cannot work."""
    nodes = kubectl_json("get", "nodes")
    for node in nodes.get("items", []):
        if node["status"]["nodeInfo"]["operatingSystem"] != "linux":
            pytest.skip("transparent inbound is Linux/iptables only")
    return True


def _deploy(ns: str, name: str, mechanism: str) -> str:
    original = _set_mechanism(ns, mechanism)
    kubectl("apply", "-f", "-", stdin=_agent_manifest(ns, name))
    try:
        _wait_ready(ns, name, READY_TIMEOUT)
    except BaseException:
        _teardown(ns, name, original)
        raise
    return original


def _teardown(ns: str, name: str, original: str):
    if os.environ.get("TI_KEEP") == "1":
        return
    for kind in ("deployment", "service", "agentruntime"):
        kubectl("delete", kind, name, "-n", ns, "--wait=false", check=False)
    # Restoring the namespace config matters: these are SHARED namespaces, so
    # leaving one flipped to transparent would silently change every other agent
    # in it on its next pod recreation.
    if original.strip():
        _write_namespace_config(ns, original)


@pytest.fixture(scope="session")
def transparent_agent(linux_nodes):
    """Agent deployed with inboundInterception: transparent."""
    ns, name = NS_TRANSPARENT, "ti-e2e-agent"
    original = _deploy(ns, name, "transparent")
    yield {"namespace": ns, "name": name}
    _teardown(ns, name, original)


@pytest.fixture(scope="session")
def reverse_proxy_agent(linux_nodes):
    """Control: agent deployed with the default port-stealing mechanism."""
    ns, name = NS_CONTROL, "ti-e2e-control"
    original = _deploy(ns, name, "reverse-proxy")
    yield {"namespace": ns, "name": name}
    _teardown(ns, name, original)


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
    # busybox wget rather than curl: it ships in the Alpine agent image, which is
    # already cached on the nodes, so the probe needs no registry pull. It writes
    # the status line to stderr, hence the 2>&1.
    out = kubectl(
        "run", f"ti-probe-{int(time.time() * 1000) % 100000}",
        "-n", namespace, "--rm", "-i", "--restart=Never",
        f"--image={PROBE_IMAGE}",
        "--image-pull-policy=IfNotPresent", "--quiet",
        "--command", "--",
        "sh", "-c",
        f"wget -q -S -T 15 -O /dev/null '{url}' 2>&1 | head -1",
        check=False,
    )
    for token in out.split():
        if token.isdigit() and len(token) == 3 and token[0] in "12345":
            return int(token)
    raise AssertionError(
        f"probe produced no HTTP status for {url}; raw output: {out!r}"
    )
