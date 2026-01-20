# Demo Runbook: Speculative Execution (Hedge Requests)

This runbook contains all commands for demonstrating hedge requests. If you have the [Runme extension](https://runme.dev) installed in VS Code, you can run each command block directly with the play button.

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
sleep 15
docker compose ps
```

### Verify all services are responding

```bash {"name": "verify-services"}
echo "Frontend:"
curl -s http://localhost:8000/health | jq
echo ""
echo "Hedger:"
curl -s http://localhost:8080/health | jq
echo ""
echo "Backend-1:"
curl -s http://localhost:8001/health | jq
echo ""
echo "Backend-2:"
curl -s http://localhost:8002/health | jq
echo ""
echo "Backend-3:"
curl -s http://localhost:8003/health | jq
```

---

## Part 2: Baseline Without Hedging

### Disable hedging

```bash {"name": "disable-hedging"}
curl -s -X POST http://localhost:8080/admin/config \
  -H "Content-Type: application/json" \
  -d '{"hedge_enabled": false}' | jq
```

### Reset statistics

```bash {"name": "reset-stats-baseline"}
curl -s -X POST http://localhost:8080/admin/reset-stats | jq
```

### Make requests without hedging (show tail latency)

```bash {"name": "baseline-requests"}
echo "=== Latency WITHOUT hedging ==="
echo ""
for i in {1..20}; do
  result=$(curl -s http://localhost:8000/api/process)
  latency=$(echo $result | jq -r '.downstream_result.actual_latency_ms')
  printf "Request %2d: %6.2f ms\n" $i $latency
done
```

### Show that some requests are slow

```bash {"name": "baseline-timing"}
echo "Making 10 requests and timing them..."
for i in {1..10}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/api/process)
  ms=$(echo "$time * 1000" | bc)
  printf "Request %2d: %6.0f ms\n" $i $ms
done
```

---

## Part 3: Enable Hedge Requests

### Enable hedging with 50ms threshold

```bash {"name": "enable-hedging"}
curl -s -X POST http://localhost:8080/admin/config \
  -H "Content-Type: application/json" \
  -d '{"hedge_enabled": true, "hedge_delay_ms": 50}' | jq
```

### Reset statistics

```bash {"name": "reset-stats-hedged"}
curl -s -X POST http://localhost:8080/admin/reset-stats | jq
```

### Make requests with hedging (show improvement)

```bash {"name": "hedged-requests"}
echo "=== Latency WITH hedging (50ms threshold) ==="
echo ""
for i in {1..20}; do
  result=$(curl -s http://localhost:8000/api/process)
  actual=$(echo $result | jq -r '.downstream_result.actual_latency_ms')
  primary=$(echo $result | jq -r '.downstream_result.primary_latency_ms')
  improvement=$(echo $result | jq -r '.downstream_result.latency_improvement_ms')
  result_type=$(echo $result | jq -r '.downstream_result.result_type')
  if [ "$result_type" = "hedge_won" ]; then
    printf "Request %2d: %6.2f ms (was %6.2f ms, saved %5.2f ms) [HEDGE WON]\n" $i $actual $primary $improvement
  else
    printf "Request %2d: %6.2f ms [PRIMARY WON]\n" $i $actual
  fi
done
```

### Check hedger statistics

```bash {"name": "hedger-stats"}
curl -s http://localhost:8080/admin/config | jq
```

---

## Part 4: Open Observability UIs

### Show URLs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Jaeger:     http://localhost:16686"
echo "  Grafana:    http://localhost:3001 (admin/admin)"
echo "  Prometheus: http://localhost:9090"
echo "  Frontend:   http://localhost:8000"
echo "  Hedger:     http://localhost:8080"
```

---

## Part 5: Side-by-Side Comparison

### Run built-in comparison

```bash {"name": "run-comparison"}
echo "Running comparison (30 requests each mode)..."
curl -s "http://localhost:8000/api/compare?iterations=30" | jq
```

### Show detailed comparison

```bash {"name": "detailed-comparison"}
echo "=== WITHOUT HEDGING ==="
curl -s -X POST http://localhost:8080/admin/config \
  -H "Content-Type: application/json" \
  -d '{"hedge_enabled": false}' > /dev/null

for i in {1..5}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/api/process)
  ms=$(echo "$time * 1000" | bc)
  printf "  Request %d: %6.0f ms\n" $i $ms
done

echo ""
echo "=== WITH HEDGING (50ms) ==="
curl -s -X POST http://localhost:8080/admin/config \
  -H "Content-Type: application/json" \
  -d '{"hedge_enabled": true, "hedge_delay_ms": 50}' > /dev/null

for i in {1..5}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/api/process)
  ms=$(echo "$time * 1000" | bc)
  printf "  Request %d: %6.0f ms\n" $i $ms
done
```

---

## Part 6: Tune Hedge Threshold

### Very aggressive hedging (10ms)

```bash {"name": "aggressive-hedging"}
echo "=== AGGRESSIVE HEDGING (10ms threshold) ==="
curl -s -X POST http://localhost:8080/admin/config \
  -H "Content-Type: application/json" \
  -d '{"hedge_delay_ms": 10}' | jq '.hedge_delay_ms'

curl -s -X POST http://localhost:8080/admin/reset-stats > /dev/null

for i in {1..20}; do
  curl -s http://localhost:8000/api/process > /dev/null
done

echo "Statistics with 10ms threshold:"
curl -s http://localhost:8080/admin/config | jq '.stats'
```

### Conservative hedging (200ms)

```bash {"name": "conservative-hedging"}
echo "=== CONSERVATIVE HEDGING (200ms threshold) ==="
curl -s -X POST http://localhost:8080/admin/config \
  -H "Content-Type: application/json" \
  -d '{"hedge_delay_ms": 200}' | jq '.hedge_delay_ms'

curl -s -X POST http://localhost:8080/admin/reset-stats > /dev/null

for i in {1..20}; do
  curl -s http://localhost:8000/api/process > /dev/null
done

echo "Statistics with 200ms threshold:"
curl -s http://localhost:8080/admin/config | jq '.stats'
```

### Optimal hedging (50ms - around P50-P75)

```bash {"name": "optimal-hedging"}
echo "=== OPTIMAL HEDGING (50ms threshold) ==="
curl -s -X POST http://localhost:8080/admin/config \
  -H "Content-Type: application/json" \
  -d '{"hedge_delay_ms": 50}' | jq '.hedge_delay_ms'

curl -s -X POST http://localhost:8080/admin/reset-stats > /dev/null

for i in {1..20}; do
  curl -s http://localhost:8000/api/process > /dev/null
done

echo "Statistics with 50ms threshold:"
curl -s http://localhost:8080/admin/config | jq '.stats'
```

---

## Part 7: Generate Load for Grafana

### Generate traffic for visualization

```bash {"name": "generate-traffic"}
echo "Generating traffic for Grafana visualization..."
echo "Open Grafana at http://localhost:3001 (admin/admin)"
echo "Select dashboard: 'Speculative Execution - Hedge Requests'"
echo ""
for i in {1..100}; do
  curl -s http://localhost:8000/api/process > /dev/null
  printf "."
  if [ $((i % 50)) -eq 0 ]; then
    echo " $i requests"
  fi
done
echo ""
echo "Done! Check Grafana dashboard."
```

---

## Part 8: Load Testing

### Run k6 load test

```bash {"name": "load-test"}
docker compose run --rm lab52-k6 run /scripts/basic.js
```

### Run comparison load test

```bash {"name": "comparison-load-test"}
docker compose run --rm lab52-k6 run /scripts/hedge-comparison.js
```

---

## Part 9: Examine Traces

### Generate traces to analyze

```bash {"name": "generate-traces"}
echo "Generating traces for Jaeger analysis..."
for i in {1..30}; do
  curl -s http://localhost:8000/api/process > /dev/null
done
echo ""
echo "Done! Open Jaeger at http://localhost:16686"
echo ""
echo "Tips for trace analysis:"
echo "  1. Select 'hedger' service"
echo "  2. Look for traces with result_type = 'hedge_won'"
echo "  3. Compare waterfall view of hedge_won vs primary_won"
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

### Check hedger logs

```bash {"name": "logs-hedger"}
docker compose logs lab52-hedger --tail=50
```

### Check backend logs

```bash {"name": "logs-backends"}
docker compose logs lab52-backend-1 lab52-backend-2 lab52-backend-3 --tail=30
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart all services

```bash {"name": "restart-all"}
docker compose restart
```

### Check metrics endpoint

```bash {"name": "check-metrics"}
echo "Hedger metrics:"
curl -s http://localhost:8080/metrics | grep -E "^hedger_"
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Enable hedging | `curl -X POST localhost:8080/admin/config -d '{"hedge_enabled":true}'` |
| Disable hedging | `curl -X POST localhost:8080/admin/config -d '{"hedge_enabled":false}'` |
| Set threshold | `curl -X POST localhost:8080/admin/config -d '{"hedge_delay_ms":50}'` |
| Reset stats | `curl -X POST localhost:8080/admin/reset-stats` |
| View config | `curl localhost:8080/admin/config` |
| Compare modes | `curl localhost:8000/api/compare?iterations=20` |
| View traces | http://localhost:16686 |
| View dashboard | http://localhost:3001 |
