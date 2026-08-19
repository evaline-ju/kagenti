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

## Namespaces

Runs in **pre-onboarded** namespaces (`team1`/`team2` by default, override with
`TI_NS_TRANSPARENT` / `TI_NS_CONTROL`) rather than creating its own. A fresh
namespace lacks the platform's per-namespace plumbing — `authbridge-config`, and
the Keycloak client registration that produces each agent's credentials Secret —
so an injected pod there never starts, for reasons unrelated to this feature.

Each fixture flips the namespace's `inboundInterception` in place and **restores
the original body on teardown**. These are shared namespaces: leaving one flipped
would silently change every other agent in it on its next pod recreation.

## Where it runs

Included in the shared e2e target list in
`.github/scripts/operator/90-run-e2e-tests.sh`, so it runs wherever that runner
does (Kind and HyperShift e2e, release validation) rather than only by hand.

It **self-gates** so that is safe on clusters running older images: if the injected
sidecar declares no `transparent-in` port, the deployed operator ignored the
namespace's `inboundInterception` and the suite skips with that cause named. It
becomes a real gate as soon as the cluster's operator and authbridge carry the
feature.

## Requirements

- A cluster with the operator + authbridge images built from this branch.
- `proxy.allowedInboundInterception` must permit `transparent` (the default).
- Linux nodes: the feature is iptables-based. Skipped elsewhere.
- **Working Keycloak client registration.** The injected sidecar mounts a
  per-agent credentials Secret created by the operator. Where that path is broken
  — e.g. the `k8s.keycloak.org/v2alpha1` CRD is absent, as on a cluster installed
  with the community Keycloak provider — every injected pod stays `Pending` on
  `FailedMount`, including default reverse-proxy ones. The suite **skips with that
  cause named** rather than failing and pointing suspicion at the interception
  rules.

  To test the boundary anyway on such a cluster, set `TI_STUB_CREDENTIALS=1` to
  create a placeholder Secret. This is opt-in on purpose: stubbing by default
  would let the suite go green on a cluster whose Keycloak registration is
  broken. The stub is sound for these assertions — the Secret is for *outbound*
  token-exchange, and inbound validation rejects a request with no Authorization
  header before any IdP contact — but it would **not** be sound for any test
  needing a valid token.

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
