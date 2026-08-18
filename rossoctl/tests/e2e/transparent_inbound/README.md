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

```sh
uv run pytest rossoctl/tests/e2e/transparent_inbound/ -v
```
