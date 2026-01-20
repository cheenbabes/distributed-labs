#!/bin/bash
# Switch traffic to GREEN environment
# This script updates nginx configuration and reloads without dropping connections

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(dirname "$SCRIPT_DIR")"
NGINX_CONF_DIR="$LAB_DIR/infra/nginx/conf.d"

echo "=== Switching traffic to GREEN ==="

# Step 1: Check green health
echo "1. Checking green environment health..."
GREEN_HEALTH=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8082/health 2>/dev/null || echo "000")

if [ "$GREEN_HEALTH" != "200" ]; then
    echo "   WARNING: Green health check returned $GREEN_HEALTH (expected 200)"
    read -p "   Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "   Aborting switch."
        exit 1
    fi
else
    echo "   Green is healthy (HTTP 200)"
fi

# Step 2: Update active configuration
echo "2. Updating nginx configuration..."
cp "$NGINX_CONF_DIR/green.conf" "$NGINX_CONF_DIR/active.conf"

# Step 3: Reload nginx
echo "3. Reloading nginx..."
docker exec lab10-nginx nginx -s reload

# Step 4: Verify the switch
echo "4. Verifying traffic routing..."
sleep 1

RESPONSE=$(curl -s http://localhost:8080/version 2>/dev/null || echo '{"error": "failed"}')
COLOR=$(echo "$RESPONSE" | grep -o '"color":"[^"]*"' | cut -d'"' -f4)

if [ "$COLOR" == "green" ]; then
    echo "   SUCCESS: Traffic is now routing to GREEN"
    echo ""
    echo "   Response: $RESPONSE"
else
    echo "   WARNING: Unexpected response - color=$COLOR"
    echo "   Response: $RESPONSE"
fi

echo ""
echo "=== Switch complete ==="
echo "Monitor in Grafana: http://localhost:3001"
echo "View traces in Jaeger: http://localhost:16686"
