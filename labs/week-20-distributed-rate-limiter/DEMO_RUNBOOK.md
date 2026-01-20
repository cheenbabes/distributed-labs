# Demo Runbook: Distributed Rate Limiter

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
echo "=== Load Balancer ==="
curl -s http://localhost:8080/health | jq
echo ""
echo "=== API-1 ==="
curl -s http://localhost:8001/health | jq
echo ""
echo "=== API-2 ==="
curl -s http://localhost:8002/health | jq
echo ""
echo "=== API-3 ==="
curl -s http://localhost:8003/health | jq
echo ""
echo "=== Redis ==="
docker compose exec redis redis-cli ping
```

---

## Part 2: Open Observability UIs

### Show URLs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Load Balancer: http://localhost:8080"
echo "  Jaeger:        http://localhost:16686"
echo "  Grafana:       http://localhost:3001 (admin/admin)"
echo "  Prometheus:    http://localhost:9090"
echo ""
echo "Direct API access:"
echo "  API-1:         http://localhost:8001"
echo "  API-2:         http://localhost:8002"
echo "  API-3:         http://localhost:8003"
```

---

## Part 3: Check Current Configuration

### View rate limiter configuration

```bash {"name": "check-config"}
echo "Current configuration:"
curl -s http://localhost:8080/admin/config | jq
```

---

## Part 4: Test Local Mode (Broken Distributed Rate Limiting)

### Switch to local mode (no shared state)

```bash {"name": "switch-local-mode"}
for port in 8001 8002 8003; do
  echo "Configuring instance on port $port..."
  curl -s -X POST "http://localhost:$port/admin/config" \
    -H "Content-Type: application/json" \
    -d '{"use_redis": false, "use_lua_script": false}'
done
echo ""
echo "All instances switched to local mode"
```

### Send requests and observe distribution

```bash {"name": "test-local-mode"}
echo "Sending 30 requests through load balancer..."
for i in {1..30}; do
  response=$(curl -s -H "X-API-Key: test-client" http://localhost:8080/api/request)
  instance=$(echo $response | jq -r '.instance')
  mode=$(echo $response | jq -r '.mode')
  remaining=$(echo $response | jq -r '.remaining')
  echo "Request $i: $instance ($mode) - remaining: $remaining"
done
```

### Check stats on each instance

```bash {"name": "check-local-stats"}
echo "=== Local Stats (each instance has its own counter) ==="
for port in 8001 8002 8003; do
  echo ""
  echo "Instance on port $port:"
  curl -s "http://localhost:$port/admin/stats" | jq '{instance: .instance, mode: .mode, local_clients: .local_clients}'
done
```

---

## Part 5: Enable Redis Shared State (Atomic Mode)

### Switch to Redis atomic mode

```bash {"name": "switch-atomic-mode"}
for port in 8001 8002 8003; do
  echo "Configuring instance on port $port..."
  curl -s -X POST "http://localhost:$port/admin/config" \
    -H "Content-Type: application/json" \
    -d '{"use_redis": true, "use_lua_script": true}'
done
echo ""
echo "All instances switched to Redis atomic mode"
```

### Reset rate limits

```bash {"name": "reset-limits"}
curl -s -X POST http://localhost:8080/admin/reset | jq
```

### Test rate limiting with shared state

```bash {"name": "test-atomic-mode"}
echo "Sending 150 requests (limit is 100)..."
allowed=0
rejected=0
for i in {1..150}; do
  response=$(curl -s -H "X-API-Key: test-client" http://localhost:8080/api/request)
  status=$(echo $response | jq -r '.status')
  if [ "$status" == "ok" ]; then
    allowed=$((allowed + 1))
  else
    rejected=$((rejected + 1))
  fi
  printf "\rProgress: $i/150 (Allowed: $allowed, Rejected: $rejected)"
done
echo ""
echo ""
echo "=== Final Results ==="
echo "Allowed: $allowed"
echo "Rejected: $rejected"
echo "Expected: ~100 allowed, ~50 rejected"
```

### Check Redis stats

```bash {"name": "check-redis-stats"}
curl -s http://localhost:8080/admin/stats | jq
```

---

## Part 6: Demonstrate Race Conditions (Naive Mode)

### Switch to Redis naive mode (non-atomic)

```bash {"name": "switch-naive-mode"}
for port in 8001 8002 8003; do
  curl -s -X POST "http://localhost:$port/admin/config" \
    -H "Content-Type: application/json" \
    -d '{"use_redis": true, "use_lua_script": false}'
done
echo "Switched to Redis naive mode (non-atomic)"
```

### Reset rate limits

```bash {"name": "reset-before-naive-test"}
curl -s -X POST http://localhost:8080/admin/reset | jq
```

### Run high concurrency test (shows race conditions)

```bash {"name": "run-naive-load-test"}
echo "Running high-concurrency load test in naive mode..."
docker compose run --rm k6 run /scripts/high-concurrency.js
```

### Check for race conditions

```bash {"name": "check-race-conditions"}
echo "=== Race Conditions Detected ==="
curl -s 'http://localhost:9090/api/v1/query?query=sum(race_conditions_detected_total)' | jq '.data.result[0].value[1]'
```

---

## Part 7: Fix with Atomic Lua Scripts

### Switch back to atomic mode

```bash {"name": "switch-back-atomic"}
for port in 8001 8002 8003; do
  curl -s -X POST "http://localhost:$port/admin/config" \
    -H "Content-Type: application/json" \
    -d '{"use_redis": true, "use_lua_script": true}'
done
echo "Switched back to Redis atomic mode"
```

### Reset and run same test

```bash {"name": "reset-before-atomic-test"}
curl -s -X POST http://localhost:8080/admin/reset | jq
```

```bash {"name": "run-atomic-load-test"}
echo "Running high-concurrency load test in atomic mode..."
docker compose run --rm k6 run /scripts/high-concurrency.js
```

---

## Part 8: Multi-Client Testing

### Test multiple clients

```bash {"name": "test-multi-client"}
curl -s -X POST http://localhost:8080/admin/reset > /dev/null

echo "Testing 3 different clients..."
for client in client-A client-B client-C; do
  echo ""
  echo "=== $client ==="
  allowed=0
  for i in {1..120}; do
    response=$(curl -s -H "X-API-Key: $client" http://localhost:8080/api/request)
    status=$(echo $response | jq -r '.status')
    if [ "$status" == "ok" ]; then
      allowed=$((allowed + 1))
    fi
  done
  echo "$client: $allowed/120 requests allowed (limit: 100)"
done
```

---

## Part 9: Load Testing

### Run basic load test

```bash {"name": "run-basic-load-test"}
docker compose run --rm k6 run /scripts/basic.js
```

### Run multi-client load test

```bash {"name": "run-multi-client-load-test"}
docker compose run --rm k6 run /scripts/multi-client.js
```

### Run comparison test

```bash {"name": "run-compare-modes-test"}
curl -s -X POST http://localhost:8080/admin/reset > /dev/null
docker compose run --rm k6 run /scripts/compare-modes.js
```

---

## Part 10: View Metrics

### Check Prometheus metrics

```bash {"name": "check-prometheus-metrics"}
echo "=== Rate Limit Allowed (total) ==="
curl -s 'http://localhost:9090/api/v1/query?query=sum(rate_limit_allowed_total)' | jq '.data.result[0].value[1]'

echo ""
echo "=== Rate Limit Rejected (total) ==="
curl -s 'http://localhost:9090/api/v1/query?query=sum(rate_limit_rejected_total)' | jq '.data.result[0].value[1]'

echo ""
echo "=== Redis Operations ==="
curl -s 'http://localhost:9090/api/v1/query?query=sum(redis_operations_total)by(operation)' | jq '.data.result[] | {operation: .metric.operation, count: .value[1]}'
```

### Check Redis info

```bash {"name": "check-redis-info"}
curl -s http://localhost:8080/admin/redis-info | jq
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

### Check API logs

```bash {"name": "logs-api"}
docker compose logs api-1 api-2 api-3 --tail=50
```

### Check Redis logs

```bash {"name": "logs-redis"}
docker compose logs redis --tail=50
```

### Check Redis keys

```bash {"name": "redis-keys"}
docker compose exec redis redis-cli keys "ratelimit:*"
```

### Check specific rate limit

```bash {"name": "redis-check-limit"}
docker compose exec redis redis-cli zcard "ratelimit:atomic:test-client"
```

### Restart services

```bash {"name": "restart-services"}
docker compose restart api-1 api-2 api-3
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Switch to local mode | `curl -X POST localhost:8080/admin/config -d '{"use_redis":false,"use_lua_script":false}'` |
| Switch to atomic mode | `curl -X POST localhost:8080/admin/config -d '{"use_redis":true,"use_lua_script":true}'` |
| Switch to naive mode | `curl -X POST localhost:8080/admin/config -d '{"use_redis":true,"use_lua_script":false}'` |
| Reset rate limits | `curl -X POST localhost:8080/admin/reset` |
| Check config | `curl localhost:8080/admin/config` |
| Check stats | `curl localhost:8080/admin/stats` |
| View Grafana | http://localhost:3001 (admin/admin) |
| View Jaeger | http://localhost:16686 |
