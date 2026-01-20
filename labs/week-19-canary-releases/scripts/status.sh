#!/bin/bash
# Show current canary deployment status

set -e

CONTROLLER_URL="${CONTROLLER_URL:-http://localhost:8090}"

echo "========================================="
echo "      CANARY DEPLOYMENT STATUS           "
echo "========================================="
echo ""

response=$(curl -s "${CONTROLLER_URL}/status")

canary_weight=$(echo "$response" | jq -r '.canary_weight')
stable_weight=$(echo "$response" | jq -r '.stable_weight')
auto_rollback=$(echo "$response" | jq -r '.auto_rollback_enabled')
error_threshold=$(echo "$response" | jq -r '.error_rate_threshold')
canary_error_rate=$(echo "$response" | jq -r '.canary_error_rate')
stable_error_rate=$(echo "$response" | jq -r '.stable_error_rate')

echo "Traffic Distribution:"
echo "  Stable (v1): ${stable_weight}%"
echo "  Canary (v2): ${canary_weight}%"
echo ""
echo "Error Rates:"
echo "  Stable (v1): $(echo "$stable_error_rate * 100" | bc)%"
echo "  Canary (v2): $(echo "$canary_error_rate * 100" | bc)%"
echo ""
echo "Auto-Rollback:"
echo "  Enabled:   ${auto_rollback}"
echo "  Threshold: $(echo "$error_threshold * 100" | bc)%"
echo ""
echo "========================================="
echo ""
echo "Actions:"
echo "  Promote:  ./scripts/promote-canary.sh"
echo "  Rollback: ./scripts/rollback-canary.sh"
echo "  Inject:   ./scripts/inject-errors.sh 0.3"
echo ""
echo "Dashboards:"
echo "  Grafana:  http://localhost:3001"
echo "  Jaeger:   http://localhost:16686"
