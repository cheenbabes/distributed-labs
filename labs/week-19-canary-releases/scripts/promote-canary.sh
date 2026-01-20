#!/bin/bash
# Promote canary to next traffic weight stage
# Stages: 0% -> 1% -> 5% -> 10% -> 25% -> 50% -> 75% -> 100%

set -e

CONTROLLER_URL="${CONTROLLER_URL:-http://localhost:8090}"

echo "Promoting canary to next stage..."
echo ""

response=$(curl -s -X POST "${CONTROLLER_URL}/promote")

previous=$(echo "$response" | jq -r '.previous_weight')
current=$(echo "$response" | jq -r '.canary_weight')
stable=$(echo "$response" | jq -r '.stable_weight')
fully_promoted=$(echo "$response" | jq -r '.fully_promoted')

echo "========================================="
echo "       CANARY PROMOTION COMPLETE         "
echo "========================================="
echo ""
echo "Previous canary weight: ${previous}%"
echo "New canary weight:      ${current}%"
echo "Stable weight:          ${stable}%"
echo ""

if [ "$fully_promoted" = "true" ]; then
    echo "FULLY PROMOTED: Canary is now receiving 100% of traffic!"
    echo "The canary (v2) is now the new stable version."
else
    echo "Next stages available: 1% -> 5% -> 10% -> 25% -> 50% -> 75% -> 100%"
    echo ""
    echo "To continue promotion, run this script again."
    echo "To rollback, run: ./scripts/rollback-canary.sh"
fi

echo ""
echo "Monitor the deployment at:"
echo "  Grafana:  http://localhost:3001"
echo "  Status:   curl ${CONTROLLER_URL}/status"
