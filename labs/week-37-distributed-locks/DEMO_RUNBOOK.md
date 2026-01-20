# Demo Runbook: Distributed Locks with Redlock

This runbook contains all commands for the lab demo. If you have the [Runme extension](https://runme.dev) installed in VS Code, you can run each command block directly with the play button.

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

### Verify lock service is responding

```bash {"name": "verify-service"}
echo "Lock Service Health:"
curl -s http://localhost:8000/health | jq
```

### Check Redis nodes status

```bash {"name": "redis-status"}
curl -s http://localhost:8000/admin/redis/status | jq
```

---

## Part 2: Basic Lock Operations

### Acquire a lock

```bash {"name": "acquire-lock"}
curl -s -X POST http://localhost:8000/lock/acquire \
  -H "Content-Type: application/json" \
  -d '{"resource": "demo-resource", "client_id": "demo-client"}' | jq
```

### Check lock status

```bash {"name": "check-lock-status"}
curl -s http://localhost:8000/lock/status/demo-resource | jq
```

### List active locks

```bash {"name": "list-active-locks"}
curl -s http://localhost:8000/locks/active | jq
```

### Release the lock

```bash {"name": "release-lock"}
# First get the lock_id from acquire response, then release
LOCK_ID=$(curl -s http://localhost:8000/locks/active | jq -r '.locks[0].lock_id // empty')
if [ -n "$LOCK_ID" ]; then
  curl -s -X POST http://localhost:8000/lock/release \
    -H "Content-Type: application/json" \
    -d "{\"resource\": \"demo-resource\", \"lock_id\": \"$LOCK_ID\"}" | jq
else
  echo "No active lock found"
fi
```

---

## Part 3: Observe Lock Contention

### View worker logs

```bash {"name": "worker-logs"}
docker compose logs --tail=20 lab37-worker-1 lab37-worker-2 lab37-worker-3
```

### Follow worker logs live

```bash {"name": "follow-workers", "background": true}
docker compose logs -f lab37-worker-1 lab37-worker-2 lab37-worker-3
```

### Check worker contention metrics

```bash {"name": "contention-metrics"}
curl -s http://localhost:8000/metrics | grep -E "lock_acquire_total|lock_contention"
```

---

## Part 4: Show Observability UIs

### Print UI URLs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Lock Service API:  http://localhost:8000"
echo "  Lock Service Docs: http://localhost:8000/docs"
echo "  Jaeger Tracing:    http://localhost:16686"
echo "  Grafana:           http://localhost:3001 (admin/admin)"
echo "  Prometheus:        http://localhost:9090"
```

---

## Part 5: Redis Failure Scenarios

### Check initial health (should show 3/3 nodes)

```bash {"name": "health-before-failure"}
echo "Health before failure:"
curl -s http://localhost:8000/health | jq
```

### Stop one Redis instance

```bash {"name": "stop-redis-1"}
docker compose stop lab37-redis-1
echo "Stopped Redis-1"
```

### Check health (should show 2/3 nodes, status: ok)

```bash {"name": "health-after-one-failure"}
echo "Health after stopping Redis-1:"
curl -s http://localhost:8000/health | jq
```

### Acquire lock with one Redis down (should succeed)

```bash {"name": "acquire-with-one-down"}
echo "Attempting to acquire lock with one Redis down..."
curl -s -X POST http://localhost:8000/lock/acquire \
  -H "Content-Type: application/json" \
  -d '{"resource": "fault-tolerant-resource", "client_id": "fault-test"}' | jq
```

### Stop second Redis instance

```bash {"name": "stop-redis-2"}
docker compose stop lab37-redis-2
echo "Stopped Redis-2"
```

### Check health (should show 1/3 nodes, status: degraded)

```bash {"name": "health-after-two-failures"}
echo "Health after stopping Redis-2:"
curl -s http://localhost:8000/health | jq
```

### Acquire lock with two Redis down (should fail)

```bash {"name": "acquire-with-two-down"}
echo "Attempting to acquire lock with two Redis down (should fail)..."
curl -s -X POST http://localhost:8000/lock/acquire \
  -H "Content-Type: application/json" \
  -d '{"resource": "no-quorum-resource", "client_id": "no-quorum-test"}' | jq
```

### Restore Redis instances

```bash {"name": "restore-redis"}
docker compose start lab37-redis-1 lab37-redis-2
echo "Restored Redis nodes"
sleep 3
curl -s http://localhost:8000/health | jq
```

---

## Part 6: Single Redis vs Redlock Comparison

### Check current mode

```bash {"name": "check-mode"}
curl -s http://localhost:8000/admin/mode | jq
```

### Switch to single Redis mode

```bash {"name": "switch-single-mode"}
curl -s -X POST "http://localhost:8000/admin/mode?mode=single" | jq
```

### Acquire lock in single mode

```bash {"name": "acquire-single-mode"}
curl -s -X POST http://localhost:8000/lock/acquire \
  -H "Content-Type: application/json" \
  -d '{"resource": "single-mode-resource"}' | jq
```

### Stop the primary Redis (causes total failure in single mode)

```bash {"name": "single-mode-failure"}
docker compose stop lab37-redis-1
echo "Stopped Redis-1 (primary for single mode)"
sleep 2
echo "Attempting to acquire lock (should fail completely)..."
curl -s -X POST http://localhost:8000/lock/acquire \
  -H "Content-Type: application/json" \
  -d '{"resource": "single-mode-fail"}' | jq
```

### Restore and switch back to Redlock

```bash {"name": "restore-redlock"}
docker compose start lab37-redis-1
sleep 2
curl -s -X POST "http://localhost:8000/admin/mode?mode=redlock" | jq
```

---

## Part 7: TTL Expiration Demo

### Acquire lock with short TTL

```bash {"name": "short-ttl-acquire"}
echo "Acquiring lock with 3-second TTL..."
curl -s -X POST http://localhost:8000/lock/acquire \
  -H "Content-Type: application/json" \
  -d '{"resource": "ttl-demo", "ttl_ms": 3000, "client_id": "original-client"}' | jq
```

### Check lock status immediately

```bash {"name": "check-ttl-immediate"}
curl -s http://localhost:8000/lock/status/ttl-demo | jq
```

### Wait for TTL to expire

```bash {"name": "wait-ttl"}
echo "Waiting 4 seconds for lock to expire..."
sleep 4
echo "Lock should be expired now"
```

### Check lock status after expiration

```bash {"name": "check-ttl-expired"}
curl -s http://localhost:8000/lock/status/ttl-demo | jq
```

### New client acquires the same resource

```bash {"name": "new-client-acquire"}
echo "New client acquiring the expired lock..."
curl -s -X POST http://localhost:8000/lock/acquire \
  -H "Content-Type: application/json" \
  -d '{"resource": "ttl-demo", "client_id": "new-client"}' | jq
```

---

## Part 8: Load Testing

### Run contention load test

```bash {"name": "run-load-test"}
docker compose run --rm lab37-k6 run /scripts/lock-contention.js
```

### Run Redis failure load test (run manually to stop Redis mid-test)

```bash {"name": "run-failure-test", "background": true}
docker compose run --rm lab37-k6 run /scripts/redis-failure.js
```

### Generate quick contention with curl

```bash {"name": "quick-contention"}
echo "Generating quick contention (10 parallel requests)..."
for i in {1..10}; do
  curl -s -X POST http://localhost:8000/lock/acquire \
    -H "Content-Type: application/json" \
    -d "{\"resource\": \"contention-test\", \"client_id\": \"client-$i\"}" &
done
wait
echo ""
echo "Check lock status:"
curl -s http://localhost:8000/lock/status/contention-test | jq
```

---

## Part 9: Tracing Demo

### Generate some traced requests

```bash {"name": "generate-traces"}
echo "Generating requests for tracing..."
for i in {1..5}; do
  curl -s -X POST http://localhost:8000/lock/acquire \
    -H "Content-Type: application/json" \
    -d "{\"resource\": \"trace-demo-$i\"}" > /dev/null
  sleep 0.5
done
echo "Traces generated. View at http://localhost:16686"
echo "Select 'lock-service' from the Service dropdown"
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

### Check lock service logs

```bash {"name": "logs-lock-service"}
docker compose logs lab37-lock-service --tail=50
```

### Check OTEL collector logs

```bash {"name": "logs-otel"}
docker compose logs lab37-otel-collector --tail=20
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart lock service

```bash {"name": "restart-lock-service"}
docker compose restart lab37-lock-service
```

### Connect to Redis directly

```bash {"name": "redis-cli"}
docker compose exec lab37-redis-1 redis-cli info server
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Acquire lock | `curl -X POST localhost:8000/lock/acquire -d '{"resource":"x"}'` |
| Release lock | `curl -X POST localhost:8000/lock/release -d '{"resource":"x","lock_id":"y"}'` |
| Check health | `curl localhost:8000/health` |
| Stop Redis-1 | `docker compose stop lab37-redis-1` |
| Switch to single mode | `curl -X POST localhost:8000/admin/mode?mode=single` |
| View traces | http://localhost:16686 |
| View metrics | http://localhost:3001 |
