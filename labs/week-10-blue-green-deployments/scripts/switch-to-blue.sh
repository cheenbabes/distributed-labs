#!/bin/bash
# Switch traffic to BLUE environment
# This script updates nginx configuration and reloads without dropping connections

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(dirname "$SCRIPT_DIR")"
NGINX_CONF_DIR="$LAB_DIR/infra/nginx/conf.d"

echo "=== Switching traffic to BLUE ==="

# Step 1: Check blue health
echo "1. Checking blue environment health..."
BLUE_HEALTH=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8081/health 2>/dev/null || echo "000")

if [ "$BLUE_HEALTH" != "200" ]; then
    echo "   WARNING: Blue health check returned $BLUE_HEALTH (expected 200)"
    read -p "   Continue anyway? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "   Aborting switch."
        exit 1
    fi
else
    echo "   Blue is healthy (HTTP 200)"
fi

# Step 2: Update active configuration
echo "2. Updating nginx configuration..."
cp "$NGINX_CONF_DIR/blue.conf" "$NGINX_CONF_DIR/active.conf"

# Step 3: Reload nginx
echo "3. Reloading nginx..."
docker exec lab10-nginx nginx -s reload

# Step 4: Verify the switch
echo "4. Verifying traffic routing..."
sleep 1

RESPONSE=$(curl -s http://localhost:8080/version 2>/dev/null || echo '{"error": "failed"}')
COLOR=$(echo "$RESPONSE" | grep -o '"color":"[^"]*"' | cut -d'"' -f4)

if [ "$COLOR" == "blue" ]; then
    echo "   SUCCESS: Traffic is now routing to BLUE"
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
