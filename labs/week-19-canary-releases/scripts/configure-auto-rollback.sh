#!/bin/bash
# Configure automatic rollback settings

set -e

CONTROLLER_URL="${CONTROLLER_URL:-http://localhost:8090}"
ENABLED="${1:-true}"
THRESHOLD="${2:-0.05}"

echo "Configuring automatic rollback..."
echo ""

response=$(curl -s -X POST "${CONTROLLER_URL}/auto-rollback" \
    -H "Content-Type: application/json" \
    -d "{\"enabled\": ${ENABLED}, \"error_rate_threshold\": ${THRESHOLD}}")

enabled=$(echo "$response" | jq -r '.auto_rollback_enabled')
threshold=$(echo "$response" | jq -r '.error_rate_threshold')

echo "========================================="
echo "     AUTO-ROLLBACK CONFIGURATION         "
echo "========================================="
echo ""
echo "Enabled:   ${enabled}"
echo "Threshold: $(echo "$threshold * 100" | bc)%"
echo ""
if [ "$enabled" = "true" ]; then
    echo "Auto-rollback ENABLED."
    echo "If canary error rate exceeds $(echo "$threshold * 100" | bc)%, traffic will"
    echo "automatically be shifted back to stable (v1)."
else
    echo "Auto-rollback DISABLED."
    echo "Manual intervention required if canary fails."
fi
echo ""
echo "Usage:"
echo "  $0 true 0.05   # Enable with 5% threshold"
echo "  $0 true 0.10   # Enable with 10% threshold"
echo "  $0 false       # Disable auto-rollback"
