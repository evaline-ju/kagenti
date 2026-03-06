Kind cluster updates after usual deployment via `./deployments/ansible/run-install.sh --env dev`

On this branch to make the Envoy spans work I just added the new resources with `helm upgrade kagenti-deps ./charts/kagenti-deps -n kagenti-system --reuse-values`


MCP gateway tracing is not yet available in a chart, to update I:
- Run MCP gateway's `make build-image`
- Replace deployment image in `mcp-gateway-broker-router` deployment (mcp-system namespace)
- Make sure there are otel env vars on the deployment:
      - OTEL_EXPORTER_OTLP_ENDPOINT:  http://otel-collector.kagenti-system.svc.cluster.local:8335
      - OTEL_EXPORTER_OTLP_INSECURE:  true  # not sure if this one was necessary but was in the MCP gateway docs
      - OTEL_EXPORTER_OTLP_PROTOCOL:  http/protobuf
    For testing these can probably just be patched in `oc set env deployment/mcp-gateway-broker-router -n mcp-system ....`

For reproducibility I put this section after the `- name: Install/upgrade mcp-gateway chart` one in this file: https://github.com/kagenti/kagenti/blob/main/deployments/ansible/roles/kagenti_installer/tasks/main.yml
```
- name: Patch mcp-gateway-broker-router with OTEL env vars
  kubernetes.core.k8s:
    api_version: apps/v1
    kind: Deployment
    name: mcp-gateway-broker-router
    namespace: "{{ charts.mcpGateway.namespace | default('mcp-system') }}"
    state: patched
    definition:
      spec:
        template:
          spec:
            containers:
              - name: mcp-broker-router
                env:
                  - name: NAMESPACE
                    valueFrom:
                      fieldRef:
                        fieldPath: metadata.namespace
                  - name: OTEL_EXPORTER_OTLP_ENDPOINT
                    value: "http://otel-collector.kagenti-system.svc.cluster.local:8335"
                  - name: OTEL_EXPORTER_OTLP_INSECURE
                    value: "true"
                  - name: OTEL_EXPORTER_OTLP_PROTOCOL
                    value: "http/protobuf"
  when: charts.mcpGateway.enabled | default(false)
```