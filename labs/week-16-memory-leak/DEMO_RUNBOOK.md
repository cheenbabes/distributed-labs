# Demo Runbook: Memory Leak Investigation

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

### Verify service is responding

```bash {"name": "verify-service"}
echo "Health check:"
curl -s http://localhost:8080/health | jq

echo ""
echo "Current leak status:"
curl -s http://localhost:8080/admin/leaks/status | jq
```

---

## Part 2: Establish Baseline

### Check initial memory usage

```bash {"name": "initial-memory"}
echo "Initial memory usage:"
curl -s http://localhost:8080/admin/memory | jq '.process'
```

### Generate baseline traffic

```bash {"name": "baseline-traffic"}
echo "Generating 100 requests..."
for i in {1..100}; do
  curl -s http://localhost:8080/api/data > /dev/null
  echo -n "."
done
echo ""
echo "Done."
```

### Verify memory is stable

```bash {"name": "verify-stable"}
echo "Memory after baseline traffic:"
curl -s http://localhost:8080/admin/memory | jq '.process'
echo ""
echo "Leak status (all should be disabled):"
curl -s http://localhost:8080/admin/leaks/status | jq '.leaks'
```

---

## Part 3: Open Observability UIs

### Show UI URLs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Leaky Service: http://localhost:8080"
echo "  Grafana:       http://localhost:3001 (admin/admin)"
echo "  Prometheus:    http://localhost:9090"
echo "  Jaeger:        http://localhost:16686"
```

---

## Part 4: Enable Memory Leak

### Enable the unbounded cache leak

```bash {"name": "enable-cache-leak"}
echo "Enabling unbounded cache leak..."
curl -s -X POST http://localhost:8080/admin/leaks/cache \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}' | jq
```

### Verify leak is enabled

```bash {"name": "verify-leak-enabled"}
curl -s http://localhost:8080/admin/leaks/status | jq '.leaks.cache'
```

---

## Part 5: Watch Memory Grow

### Generate traffic to trigger leak

```bash {"name": "leak-traffic-round1"}
echo "Generating 300 requests (round 1)..."
for i in {1..300}; do
  curl -s http://localhost:8080/api/data > /dev/null
  echo -n "."
done
echo ""
echo "Memory after round 1:"
curl -s http://localhost:8080/admin/leaks/status | jq
```

### More traffic

```bash {"name": "leak-traffic-round2"}
echo "Generating 300 more requests (round 2)..."
for i in {1..300}; do
  curl -s http://localhost:8080/api/data > /dev/null
  echo -n "."
done
echo ""
echo "Memory after round 2:"
curl -s http://localhost:8080/admin/leaks/status | jq
```

### Check memory growth

```bash {"name": "check-growth"}
echo "Current memory status:"
curl -s http://localhost:8080/admin/memory | jq '.process'
echo ""
echo "Leak details:"
curl -s http://localhost:8080/admin/leaks/status | jq '.leaks'
```

---

## Part 6: Identify Leak Source with tracemalloc

### Set baseline snapshot

```bash {"name": "set-baseline"}
echo "Setting memory baseline snapshot..."
curl -s http://localhost:8080/admin/memory/diff | jq
```

### Generate more traffic

```bash {"name": "traffic-for-diff"}
echo "Generating traffic for diff analysis..."
for i in {1..200}; do
  curl -s http://localhost:8080/api/data > /dev/null
done
echo "Done."
```

### Get memory diff

```bash {"name": "get-diff"}
echo "Memory allocations that grew:"
curl -s http://localhost:8080/admin/memory/diff | jq
```

### Get detailed snapshot

```bash {"name": "detailed-snapshot"}
echo "Top memory allocations by line:"
curl -s http://localhost:8080/admin/memory/snapshot | jq '.by_line[:10]'
```

---

## Part 7: Fix the Leak

### Clear leaked data

```bash {"name": "clear-leaks"}
echo "Clearing cached data..."
curl -s -X POST http://localhost:8080/admin/leaks/clear \
  -H "Content-Type: application/json" \
  -d '{"leak_type": "cache"}' | jq
```

### Trigger garbage collection

```bash {"name": "trigger-gc"}
echo "Triggering garbage collection..."
curl -s -X POST http://localhost:8080/admin/gc | jq
```

### Disable the leak

```bash {"name": "disable-leak"}
echo "Disabling cache leak..."
curl -s -X POST http://localhost:8080/admin/leaks/cache \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}' | jq
```

### Verify memory dropped

```bash {"name": "verify-fix"}
echo "Memory after fix:"
curl -s http://localhost:8080/admin/memory | jq '.process'
```

---

## Part 8: Verify Leak is Fixed

### Generate traffic and confirm stable memory

```bash {"name": "verify-stable-after-fix"}
echo "Generating 300 requests..."
for i in {1..300}; do
  curl -s http://localhost:8080/api/data > /dev/null
done

echo "Memory should be stable:"
curl -s http://localhost:8080/admin/memory | jq '.process'
```

---

## Part 9: Try Other Leak Types (Optional)

### Enable listener leak

```bash {"name": "enable-listener-leak"}
curl -s -X POST http://localhost:8080/admin/leaks/listeners \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}' | jq
```

### Enable history leak

```bash {"name": "enable-history-leak"}
curl -s -X POST http://localhost:8080/admin/leaks/history \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}' | jq
```

### Check all leak statuses

```bash {"name": "all-leak-status"}
curl -s http://localhost:8080/admin/leaks/status | jq
```

### Generate traffic to see combined effect

```bash {"name": "combined-leak-traffic"}
echo "Generating traffic with multiple leaks enabled..."
for i in {1..500}; do
  curl -s http://localhost:8080/api/data > /dev/null
  echo -n "."
done
echo ""
echo "Final status:"
curl -s http://localhost:8080/admin/leaks/status | jq
```

### Clear all leaks

```bash {"name": "clear-all-leaks"}
curl -s -X POST http://localhost:8080/admin/leaks/clear \
  -H "Content-Type: application/json" \
  -d '{"leak_type": "all"}' | jq

curl -s -X POST http://localhost:8080/admin/gc | jq

curl -s -X POST http://localhost:8080/admin/leaks/cache \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'

curl -s -X POST http://localhost:8080/admin/leaks/listeners \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'

curl -s -X POST http://localhost:8080/admin/leaks/history \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}'

echo "All leaks disabled."
```

---

## Part 10: Load Test (Aggressive)

### Run k6 load test

```bash {"name": "load-test"}
echo "Running 5-minute load test..."
docker compose run --rm lab16-k6 run /scripts/memory-stress.js
```

---

## Cleanup

### Stop all services

```bash {"name": "cleanup"}
docker compose down -v
echo "Lab cleaned up."
```

---

## Troubleshooting Commands

### Check service logs

```bash {"name": "logs-service"}
docker compose logs lab16-leaky-service --tail=50
```

### Check all logs

```bash {"name": "logs-all"}
docker compose logs --tail=30
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart service if OOM

```bash {"name": "restart-service"}
docker compose restart lab16-leaky-service
sleep 5
docker compose ps
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Enable cache leak | `curl -X POST localhost:8080/admin/leaks/cache -d '{"enabled":true}'` |
| Enable listener leak | `curl -X POST localhost:8080/admin/leaks/listeners -d '{"enabled":true}'` |
| Enable history leak | `curl -X POST localhost:8080/admin/leaks/history -d '{"enabled":true}'` |
| Check status | `curl localhost:8080/admin/leaks/status` |
| Check memory | `curl localhost:8080/admin/memory` |
| Clear leaks | `curl -X POST localhost:8080/admin/leaks/clear -d '{"leak_type":"all"}'` |
| Trigger GC | `curl -X POST localhost:8080/admin/gc` |
| Memory snapshot | `curl localhost:8080/admin/memory/snapshot` |
| Memory diff | `curl localhost:8080/admin/memory/diff` |
| View dashboard | http://localhost:3001 |
| View traces | http://localhost:16686 |
