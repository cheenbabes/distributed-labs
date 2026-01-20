# Demo Runbook: Stripe Idempotency Keys

This runbook contains all commands for the video demo. If you have the [Runme extension](https://runme.dev) installed in VS Code, you can run each command block directly with the play button.

---

## Pre-Demo Setup

### Check Docker is running

```bash {"name": "check-docker"}
docker info > /dev/null 2>&1 && echo "Docker is running" || echo "Docker is not running"
```

### Clean any previous lab state

```bash {"name": "clean-previous"}
docker compose down -v 2>/dev/null || true
docker system prune -f
```

---

## Part 1: Start the Lab

### Build and start all services

```bash {"name": "start-lab"}
docker compose up --build -d
```

### Wait for services to be healthy

```bash {"name": "wait-healthy"}
echo "Waiting for services to be healthy..."
sleep 15
docker compose ps
```

### Verify services are responding

```bash {"name": "verify-services"}
echo "Payment API:"
curl -s http://localhost:8000/health | jq

echo ""
echo "Database Stats:"
curl -s http://localhost:8000/admin/stats | jq
```

---

## Part 2: Open Observability UIs

### Show URLs for manual access

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Payment API Docs: http://localhost:8000/docs"
echo "  Jaeger:           http://localhost:16686"
echo "  Grafana:          http://localhost:3001 (admin/admin)"
echo "  Prometheus:       http://localhost:9090"
```

---

## Part 3: The Problem - Payment Without Idempotency Key

### Show the danger: duplicate payments

```bash {"name": "dangerous-no-key"}
echo "=== DANGER: Making same payment WITHOUT idempotency key ==="
echo ""

echo "First payment:"
PAYMENT1=$(curl -s -X POST http://localhost:8000/payments \
  -H "Content-Type: application/json" \
  -d '{"amount": 5000, "currency": "usd", "description": "Dangerous payment"}')
echo $PAYMENT1 | jq

echo ""
echo "Second payment (same request, no idempotency key):"
PAYMENT2=$(curl -s -X POST http://localhost:8000/payments \
  -H "Content-Type: application/json" \
  -d '{"amount": 5000, "currency": "usd", "description": "Dangerous payment"}')
echo $PAYMENT2 | jq

echo ""
echo "=== RESULT: TWO DIFFERENT PAYMENT IDs! Customer charged twice! ==="
echo "Payment 1 ID: $(echo $PAYMENT1 | jq -r '.id')"
echo "Payment 2 ID: $(echo $PAYMENT2 | jq -r '.id')"
```

---

## Part 4: The Solution - Using Idempotency Keys

### Make a payment with idempotency key

```bash {"name": "payment-with-key"}
# Generate a unique idempotency key
export IDEM_KEY="demo-payment-$(date +%s)"
echo "Using idempotency key: $IDEM_KEY"
echo ""

echo "Making payment with idempotency key..."
curl -s -X POST http://localhost:8000/payments \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $IDEM_KEY" \
  -d '{"amount": 2500, "currency": "usd", "description": "Safe payment with idempotency"}' | jq
```

### Inspect the idempotency key record

```bash {"name": "inspect-key"}
echo "Inspecting idempotency key record..."
curl -s http://localhost:8000/idempotency-keys/$IDEM_KEY | jq
```

### Retry the same payment (should be cached)

```bash {"name": "retry-payment"}
echo "=== RETRY: Same key, same request body ==="
echo ""

echo "Retry payment (should return cached response):"
curl -s -X POST http://localhost:8000/payments \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $IDEM_KEY" \
  -d '{"amount": 2500, "currency": "usd", "description": "Safe payment with idempotency"}' | jq

echo ""
echo "Notice: 'cached: true' - Same payment ID, no duplicate charge!"
```

---

## Part 5: Conflict Detection

### Try to reuse key with different request body

```bash {"name": "conflict-demo"}
echo "=== CONFLICT: Same key, DIFFERENT request body ==="
echo ""

echo "Attempting to reuse key '$IDEM_KEY' with different amount..."
curl -s -X POST http://localhost:8000/payments \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $IDEM_KEY" \
  -d '{"amount": 9999, "currency": "usd", "description": "Trying to reuse key!"}' | jq

echo ""
echo "Result: 409 Conflict - Key cannot be reused with different request!"
```

---

## Part 6: Concurrent Request Handling

### Race two concurrent requests

```bash {"name": "concurrent-race"}
export RACE_KEY="race-$(date +%s)"
echo "=== CONCURRENT REQUESTS with key: $RACE_KEY ==="
echo ""

# Send two concurrent requests
curl -s -X POST http://localhost:8000/payments \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $RACE_KEY" \
  -d '{"amount": 7500, "currency": "usd"}' > /tmp/race1.json &
PID1=$!

curl -s -X POST http://localhost:8000/payments \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $RACE_KEY" \
  -d '{"amount": 7500, "currency": "usd"}' > /tmp/race2.json &
PID2=$!

wait $PID1 $PID2

echo "Response 1:"
cat /tmp/race1.json | jq '{id, cached}'

echo ""
echo "Response 2:"
cat /tmp/race2.json | jq '{id, cached}'

echo ""
echo "Both responses have the SAME payment ID - only one charge!"
```

---

## Part 7: Compare Timing

### Show timing difference between new and cached requests

```bash {"name": "timing-comparison"}
export TIMING_KEY="timing-$(date +%s)"
echo "=== TIMING COMPARISON ==="
echo ""

echo "New payment (with processing time):"
time curl -s -X POST http://localhost:8000/payments \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $TIMING_KEY" \
  -d '{"amount": 1000, "currency": "usd"}' > /dev/null

echo ""
echo "Cached response (instant):"
time curl -s -X POST http://localhost:8000/payments \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $TIMING_KEY" \
  -d '{"amount": 1000, "currency": "usd"}' > /dev/null

echo ""
echo "Cached responses are much faster!"
```

---

## Part 8: View Metrics

### Check idempotency metrics

```bash {"name": "view-metrics"}
echo "=== IDEMPOTENCY METRICS ==="
echo ""

echo "Cache hits (duplicate requests blocked):"
curl -s http://localhost:8000/metrics | grep idempotency_cache_hits

echo ""
echo "Conflicts (same key, different body):"
curl -s http://localhost:8000/metrics | grep idempotency_conflicts

echo ""
echo "Concurrent waits:"
curl -s http://localhost:8000/metrics | grep concurrent_request_waits

echo ""
echo "Payment totals:"
curl -s http://localhost:8000/metrics | grep payments_total
```

### View admin statistics

```bash {"name": "admin-stats"}
echo "=== ADMIN STATISTICS ==="
curl -s http://localhost:8000/admin/stats | jq
```

---

## Part 9: Load Testing

### Run k6 load test

```bash {"name": "load-test"}
echo "Running comprehensive k6 load test..."
docker compose run --rm lab13-k6 run /scripts/retry-test.js
```

### Quick stress test

```bash {"name": "quick-stress"}
echo "Quick stress test - 20 requests with same key..."

export STRESS_KEY="stress-$(date +%s)"

for i in {1..20}; do
  curl -s -X POST http://localhost:8000/payments \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $STRESS_KEY" \
    -d '{"amount": 500, "currency": "usd"}' &
done

wait
echo ""
echo "Done! Check metrics for cache hits..."
curl -s http://localhost:8000/metrics | grep idempotency_cache_hits
```

---

## Part 10: Explore Traces

### Generate some traced requests

```bash {"name": "generate-traces"}
echo "Generating traced requests..."

for i in {1..5}; do
  KEY="trace-$i-$(date +%s)"
  curl -s -X POST http://localhost:8000/payments \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $KEY" \
    -d "{\"amount\": $((i * 1000)), \"currency\": \"usd\"}" > /dev/null

  # Retry to see cache hit
  curl -s -X POST http://localhost:8000/payments \
    -H "Content-Type: application/json" \
    -H "Idempotency-Key: $KEY" \
    -d "{\"amount\": $((i * 1000)), \"currency\": \"usd\"}" > /dev/null
done

echo "Done! View traces at: http://localhost:16686"
echo "Look for 'idempotency.cache_hit' attribute in spans"
```

---

## Cleanup

### Stop all services

```bash {"name": "cleanup"}
docker compose down -v
echo "Lab cleaned up"
```

---

## Troubleshooting Commands

### Check all logs

```bash {"name": "logs-all"}
docker compose logs --tail=50
```

### Check payment API logs

```bash {"name": "logs-payment-api"}
docker compose logs lab13-payment-api --tail=100
```

### Check database

```bash {"name": "check-database"}
docker compose exec lab13-postgres psql -U payments -d payments -c "SELECT * FROM idempotency_keys LIMIT 5;"
```

### Check Redis

```bash {"name": "check-redis"}
docker compose exec lab13-redis redis-cli KEYS "idempotency_lock:*"
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart payment API

```bash {"name": "restart-api"}
docker compose restart lab13-payment-api
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Make payment | `curl -X POST localhost:8000/payments -H "Idempotency-Key: xxx" -H "Content-Type: application/json" -d '{"amount":100}'` |
| Check key | `curl localhost:8000/idempotency-keys/xxx` |
| View stats | `curl localhost:8000/admin/stats` |
| View traces | http://localhost:16686 |
| View metrics | http://localhost:9090 |
| View dashboard | http://localhost:3001 (admin/admin) |
