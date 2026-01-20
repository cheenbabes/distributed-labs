#!/bin/bash
# Set specific canary weight (0-100)

set -e

CONTROLLER_URL="${CONTROLLER_URL:-http://localhost:8090}"
WEIGHT="${1:-0}"

if [ -z "$1" ]; then
    echo "Usage: $0 <weight>"
    echo "  weight: 0-100 (percentage of traffic to canary)"
    echo ""
    echo "Examples:"
    echo "  $0 1     # 1% to canary, 99% to stable"
    echo "  $0 10    # 10% to canary, 90% to stable"
    echo "  $0 50    # 50% to canary, 50% to stable"
    echo "  $0 100   # 100% to canary (full promotion)"
    echo "  $0 0     # Rollback (0% to canary)"
    exit 1
fi

echo "Setting canary weight to ${WEIGHT}%..."
echo ""

response=$(curl -s -X POST "${CONTROLLER_URL}/weight" \
    -H "Content-Type: application/json" \
    -d "{\"canary_weight\": ${WEIGHT}}")

current=$(echo "$response" | jq -r '.canary_weight')
stable=$(echo "$response" | jq -r '.stable_weight')

echo "========================================="
echo "       TRAFFIC WEIGHT UPDATED            "
echo "========================================="
echo ""
echo "Canary (v2) weight: ${current}%"
echo "Stable (v1) weight: ${stable}%"
echo ""
echo "Monitor the deployment at:"
echo "  Grafana:  http://localhost:3001"
echo "  Status:   curl ${CONTROLLER_URL}/status"
