#!/bin/bash
# Immediately rollback canary to 0% traffic

set -e

CONTROLLER_URL="${CONTROLLER_URL:-http://localhost:8090}"

echo "Rolling back canary deployment..."
echo ""

response=$(curl -s -X POST "${CONTROLLER_URL}/rollback")

previous=$(echo "$response" | jq -r '.previous_weight')
current=$(echo "$response" | jq -r '.canary_weight')
stable=$(echo "$response" | jq -r '.stable_weight')

echo "========================================="
echo "       CANARY ROLLBACK COMPLETE          "
echo "========================================="
echo ""
echo "Previous canary weight: ${previous}%"
echo "Current canary weight:  ${current}%"
echo "Stable weight:          ${stable}%"
echo ""
echo "All traffic is now being served by stable (v1)."
echo ""
echo "To restart canary deployment:"
echo "  ./scripts/promote-canary.sh"
