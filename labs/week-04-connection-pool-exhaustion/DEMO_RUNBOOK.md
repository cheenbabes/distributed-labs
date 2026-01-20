# Demo Runbook: Connection Pool Exhaustion

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

### Verify API is responding

```bash {"name": "verify-api"}
curl -s http://localhost:8000/health | jq
```

### Check database connection

```bash {"name": "verify-db"}
docker compose exec postgres psql -U labuser -d labdb -c "SELECT COUNT(*) FROM items;"
```

---

## Part 2: Understand the Configuration

### Show current pool stats

```bash {"name": "show-pool-stats"}
echo "Current connection pool statistics:"
curl -s http://localhost:8000/pool/stats | jq
```

### Show current config

```bash {"name": "show-config"}
echo "Current runtime configuration:"
curl -s http://localhost:8000/admin/config | jq
```

### Print key settings

```bash {"name": "print-settings"}
echo "=== KEY SETTINGS FOR THIS LAB ==="
echo ""
echo "Pool Configuration:"
echo "  - Max connections: 5"
echo "  - Acquire timeout: 10 seconds"
echo ""
echo "Request Behavior:"
echo "  - Work time: 1000ms (1 second)"
echo "  - Each request holds connection for 1 second"
echo ""
echo "The Math:"
echo "  - 5 connections / 1s hold time = 5 req/sec max"
echo "  - Send 50 concurrent requests..."
echo "  - 45 must wait, many will timeout!"
```

---

## Part 3: Open Observability UIs

### Print URLs for manual opening

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Grafana:    http://localhost:3001 (admin/admin)"
echo "  Jaeger:     http://localhost:16686"
echo "  Prometheus: http://localhost:9090"
echo "  API:        http://localhost:8000"
echo ""
echo "In Grafana, navigate to: Dashboards > Connection Pool Dashboard"
```

---

## Part 4: Establish Baseline

### Single request (should succeed quickly)

```bash {"name": "single-request"}
echo "Making a single request..."
curl -s http://localhost:8000/api/query | jq
```

### 5 sequential requests (within pool capacity)

```bash {"name": "sequential-requests"}
echo "Making 5 sequential requests..."
for i in {1..5}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/api/query)
  echo "Request $i: ${time}s"
done
echo ""
echo "Each request takes ~1.1s (1s work + overhead)"
```

### Check pool after baseline

```bash {"name": "check-pool-baseline"}
echo "Pool stats after baseline requests:"
curl -s http://localhost:8000/pool/stats | jq
```

---

## Part 5: Demonstrate Pool Exhaustion

### Send 10 concurrent requests (pool size is 5)

```bash {"name": "concurrent-10"}
echo "Sending 10 concurrent requests (pool has 5 connections)..."
echo ""
for i in {1..10}; do
  curl -s http://localhost:8000/api/query -o /dev/null -w "Request $i: %{http_code} in %{time_total}s\n" &
done
wait
echo ""
echo "Notice: Some requests took ~2s because they had to wait!"
```

### Show pool stats during load

```bash {"name": "pool-during-load"}
echo "Start this, then quickly run the concurrent test in another terminal"
echo "to see the pool stats change in real-time:"
echo ""
for i in {1..10}; do
  curl -s http://localhost:8000/pool/stats | jq -c '{used: .pool_used_size, free: .pool_free_size, waiting: .waiting_requests}'
  sleep 0.5
done
```

---

## Part 6: Severe Exhaustion with Timeouts

### Send 50 concurrent requests (expect failures!)

```bash {"name": "concurrent-50"}
echo "Sending 50 concurrent requests..."
echo "Pool size: 5, Timeout: 10s, Work time: 1s"
echo ""
echo "Expected: First 5 succeed immediately, next batches queue up,"
echo "          some will timeout after 10s and return 503"
echo ""

success=0
failed=0
for i in {1..50}; do
  (
    result=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/query)
    echo "Request $i: $result"
  ) &
done
wait

echo ""
echo "Check Grafana dashboard for the full picture!"
```

### Check for exhaustion events

```bash {"name": "check-exhaustion"}
echo "Checking pool exhaustion metrics..."
curl -s http://localhost:8000/metrics | grep -E "pool_(exhaustion|waiting|used|free)"
```

---

## Part 7: Analyze in Jaeger

### Generate traces for analysis

```bash {"name": "generate-traces"}
echo "Generating some traces..."
for i in {1..20}; do
  curl -s http://localhost:8000/api/query > /dev/null &
done
wait
echo ""
echo "Now open Jaeger: http://localhost:16686"
echo "Select service 'api' and click 'Find Traces'"
echo ""
echo "Look for:"
echo "  1. Traces with errors (503 responses)"
echo "  2. Long 'acquire_connection' spans"
echo "  3. Compare fast vs slow requests"
```

---

## Part 8: Run k6 Load Test

### Run the pool exhaustion test

```bash {"name": "k6-exhaustion-test"}
echo "Running k6 pool exhaustion test..."
docker compose run --rm k6 run /scripts/pool-exhaustion.js
```

### Run concurrent burst test

```bash {"name": "k6-burst-test"}
echo "Running k6 concurrent burst test (50 simultaneous requests)..."
docker compose run --rm k6 run /scripts/concurrent-burst.js
```

---

## Part 9: Fix #1 - Reduce Hold Time

### Reduce work time from 1000ms to 100ms

```bash {"name": "reduce-work-time"}
echo "Fixing: Reduce work time from 1000ms to 100ms"
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"work_time_ms": 100}' | jq
```

### Verify the change

```bash {"name": "verify-work-time"}
curl -s http://localhost:8000/admin/config | jq
```

### Test with reduced work time

```bash {"name": "test-reduced-work-time"}
echo "Testing 50 concurrent requests with 100ms work time..."
echo ""
success=0
for i in {1..50}; do
  (
    result=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8000/api/query)
    if [ "$result" = "200" ]; then
      echo "Request $i: SUCCESS"
    else
      echo "Request $i: FAILED ($result)"
    fi
  ) &
done
wait
echo ""
echo "Most/all should succeed now!"
```

### Reset work time

```bash {"name": "reset-work-time"}
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"work_time_ms": 1000}' | jq
```

---

## Part 10: Fix #2 - Increase Pool Size

### Stop API service

```bash {"name": "stop-api"}
docker compose stop api
```

### Start with larger pool

```bash {"name": "start-larger-pool"}
echo "Starting API with pool size of 20..."
docker compose run -d --name api-large-pool \
  -p 8000:8000 \
  -e POOL_MAX_SIZE=20 \
  -e OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317 \
  -e OTEL_SERVICE_NAME=api \
  -e DB_HOST=postgres \
  -e DB_PORT=5432 \
  -e DB_NAME=labdb \
  -e DB_USER=labuser \
  -e DB_PASSWORD=labpassword \
  --network week-04-connection-pool-exhaustion_lab-network \
  api

sleep 10
echo "New pool configuration:"
curl -s http://localhost:8000/pool/stats | jq
```

### Test with larger pool

```bash {"name": "test-larger-pool"}
echo "Testing 20 concurrent requests with pool size 20..."
for i in {1..20}; do
  curl -s http://localhost:8000/api/query -o /dev/null -w "Request $i: %{http_code}\n" &
done
wait
echo ""
echo "All should succeed without waiting!"
```

---

## Part 11: Compare Endpoints

### Regular endpoint (explicit acquire/release)

```bash {"name": "regular-endpoint"}
echo "Regular endpoint (explicit connection handling):"
curl -s http://localhost:8000/api/query | jq
```

### Proper endpoint (context manager)

```bash {"name": "proper-endpoint"}
echo "Proper endpoint (context manager pattern):"
curl -s http://localhost:8000/api/query-proper | jq
```

### Explain the difference

```bash {"name": "explain-patterns"}
echo "=== CONNECTION HANDLING PATTERNS ==="
echo ""
echo "EXPLICIT (what we use in /api/query):"
echo "  conn = await pool.acquire()"
echo "  try:"
echo "      # do work"
echo "  finally:"
echo "      await pool.release(conn)"
echo ""
echo "CONTEXT MANAGER (what /api/query-proper uses):"
echo "  async with pool.acquire() as conn:"
echo "      # do work"
echo "      # connection automatically released"
echo ""
echo "Both work, but context manager is safer and recommended!"
```

---

## Cleanup

### Stop all services

```bash {"name": "cleanup"}
docker stop api-large-pool 2>/dev/null || true
docker rm api-large-pool 2>/dev/null || true
docker compose down -v
echo "Lab cleaned up"
```

---

## Troubleshooting Commands

### Check all logs

```bash {"name": "logs-all"}
docker compose logs --tail=50
```

### Check API logs

```bash {"name": "logs-api"}
docker compose logs api --tail=50
```

### Check Postgres logs

```bash {"name": "logs-postgres"}
docker compose logs postgres --tail=50
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Connect to Postgres directly

```bash {"name": "psql-connect"}
docker compose exec postgres psql -U labuser -d labdb
```

### Check active database connections

```bash {"name": "check-db-conns"}
docker compose exec postgres psql -U labuser -d labdb -c "SELECT count(*) as active_connections FROM pg_stat_activity WHERE datname = 'labdb';"
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Pool stats | `curl localhost:8000/pool/stats` |
| Current config | `curl localhost:8000/admin/config` |
| Change work time | `curl -X POST localhost:8000/admin/config -d '{"work_time_ms": 100}'` |
| Single query | `curl localhost:8000/api/query` |
| 50 concurrent | `for i in {1..50}; do curl localhost:8000/api/query & done; wait` |
| View traces | http://localhost:16686 |
| View dashboard | http://localhost:3001 (admin/admin) |
