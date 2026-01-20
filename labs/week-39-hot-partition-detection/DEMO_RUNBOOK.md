# Demo Runbook: Hot Partition Detection

This runbook contains all commands for demonstrating hot partition detection and mitigation. If you have the [Runme extension](https://runme.dev) installed in VS Code, you can run each command block directly with the play button.

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
sleep 20
docker compose ps
```

### Verify all services are responding

```bash {"name": "verify-services"}
echo "API Gateway:"
curl -s http://localhost:8000/health | jq
echo ""
echo "Shards:"
for shard in 0 1 2 3; do
  echo "Shard $shard:"
  curl -s "http://localhost:900$shard/health" | jq -c
done
```

---

## Part 2: Open Observability UIs

### Show URLs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Grafana:     http://localhost:3001 (admin/admin)"
echo "  Jaeger:      http://localhost:16686"
echo "  Prometheus:  http://localhost:9090"
echo "  API Gateway: http://localhost:8000"
```

---

## Part 3: Understand Sharding

### Show shard mapping for different keys

```bash {"name": "shard-mapping"}
echo "Key to Shard Mapping:"
echo "====================="
for key in user-0 user-1 user-2 user-3 user-100 celebrity-user-kardashian; do
  result=$(curl -s "http://localhost:8000/admin/shard-mapping/$key")
  shard=$(echo $result | jq -r '.shard_id')
  echo "$key -> Shard $shard"
done
```

### Make single request and show response

```bash {"name": "single-request"}
curl -s http://localhost:8000/data/test-key | jq
```

---

## Part 4: Baseline - Uniform Distribution

### Run uniform traffic test

```bash {"name": "uniform-test", "background": true}
docker compose run --rm lab39-k6 run /scripts/uniform.js
```

### Check shard statistics during uniform test

```bash {"name": "check-uniform-stats"}
echo "Shard Statistics (during uniform traffic):"
echo "==========================================="
for shard in 0 1 2 3; do
  echo ""
  echo "=== Shard $shard ==="
  curl -s "http://localhost:900$shard/stats" | jq '{total_keys, total_accesses, active_requests, hot_keys}'
done
```

---

## Part 5: Create Hot Partition - Zipfian Distribution

### Reset all statistics

```bash {"name": "reset-stats"}
echo "Resetting all statistics..."
curl -s -X POST http://localhost:8000/admin/reset | jq
for shard in 0 1 2 3; do
  curl -s -X POST "http://localhost:900$shard/admin/reset" | jq -c
done
echo "Done!"
```

### Run Zipfian traffic test

```bash {"name": "zipfian-test", "background": true}
docker compose run --rm lab39-k6 run /scripts/zipfian.js
```

### Check shard statistics during Zipfian test

```bash {"name": "check-zipfian-stats"}
echo "Shard Statistics (during Zipfian traffic):"
echo "==========================================="
for shard in 0 1 2 3; do
  echo ""
  echo "=== Shard $shard ==="
  stats=$(curl -s "http://localhost:900$shard/stats")
  echo $stats | jq '{total_accesses, hot_keys, top_keys: .top_keys[:3]}'
done
```

---

## Part 6: Celebrity User Scenario

### Reset statistics before celebrity test

```bash {"name": "reset-before-celebrity"}
echo "Resetting statistics..."
curl -s -X POST http://localhost:8000/admin/reset > /dev/null
for shard in 0 1 2 3; do
  curl -s -X POST "http://localhost:900$shard/admin/reset" > /dev/null
done
echo "Done!"
```

### Find celebrity's shard

```bash {"name": "find-celebrity-shard"}
echo "Celebrity user mapping:"
curl -s http://localhost:8000/admin/shard-mapping/celebrity-user-kardashian | jq
```

### Run celebrity traffic test (WITHOUT caching)

```bash {"name": "celebrity-test-no-cache", "background": true}
# Ensure cache is disabled
curl -s -X POST http://localhost:8000/admin/cache \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}' > /dev/null

docker compose run --rm lab39-k6 run /scripts/celebrity.js
```

### Check hot partition during celebrity test

```bash {"name": "check-celebrity-stats"}
echo "Shard Statistics (during celebrity traffic):"
echo "============================================="
for shard in 0 1 2 3; do
  echo ""
  echo "=== Shard $shard ==="
  stats=$(curl -s "http://localhost:900$shard/stats")
  echo $stats | jq '{active_requests, hot_keys, top_3_keys: .top_keys[:3]}'
done

echo ""
echo "Gateway Statistics:"
curl -s http://localhost:8000/stats | jq '{cache_enabled, cache_size, top_5_keys: .top_keys[:5]}'
```

---

## Part 7: Mitigation - Enable Caching

### Enable gateway caching

```bash {"name": "enable-cache"}
curl -s -X POST http://localhost:8000/admin/cache \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "ttl_seconds": 60}' | jq
```

### Reset and run celebrity test WITH caching

```bash {"name": "celebrity-test-with-cache", "background": true}
# Reset stats first
curl -s -X POST http://localhost:8000/admin/reset > /dev/null
for shard in 0 1 2 3; do
  curl -s -X POST "http://localhost:900$shard/admin/reset" > /dev/null
done

docker compose run --rm lab39-k6 run /scripts/with-caching.js
```

### Check cache effectiveness

```bash {"name": "check-cache-stats"}
echo "Gateway Cache Statistics:"
echo "========================="
curl -s http://localhost:8000/stats | jq

echo ""
echo "Shard Load (should be more balanced now):"
for shard in 0 1 2 3; do
  accesses=$(curl -s "http://localhost:900$shard/stats" | jq '.total_accesses')
  echo "Shard $shard: $accesses accesses"
done
```

### Disable caching

```bash {"name": "disable-cache"}
curl -s -X POST http://localhost:8000/admin/cache \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}' | jq
```

---

## Part 8: Comparison Demo

### Side-by-side comparison

```bash {"name": "comparison-demo"}
echo "=== COMPARISON: Celebrity Traffic Impact ==="
echo ""

# Reset
for shard in 0 1 2 3; do
  curl -s -X POST "http://localhost:900$shard/admin/reset" > /dev/null
done
curl -s -X POST http://localhost:8000/admin/reset > /dev/null
curl -s -X POST http://localhost:8000/admin/cache -H "Content-Type: application/json" -d '{"enabled": false}' > /dev/null

echo "1. Testing WITHOUT caching (10 seconds)..."
for i in {1..100}; do
  curl -s http://localhost:8000/data/celebrity-user-kardashian > /dev/null &
done
wait
sleep 2

echo ""
echo "Results WITHOUT caching:"
for shard in 0 1 2 3; do
  accesses=$(curl -s "http://localhost:900$shard/stats" | jq '.total_accesses')
  echo "  Shard $shard: $accesses accesses"
done

# Reset
for shard in 0 1 2 3; do
  curl -s -X POST "http://localhost:900$shard/admin/reset" > /dev/null
done
curl -s -X POST http://localhost:8000/admin/reset > /dev/null
curl -s -X POST http://localhost:8000/admin/cache -H "Content-Type: application/json" -d '{"enabled": true, "ttl_seconds": 60}' > /dev/null

echo ""
echo "2. Testing WITH caching (same 100 requests)..."
for i in {1..100}; do
  curl -s http://localhost:8000/data/celebrity-user-kardashian > /dev/null &
done
wait
sleep 2

echo ""
echo "Results WITH caching:"
for shard in 0 1 2 3; do
  accesses=$(curl -s "http://localhost:900$shard/stats" | jq '.total_accesses')
  echo "  Shard $shard: $accesses accesses"
done

cache_size=$(curl -s http://localhost:8000/stats | jq '.cache_size')
echo ""
echo "Gateway cache size: $cache_size entries"
echo ""
echo "Conclusion: With caching, only 1 request hits the shard (cache miss)."
echo "            Remaining 99 requests served from cache!"
```

---

## Part 9: Trace Analysis

### Generate traces for Jaeger

```bash {"name": "generate-traces"}
echo "Generating traces for analysis..."
for i in {1..20}; do
  curl -s http://localhost:8000/data/celebrity-user-kardashian > /dev/null
  curl -s http://localhost:8000/data/normal-user-$i > /dev/null
done
echo "Done! Open Jaeger: http://localhost:16686"
echo ""
echo "In Jaeger:"
echo "  1. Select service: api-gateway"
echo "  2. Click 'Find Traces'"
echo "  3. Compare traces - note shard.id attribute"
echo "  4. Celebrity key traces all go to same shard"
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

### Check specific shard logs

```bash {"name": "logs-shard-0"}
docker compose logs lab39-shard-0 --tail=50
```

### Check API gateway logs

```bash {"name": "logs-gateway"}
docker compose logs lab39-api-gateway --tail=50
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart all services

```bash {"name": "restart-all"}
docker compose restart
```

### Check Prometheus targets

```bash {"name": "check-prometheus"}
curl -s http://localhost:9090/api/v1/targets | jq '.data.activeTargets[] | {scrapePool: .scrapePool, health: .health}'
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Enable cache | `curl -X POST localhost:8000/admin/cache -d '{"enabled":true,"ttl_seconds":60}'` |
| Disable cache | `curl -X POST localhost:8000/admin/cache -d '{"enabled":false}'` |
| Check shard mapping | `curl localhost:8000/admin/shard-mapping/<key>` |
| Get data | `curl localhost:8000/data/<key>` |
| Shard stats | `curl localhost:9000/stats` (adjust port for shard) |
| Gateway stats | `curl localhost:8000/stats` |
| Reset stats | `curl -X POST localhost:8000/admin/reset` |
| View traces | http://localhost:16686 |
| View dashboard | http://localhost:3001 |
