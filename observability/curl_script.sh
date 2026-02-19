cat <<'SCRIPT' | oc run mcp-test --rm -i --restart=Never --image=ghcr.io/curl/curl-container/curl:master -- sh
TRACE_ID=$(head /dev/urandom | tr -dc a-f0-9 | head -c 32)
SPAN_ID=$(head /dev/urandom | tr -dc a-f0-9 | head -c 16)
TRACEPARENT="00-${TRACE_ID}-${SPAN_ID}-01"
echo "Trace ID: $TRACE_ID"

curl -s -D /tmp/mcp_headers -X POST http://mcp-gateway-istio.gateway-system.svc.cluster.local:8080/mcp \
  -H "Content-Type: application/json" \
  -H "traceparent: $TRACEPARENT" \
  -d '{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test-client", "version": "1.0.0"}}}'

SESSION_ID=$(grep -i "mcp-session-id:" /tmp/mcp_headers | cut -d' ' -f2 | tr -d '\r')

curl -s -X POST http://mcp-gateway-istio.gateway-system.svc.cluster.local:8080/mcp \
  -H "Content-Type: application/json" \
  -H "mcp-session-id: $SESSION_ID" \
  -H "traceparent: $TRACEPARENT" \
  -d '{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}'

curl -s -X POST http://mcp-gateway-istio.gateway-system.svc.cluster.local:8080/mcp \
  -H "Content-Type: application/json" \
  -H "mcp-session-id: $SESSION_ID" \
  -H "traceparent: $TRACEPARENT" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "weather_get_weather", "arguments": {"city": "New York"}}}'

echo "Search for trace: $TRACE_ID"
SCRIPT
