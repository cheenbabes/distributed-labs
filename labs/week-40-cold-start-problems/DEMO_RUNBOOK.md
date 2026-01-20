# Demo Runbook: Cold Start Problems

This runbook contains all commands for the demo. If you have the [Runme extension](https://runme.dev) installed in VS Code, you can run each command block directly with the play button.

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
sleep 30
docker compose ps
```

### Verify all services are responding

```bash {"name": "verify-services"}
echo "API Warm:"
curl -s http://localhost:8000/health | jq
echo ""
echo "API Cold:"
curl -s http://localhost:8002/health | jq
echo ""
echo "Backend:"
curl -s http://localhost:8001/health | jq
echo ""
echo "Warmup Service:"
curl -s http://localhost:8003/health | jq
```

---

## Part 2: Show URLs

### Print all service URLs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Grafana:          http://localhost:3001 (admin/admin)"
echo "  Jaeger:           http://localhost:16686"
echo "  Prometheus:       http://localhost:9090"
echo ""
echo "  API Warm:         http://localhost:8000"
echo "  API Cold:         http://localhost:8002"
echo "  Warmup Service:   http://localhost:8003"
echo "  Load Balancer:    http://localhost:8080"
```

---

## Part 3: Cold vs Warm Comparison

### Check warm instance status

```bash {"name": "warm-status"}
echo "=== WARM INSTANCE STATUS ==="
curl -s http://localhost:8000/admin/status | jq
```

### Check cold instance status

```bash {"name": "cold-status"}
echo "=== COLD INSTANCE STATUS ==="
curl -s http://localhost:8002/admin/status | jq
```

### Compare product latency - Warm instance

```bash {"name": "warm-product-test"}
echo "=== WARM INSTANCE - Product Lookup ==="
for i in 1 2 3 4 5; do
  result=$(curl -s http://localhost:8000/api/product/$i)
  echo "Product $i: $(echo $result | jq -c '{cache_hit, duration_ms}')"
done
```

### Compare product latency - Cold instance

```bash {"name": "cold-product-test"}
echo "=== COLD INSTANCE - Product Lookup ==="
for i in 1 2 3 4 5; do
  result=$(curl -s http://localhost:8002/api/product/$i)
  echo "Product $i: $(echo $result | jq -c '{cache_hit, duration_ms}')"
done
```

### Compare DB query latency - Both instances

```bash {"name": "compare-db-latency"}
echo "=== WARM INSTANCE - DB Query ==="
for i in 1 2 3; do
  curl -s http://localhost:8000/api/db-query | jq '{acquire_time_ms, query_time_ms, pool_size}'
done

echo ""
echo "=== COLD INSTANCE - DB Query ==="
for i in 1 2 3; do
  curl -s http://localhost:8002/api/db-query | jq '{acquire_time_ms, query_time_ms, pool_size}'
done
```

---

## Part 4: Simulate Fresh Deploy

### Clear cache on cold instance

```bash {"name": "clear-cold-cache"}
echo "Clearing cache on cold instance..."
curl -s -X POST http://localhost:8002/admin/clear-cache | jq
echo ""
echo "Cache status:"
curl -s http://localhost:8002/admin/status | jq '{cache_warmed, connections_warmed}'
```

### Test cold instance after cache clear

```bash {"name": "test-after-clear"}
echo "=== COLD INSTANCE AFTER CACHE CLEAR ==="
for i in 1 2 3 4 5 6 7 8 9 10; do
  result=$(curl -s http://localhost:8002/api/product/$i)
  echo "Product $i: $(echo $result | jq -c '{cache_hit, duration_ms}')"
done
```

---

## Part 5: Cache Warming

### Warm the cold instance cache (synchronous)

```bash {"name": "warm-cold-cache"}
echo "Starting cache warming on cold instance..."
echo "This may take 10-30 seconds..."
curl -s -X POST http://localhost:8003/warmup/cache/cold/sync | jq
```

### Verify cache is warmed

```bash {"name": "verify-cache-warmed"}
curl -s http://localhost:8002/admin/status | jq '{cache_warmed}'
```

### Test cold instance after warming

```bash {"name": "test-after-warming"}
echo "=== COLD INSTANCE AFTER WARMING ==="
for i in 1 2 3 4 5; do
  result=$(curl -s http://localhost:8002/api/product/$i)
  echo "Product $i: $(echo $result | jq -c '{cache_hit, duration_ms}')"
done
```

---

## Part 6: Traffic Shifting

### Check current traffic weights

```bash {"name": "check-traffic-weights"}
curl -s http://localhost:8003/traffic/weights | jq
```

### Set traffic 100% to warm

```bash {"name": "traffic-all-warm"}
curl -s -X POST "http://localhost:8003/traffic/set?warm=100&cold=0" | jq
```

### Set traffic 50/50

```bash {"name": "traffic-50-50"}
curl -s -X POST "http://localhost:8003/traffic/set?warm=50&cold=50" | jq
```

### Gradual traffic shift to cold (60 seconds)

```bash {"name": "gradual-shift-to-cold"}
echo "Starting gradual traffic shift: warm -> cold over 60 seconds"
curl -s -X POST "http://localhost:8003/traffic/shift?from_instance=warm&to_instance=cold&duration_seconds=60&step_percent=10" | jq
```

### Watch traffic weights change

```bash {"name": "watch-traffic-weights"}
for i in {1..15}; do
  echo "=== $(date +%T) ==="
  curl -s http://localhost:8003/traffic/weights | jq -c '.weights'
  sleep 5
done
```

---

## Part 7: Full Deploy Simulation

### Reset state

```bash {"name": "reset-state"}
echo "Resetting state..."
curl -s -X POST http://localhost:8002/admin/clear-cache > /dev/null
curl -s -X POST "http://localhost:8003/traffic/set?warm=100&cold=0" > /dev/null
echo "State reset complete:"
echo "  - Cold instance cache cleared"
echo "  - Traffic 100% to warm instance"
curl -s http://localhost:8003/traffic/weights | jq -c '.weights'
```

### Run deploy simulation

```bash {"name": "deploy-simulation"}
echo "Starting deploy simulation..."
echo "This will:"
echo "  1. Clear cold instance cache (simulate fresh deploy)"
echo "  2. Warm the cache"
echo "  3. Gradually shift traffic over 60 seconds"
echo ""
curl -s -X POST "http://localhost:8003/simulate/deploy?warmup_first=true&shift_duration=60" | jq
```

---

## Part 8: Load Testing

### Run basic load test

```bash {"name": "load-test-basic"}
docker compose run --rm k6 run /scripts/basic.js
```

### Run cold start comparison test

```bash {"name": "load-test-comparison"}
docker compose run --rm k6 run /scripts/cold-start-comparison.js
```

### Run deploy simulation test (long running)

```bash {"name": "load-test-deploy", "background": true}
docker compose run --rm k6 run /scripts/deploy-simulation.js
```

---

## Part 9: Observability Deep Dive

### Generate traces for Jaeger

```bash {"name": "generate-traces"}
echo "Generating traces..."
for i in {1..30}; do
  curl -s http://localhost:8000/api/product/$((RANDOM % 50 + 1)) > /dev/null &
  curl -s http://localhost:8002/api/product/$((RANDOM % 50 + 1)) > /dev/null &
done
wait
echo "Done. Check Jaeger: http://localhost:16686"
```

### Check Prometheus metrics

```bash {"name": "prometheus-metrics"}
echo "=== Cache Hit/Miss Metrics ==="
curl -s http://localhost:9090/api/v1/query?query=cache_hits_total | jq '.data.result[] | {instance: .metric.instance, value: .value[1]}'
echo ""
curl -s http://localhost:9090/api/v1/query?query=cache_misses_total | jq '.data.result[] | {instance: .metric.instance, value: .value[1]}'
```

### Check cold start latency metrics

```bash {"name": "cold-start-metrics"}
echo "=== Cold Start Latency (p95) ==="
curl -s 'http://localhost:9090/api/v1/query?query=histogram_quantile(0.95,sum(rate(cold_start_latency_seconds_bucket[5m]))by(le,operation,instance))' | jq '.data.result[] | {operation: .metric.operation, instance: .metric.instance, p95_seconds: .value[1]}'
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

### View all logs

```bash {"name": "logs-all"}
docker compose logs --tail=50
```

### View API warm logs

```bash {"name": "logs-api-warm"}
docker compose logs api-warm --tail=50
```

### View API cold logs

```bash {"name": "logs-api-cold"}
docker compose logs api-cold --tail=50
```

### View warmup service logs

```bash {"name": "logs-warmup"}
docker compose logs warmup-service --tail=50
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart all services

```bash {"name": "restart-all"}
docker compose restart
```

### Restart cold instance only

```bash {"name": "restart-cold"}
docker compose restart api-cold
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Clear cold cache | `curl -X POST http://localhost:8002/admin/clear-cache` |
| Warm cold cache | `curl -X POST http://localhost:8003/warmup/cache/cold/sync` |
| Check traffic weights | `curl http://localhost:8003/traffic/weights` |
| Set 50/50 traffic | `curl -X POST "http://localhost:8003/traffic/set?warm=50&cold=0"` |
| Gradual shift | `curl -X POST "http://localhost:8003/traffic/shift?from_instance=warm&to_instance=cold&duration_seconds=60"` |
| Simulate deploy | `curl -X POST "http://localhost:8003/simulate/deploy?warmup_first=true"` |
| View traces | http://localhost:16686 |
| View dashboard | http://localhost:3001 |
