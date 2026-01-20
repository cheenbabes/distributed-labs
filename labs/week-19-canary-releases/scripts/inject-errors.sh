#!/bin/bash
# Inject errors into canary (v2) to simulate a bad deployment

set -e

APP_V2_URL="${APP_V2_URL:-http://localhost:8082}"
ERROR_RATE="${1:-0.3}"
EXTRA_LATENCY="${2:-0}"

echo "Injecting errors into canary (v2)..."
echo ""
echo "Error rate:    ${ERROR_RATE} ($(echo "$ERROR_RATE * 100" | bc)%)"
echo "Extra latency: ${EXTRA_LATENCY}ms"
echo ""

response=$(curl -s -X POST "${APP_V2_URL}/admin/config" \
    -H "Content-Type: application/json" \
    -d "{\"error_rate\": ${ERROR_RATE}, \"extra_latency_ms\": ${EXTRA_LATENCY}}")

echo "Response:"
echo "$response" | jq

echo ""
echo "========================================="
echo "       ERROR INJECTION ACTIVE            "
echo "========================================="
echo ""
echo "The canary version will now fail ${ERROR_RATE}% of requests."
echo ""
echo "If auto-rollback is enabled, the controller will detect"
echo "the elevated error rate and automatically roll back."
echo ""
echo "To disable error injection:"
echo "  $0 0 0"
echo ""
echo "To manually rollback:"
echo "  ./scripts/rollback-canary.sh"
