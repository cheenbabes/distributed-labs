# Demo Runbook: Rate Limiting Fundamentals

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
sleep 10
docker compose ps
```

### Verify rate limiter is responding

```bash {"name": "verify-service"}
echo "Health check:"
curl -s http://localhost:8000/health | jq

echo ""
echo "Current algorithm:"
curl -s http://localhost:8000/admin/algorithm | jq

echo ""
echo "Current stats:"
curl -s http://localhost:8000/admin/stats | jq
```

---

## Part 2: Open Observability UIs

### Show URLs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Rate Limiter API: http://localhost:8000"
echo "  Jaeger:           http://localhost:16686"
echo "  Grafana:          http://localhost:3001 (admin/admin)"
echo "  Prometheus:       http://localhost:9090"
```

---

## Part 3: Test Fixed Window Algorithm

### Set Fixed Window algorithm

```bash {"name": "set-fixed-window"}
curl -s -X POST http://localhost:8000/admin/algorithm \
  -H "Content-Type: application/json" \
  -d '{"algorithm": "fixed_window"}' | jq
```

### Reset the rate limiter

```bash {"name": "reset-limiter"}
curl -s -X POST http://localhost:8000/admin/reset | jq
```

### Send 15 rapid requests

```bash {"name": "fixed-window-test"}
echo "=== Fixed Window Test: 15 requests at 10/s limit ==="
for i in {1..15}; do
  response=$(curl -s -w "\n%{http_code}" http://localhost:8000/api/request)
  code=$(echo "$response" | tail -1)
  body=$(echo "$response" | head -1)
  remaining=$(echo "$body" | jq -r '.remaining // "N/A"')
  if [ "$code" = "200" ]; then
    echo "Request $i: ALLOWED (remaining: $remaining)"
  else
    echo "Request $i: REJECTED (429)"
  fi
done
```

### Check fixed window stats

```bash {"name": "fixed-window-stats"}
curl -s http://localhost:8000/admin/stats | jq '.stats'
```

---

## Part 4: Demonstrate Boundary Burst Problem

### Fixed window boundary burst

```bash {"name": "boundary-burst"}
echo "=== Demonstrating Fixed Window Boundary Burst ==="

# Reset
curl -s -X POST http://localhost:8000/admin/reset > /dev/null

# Send 10 requests (exhaust quota)
echo "Phase 1: Sending 10 requests..."
for i in {1..10}; do
  curl -s http://localhost:8000/api/request > /dev/null
done
echo "Done. Window is now at 10/10."

# Wait until just before window resets
echo "Waiting 0.9 seconds (window is 1s)..."
sleep 0.9

# Send 10 more at boundary
echo ""
echo "Phase 2: Sending 10 more at boundary..."
allowed=0
for i in {1..10}; do
  code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/request)
  if [ "$code" = "200" ]; then
    allowed=$((allowed + 1))
    echo "Request $i: ALLOWED"
  else
    echo "Request $i: REJECTED"
  fi
done

echo ""
echo "Result: $allowed of 10 requests allowed at boundary"
echo "This demonstrates the boundary burst problem!"
```

---

## Part 5: Test Sliding Window (Accurate)

### Set Sliding Window algorithm

```bash {"name": "set-sliding-window"}
curl -s -X POST http://localhost:8000/admin/algorithm \
  -H "Content-Type: application/json" \
  -d '{"algorithm": "sliding_window"}' | jq
```

### Sliding window boundary test

```bash {"name": "sliding-window-boundary"}
echo "=== Sliding Window Boundary Test ==="

# Reset
curl -s -X POST http://localhost:8000/admin/reset > /dev/null

# Send 10 requests
echo "Phase 1: Sending 10 requests..."
for i in {1..10}; do
  curl -s http://localhost:8000/api/request > /dev/null
done
echo "Done."

# Wait
echo "Waiting 0.9 seconds..."
sleep 0.9

# Send 10 more
echo ""
echo "Phase 2: Sending 10 more..."
allowed=0
for i in {1..10}; do
  code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/request)
  if [ "$code" = "200" ]; then
    allowed=$((allowed + 1))
    echo "Request $i: ALLOWED"
  else
    echo "Request $i: REJECTED"
  fi
done

echo ""
echo "Result: $allowed of 10 requests allowed"
echo "Sliding window prevents boundary bursts!"
```

### Check sliding window stats

```bash {"name": "sliding-window-stats"}
curl -s http://localhost:8000/admin/stats | jq '.stats'
```

---

## Part 6: Test Token Bucket (Allows Bursts)

### Set Token Bucket algorithm

```bash {"name": "set-token-bucket"}
curl -s -X POST http://localhost:8000/admin/algorithm \
  -H "Content-Type: application/json" \
  -d '{"algorithm": "token_bucket"}' | jq
```

### Token bucket burst test

```bash {"name": "token-bucket-test"}
echo "=== Token Bucket Burst Test ==="

# Reset (bucket fills to 10)
curl -s -X POST http://localhost:8000/admin/reset > /dev/null

echo "Initial tokens:"
curl -s http://localhost:8000/admin/stats | jq '.stats.current_tokens'

echo ""
echo "Sending burst of 10 requests..."
for i in {1..10}; do
  response=$(curl -s http://localhost:8000/api/request)
  tokens=$(echo "$response" | jq -r '.remaining')
  echo "Request $i: tokens remaining = $tokens"
done

echo ""
echo "Tokens after burst:"
curl -s http://localhost:8000/admin/stats | jq '.stats.current_tokens'

echo ""
echo "Waiting 0.5 seconds for partial refill..."
sleep 0.5

echo "Tokens after 0.5s:"
curl -s http://localhost:8000/admin/stats | jq '.stats.current_tokens'
```

---

## Part 7: Test Leaky Bucket (Smooth Rate)

### Set Leaky Bucket algorithm

```bash {"name": "set-leaky-bucket"}
curl -s -X POST http://localhost:8000/admin/algorithm \
  -H "Content-Type: application/json" \
  -d '{"algorithm": "leaky_bucket"}' | jq
```

### Leaky bucket test

```bash {"name": "leaky-bucket-test"}
echo "=== Leaky Bucket Test ==="

# Reset
curl -s -X POST http://localhost:8000/admin/reset > /dev/null

echo "Initial queue size:"
curl -s http://localhost:8000/admin/stats | jq '.stats.current_queue_size'

echo ""
echo "Sending 15 requests..."
for i in {1..15}; do
  response=$(curl -s http://localhost:8000/api/request)
  status=$(echo "$response" | jq -r '.status')
  remaining=$(echo "$response" | jq -r '.remaining')
  echo "Request $i: $status, remaining capacity: $remaining"
done

echo ""
echo "Final stats:"
curl -s http://localhost:8000/admin/stats | jq '.stats'
```

---

## Part 8: Load Testing

### Run basic load test

```bash {"name": "load-test-basic"}
docker compose run --rm k6 run /scripts/basic.js
```

### Run burst test

```bash {"name": "load-test-burst"}
docker compose run --rm k6 run /scripts/burst.js
```

### Run algorithm comparison

```bash {"name": "load-test-compare"}
docker compose run --rm k6 run /scripts/compare-algorithms.js
```

### Run boundary test

```bash {"name": "load-test-boundary"}
docker compose run --rm k6 run /scripts/boundary-test.js
```

---

## Part 9: View Metrics

### Check Prometheus metrics

```bash {"name": "check-metrics"}
echo "=== Rate Limit Metrics ==="
curl -s http://localhost:8000/metrics | grep rate_limit
```

### Generate traffic for dashboard

```bash {"name": "generate-traffic"}
echo "Generating traffic for 30 seconds..."
echo "Watch the Grafana dashboard at http://localhost:3001"
echo ""

end=$((SECONDS + 30))
count=0
while [ $SECONDS -lt $end ]; do
  curl -s http://localhost:8000/api/request > /dev/null &
  count=$((count + 1))
  sleep 0.05
done
wait

echo "Completed $count requests in 30 seconds"
```

---

## Part 10: Algorithm Comparison Demo

### Side-by-side comparison

```bash {"name": "side-by-side"}
echo "=== Algorithm Comparison (5s each) ==="
echo ""

for algo in fixed_window sliding_window token_bucket leaky_bucket; do
  # Set algorithm
  curl -s -X POST http://localhost:8000/admin/algorithm \
    -H "Content-Type: application/json" \
    -d "{\"algorithm\": \"$algo\"}" > /dev/null

  # Reset
  curl -s -X POST http://localhost:8000/admin/reset > /dev/null

  echo "Testing $algo..."
  allowed=0
  rejected=0

  end=$((SECONDS + 5))
  while [ $SECONDS -lt $end ]; do
    code=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/request)
    if [ "$code" = "200" ]; then
      allowed=$((allowed + 1))
    else
      rejected=$((rejected + 1))
    fi
    sleep 0.05
  done

  total=$((allowed + rejected))
  pct=$((allowed * 100 / total))
  printf "  %-20s: %d allowed, %d rejected (%d%% allowed)\n" "$algo" "$allowed" "$rejected" "$pct"
done

echo ""
echo "=== Comparison Complete ==="
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

### Check service logs

```bash {"name": "logs-all"}
docker compose logs --tail=50
```

### Check rate limiter logs

```bash {"name": "logs-rate-limiter"}
docker compose logs rate-limiter --tail=50
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart rate limiter

```bash {"name": "restart-rate-limiter"}
docker compose restart rate-limiter
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Set algorithm | `curl -X POST localhost:8000/admin/algorithm -d '{"algorithm":"..."}'` |
| Reset limiter | `curl -X POST localhost:8000/admin/reset` |
| Check stats | `curl localhost:8000/admin/stats` |
| Single request | `curl localhost:8000/api/request` |
| View traces | http://localhost:16686 |
| View dashboard | http://localhost:3001 |
