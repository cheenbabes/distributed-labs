# Lab 33: Reliable Webhook Delivery

Build a webhook delivery system that handles failures gracefully. You'll implement exponential backoff retries, circuit breakers per endpoint, and observe what happens when receivers go down, become flaky, or return errors.

## What You'll Learn

- How to implement reliable webhook delivery with retries
- Exponential backoff strategies (1s, 2s, 4s, 8s, 16s)
- Circuit breaker pattern to stop hammering failing endpoints
- HMAC signature verification for webhook security
- Observing retry storms and circuit breaker transitions

## Architecture

```
┌──────────────────┐     ┌──────────────────────────────────────────┐
│   Webhook        │     │          Receiver Endpoints              │
│   Sender         │────▶│  ┌─────────────┐ ┌─────────────────────┐ │
│                  │     │  │  Healthy    │ │  Flaky (30% fail)   │ │
│  - Retry Queue   │     │  │  (always    │ │  (random failures,  │ │
│  - Circuit       │     │  │   succeeds) │ │   timeouts)         │ │
│    Breakers      │     │  └─────────────┘ └─────────────────────┘ │
│  - Backoff       │     │  ┌─────────────────────────────────────┐ │
│                  │     │  │  Dead (always 503)                  │ │
└──────────────────┘     │  └─────────────────────────────────────┘ │
         │               └──────────────────────────────────────────┘
         │
         ▼
┌──────────────────┐
│   Observability  │
│   - Jaeger       │
│   - Prometheus   │
│   - Grafana      │
└──────────────────┘
```

## Key Concepts

### Exponential Backoff
When a webhook delivery fails, we wait progressively longer before retrying:
- 1st retry: 1 second
- 2nd retry: 2 seconds
- 3rd retry: 4 seconds
- 4th retry: 8 seconds
- 5th retry: 16 seconds (max)

### Circuit Breaker
After 5 consecutive failures to an endpoint:
- Circuit opens (stops attempting delivery)
- Waits 30 seconds before trying again (half-open state)
- 2 successes closes the circuit
- Any failure in half-open reopens the circuit

### Webhook Signatures
Each webhook includes an HMAC-SHA256 signature for verification:
```
X-Webhook-Signature: sha256=<signature>
```

## Prerequisites

- Docker and Docker Compose
- curl (for manual testing)
- k6 (optional, for load testing)

## Quick Start

### 1. Start the Lab

```bash
docker compose up --build -d
```

### 2. Verify Services Are Running

```bash
docker compose ps
```

### 3. Access the UIs

| Service | URL | Purpose |
|---------|-----|---------|
| Webhook Sender | http://localhost:8000 | Send webhooks |
| Receiver Healthy | http://localhost:8001 | Always succeeds |
| Receiver Flaky | http://localhost:8002 | 30% failures |
| Receiver Dead | http://localhost:8003 | Always 503 |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Send to a Healthy Endpoint

Let's start with a webhook that always succeeds.

```bash
# Send webhook to healthy receiver
curl -X POST http://localhost:8000/webhooks \
  -H "Content-Type: application/json" \
  -d '{
    "endpoint": "http://lab33-receiver-healthy:8001/webhook",
    "event_type": "order.created",
    "payload": {"order_id": "12345", "amount": 99.99}
  }' | jq

# Check webhook status
WEBHOOK_ID=$(curl -s http://localhost:8000/webhooks | jq -r '.webhooks[0].id')
curl -s "http://localhost:8000/webhooks/$WEBHOOK_ID" | jq
```

**Expected:** Status changes from `pending` → `in_progress` → `delivered` almost immediately.

---

### Exercise 2: Observe Retries with Flaky Endpoint

Now let's send to an endpoint that fails 30% of the time.

```bash
# Send multiple webhooks to flaky receiver
for i in {1..5}; do
  curl -s -X POST http://localhost:8000/webhooks \
    -H "Content-Type: application/json" \
    -d "{
      \"endpoint\": \"http://lab33-receiver-flaky:8002/webhook\",
      \"event_type\": \"payment.processed\",
      \"payload\": {\"payment_id\": \"PAY-$i\"}
    }" | jq -r '"Sent: " + .id'
done

# Wait for retries and check results
sleep 10
curl -s http://localhost:8000/webhooks | jq
```

**Open Jaeger** (http://localhost:16686):
1. Select "webhook-sender" service
2. Find traces with multiple "webhook_delivery" spans
3. Observe the retry delays between attempts

---

### Exercise 3: Trigger Circuit Breaker

Send webhooks to the dead endpoint and watch the circuit breaker open.

```bash
# Send webhooks to dead receiver (will trigger circuit breaker)
for i in {1..10}; do
  curl -s -X POST http://localhost:8000/webhooks \
    -H "Content-Type: application/json" \
    -d "{
      \"endpoint\": \"http://lab33-receiver-dead:8003/webhook\",
      \"event_type\": \"user.signup\",
      \"payload\": {\"user_id\": \"USER-$i\"}
    }" | jq -r '"Sent: " + .id'
  sleep 0.5
done

# Check circuit breaker status
curl -s http://localhost:8000/circuit-breakers | jq
```

**Expected:** After 5 failures, the circuit opens. New webhooks get status `circuit_open` instead of being attempted.

---

### Exercise 4: Observe Circuit Recovery

Wait for the circuit to transition to half-open and test recovery.

```bash
# Wait for circuit reset timeout (30 seconds)
echo "Waiting for circuit reset..."
sleep 35

# Check circuit breaker - should be half-open
curl -s http://localhost:8000/circuit-breakers | jq

# Send a test webhook (circuit will try one request)
curl -X POST http://localhost:8000/webhooks \
  -H "Content-Type: application/json" \
  -d '{
    "endpoint": "http://lab33-receiver-dead:8003/webhook",
    "event_type": "test.recovery",
    "payload": {"test": true}
  }' | jq
```

**Expected:** In half-open state, one request is allowed. If it fails, circuit opens again.

---

### Exercise 5: Manual Circuit Reset

Manually reset a circuit breaker to restore service.

```bash
# Check current state
curl -s http://localhost:8000/circuit-breakers | jq

# Reset circuit for dead endpoint (URL encode the endpoint)
curl -X POST "http://localhost:8000/circuit-breakers/http%3A%2F%2Flab33-receiver-dead%3A8003%2Fwebhook/reset" | jq

# Verify reset
curl -s http://localhost:8000/circuit-breakers | jq
```

---

### Exercise 6: View Delivery Audit Log

Examine the complete delivery history.

```bash
# Get overall stats
curl -s http://localhost:8000/stats | jq

# View delivery log
curl -s http://localhost:8000/delivery-log | jq

# View specific webhook with all attempts
WEBHOOK_ID=$(curl -s http://localhost:8000/webhooks | jq -r '.webhooks[0].id')
curl -s "http://localhost:8000/webhooks/$WEBHOOK_ID" | jq
```

---

### Exercise 7: Load Test with Mixed Endpoints

Run a load test to see behavior at scale.

```bash
# Run load test
docker compose run --rm k6 run /scripts/webhook-load.js

# Check metrics
curl -s http://localhost:8000/stats | jq
```

**In Grafana** (http://localhost:3001):
- Observe retry rates per endpoint
- Watch circuit breaker state changes
- Compare latency distributions

---

## Key Takeaways

1. **Exponential Backoff Prevents Overload** - Progressive delays prevent hammering failing services while still retrying

2. **Circuit Breakers Provide Fast Failure** - Stop wasting resources on known-broken endpoints

3. **Delivery Audit Trail is Essential** - Track every attempt for debugging and compliance

4. **Signature Verification is Critical** - Ensure webhooks are authentic and unmodified

5. **Different Endpoints Need Isolation** - Circuit breakers are per-endpoint so one bad receiver doesn't affect others

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Webhooks stuck in pending
Check if the sender has network access to receivers:
```bash
docker compose exec webhook-sender ping -c 1 receiver-healthy
```

### Circuit breaker won't reset
The reset timeout is 30 seconds. Check the remaining time:
```bash
curl -s http://localhost:8000/circuit-breakers | jq
```

### Traces not appearing in Jaeger
Wait 10-15 seconds, then check the OTEL collector:
```bash
docker compose logs otel-collector
```
