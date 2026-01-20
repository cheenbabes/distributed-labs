# Demo Runbook: Zuul Load Shedding

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
echo "Backend:"
curl -s http://localhost:8001/health | jq
```

---

## Part 2: Show URLs

### Display all relevant URLs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Gateway API:    http://localhost:8000"
echo "  Backend API:    http://localhost:8001"
echo "  Grafana:        http://localhost:3001 (admin/admin)"
echo "  Prometheus:     http://localhost:9090"
echo "  Jaeger:         http://localhost:16686"
```

---

## Part 3: Establish Baseline (No Load Shedding)

### Check current configuration

```bash {"name": "check-config"}
echo "Gateway config (load shedding should be disabled):"
curl -s http://localhost:8000/config | jq
echo ""
echo "Backend config:"
curl -s http://localhost:8001/config | jq
```

### Make a single request

```bash {"name": "single-request"}
curl -s http://localhost:8000/api/process | jq
```

### Show backend capacity

```bash {"name": "backend-stats"}
curl -s http://localhost:8001/stats | jq
```

### Run baseline load test (backend gets overwhelmed)

```bash {"name": "baseline-load-test"}
echo "Running baseline load test - backend will be overwhelmed..."
echo "Watch Grafana for backend degradation!"
docker compose run --rm lab26-k6 run /scripts/basic.js
```

---

## Part 4: Enable Concurrency Limit

### Enable basic load shedding with concurrency limit

```bash {"name": "enable-concurrency-limit"}
curl -s -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "max_concurrent": 25,
    "concurrency_limit_enabled": true,
    "latency_shedding_enabled": false,
    "error_rate_shedding_enabled": false,
    "adaptive_enabled": false,
    "priority_enabled": false
  }' | jq
```

### Verify the new config

```bash {"name": "verify-concurrency-config"}
curl -s http://localhost:8000/config | jq
```

### Run load test with concurrency limit

```bash {"name": "concurrency-load-test"}
echo "Running load test with concurrency limit enabled..."
echo "Watch for shed requests in Grafana!"
docker compose run --rm lab26-k6 run /scripts/basic.js
```

### Check gateway stats after test

```bash {"name": "gateway-stats-after-concurrency"}
curl -s http://localhost:8000/stats | jq
```

---

## Part 5: Enable Latency-Based Shedding

### Switch to latency-based shedding

```bash {"name": "enable-latency-shedding"}
curl -s -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "latency_shedding_enabled": true,
    "latency_threshold_ms": 300,
    "concurrency_limit_enabled": false,
    "adaptive_enabled": false
  }' | jq
```

### Run load test with latency-based shedding

```bash {"name": "latency-load-test"}
echo "Running load test with latency-based shedding..."
echo "Watch for high_latency shed events in Grafana!"
docker compose run --rm lab26-k6 run /scripts/basic.js
```

---

## Part 6: Enable Adaptive Concurrency

### Enable adaptive (Vegas-style) concurrency

```bash {"name": "enable-adaptive"}
curl -s -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "adaptive_enabled": true,
    "concurrency_limit_enabled": true,
    "max_concurrent": 100,
    "latency_shedding_enabled": false
  }' | jq
```

### Run adaptive test with variable load

```bash {"name": "adaptive-load-test"}
echo "Running adaptive test with variable load..."
echo "Watch adaptive_concurrency_limit change in Grafana!"
docker compose run --rm lab26-k6 run /scripts/adaptive-test.js
```

---

## Part 7: Priority-Based Shedding

### Enable priority handling

```bash {"name": "enable-priority"}
curl -s -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "priority_enabled": true,
    "concurrency_limit_enabled": true,
    "max_concurrent": 20,
    "adaptive_enabled": false
  }' | jq
```

### Test normal vs VIP request

```bash {"name": "test-priority-manual"}
echo "Normal request:"
curl -s -w "\nStatus: %{http_code}\n" http://localhost:8000/api/process | head -5
echo ""
echo "VIP request:"
curl -s -w "\nStatus: %{http_code}\n" \
  -H "X-Priority: vip" \
  http://localhost:8000/api/process | head -5
```

### Run priority load test

```bash {"name": "priority-load-test"}
echo "Running priority test (20% VIP, 80% normal traffic)..."
echo "VIP should have higher success rate!"
docker compose run --rm lab26-k6 run /scripts/priority-test.js
```

---

## Part 8: Compare Backend Health

### Disable load shedding and check backend health under load

```bash {"name": "disable-shedding"}
curl -s -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}' | jq
```

### Generate load and watch backend stats

```bash {"name": "watch-backend-no-shedding"}
echo "Starting load test without shedding..."
echo "Watch backend stats - load_factor will exceed 1.0!"
# Run in background
docker compose run --rm lab26-k6 run /scripts/basic.js &
K6_PID=$!
# Monitor backend
for i in {1..10}; do
  sleep 5
  echo "--- Backend stats at $(date +%T) ---"
  curl -s http://localhost:8001/stats | jq
done
wait $K6_PID 2>/dev/null || true
```

### Enable shedding and compare

```bash {"name": "enable-and-compare"}
curl -s -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "concurrency_limit_enabled": true,
    "max_concurrent": 25
  }' | jq

echo "Starting load test WITH shedding..."
docker compose run --rm lab26-k6 run /scripts/basic.js &
K6_PID=$!
for i in {1..10}; do
  sleep 5
  echo "--- Backend stats at $(date +%T) ---"
  curl -s http://localhost:8001/stats | jq
done
wait $K6_PID 2>/dev/null || true
```

---

## Part 9: Inject Backend Issues

### Inject additional latency into backend

```bash {"name": "inject-backend-latency"}
curl -s -X POST http://localhost:8001/config \
  -H "Content-Type: application/json" \
  -d '{
    "inject_latency": true,
    "injected_latency_ms": 200
  }' | jq
```

### Enable latency-based shedding to respond

```bash {"name": "respond-to-latency"}
curl -s -X POST http://localhost:8000/config \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "latency_shedding_enabled": true,
    "latency_threshold_ms": 400
  }' | jq
```

### Test a few requests

```bash {"name": "test-with-injected-latency"}
for i in {1..5}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/api/process)
  echo "Request $i: ${time}s"
done
```

### Remove injected latency

```bash {"name": "remove-injected-latency"}
curl -s -X POST http://localhost:8001/config \
  -H "Content-Type: application/json" \
  -d '{
    "inject_latency": false
  }' | jq
```

---

## Cleanup

### Stop all services

```bash {"name": "cleanup"}
docker compose down -v
echo "Lab cleaned up"
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Enable shedding | `curl -X POST localhost:8000/config -d '{"enabled":true}'` |
| Disable shedding | `curl -X POST localhost:8000/config -d '{"enabled":false}'` |
| Check config | `curl localhost:8000/config` |
| Check stats | `curl localhost:8000/stats` |
| VIP request | `curl -H "X-Priority: vip" localhost:8000/api/process` |
| View dashboard | http://localhost:3001 |
| View traces | http://localhost:16686 |

---

## Key Metrics to Watch in Grafana

1. **Request Rate (Success vs Shed)** - Shows how many requests succeed vs get rejected
2. **Shed Requests by Reason** - Shows why requests are being shed
3. **Backend Capacity Utilization** - Should stay below 100% with shedding enabled
4. **Gateway Latency Percentiles** - p99 should be controlled with shedding
5. **Adaptive Concurrency Limit** - Changes over time with adaptive enabled
6. **Priority Traffic** - VIP vs Normal success rates
