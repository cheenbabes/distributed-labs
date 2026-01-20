#!/bin/bash
# Simulate a bug in the specified environment
# Usage: ./simulate-bug.sh [blue|green] [enable|disable]

ENVIRONMENT=${1:-green}
ACTION=${2:-enable}

if [ "$ENVIRONMENT" == "blue" ]; then
    PORT=8081
elif [ "$ENVIRONMENT" == "green" ]; then
    PORT=8082
else
    echo "Usage: $0 [blue|green] [enable|disable]"
    exit 1
fi

if [ "$ACTION" == "enable" ]; then
    ENABLED="true"
    ERROR_RATE="0.5"
    echo "=== Enabling bug simulation in $ENVIRONMENT ==="
elif [ "$ACTION" == "disable" ]; then
    ENABLED="false"
    ERROR_RATE="0"
    echo "=== Disabling bug simulation in $ENVIRONMENT ==="
else
    echo "Usage: $0 [blue|green] [enable|disable]"
    exit 1
fi

RESPONSE=$(curl -s -X POST "http://localhost:$PORT/admin/bug?enabled=$ENABLED&error_rate=$ERROR_RATE")
echo "Response: $RESPONSE"

echo ""
echo "Bug simulation $ACTION for $ENVIRONMENT environment"
echo "50% of requests will now fail with HTTP 500"
