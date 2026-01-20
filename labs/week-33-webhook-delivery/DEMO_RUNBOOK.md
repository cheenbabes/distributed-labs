# Webhook Delivery Demo Runbook

Step-by-step demo for reliable webhook delivery with retries and circuit breakers.

## Setup

### Start Infrastructure

```bash {"name": "start-lab"}
cd /Users/ebaibourine/github/distributed-lab/labs/week-33-webhook-delivery && docker compose up --build -d
```

### Verify Services

```bash {"name": "check-services"}
docker compose ps
```

### Wait for Services to be Ready

```bash {"name": "wait-ready"}
sleep 10 && curl -s http://localhost:8000/health | jq
```

## Demo Flow

### Demo 1: Successful Webhook Delivery

Send a webhook to the healthy endpoint:

```bash {"name": "send-healthy-webhook"}
curl -X POST http://localhost:8000/webhooks \
  -H "Content-Type: application/json" \
  -d '{
    "endpoint": "http://lab33-receiver-healthy:8001/webhook",
    "event_type": "order.created",
    "payload": {"order_id": "12345", "amount": 99.99}
  }' | jq
```

Check the webhook status:

```bash {"name": "check-webhook-status"}
sleep 2 && curl -s http://localhost:8000/webhooks | jq '.webhooks[0]'
```

### Demo 2: Observe Retries with Flaky Endpoint

Send to flaky endpoint (30% failure rate):

```bash {"name": "send-flaky-webhooks"}
for i in {1..5}; do
  curl -s -X POST http://localhost:8000/webhooks \
    -H "Content-Type: application/json" \
    -d "{
      \"endpoint\": \"http://lab33-receiver-flaky:8002/webhook\",
      \"event_type\": \"payment.processed\",
      \"payload\": {\"payment_id\": \"PAY-$i\"}
    }" | jq -r '"Webhook created: " + .id'
done
```

Wait and check results:

```bash {"name": "check-flaky-results"}
sleep 15 && curl -s http://localhost:8000/webhooks?limit=5 | jq
```

View a webhook with retry attempts:

```bash {"name": "view-retry-attempts"}
WEBHOOK_ID=$(curl -s http://localhost:8000/webhooks | jq -r '.webhooks | map(select(.endpoint | contains("flaky"))) | .[0].id')
curl -s "http://localhost:8000/webhooks/$WEBHOOK_ID" | jq '.attempts'
```

### Demo 3: Trigger Circuit Breaker

Send to dead endpoint to trigger circuit breaker:

```bash {"name": "trigger-circuit-breaker"}
for i in {1..8}; do
  echo "Sending webhook $i..."
  curl -s -X POST http://localhost:8000/webhooks \
    -H "Content-Type: application/json" \
    -d "{
      \"endpoint\": \"http://lab33-receiver-dead:8003/webhook\",
      \"event_type\": \"user.signup\",
      \"payload\": {\"user_id\": \"USER-$i\"}
    }" | jq -r '"Status: " + .status'
  sleep 1
done
```

Check circuit breaker state:

```bash {"name": "check-circuit-state"}
curl -s http://localhost:8000/circuit-breakers | jq
```

### Demo 4: Circuit Breaker in Action

Try to send another webhook (should be rejected by circuit):

```bash {"name": "test-circuit-open"}
curl -X POST http://localhost:8000/webhooks \
  -H "Content-Type: application/json" \
  -d '{
    "endpoint": "http://lab33-receiver-dead:8003/webhook",
    "event_type": "test.blocked",
    "payload": {"test": true}
  }' | jq
```

Wait and check for half-open transition:

```bash {"name": "check-half-open"}
echo "Waiting 35 seconds for circuit reset timeout..."
sleep 35
curl -s http://localhost:8000/circuit-breakers | jq
```

### Demo 5: Manual Circuit Reset

Reset the circuit breaker manually:

```bash {"name": "reset-circuit"}
curl -X POST "http://localhost:8000/circuit-breakers/http%3A%2F%2Flab33-receiver-dead%3A8003%2Fwebhook/reset" | jq
```

Verify reset:

```bash {"name": "verify-reset"}
curl -s http://localhost:8000/circuit-breakers | jq
```

### Demo 6: View Delivery Statistics

Get overall statistics:

```bash {"name": "get-stats"}
curl -s http://localhost:8000/stats | jq
```

View delivery log:

```bash {"name": "view-delivery-log"}
curl -s http://localhost:8000/delivery-log?limit=10 | jq
```

### Demo 7: Run Load Test

```bash {"name": "run-loadtest"}
docker compose run --rm k6 run /scripts/webhook-load.js
```

Check stats after load test:

```bash {"name": "post-loadtest-stats"}
curl -s http://localhost:8000/stats | jq
```

## Observability

### Open Jaeger

```bash {"name": "open-jaeger"}
echo "Open http://localhost:16686 in your browser"
echo "Select 'webhook-sender' service to see delivery traces"
```

### Open Grafana

```bash {"name": "open-grafana"}
echo "Open http://localhost:3001 in your browser"
echo "Login: admin/admin"
```

### Check Prometheus Metrics

```bash {"name": "check-metrics"}
curl -s http://localhost:8000/metrics | grep -E "(webhook|circuit)" | head -30
```

## Cleanup

```bash {"name": "cleanup"}
cd /Users/ebaibourine/github/distributed-lab/labs/week-33-webhook-delivery && docker compose down -v
```
