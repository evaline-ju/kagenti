#!/bin/bash
# Deploy Hermes on an existing Kagenti Kind cluster with unified hook experiment.
# Prerequisites:
#   - Kind cluster 'kagenti' with platform namespace (Redis + state-query-api running)
#   - hermes-agent:local image loaded into Kind
#   - LiteLLM credentials available
#
# Usage: ./run-kind-unified.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Deploy Hermes with unified state-query-api hook ==="

echo "1. Creating hermes namespace..."
kubectl create namespace hermes --dry-run=client -o yaml | kubectl apply -f -

echo "2. Creating secret..."
kubectl create secret generic hermes-secrets -n hermes \
  --from-literal=LITELLM_API_KEY="$(kubectl get secret -n claude-code claude-code-secret -o jsonpath='{.data.litellm-api-key}' | base64 -d)" \
  --from-literal=LITELLM_BASE_URL="$(kubectl get secret -n claude-code claude-code-secret -o jsonpath='{.data.litellm-base-url}' | base64 -d)" \
  --from-literal=API_SERVER_KEY=hermes-experiment \

  --dry-run=client -o yaml | kubectl apply -f -

echo "3. Creating hook script ConfigMap..."
kubectl create configmap hermes-hook-scripts -n hermes \
  --from-file=session-to-state-api.py="$SCRIPT_DIR/hooks/session-to-state-api.py" \
  --from-file=recovery-inject.py="$SCRIPT_DIR/hooks/recovery-inject.py" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "4. Applying PVC..."
kubectl apply -f "$SCRIPT_DIR/hermes-pvc.yaml"

echo "5. Applying Deployment..."
kubectl apply -f "$SCRIPT_DIR/hermes-deployment-kind.yaml"

echo "6. Applying Service..."
kubectl apply -f "$SCRIPT_DIR/hermes-service.yaml"

echo "7. Waiting for pod ready..."
kubectl rollout status deploy/hermes-agent -n hermes --timeout=180s

echo ""
echo "=== Verification ==="

echo "Pod status:"
kubectl get pods -n hermes -l app.kubernetes.io/name=hermes-agent

echo ""
echo "=== Ready. Test with: ==="
echo "  kubectl exec -n hermes deploy/hermes-agent -- curl -s -X POST http://localhost:8642/v1/chat/completions \\"
echo "    -H 'Authorization: Bearer \$API_SERVER_KEY' \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d '{\"model\":\"hermes-agent\",\"messages\":[{\"role\":\"user\",\"content\":\"say papaya\"}]}'"
echo ""
echo "Check unified state:"
echo "  kubectl exec -n platform deploy/redis -- redis-cli ZRANGE sessions:all 0 -1"
echo "  kubectl exec -n platform deploy/state-query-api -- curl -s localhost:8000/sessions"
