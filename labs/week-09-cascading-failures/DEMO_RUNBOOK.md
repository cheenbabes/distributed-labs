# Demo Runbook: Cascading Failures

This runbook contains all commands for demonstrating cascading failures. If you have the [Runme extension](https://runme.dev) installed in VS Code, you can run each command block directly with the play button.

---

## Pre-Demo Setup

### Check Docker is running

```bash {"name": "check-docker"}
docker info > /dev/null 2>&1 && echo "Docker is running" || echo "Docker is not running"
```

### Clean any previous lab state

```bash {"name": "clean-previous"}
docker compose down -v 2>/dev/null || true
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

### Verify all services are responding

```bash {"name": "verify-services"}
echo "Frontend:"
curl -s http://localhost:8000/health | jq
echo ""
echo "API:"
curl -s http://localhost:8001/health | jq
echo ""
echo "Orders:"
curl -s http://localhost:8002/health | jq
echo ""
echo "Inventory:"
curl -s http://localhost:8003/health | jq
```

---

## Part 2: Establish Baseline

### Make a single request and show response

```bash {"name": "single-request"}
curl -s http://localhost:8000/api/order | jq
```

### Make 5 requests and show timing

```bash {"name": "baseline-timing"}
echo "Baseline timing (5 requests):"
for i in {1..5}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/api/order)
  echo "Request $i: ${time}s"
done
```

### Check concurrency status for all services

```bash {"name": "check-status"}
echo "=== SERVICE STATUS ==="
echo ""
echo "Frontend (max 50):"
curl -s http://localhost:8000/status | jq '{active: .active_requests, queued: .queued_requests, max: .max_concurrent}'
echo ""
echo "API (max 30):"
curl -s http://localhost:8001/status | jq '{active: .active_requests, queued: .queued_requests, max: .max_concurrent}'
echo ""
echo "Orders (max 20):"
curl -s http://localhost:8002/status | jq '{active: .active_requests, queued: .queued_requests, max: .max_concurrent}'
echo ""
echo "Inventory (max 10):"
curl -s http://localhost:8003/status | jq '{active: .active_requests, queued: .queued_requests, max: .max_concurrent}'
```

---

## Part 3: Open Observability UIs

### Show URLs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Jaeger:     http://localhost:16686"
echo "  Grafana:    http://localhost:3001 (admin/admin)"
echo "  Prometheus: http://localhost:9090"
echo "  Frontend:   http://localhost:8000"
echo ""
echo "In Grafana, open the 'Lab 09: Cascading Failures' dashboard"
```

---

## Part 4: Inject Slowdown (The Cascade Trigger)

### Add 2 second latency to inventory

```bash {"name": "inject-slow"}
curl -s -X POST http://localhost:8003/admin/slow \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "latency_ms": 2000}' | jq
```

### Verify slowdown is active

```bash {"name": "verify-slow"}
curl -s http://localhost:8003/admin/slow | jq
```

---

## Part 5: Trigger the Cascade

### Generate burst of concurrent requests

```bash {"name": "burst-requests"}
echo "Sending 30 concurrent requests..."
for i in {1..30}; do
  curl -s -o /dev/null -w "." http://localhost:8000/api/order &
done
wait
echo ""
echo "Done! Check Grafana to see the cascade."
```

### Watch status during load

```bash {"name": "watch-status"}
echo "Generating load and checking status every 2 seconds..."
for round in {1..5}; do
  # Generate some load
  for i in {1..10}; do
    curl -s http://localhost:8000/api/order > /dev/null &
  done

  echo ""
  echo "=== Round $round ==="
  echo "Frontend: $(curl -s http://localhost:8000/status | jq -r '[.active_requests, .queued_requests] | "active=\(.[0]) queued=\(.[1])"')"
  echo "API:      $(curl -s http://localhost:8001/status | jq -r '[.active_requests, .queued_requests] | "active=\(.[0]) queued=\(.[1])"')"
  echo "Orders:   $(curl -s http://localhost:8002/status | jq -r '[.active_requests, .queued_requests] | "active=\(.[0]) queued=\(.[1])"')"
  echo "Inventory:$(curl -s http://localhost:8003/status | jq -r '[.active_requests, .queued_requests] | "active=\(.[0]) queued=\(.[1])"')"

  sleep 2
done
wait
```

---

## Part 6: Measure Impact

### Time a single request with slowdown

```bash {"name": "slow-request-timing"}
echo "Timing single request with 2s inventory latency:"
time curl -s http://localhost:8000/api/order | jq
```

### Run k6 load test to see full cascade

```bash {"name": "load-test-cascade"}
echo "Running cascade load test (3 minutes)..."
echo "Watch Grafana dashboard for the full effect!"
docker compose run --rm k6 run /scripts/cascade-test.js
```

---

## Part 7: Reset and Kill Service

### Reset inventory to normal

```bash {"name": "reset-inventory"}
curl -s -X POST http://localhost:8003/admin/reset | jq
```

### Verify reset worked

```bash {"name": "verify-reset"}
echo "Timing after reset (should be fast):"
for i in {1..3}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/api/order)
  echo "Request $i: ${time}s"
done
```

### Stop inventory service completely

```bash {"name": "stop-inventory"}
docker compose stop inventory
echo "Inventory service stopped"
```

### Test with dead inventory

```bash {"name": "test-dead-inventory"}
echo "Testing with inventory stopped:"
for i in {1..5}; do
  start=$(date +%s.%N)
  status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 15 http://localhost:8000/api/order)
  end=$(date +%s.%N)
  duration=$(echo "$end - $start" | bc)
  echo "Request $i: Status=$status Time=${duration}s"
done
```

### Restart inventory service

```bash {"name": "start-inventory"}
docker compose start inventory
echo "Waiting for inventory to be healthy..."
sleep 5
curl -s http://localhost:8003/health | jq
```

---

## Part 8: Recovery Observation

### Test recovery

```bash {"name": "test-recovery"}
echo "Testing recovery (requests should succeed again):"
for i in {1..5}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/api/order)
  status=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/order)
  echo "Request $i: Status=$status Time=${time}s"
  sleep 0.5
done
```

---

## Part 9: Failure Injection

### Enable 100% failure rate on inventory

```bash {"name": "inject-failures"}
curl -s -X POST http://localhost:8003/admin/fail \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "rate": 1.0}' | jq
```

### Test with failures

```bash {"name": "test-failures"}
echo "Testing with 100% failure rate:"
for i in {1..5}; do
  status=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/order)
  echo "Request $i: Status=$status"
done
```

### Try partial failures (50%)

```bash {"name": "partial-failures"}
curl -s -X POST http://localhost:8003/admin/fail \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "rate": 0.5}' | jq

echo ""
echo "Testing with 50% failure rate:"
for i in {1..10}; do
  status=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/order)
  echo "Request $i: Status=$status"
done
```

### Reset all admin settings

```bash {"name": "reset-all"}
curl -s -X POST http://localhost:8003/admin/reset | jq
echo "All admin settings reset"
```

---

## Part 10: Full Load Test

### Run steady load test (5 minutes)

```bash {"name": "steady-load-test"}
echo "Running 5-minute steady load test..."
echo "Watch Grafana for metrics!"
docker compose run --rm k6 run /scripts/steady-load.js
```

### Run ramp load test

```bash {"name": "ramp-load-test"}
echo "Running ramp load test to find breaking point..."
docker compose run --rm k6 run /scripts/ramp-load.js
```

---

## Cleanup

### Stop all services

```bash {"name": "cleanup"}
docker compose down -v
echo "Lab cleaned up"
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Add 2s latency | `curl -X POST localhost:8003/admin/slow -H "Content-Type: application/json" -d '{"enabled":true,"latency_ms":2000}'` |
| Remove latency | `curl -X POST localhost:8003/admin/slow -H "Content-Type: application/json" -d '{"enabled":false}'` |
| Enable failures | `curl -X POST localhost:8003/admin/fail -H "Content-Type: application/json" -d '{"enabled":true,"rate":1.0}'` |
| Disable failures | `curl -X POST localhost:8003/admin/fail -H "Content-Type: application/json" -d '{"enabled":false}'` |
| Reset all | `curl -X POST localhost:8003/admin/reset` |
| Check status | `curl localhost:8000/status \| jq` |
| Stop inventory | `docker compose stop inventory` |
| Start inventory | `docker compose start inventory` |
| View dashboard | http://localhost:3001 (admin/admin) |
| View traces | http://localhost:16686 |
