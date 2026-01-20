# Demo Runbook: Thundering Herd (Cache Stampede)

This runbook contains all commands for demonstrating the thundering herd problem. If you have the [Runme extension](https://runme.dev) installed in VS Code, you can run each command block directly with the play button.

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

### Verify API is responding

```bash {"name": "verify-api"}
curl -s http://localhost:8000/health | jq
```

### Show current configuration

```bash {"name": "show-config"}
curl -s http://localhost:8000/admin/config | jq
```

---

## Part 2: Open Observability UIs

### Print URLs to open

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Grafana:     http://localhost:3001 (admin/admin)"
echo "  Prometheus:  http://localhost:9090"
echo "  Jaeger:      http://localhost:16686"
echo "  API:         http://localhost:8000"
echo ""
echo "In Grafana, open the 'Thundering Herd Lab' dashboard"
```

---

## Part 3: Demonstrate Normal Caching

### First request (cache miss - slow)

```bash {"name": "first-request"}
echo "First request (cache miss - will be slow ~500-1000ms):"
time curl -s http://localhost:8000/product/demo-product | jq
```

### Second request (cache hit - fast)

```bash {"name": "second-request"}
echo "Second request (cache hit - will be fast ~10ms):"
time curl -s http://localhost:8000/product/demo-product | jq
```

### Show the difference

```bash {"name": "cache-demo"}
echo "=== CACHE DEMONSTRATION ==="
echo ""

# Clear cache
curl -s -X DELETE http://localhost:8000/cache > /dev/null

echo "Request 1 (cache miss):"
time curl -s -o /dev/null -w "  Latency: %{time_total}s\n" http://localhost:8000/product/cache-demo

echo ""
echo "Request 2 (cache hit):"
time curl -s -o /dev/null -w "  Latency: %{time_total}s\n" http://localhost:8000/product/cache-demo

echo ""
echo "Request 3 (cache hit):"
time curl -s -o /dev/null -w "  Latency: %{time_total}s\n" http://localhost:8000/product/cache-demo
```

---

## Part 4: Trigger the Stampede

### Clear cache and send concurrent requests

```bash {"name": "trigger-stampede"}
echo "=== TRIGGERING STAMPEDE ==="
echo ""

# Clear cache first
curl -s -X DELETE http://localhost:8000/cache > /dev/null
echo "Cache cleared"
echo ""

echo "Sending 20 concurrent requests..."
for i in {1..20}; do
  curl -s -o /dev/null http://localhost:8000/product/stampede-demo &
done
wait

echo ""
echo "Stampede complete! Check the stats:"
curl -s http://localhost:8000/admin/stats | jq
```

### Check for stampede events

```bash {"name": "check-stampede"}
echo "Current stampede statistics:"
curl -s http://localhost:8000/admin/stats | jq '{
  concurrent_db_queries,
  cached_products,
  protection
}'
```

---

## Part 5: Run Load Test Without Protection

### Ensure protection is disabled

```bash {"name": "disable-protection"}
curl -s -X POST http://localhost:8000/admin/protection \
  -H "Content-Type: application/json" \
  -d '{
    "enable_lock_protection": false,
    "enable_probabilistic_refresh": false
  }' | jq
```

### Clear cache and run load test

```bash {"name": "loadtest-no-protection"}
echo "Clearing cache..."
curl -s -X DELETE http://localhost:8000/cache > /dev/null

echo "Running load test WITHOUT protection (2 minutes)..."
echo "Watch Grafana for stampedes every ~5 seconds!"
echo ""
docker compose run --rm lab12-k6 run /scripts/thundering-herd.js
```

---

## Part 6: Enable Lock-Based Protection

### Enable lock protection

```bash {"name": "enable-lock-protection"}
echo "Enabling lock-based protection..."
curl -s -X POST http://localhost:8000/admin/protection \
  -H "Content-Type: application/json" \
  -d '{"enable_lock_protection": true}' | jq
```

### Run load test with lock protection

```bash {"name": "loadtest-with-lock"}
echo "Clearing cache..."
curl -s -X DELETE http://localhost:8000/cache > /dev/null

echo "Running load test WITH lock protection (2 minutes)..."
echo "Watch Grafana - concurrent DB queries should stay at 1!"
echo ""
docker compose run --rm lab12-k6 run /scripts/thundering-herd.js
```

---

## Part 7: Enable Probabilistic Early Refresh

### Switch to probabilistic refresh only

```bash {"name": "enable-probabilistic"}
echo "Enabling probabilistic early refresh..."
curl -s -X POST http://localhost:8000/admin/protection \
  -H "Content-Type: application/json" \
  -d '{
    "enable_lock_protection": false,
    "enable_probabilistic_refresh": true,
    "probabilistic_refresh_factor": 0.5
  }' | jq
```

### Run load test with probabilistic refresh

```bash {"name": "loadtest-with-probabilistic"}
echo "Clearing cache..."
curl -s -X DELETE http://localhost:8000/cache > /dev/null

echo "Running load test WITH probabilistic refresh (2 minutes)..."
echo "Watch Grafana - early refresh should spread out the load!"
echo ""
docker compose run --rm lab12-k6 run /scripts/thundering-herd.js
```

---

## Part 8: Enable Both Protections (Best Practice)

### Enable both lock and probabilistic refresh

```bash {"name": "enable-both"}
echo "Enabling BOTH protections (defense in depth)..."
curl -s -X POST http://localhost:8000/admin/protection \
  -H "Content-Type: application/json" \
  -d '{
    "enable_lock_protection": true,
    "enable_probabilistic_refresh": true,
    "probabilistic_refresh_factor": 0.3
  }' | jq
```

### Run final load test

```bash {"name": "loadtest-both"}
echo "Clearing cache..."
curl -s -X DELETE http://localhost:8000/cache > /dev/null

echo "Running load test WITH BOTH protections (2 minutes)..."
echo "This should show the best results!"
echo ""
docker compose run --rm lab12-k6 run /scripts/thundering-herd.js
```

---

## Part 9: Compare Results Summary

### Generate comparison requests

```bash {"name": "comparison-demo"}
echo "=== PROTECTION COMPARISON ==="
echo ""

# Test 1: No protection
echo "1. NO PROTECTION:"
curl -s -X POST http://localhost:8000/admin/protection \
  -H "Content-Type: application/json" \
  -d '{"enable_lock_protection": false, "enable_probabilistic_refresh": false}' > /dev/null
curl -s -X DELETE http://localhost:8000/cache > /dev/null
echo "   Sending 10 concurrent requests..."
start=$(date +%s%3N)
for i in {1..10}; do curl -s http://localhost:8000/product/compare-test > /dev/null & done
wait
end=$(date +%s%3N)
echo "   Total time: $((end-start))ms"
curl -s http://localhost:8000/admin/stats | jq -r '"   Stampede events possible"'

echo ""

# Test 2: Lock protection
echo "2. WITH LOCK PROTECTION:"
curl -s -X POST http://localhost:8000/admin/protection \
  -H "Content-Type: application/json" \
  -d '{"enable_lock_protection": true, "enable_probabilistic_refresh": false}' > /dev/null
curl -s -X DELETE http://localhost:8000/cache > /dev/null
echo "   Sending 10 concurrent requests..."
start=$(date +%s%3N)
for i in {1..10}; do curl -s http://localhost:8000/product/compare-test > /dev/null & done
wait
end=$(date +%s%3N)
echo "   Total time: $((end-start))ms"
echo "   Only 1 DB query executed, others waited"

echo ""
echo "Check Grafana to see the full comparison!"
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

### Check API logs specifically

```bash {"name": "logs-api"}
docker compose logs lab12-api --tail=100
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart services

```bash {"name": "restart-services"}
docker compose restart lab12-api
```

### Check Prometheus targets

```bash {"name": "check-prometheus"}
curl -s http://localhost:9090/api/v1/targets | jq '.data.activeTargets[] | {instance: .labels.instance, health}'
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Clear cache | `curl -X DELETE localhost:8000/cache` |
| Enable lock protection | `curl -X POST localhost:8000/admin/protection -d '{"enable_lock_protection":true}'` |
| Enable probabilistic | `curl -X POST localhost:8000/admin/protection -d '{"enable_probabilistic_refresh":true}'` |
| Check stats | `curl localhost:8000/admin/stats` |
| Run load test | `docker compose run --rm lab12-k6 run /scripts/thundering-herd.js` |
| View dashboard | http://localhost:3001 |

---

## Key Metrics to Watch

| Metric | Normal | Stampede | With Protection |
|--------|--------|----------|-----------------|
| Concurrent DB Queries | 0-1 | 10-50+ | 1 |
| Cache Hit Rate | 95%+ | Drops to 0% | 95%+ |
| p99 Latency | ~10ms | 500-1000ms | ~10ms (or ~500ms waiting) |
| Stampede Events | 0 | Many | 0 |
