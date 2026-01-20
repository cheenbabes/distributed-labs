#!/bin/bash
# Check health of both blue and green environments

echo "=== Blue-Green Health Check ==="
echo ""

# Check Blue
echo "BLUE Environment (port 8081):"
BLUE_RESPONSE=$(curl -s http://localhost:8081/health 2>/dev/null || echo '{"error": "unreachable"}')
BLUE_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8081/health 2>/dev/null || echo "000")
echo "  Status: HTTP $BLUE_STATUS"
echo "  Response: $BLUE_RESPONSE"
echo ""

# Check Green
echo "GREEN Environment (port 8082):"
GREEN_RESPONSE=$(curl -s http://localhost:8082/health 2>/dev/null || echo '{"error": "unreachable"}')
GREEN_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8082/health 2>/dev/null || echo "000")
echo "  Status: HTTP $GREEN_STATUS"
echo "  Response: $GREEN_RESPONSE"
echo ""

# Check Router
echo "ROUTER (nginx on port 8080):"
ROUTER_RESPONSE=$(curl -s http://localhost:8080/version 2>/dev/null || echo '{"error": "unreachable"}')
ROUTER_STATUS=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/version 2>/dev/null || echo "000")
ACTIVE_COLOR=$(echo "$ROUTER_RESPONSE" | grep -o '"color":"[^"]*"' | cut -d'"' -f4)
echo "  Status: HTTP $ROUTER_STATUS"
echo "  Active Environment: $ACTIVE_COLOR"
echo "  Response: $ROUTER_RESPONSE"
echo ""

# Summary
echo "=== Summary ==="
if [ "$BLUE_STATUS" == "200" ]; then
    echo "  [OK] Blue is healthy"
else
    echo "  [!!] Blue is unhealthy (HTTP $BLUE_STATUS)"
fi

if [ "$GREEN_STATUS" == "200" ]; then
    echo "  [OK] Green is healthy"
else
    echo "  [!!] Green is unhealthy (HTTP $GREEN_STATUS)"
fi

echo "  [->] Traffic routing to: $ACTIVE_COLOR"
