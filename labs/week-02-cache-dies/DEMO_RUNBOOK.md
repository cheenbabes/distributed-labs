# Demo Runbook: What Happens When Your Cache Dies

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
echo "Gateway:"
curl -s http://localhost:8000/health | jq
echo ""
echo "Application:"
curl -s http://localhost:8001/health | jq
echo ""
echo "Redis:"
docker compose exec redis redis-cli ping
echo ""
echo "Postgres:"
docker compose exec postgres pg_isready -U app
```

---

## Part 2: Establish Baseline (Cache Working)

### Warm up the cache with initial requests

```bash {"name": "warm-cache"}
echo "Warming up cache..."
for i in {1..10}; do
  curl -s http://localhost:8000/api/data > /dev/null
done
echo "Cache warmed up!"
```

### Show current cache statistics

```bash {"name": "show-stats"}
curl -s http://localhost:8000/api/stats | jq
```

### Show timing with cache hits

```bash {"name": "baseline-timing"}
echo "Timing with cache (should be fast ~5-20ms):"
for i in {1..5}; do
  result=$(curl -s http://localhost:8000/api/data)
  latency=$(echo $result | jq -r '.latency_ms')
  cache=$(echo $result | jq -r '.cache_hit')
  echo "Request $i: ${latency}ms (cache_hit: $cache)"
done
```

### Show detailed response

```bash {"name": "single-request"}
curl -s http://localhost:8000/api/data | jq
```

---

## Part 3: Open Observability UIs

### Print URLs for monitoring

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Grafana:    http://localhost:3001 (admin/admin)"
echo "  Jaeger:     http://localhost:16686"
echo "  Prometheus: http://localhost:9090"
echo "  Gateway:    http://localhost:8000"
echo ""
echo "In Grafana, look for the 'Cache Performance' dashboard"
```

---

## Part 4: Start Load Test

### Run continuous load in background

```bash {"name": "start-load", "background": true}
docker compose run --rm k6 run /scripts/cache-test.js
```

### Alternative: Simple inline load generator

```bash {"name": "simple-load", "background": true}
echo "Starting continuous load (Ctrl+C to stop)..."
while true; do
  curl -s http://localhost:8000/api/data > /dev/null
  sleep 0.1
done
```

---

## Part 5: Kill the Cache (THE DRAMATIC MOMENT)

### Show current stats before killing

```bash {"name": "stats-before-kill"}
echo "=== BEFORE KILLING CACHE ==="
curl -s http://localhost:8000/api/stats | jq
```

### KILL REDIS

```bash {"name": "kill-redis"}
echo "KILLING REDIS..."
docker compose stop redis
echo "Redis is dead!"
```

### Immediately show the impact

```bash {"name": "show-impact"}
echo "=== AFTER KILLING CACHE ==="
echo ""
echo "Making requests (notice the latency spike):"
for i in {1..5}; do
  start=$(date +%s%N)
  result=$(curl -s http://localhost:8000/api/data 2>/dev/null || echo '{"error": true}')
  end=$(date +%s%N)
  elapsed=$(( (end - start) / 1000000 ))
  cache=$(echo $result | jq -r '.cache_hit // "error"')
  echo "Request $i: ${elapsed}ms (cache_hit: $cache)"
done
```

### Show updated stats

```bash {"name": "stats-after-kill"}
curl -s http://localhost:8000/api/stats | jq
```

---

## Part 6: Observe the Degradation

### Continuous monitoring while cache is dead

```bash {"name": "monitor-degraded"}
echo "Monitoring requests with cache dead (10 requests):"
for i in {1..10}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/api/data)
  echo "Request $i: ${time}s"
  sleep 0.5
done
```

### Compare to baseline

```bash {"name": "comparison-summary"}
echo "=== COMPARISON SUMMARY ==="
echo ""
echo "With Cache (typical):"
echo "  - Latency: 5-20ms"
echo "  - Cache hit rate: 80-90%"
echo "  - DB queries: Low"
echo ""
echo "Without Cache (now):"
echo "  - Latency: 50-150ms (10-30x slower!)"
echo "  - Cache hit rate: 0%"
echo "  - DB queries: 100% of traffic"
```

---

## Part 7: Restore the Cache

### Bring Redis back

```bash {"name": "restore-redis"}
echo "Restoring Redis..."
docker compose start redis
sleep 3
echo "Redis is back!"
```

### Warm up the cache

```bash {"name": "warm-after-restore"}
echo "Warming cache..."
for i in {1..10}; do
  curl -s http://localhost:8000/api/data > /dev/null
done
echo "Cache warmed!"
```

### Verify recovery

```bash {"name": "verify-recovery"}
echo "Verifying recovery:"
for i in {1..5}; do
  result=$(curl -s http://localhost:8000/api/data)
  latency=$(echo $result | jq -r '.latency_ms')
  cache=$(echo $result | jq -r '.cache_hit')
  echo "Request $i: ${latency}ms (cache_hit: $cache)"
done
```

### Show final stats

```bash {"name": "final-stats"}
echo "=== FINAL STATS ==="
curl -s http://localhost:8000/api/stats | jq
```

---

## Part 8: Side-by-Side Comparison

### Full comparison cycle

```bash {"name": "full-comparison"}
echo "=== FULL COMPARISON ==="
echo ""

# Ensure cache is up and warm
docker compose start redis 2>/dev/null || true
sleep 2
for i in {1..5}; do curl -s http://localhost:8000/api/data > /dev/null; done

echo "WITH CACHE:"
for i in {1..3}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/api/data)
  echo "  Request $i: ${time}s"
done

# Kill cache
docker compose stop redis
sleep 1

echo ""
echo "WITHOUT CACHE:"
for i in {1..3}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/api/data)
  echo "  Request $i: ${time}s"
done

# Restore
docker compose start redis
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

### Check specific service logs

```bash {"name": "logs-app"}
docker compose logs app --tail=50
```

### Check Redis connection

```bash {"name": "check-redis"}
docker compose exec redis redis-cli info clients
```

### Check Postgres connections

```bash {"name": "check-postgres"}
docker compose exec postgres psql -U app -d app -c "SELECT count(*) FROM pg_stat_activity;"
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Kill Redis | `docker compose stop redis` |
| Restore Redis | `docker compose start redis` |
| Check stats | `curl -s localhost:8000/api/stats \| jq` |
| View traces | http://localhost:16686 |
| View dashboards | http://localhost:3001 |
