# Demo Runbook: Retry Storms

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
echo "Client service:"
curl -s http://localhost:8000/health | jq
echo ""
echo "Backend service:"
curl -s http://localhost:8001/health | jq
```

### Show URLs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Grafana:    http://localhost:3001 (admin/admin)"
echo "  Jaeger:     http://localhost:16686"
echo "  Prometheus: http://localhost:9090"
echo "  Client:     http://localhost:8000"
echo "  Backend:    http://localhost:8001"
```

---

## Part 2: Baseline - Normal Operation

### Make a single successful request

```bash {"name": "single-request"}
echo "Making a single request through the client..."
curl -s http://localhost:8000/process | jq
```

### Check baseline timing

```bash {"name": "baseline-timing"}
echo "Baseline timing (5 requests):"
for i in {1..5}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/process)
  echo "Request $i: ${time}s"
done
```

### Verify no retries in baseline

```bash {"name": "baseline-metrics"}
echo "Retry metrics (should be zero or low):"
curl -s http://localhost:8000/metrics | grep -E "retry_attempts|request_total" | head -10
```

---

## Part 3: Naive Retries - The Problem

### Set naive retry strategy

```bash {"name": "set-naive"}
echo "Setting retry strategy to: naive (immediate, 3 attempts)"
curl -s -X POST http://localhost:8000/admin/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "naive"}' | jq
```

### Verify strategy is set

```bash {"name": "verify-strategy"}
curl -s http://localhost:8000/admin/strategy | jq
```

### Trigger backend failure (10 seconds)

```bash {"name": "trigger-failure-naive"}
echo "Triggering backend failure for 10 seconds..."
curl -s -X POST http://localhost:8001/admin/fail \
  -H "Content-Type: application/json" \
  -d '{"duration_seconds": 10}' | jq
```

### Immediately send 10 concurrent requests

```bash {"name": "send-naive-requests"}
echo "Sending 10 concurrent requests..."
for i in {1..10}; do
  curl -s -o /dev/null -w "Client $i: %{http_code}\n" http://localhost:8000/process &
done
wait
echo ""
echo "Done - check backend metrics for amplification!"
```

### Check backend request count (amplification!)

```bash {"name": "check-amplification"}
echo "Backend request count (should be ~30 for 10 client requests):"
curl -s http://localhost:8001/metrics | grep http_requests_total
```

### Check failure status

```bash {"name": "check-failure-status"}
curl -s http://localhost:8001/admin/status | jq
```

---

## Part 4: Exponential Backoff - Better Recovery

### Wait for system to stabilize

```bash {"name": "wait-stabilize"}
echo "Waiting for system to stabilize..."
sleep 5
```

### Set backoff retry strategy

```bash {"name": "set-backoff"}
echo "Setting retry strategy to: backoff (1s, 2s, 4s)"
curl -s -X POST http://localhost:8000/admin/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "backoff"}' | jq
```

### Trigger backend failure (10 seconds)

```bash {"name": "trigger-failure-backoff"}
echo "Triggering backend failure for 10 seconds..."
curl -s -X POST http://localhost:8001/admin/fail \
  -H "Content-Type: application/json" \
  -d '{"duration_seconds": 10}' | jq
```

### Send 10 concurrent requests with backoff

```bash {"name": "send-backoff-requests"}
echo "Sending 10 concurrent requests with backoff strategy..."
for i in {1..10}; do
  curl -s -o /dev/null -w "Client $i: %{http_code}\n" http://localhost:8000/process &
done
wait
echo ""
echo "Done - compare backend metrics to naive!"
```

### Check backend metrics (should be lower)

```bash {"name": "check-backoff-metrics"}
echo "Backend request count (should be lower than naive):"
curl -s http://localhost:8001/metrics | grep http_requests_total
```

---

## Part 5: Jitter - Prevent Synchronized Spikes

### Wait for system to stabilize

```bash {"name": "wait-stabilize-2"}
echo "Waiting for system to stabilize..."
sleep 5
```

### Set jitter retry strategy

```bash {"name": "set-jitter"}
echo "Setting retry strategy to: jitter (backoff + random delay)"
curl -s -X POST http://localhost:8000/admin/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "jitter"}' | jq
```

### Trigger backend failure (15 seconds)

```bash {"name": "trigger-failure-jitter"}
echo "Triggering backend failure for 15 seconds..."
curl -s -X POST http://localhost:8001/admin/fail \
  -H "Content-Type: application/json" \
  -d '{"duration_seconds": 15}' | jq
```

### Send 10 concurrent requests with jitter

```bash {"name": "send-jitter-requests"}
echo "Sending 10 concurrent requests with jitter strategy..."
for i in {1..10}; do
  curl -s -o /dev/null -w "Client $i: %{http_code}\n" http://localhost:8000/process &
done
wait
echo ""
echo "Done - check Grafana for smooth retry distribution!"
```

### Check final metrics

```bash {"name": "check-jitter-metrics"}
echo "Backend request count (should show smoothest pattern):"
curl -s http://localhost:8001/metrics | grep http_requests_total
```

---

## Part 6: Load Test Comparison

### Run k6 load test with naive retries

```bash {"name": "loadtest-naive"}
echo "Running load test with naive retries..."
curl -s -X POST http://localhost:8000/admin/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "naive"}' > /dev/null

docker compose run --rm k6 run /scripts/retry-storm.js
```

### Run k6 load test with jitter

```bash {"name": "loadtest-jitter"}
echo "Running load test with jitter retries..."
curl -s -X POST http://localhost:8000/admin/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "jitter"}' > /dev/null

docker compose run --rm k6 run /scripts/retry-storm.js
```

---

## Part 7: Side-by-Side Comparison

### Reset metrics and run comparison

```bash {"name": "comparison-test"}
echo "=== RETRY STRATEGY COMPARISON ==="
echo ""

# Test 1: Naive
echo "1. Testing NAIVE strategy..."
curl -s -X POST http://localhost:8000/admin/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "naive"}' > /dev/null

curl -s -X POST http://localhost:8001/admin/fail \
  -H "Content-Type: application/json" \
  -d '{"duration_seconds": 5}' > /dev/null

start=$(date +%s%N)
for i in {1..5}; do
  curl -s http://localhost:8000/process > /dev/null &
done
wait
naive_time=$(( ($(date +%s%N) - start) / 1000000 ))
echo "   Naive completed in ${naive_time}ms"

sleep 6

# Test 2: Backoff
echo "2. Testing BACKOFF strategy..."
curl -s -X POST http://localhost:8000/admin/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "backoff"}' > /dev/null

curl -s -X POST http://localhost:8001/admin/fail \
  -H "Content-Type: application/json" \
  -d '{"duration_seconds": 5}' > /dev/null

start=$(date +%s%N)
for i in {1..5}; do
  curl -s http://localhost:8000/process > /dev/null &
done
wait
backoff_time=$(( ($(date +%s%N) - start) / 1000000 ))
echo "   Backoff completed in ${backoff_time}ms"

sleep 6

# Test 3: Jitter
echo "3. Testing JITTER strategy..."
curl -s -X POST http://localhost:8000/admin/strategy \
  -H "Content-Type: application/json" \
  -d '{"strategy": "jitter"}' > /dev/null

curl -s -X POST http://localhost:8001/admin/fail \
  -H "Content-Type: application/json" \
  -d '{"duration_seconds": 5}' > /dev/null

start=$(date +%s%N)
for i in {1..5}; do
  curl -s http://localhost:8000/process > /dev/null &
done
wait
jitter_time=$(( ($(date +%s%N) - start) / 1000000 ))
echo "   Jitter completed in ${jitter_time}ms"

echo ""
echo "=== RESULTS ==="
echo "Naive:   ${naive_time}ms (fast but hammers backend)"
echo "Backoff: ${backoff_time}ms (slower, synchronized spikes)"
echo "Jitter:  ${jitter_time}ms (best distribution, smoothest recovery)"
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

### Check all service logs

```bash {"name": "logs-all"}
docker compose logs --tail=50
```

### Check client service logs

```bash {"name": "logs-client"}
docker compose logs client --tail=50
```

### Check backend service logs

```bash {"name": "logs-backend"}
docker compose logs backend --tail=50
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart services

```bash {"name": "restart-services"}
docker compose restart client backend
```

### Reset failure state

```bash {"name": "reset-failure"}
curl -s http://localhost:8001/admin/status | jq
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Set naive retries | `curl -X POST localhost:8000/admin/strategy -d '{"strategy":"naive"}'` |
| Set backoff retries | `curl -X POST localhost:8000/admin/strategy -d '{"strategy":"backoff"}'` |
| Set jitter retries | `curl -X POST localhost:8000/admin/strategy -d '{"strategy":"jitter"}'` |
| Trigger failure | `curl -X POST localhost:8001/admin/fail -d '{"duration_seconds":10}'` |
| Check failure status | `curl localhost:8001/admin/status` |
| View traces | http://localhost:16686 |
| View dashboards | http://localhost:3001 |
