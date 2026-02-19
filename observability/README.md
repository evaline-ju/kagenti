- To leverage MCP gateway traces, had to upgrade to 0.5 MCP gateway chart
	- Had to "re-register" tool with `MCPServerRegistration`
- Then built MCP gateway image with MCP gateway's `make build-image` and replaced deployment in `mcp-gateway-broker-router`
- Env vars (below) had to be updated to make this work more correctly (tried to persist these on kagenti charts)
- Tried to consolidate the otel-collector usage - updated this with tempo and grafana that got deployed in kagenti-system namespace instead of separate observability

- Used curl script to send calls through the MCP gateway for weather tool
- make `otel-forward-kagenti`


```sh
otel-kagenti:
    # initial separate namespace
	# kubectl apply -f examples/otel/namespace.yaml -f examples/otel/tempo.yaml -f examples/otel/loki.yaml -f examples/otel/otel-collector.yaml -f examples/otel/grafana.yaml
	# @kubectl wait --for=condition=Available deployment -n observability --all --timeout=120s
    kubectl set env deployment/mcp-gateway-broker-router -n mcp-system \
		OTEL_EXPORTER_OTLP_ENDPOINT="http://otel-collector.kagenti-system.svc.cluster.local:8335" OTEL_EXPORTER_OTLP_INSECURE="true"
	@kubectl rollout status deployment/mcp-gateway-broker-router -n mcp-system --timeout=120s

#     Environment:
#       NAMESPACE:                     (v1:metadata.namespace)
#       OTEL_EXPORTER_OTLP_ENDPOINT:  http://otel-collector.kagenti-system.svc.cluster.local:8335
#       OTEL_EXPORTER_OTLP_INSECURE:  true
#       OTEL_EXPORTER_OTLP_PROTOCOL:  http/protobuf
#       OTEL_LOG_LEVEL:               debug

.PHONY: otel-forward
otel-forward-kagenti: ## Port-forward Grafana (3000)
	@echo "Grafana: http://localhost:3000"
	@kubectl port-forward -n kagenti-system svc/grafana 3000:3000
```

