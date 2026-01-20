# Demo Runbook: Finding the Slow Service

This runbook contains all commands for the video demo. If you have the [Runme extension](https://runme.dev) installed in VS Code, you can run each command block directly with the play button.

---

## Pre-Demo Setup

### Check Docker is running

```bash {"name": "check-docker"}
docker info > /dev/null 2>&1 && echo "✓ Docker is running" || echo "✗ Docker is not running"
```

### Clean any previous lab state

```bash {"name": "clean-previous"}
docker compose down -v 2>/dev/null || true
docker system prune -f
```

---

## Part 1: Start the Lab

### Build and start all services

```bash {"name": "start-lab", "background": true}
docker compose up --build -d
```

### Wait for services to be healthy

```bash {"name": "wait-healthy"}
echo "Waiting for services to be healthy..."
sleep 10
docker compose ps
```

### Verify all services are responding

```bash {"name": "verify-services"}
echo "Gateway:"
curl -s http://localhost:8000/health | jq
echo ""
echo "Service A:"
curl -s http://localhost:8001/health | jq
echo ""
echo "Service B:"
curl -s http://localhost:8002/health | jq
echo ""
echo "Service C:"
curl -s http://localhost:8003/health | jq
echo ""
echo "Service D:"
curl -s http://localhost:8004/health | jq
```

---

## Part 2: Establish Baseline

### Make a single request and show response

```bash {"name": "single-request"}
curl -s http://localhost:8000/api/process | jq
```

### Make 5 requests and show timing

```bash {"name": "baseline-timing"}
echo "Baseline timing (5 requests):"
for i in {1..5}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/api/process)
  echo "Request $i: ${time}s"
done
```

### Show average baseline

```bash {"name": "baseline-average"}
total=0
for i in {1..10}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/api/process)
  total=$(echo "$total + $time" | bc)
done
avg=$(echo "scale=3; $total / 10" | bc)
echo "Average baseline latency: ${avg}s"
```

---

## Part 3: Open Observability UIs

### Open Jaeger (run this to print URLs)

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Jaeger:     http://localhost:16686"
echo "  Grafana:    http://localhost:3001 (admin/admin)"
echo "  Prometheus: http://localhost:9090"
echo "  Gateway:    http://localhost:8000"
```

---

## Part 4: Inject Latency

### Inject 500ms latency into Service D

```bash {"name": "inject-latency"}
curl -s -X POST http://localhost:8004/admin/latency \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "ms": 500}' | jq
```

### Verify latency is injected

```bash {"name": "verify-injection"}
curl -s http://localhost:8004/admin/latency | jq
```

---

## Part 5: Measure Impact

### Single slow request

```bash {"name": "slow-request"}
echo "Making request with injected latency..."
time curl -s http://localhost:8000/api/process | jq
```

### Compare timing (5 requests)

```bash {"name": "slow-timing"}
echo "Timing with injected latency (5 requests):"
for i in {1..5}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/api/process)
  echo "Request $i: ${time}s"
done
```

### Side-by-side comparison

```bash {"name": "comparison"}
echo "=== COMPARISON ==="
echo ""

# Disable latency
curl -s -X POST http://localhost:8004/admin/latency \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}' > /dev/null

echo "WITHOUT injected latency:"
for i in {1..3}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/api/process)
  echo "  Request $i: ${time}s"
done

# Enable latency
curl -s -X POST http://localhost:8004/admin/latency \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "ms": 500}' > /dev/null

echo ""
echo "WITH 500ms injected latency:"
for i in {1..3}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/api/process)
  echo "  Request $i: ${time}s"
done
```

---

## Part 6: Find the Bottleneck

### Generate some traces to analyze

```bash {"name": "generate-traces"}
echo "Generating 20 traces for analysis..."
for i in {1..20}; do
  curl -s http://localhost:8000/api/process > /dev/null
  echo -n "."
done
echo ""
echo "Done. Check Jaeger: http://localhost:16686"
```

---

## Part 7: Fix the Problem

### Disable injected latency

```bash {"name": "fix-latency"}
curl -s -X POST http://localhost:8004/admin/latency \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}' | jq
```

### Verify fix

```bash {"name": "verify-fix"}
echo "Verifying fix (3 requests):"
for i in {1..3}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/api/process)
  echo "Request $i: ${time}s"
done
echo ""
echo "✓ Latency should be back to ~0.15s"
```

---

## Part 8: Load Testing (Optional)

### Run k6 load test

```bash {"name": "load-test", "background": true}
docker compose run --rm k6 run /scripts/basic.js
```

### Quick load test inline

```bash {"name": "quick-load"}
echo "Running 30-second load test..."
end=$((SECONDS+30))
count=0
while [ $SECONDS -lt $end ]; do
  curl -s http://localhost:8000/api/process > /dev/null &
  count=$((count+1))
done
wait
echo "Completed $count requests in 30 seconds"
```

---

## Cleanup

### Stop all services

```bash {"name": "cleanup"}
docker compose down -v
echo "✓ Lab cleaned up"
```

---

## Troubleshooting Commands

### Check service logs

```bash {"name": "logs-all"}
docker compose logs --tail=50
```

### Check specific service logs

```bash {"name": "logs-service-d"}
docker compose logs service-d --tail=50
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart a service

```bash {"name": "restart-service-d"}
docker compose restart service-d
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Inject latency | `curl -X POST localhost:8004/admin/latency -d '{"enabled":true,"ms":500}'` |
| Remove latency | `curl -X POST localhost:8004/admin/latency -d '{"enabled":false}'` |
| Check timing | `curl -w "%{time_total}s" localhost:8000/api/process` |
| View traces | http://localhost:16686 |
