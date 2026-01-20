# Demo Runbook: Backpressure

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
echo "Producer status:"
curl -s http://localhost:8000/status | jq
echo ""
echo "Consumer status:"
curl -s http://localhost:8001/status | jq
```

---

## Part 2: Open Observability UIs

### Show URLs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Grafana:    http://localhost:3001 (admin/admin)"
echo "  Jaeger:     http://localhost:16686"
echo "  Prometheus: http://localhost:9090"
echo "  Producer:   http://localhost:8000/status"
echo "  Consumer:   http://localhost:8001/status"
```

---

## Part 3: Baseline - Balanced System

### Show queue is stable when balanced

```bash {"name": "baseline-queue"}
echo "Watching queue depth (producer=10/s, consumer=10/s)..."
echo ""
for i in {1..10}; do
  size=$(curl -s http://localhost:8000/status | jq -r '.queue.size')
  echo "Check $i: Queue size = $size"
  sleep 2
done
echo ""
echo "Queue stays small when producer and consumer are balanced."
```

---

## Part 4: Create Backpressure

### Slow down the consumer (5x slower)

```bash {"name": "slow-consumer"}
echo "Slowing down consumer from 100ms to 500ms per item..."
curl -s -X POST http://localhost:8001/admin/slow-down \
  -H "Content-Type: application/json" \
  -d '{"factor": 5}' | jq
```

### Watch queue grow

```bash {"name": "watch-queue-grow"}
echo "Watching queue grow (producer=10/s, consumer=2/s)..."
echo ""
for i in {1..15}; do
  result=$(curl -s http://localhost:8000/status)
  size=$(echo "$result" | jq -r '.queue.size')
  mem=$(echo "$result" | jq -r '.queue.memory_mb')
  echo "Check $i: Queue size = $size, Memory = ${mem}MB"
  sleep 2
done
echo ""
echo "Queue is growing unbounded - this will eventually crash!"
```

### Check memory pressure

```bash {"name": "check-memory"}
echo "Producer memory status:"
curl -s http://localhost:8000/status | jq '.process'
echo ""
echo "Docker container stats:"
docker stats lab18-producer --no-stream
```

---

## Part 5: Solution 1 - Bounded Queue

### Clear queue and switch to bounded mode

```bash {"name": "enable-bounded-queue"}
echo "Clearing queue..."
curl -s -X POST http://localhost:8000/admin/clear-queue | jq

echo ""
echo "Switching to bounded queue (max 100 items)..."
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"queue_mode": "bounded", "max_queue_size": 100}' | jq
```

### Watch bounded queue behavior

```bash {"name": "watch-bounded-queue"}
echo "Watching bounded queue..."
echo ""
for i in {1..15}; do
  result=$(curl -s http://localhost:8000/status)
  size=$(echo "$result" | jq -r '.queue.size')
  mode=$(echo "$result" | jq -r '.queue.mode')
  max=$(echo "$result" | jq -r '.queue.max_size')
  echo "Check $i: Mode = $mode, Size = $size / $max"
  sleep 2
done
echo ""
echo "Queue capped at max size. New items are rejected."
```

### Check rejection metrics

```bash {"name": "check-rejections"}
echo "Checking Prometheus for rejected items..."
curl -s "http://localhost:9090/api/v1/query?query=backpressure_items_rejected_total" | jq '.data.result[0].value[1]'
```

---

## Part 6: Solution 2 - Rate Limiting

### Clear queue and switch to rate-limited mode

```bash {"name": "enable-rate-limiting"}
echo "Clearing queue..."
curl -s -X POST http://localhost:8000/admin/clear-queue | jq

echo ""
echo "Switching to rate-limited mode (2 items/sec to match consumer)..."
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"queue_mode": "rate_limited", "rate_limit": 2}' | jq
```

### Watch rate-limited behavior

```bash {"name": "watch-rate-limited"}
echo "Watching rate-limited queue..."
echo ""
for i in {1..10}; do
  result=$(curl -s http://localhost:8000/status)
  size=$(echo "$result" | jq -r '.queue.size')
  mode=$(echo "$result" | jq -r '.queue.mode')
  echo "Check $i: Mode = $mode, Size = $size (should stay small)"
  sleep 2
done
echo ""
echo "Queue stays small because producer is throttled to match consumer."
```

---

## Part 7: Reset System

### Restore normal operation

```bash {"name": "reset-system"}
echo "Speeding up consumer..."
curl -s -X POST http://localhost:8001/admin/config \
  -H "Content-Type: application/json" \
  -d '{"processing_time_ms": 100}' | jq

echo ""
echo "Clearing queue..."
curl -s -X POST http://localhost:8000/admin/clear-queue | jq

echo ""
echo "Returning to unbounded mode..."
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"queue_mode": "unbounded", "production_rate": 10}' | jq
```

### Verify system health

```bash {"name": "verify-health"}
echo "Producer status:"
curl -s http://localhost:8000/status | jq '{queue: .queue, process: .process}'
echo ""
echo "Consumer status:"
curl -s http://localhost:8001/status | jq
```

---

## Part 8: Comparison Demo

### Side-by-side comparison of all modes

```bash {"name": "comparison-demo"}
echo "=== BACKPRESSURE MODES COMPARISON ==="
echo ""

# Reset to clean state
curl -s -X POST http://localhost:8000/admin/clear-queue > /dev/null
curl -s -X POST http://localhost:8001/admin/config \
  -H "Content-Type: application/json" \
  -d '{"processing_time_ms": 500}' > /dev/null

echo "1. UNBOUNDED MODE (10 seconds)"
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"queue_mode": "unbounded", "production_rate": 10}' > /dev/null
for i in {1..5}; do
  size=$(curl -s http://localhost:8000/status | jq -r '.queue.size')
  echo "   Queue size: $size (growing...)"
  sleep 2
done

echo ""
echo "2. BOUNDED MODE (10 seconds)"
curl -s -X POST http://localhost:8000/admin/clear-queue > /dev/null
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"queue_mode": "bounded", "max_queue_size": 50}' > /dev/null
for i in {1..5}; do
  size=$(curl -s http://localhost:8000/status | jq -r '.queue.size')
  echo "   Queue size: $size (capped at 50)"
  sleep 2
done

echo ""
echo "3. RATE LIMITED MODE (10 seconds)"
curl -s -X POST http://localhost:8000/admin/clear-queue > /dev/null
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"queue_mode": "rate_limited", "rate_limit": 2}' > /dev/null
for i in {1..5}; do
  size=$(curl -s http://localhost:8000/status | jq -r '.queue.size')
  echo "   Queue size: $size (stable)"
  sleep 2
done

echo ""
echo "=== COMPARISON COMPLETE ==="
```

---

## Part 9: Load Testing (Optional)

### Run k6 monitoring script

```bash {"name": "run-k6-monitor"}
docker compose run --rm lab18-k6 run /scripts/monitor-queue.js --duration 1m
```

### Run stress test

```bash {"name": "run-k6-stress"}
docker compose run --rm lab18-k6 run /scripts/producer-stress.js
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

### Check producer logs

```bash {"name": "logs-producer"}
docker compose logs lab18-producer --tail=50
```

### Check consumer logs

```bash {"name": "logs-consumer"}
docker compose logs lab18-consumer --tail=50
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart producer

```bash {"name": "restart-producer"}
docker compose restart lab18-producer
```

### Check Prometheus targets

```bash {"name": "check-prometheus-targets"}
curl -s http://localhost:9090/api/v1/targets | jq '.data.activeTargets[] | {scrapePool: .scrapePool, health: .health}'
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Check queue | `curl -s localhost:8000/status \| jq '.queue'` |
| Slow consumer | `curl -X POST localhost:8001/admin/slow-down -d '{"factor":5}'` |
| Speed up consumer | `curl -X POST localhost:8001/admin/speed-up -d '{"factor":5}'` |
| Bounded queue | `curl -X POST localhost:8000/admin/config -d '{"queue_mode":"bounded","max_queue_size":100}'` |
| Rate limited | `curl -X POST localhost:8000/admin/config -d '{"queue_mode":"rate_limited","rate_limit":5}'` |
| Clear queue | `curl -X POST localhost:8000/admin/clear-queue` |
| Grafana | http://localhost:3001 (admin/admin) |
| Jaeger | http://localhost:16686 |
