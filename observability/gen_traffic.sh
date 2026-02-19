TRACE_ID=$(openssl rand -hex 16)
echo "Trace ID: $TRACE_ID"

curl -s -D /tmp/mcp_headers -X POST http://mcp.127-0-0-1.sslip.io:8080/mcp \
  -H "Content-Type: application/json" \
  -H "traceparent: 00-${TRACE_ID}-$(openssl rand -hex 8)-01" \
  -d '{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test-client", "version": "1.0.0"}}}' | jq .

SESSION_ID=$(grep -i "mcp-session-id:" /tmp/mcp_headers | cut -d' ' -f2 | tr -d '\r')

curl -s -X POST http://mcp.127-0-0-1.sslip.io:8080/mcp \
  -H "Content-Type: application/json" \
  -H "mcp-session-id: $SESSION_ID" \
  -H "traceparent: 00-${TRACE_ID}-$(openssl rand -hex 8)-01" \
  -d '{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}' | jq .


curl -s -X POST http://mcp.127-0-0-1.sslip.io:8080/mcp \
  -H "Content-Type: application/json" \
  -H "mcp-session-id: $SESSION_ID" \
  -H "traceparent: 00-${TRACE_ID}-$(openssl rand -hex 8)-01" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "weather_get_weather", "arguments": {"city": "New York"}}}' | jq .

echo "Search for trace: $TRACE_ID"