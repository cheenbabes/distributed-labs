# Demo Runbook: Bulkhead Isolation

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

### Verify all services are responding

```bash {"name": "verify-services"}
echo "API Gateway:"
curl -s http://localhost:8000/health | jq
echo ""
echo "Backend Fast:"
curl -s http://localhost:8001/health | jq
echo ""
echo "Backend Slow:"
curl -s http://localhost:8002/health | jq
echo ""
echo "Backend Flaky:"
curl -s http://localhost:8003/health | jq
echo ""
echo "Client:"
curl -s http://localhost:8080/health | jq
```

---

## Part 2: Understand the Baseline

### Check bulkhead status

```bash {"name": "bulkhead-status"}
curl -s http://localhost:8000/status | jq
```

### Test fast backend

```bash {"name": "test-fast"}
echo "Testing fast backend (should be ~20-30ms):"
time curl -s http://localhost:8000/api/fast | jq
```

### Test slow backend

```bash {"name": "test-slow"}
echo "Testing slow backend (should be ~3s):"
time curl -s http://localhost:8000/api/slow | jq
```

### Test flaky backend

```bash {"name": "test-flaky"}
echo "Testing flaky backend (may succeed or fail):"
for i in {1..5}; do
  echo "Request $i:"
  curl -s http://localhost:8000/api/flaky | jq -c
done
```

---

## Part 3: Open Observability UIs

### Show all URLs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  API Gateway:  http://localhost:8000"
echo "  Jaeger:       http://localhost:16686"
echo "  Grafana:      http://localhost:3001 (admin/admin)"
echo "  Prometheus:   http://localhost:9090"
echo ""
echo "  Client API:   http://localhost:8080"
```

---

## Part 4: Demonstrate Bulkhead Protection

### Send concurrent requests to all backends

```bash {"name": "concurrent-test"}
echo "Sending 30 concurrent requests to each backend..."
curl -s -X POST http://localhost:8080/test/compare \
  -H "Content-Type: application/json" \
  -d '{"count": 30, "timeout": 10}' | jq
```

### Show bulkhead active requests

```bash {"name": "active-requests"}
echo "Current bulkhead status:"
curl -s http://localhost:8000/status | jq '.bulkheads'
```

---

## Part 5: Disable Bulkheads (Show the Problem)

### Disable bulkhead protection

```bash {"name": "disable-bulkheads"}
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "disabled"}' | jq
```

### Verify bulkheads are disabled

```bash {"name": "verify-disabled"}
curl -s http://localhost:8000/status | jq
```

### Test without bulkheads

```bash {"name": "test-without-bulkheads"}
echo "Testing WITHOUT bulkhead protection..."
curl -s -X POST http://localhost:8080/test/compare \
  -H "Content-Type: application/json" \
  -d '{"count": 50, "timeout": 10}' | jq
```

### Re-enable bulkheads

```bash {"name": "enable-bulkheads"}
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "enabled"}' | jq
```

---

## Part 6: Bulkhead Rejection Demo

### Reduce bulkhead limits

```bash {"name": "reduce-limits"}
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"semaphore_limit": 3}' | jq
```

### Burst test with limited bulkheads

```bash {"name": "burst-limited"}
echo "Sending 20 requests with only 3 slots available..."
curl -s -X POST http://localhost:8080/test/burst \
  -H "Content-Type: application/json" \
  -d '{"backend": "slow", "count": 20, "timeout": 10}' | jq
```

### Restore bulkhead limits

```bash {"name": "restore-limits"}
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"semaphore_limit": 10}' | jq
```

---

## Part 7: Cascade Effect Demo

### Make slow backend extremely slow

```bash {"name": "extreme-slow"}
curl -s -X POST http://localhost:8002/admin/slow-mode \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "latency_ms": 10000}' | jq
```

### Test fast backend with bulkheads enabled

```bash {"name": "fast-with-bulkheads"}
echo "Testing fast backend WITH bulkheads (should still be fast):"
for i in {1..5}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/api/fast)
  echo "Request $i: ${time}s"
done
```

### Disable bulkheads and create contention

```bash {"name": "cascade-demo"}
echo "Disabling bulkheads and creating contention..."

# Disable bulkheads
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "disabled"}' > /dev/null

# Start slow requests in background
for i in {1..10}; do
  curl -s http://localhost:8000/api/slow > /dev/null &
done

sleep 1

echo "Testing fast backend WITHOUT bulkheads:"
for i in {1..5}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/api/fast)
  echo "Request $i: ${time}s"
done

wait
```

### Restore all settings

```bash {"name": "restore-all"}
# Re-enable bulkheads
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "enabled"}' > /dev/null

# Reset slow backend
curl -s -X POST http://localhost:8002/admin/slow-mode \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "latency_ms": 3000}' > /dev/null

echo "Settings restored"
curl -s http://localhost:8000/status | jq
```

---

## Part 8: Load Testing

### Run k6 load test

```bash {"name": "load-test"}
docker compose run --rm k6 run /scripts/bulkhead-test.js
```

### Quick inline load test

```bash {"name": "quick-load"}
echo "Running 30-second load test..."
end=$((SECONDS+30))
count=0
while [ $SECONDS -lt $end ]; do
  curl -s http://localhost:8000/api/fast > /dev/null &
  curl -s http://localhost:8000/api/slow > /dev/null &
  curl -s http://localhost:8000/api/flaky > /dev/null &
  count=$((count+3))
  sleep 0.1
done
wait
echo "Completed $count requests in 30 seconds"
```

---

## Part 9: Observe Metrics

### Check Prometheus metrics

```bash {"name": "prom-metrics"}
echo "Bulkhead active requests:"
curl -s 'http://localhost:9090/api/v1/query?query=bulkhead_active_requests' | jq '.data.result'
echo ""
echo "Bulkhead rejected total:"
curl -s 'http://localhost:9090/api/v1/query?query=bulkhead_rejected_total' | jq '.data.result'
```

### Generate sample traffic for dashboard

```bash {"name": "generate-traffic"}
echo "Generating traffic for Grafana dashboard..."
for i in {1..50}; do
  curl -s http://localhost:8000/api/fast > /dev/null &
  curl -s http://localhost:8000/api/slow > /dev/null &
  curl -s http://localhost:8000/api/flaky > /dev/null &
  sleep 0.2
done
wait
echo "Traffic generation complete. Check Grafana: http://localhost:3001"
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

### Check API gateway logs

```bash {"name": "logs-gateway"}
docker compose logs lab47-api-gateway --tail=50
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart API gateway

```bash {"name": "restart-gateway"}
docker compose restart api-gateway
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Enable bulkheads | `curl -X POST localhost:8000/admin/config -d '{"mode":"enabled"}'` |
| Disable bulkheads | `curl -X POST localhost:8000/admin/config -d '{"mode":"disabled"}'` |
| Set bulkhead limit | `curl -X POST localhost:8000/admin/config -d '{"semaphore_limit":5}'` |
| Check status | `curl localhost:8000/status` |
| Make slow backend slower | `curl -X POST localhost:8002/admin/slow-mode -d '{"latency_ms":5000}'` |
| Set flaky error rate | `curl -X POST localhost:8003/admin/failure -d '{"error_rate":0.5}'` |
| View traces | http://localhost:16686 |
| View dashboard | http://localhost:3001 |
