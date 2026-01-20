# Demo Runbook: Timeout Budgets (Deadline Propagation)

This runbook contains all commands for demonstrating timeout budgets and deadline propagation. If you have the [Runme extension](https://runme.dev) installed in VS Code, you can run each command block directly with the play button.

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
echo "Frontend API:"
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
```

---

## Part 2: Show Observability URLs

### Display URLs for observability tools

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Frontend API: http://localhost:8000"
echo "  Jaeger:       http://localhost:16686"
echo "  Prometheus:   http://localhost:9090"
echo "  Grafana:      http://localhost:3001 (admin/admin)"
```

---

## Part 3: Normal Budget Flow

### Make a single request with default budget

```bash {"name": "single-request"}
echo "Request with default 1000ms budget:"
curl -s http://localhost:8000/api/process | jq
```

### Show budget consumption across chain

```bash {"name": "budget-chain"}
echo "Budget flows through the service chain:"
echo ""
result=$(curl -s http://localhost:8000/api/process)
echo "Frontend API:"
echo "  Initial: $(echo $result | jq -r '.budget.initial_ms')ms"
echo "  Elapsed: $(echo $result | jq -r '.budget.elapsed_ms')ms"
echo "  Remaining: $(echo $result | jq -r '.budget.remaining_ms')ms"
echo ""
echo "Service A:"
echo "  Received: $(echo $result | jq -r '.chain.budget.received_ms')ms"
echo "  Remaining: $(echo $result | jq -r '.chain.budget.remaining_ms')ms"
echo ""
echo "Service B:"
echo "  Received: $(echo $result | jq -r '.chain.chain.budget.received_ms')ms"
echo "  Remaining: $(echo $result | jq -r '.chain.chain.budget.remaining_ms')ms"
echo ""
echo "Service C:"
echo "  Received: $(echo $result | jq -r '.chain.chain.chain.budget.received_ms')ms"
echo "  Remaining: $(echo $result | jq -r '.chain.chain.chain.budget.remaining_ms')ms"
```

### Make multiple requests to generate traces

```bash {"name": "generate-traces"}
echo "Generating 10 requests for Jaeger traces..."
for i in {1..10}; do
  curl -s http://localhost:8000/api/process > /dev/null
  echo -n "."
done
echo ""
echo "Done. View traces at: http://localhost:16686"
```

---

## Part 4: Tight Budget Scenarios

### Request with tight budget (600ms)

```bash {"name": "tight-budget"}
echo "Request with 600ms budget (tight but should succeed):"
curl -s "http://localhost:8000/api/process?budget_ms=600" | jq
```

### Request with very tight budget (300ms)

```bash {"name": "very-tight-budget"}
echo "Request with 300ms budget (likely to exhaust):"
curl -s "http://localhost:8000/api/process?budget_ms=300" | jq
```

### Request with impossible budget (100ms)

```bash {"name": "impossible-budget"}
echo "Request with 100ms budget (will definitely exhaust):"
curl -s "http://localhost:8000/api/process?budget_ms=100" | jq
```

---

## Part 5: Early Termination Demo

### Enable slow mode on Service C

```bash {"name": "enable-slow-mode"}
echo "Enabling slow mode (800ms extra latency) on Service C..."
curl -s -X POST "http://localhost:8003/admin/slow-mode?enabled=true&ms=800" | jq
```

### Verify slow mode is enabled

```bash {"name": "verify-slow-mode"}
curl -s http://localhost:8003/admin/config | jq
```

### Request with default budget (should exhaust)

```bash {"name": "request-slow-mode"}
echo "Request with 1000ms budget (Service C now takes ~1000ms):"
curl -s http://localhost:8000/api/process | jq
```

### Show early termination in action

```bash {"name": "early-termination"}
echo "Watch where budget exhausts:"
echo ""
curl -s http://localhost:8000/api/process | jq '.detail // .'
```

### Disable slow mode

```bash {"name": "disable-slow-mode"}
echo "Disabling slow mode on Service C..."
curl -s -X POST "http://localhost:8003/admin/slow-mode?enabled=false&ms=0" | jq
```

---

## Part 6: Compare Budget vs Fixed Modes

### Show current config

```bash {"name": "show-config"}
echo "Current configuration:"
echo ""
echo "Frontend API:"
curl -s http://localhost:8000/admin/config | jq
echo ""
echo "Service A:"
curl -s http://localhost:8001/admin/config | jq
echo ""
echo "Service C:"
curl -s http://localhost:8003/admin/config | jq
```

### Enable slow mode for comparison

```bash {"name": "setup-comparison"}
echo "Setting up comparison scenario..."
curl -s -X POST "http://localhost:8003/admin/slow-mode?enabled=true&ms=800"
echo "Slow mode enabled (800ms extra latency)"
```

### Test with budget propagation mode

```bash {"name": "budget-mode-test"}
echo "Testing with BUDGET PROPAGATION mode..."
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "budget", "default_budget_ms": 1000, "processing_time_ms": 50}' > /dev/null

echo "Making request with 500ms budget:"
start=$(date +%s%N)
result=$(curl -s "http://localhost:8000/api/process?budget_ms=500")
end=$(date +%s%N)
elapsed=$(( (end - start) / 1000000 ))

echo "Result: $(echo $result | jq -r '.detail.message // .budget.remaining_ms + "ms remaining"')"
echo "Actual time: ${elapsed}ms"
echo ""
echo "Note: Request terminated EARLY because budget was propagated"
```

### Test with fixed timeout mode

```bash {"name": "fixed-mode-test"}
echo "Testing with FIXED TIMEOUT mode (no propagation)..."
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "fixed", "default_budget_ms": 1000, "processing_time_ms": 50}' > /dev/null

# Also set downstream services to ignore budget
curl -s -X POST http://localhost:8001/admin/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "ignore", "processing_time_ms": 100}' > /dev/null
curl -s -X POST http://localhost:8002/admin/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "ignore", "processing_time_ms": 150}' > /dev/null
curl -s -X POST http://localhost:8003/admin/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "ignore", "processing_time_ms": 200, "slow_mode": true, "slow_mode_ms": 800}' > /dev/null

echo "Making request with 500ms client timeout:"
start=$(date +%s%N)
result=$(curl -s --max-time 0.5 "http://localhost:8000/api/process" 2>&1 || echo '{"error": "client timeout"}')
end=$(date +%s%N)
elapsed=$(( (end - start) / 1000000 ))

echo "Result: Client timed out after ${elapsed}ms"
echo ""
echo "But services continued processing! Check logs:"
sleep 2
docker compose logs service-c --tail=3 | grep -i "completed\|processed"
```

### Reset to budget propagation mode

```bash {"name": "reset-modes"}
echo "Resetting all services to budget propagation mode..."
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "budget", "default_budget_ms": 1000, "processing_time_ms": 50}' > /dev/null

curl -s -X POST http://localhost:8001/admin/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "propagate", "processing_time_ms": 100}' > /dev/null

curl -s -X POST http://localhost:8002/admin/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "propagate", "processing_time_ms": 150}' > /dev/null

curl -s -X POST http://localhost:8003/admin/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "propagate", "processing_time_ms": 200, "slow_mode": false, "slow_mode_ms": 0}' > /dev/null

echo "All services reset to budget propagation mode"
echo "Slow mode disabled"
```

---

## Part 7: Load Testing

### Run basic load test

```bash {"name": "load-test-basic"}
docker compose run --rm k6 run /scripts/basic.js
```

### Run budget stress test

```bash {"name": "load-test-stress"}
docker compose run --rm k6 run /scripts/budget-stress.js
```

### Run mode comparison test

```bash {"name": "load-test-compare"}
docker compose run --rm k6 run /scripts/compare-modes.js
```

---

## Part 8: Observe Metrics

### Check early termination metrics

```bash {"name": "check-metrics"}
echo "Early Termination Metrics:"
echo ""
curl -s http://localhost:8000/metrics | grep -E "early_termination|budget_exhausted|work_skipped" | head -20
echo ""
curl -s http://localhost:8001/metrics | grep -E "early_termination|budget_exhausted|work_skipped" | head -10
```

### Query Prometheus for budget data

```bash {"name": "prometheus-query"}
echo "Budget exhaustion rate (last 5 minutes):"
curl -s "http://localhost:9090/api/v1/query?query=sum(rate(timeout_budget_exhausted_total[5m]))" | jq '.data.result'
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

```bash {"name": "logs-frontend"}
docker compose logs frontend-api --tail=30
```

```bash {"name": "logs-service-c"}
docker compose logs service-c --tail=30
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart all services

```bash {"name": "restart-all"}
docker compose restart
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Enable slow mode | `curl -X POST "localhost:8003/admin/slow-mode?enabled=true&ms=800"` |
| Disable slow mode | `curl -X POST "localhost:8003/admin/slow-mode?enabled=false"` |
| Set budget mode | `curl -X POST localhost:8000/admin/config -d '{"mode":"budget"}'` |
| Set fixed mode | `curl -X POST localhost:8000/admin/config -d '{"mode":"fixed"}'` |
| Custom budget | `curl "localhost:8000/api/process?budget_ms=500"` |
| View traces | http://localhost:16686 |
| View dashboard | http://localhost:3001 |
