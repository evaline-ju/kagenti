# Transparent inbound E2E

Gates the inbound half of the AuthBridge interception story
(rossoctl/cortex#330): with `inboundInterception: transparent`, JWT validation
must not be sidestepped by another pod dialing the agent's real port.

The assertion that matters is `test_direct_pod_dial_is_validated`. Everything
else exists to prove the shape is actually the one under test, and that turning
the feature on did not break the things it runs alongside (health probes, the
session-events API, egress enforcement).

`test_reverse_proxy_control_bypass_is_reachable` deliberately documents the
*old* behavior: under the default `reverse-proxy` mechanism the relocated agent
port answers without validation. If that test ever fails because the bypass is
gone, the default changed — which is a decision, not a bug, and the test should
be retired with it.

## Requirements

- A cluster with the operator + authbridge images built from this branch.
- `proxy.allowedInboundInterception` must permit `transparent` (the default).
- Linux nodes: the feature is iptables-based. Skipped elsewhere.
- **A namespace the platform can onboard.** The injected sidecar mounts a
  per-agent Keycloak client-credentials Secret, created by the operator's
  client-registration path. If that path is broken — e.g. the Keycloak CRD
  (`k8s.keycloak.org/v2alpha1`) is absent, as on a cluster installed with the
  community Keycloak provider — every injected pod stays `Pending` on
  `FailedMount` and the suite errors in fixture setup regardless of this
  feature. Check `kubectl describe pod` for a missing
  `rossoctl-keycloak-client-credentials-*` Secret before debugging the
  interception rules.

Three platform constraints the fixtures satisfy, each of which otherwise
produces a silently meaningless run:

- The namespace needs `rossoctl-enabled=true`, or the injection webhook's
  `namespaceSelector` skips it and the pod comes up with no sidecar at all.
- `rossoctl.io/type` cannot be applied by hand — the `agent-label-protection`
  ValidatingAdmissionPolicy rejects it. Injection is driven through an
  AgentRuntime CR, which is how agents are deployed for real.
- Readiness alone is not a usable gate: the AgentRuntime controller labels the
  pod template *after* the Deployment exists, so the first ready pod predates
  injection. The fixtures wait for the `authbridge-proxy` container.

```sh
uv run pytest rossoctl/tests/e2e/transparent_inbound/ -v
```
