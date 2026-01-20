# Demo Runbook: Chaos Engineering

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

```bash {"name": "start-lab", "background": true}
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
echo "Chaos Controller:"
curl -s http://localhost:8080/health | jq
echo ""
echo "Order Service:"
curl -s http://localhost:8001/health | jq
echo ""
echo "Inventory Service:"
curl -s http://localhost:8002/health | jq
echo ""
echo "Payment Service:"
curl -s http://localhost:8003/health | jq
```

### Show URLs

```bash {"name": "show-urls"}
echo "Open these URLs in your browser:"
echo ""
echo "  Gateway:          http://localhost:8000"
echo "  Chaos Controller: http://localhost:8080"
echo "  Jaeger:           http://localhost:16686"
echo "  Grafana:          http://localhost:3001 (admin/admin)"
echo "  Prometheus:       http://localhost:9090"
```

---

## Part 2: Establish Baseline (Steady State)

### Make a single order request

```bash {"name": "single-order"}
curl -s -X POST http://localhost:8000/api/orders \
  -H "Content-Type: application/json" \
  -d '{"items": [{"product_id": "product-001", "quantity": 1}], "customer_id": "demo-user"}' | jq
```

### Measure baseline latency

```bash {"name": "baseline-latency"}
echo "Baseline latency (10 requests):"
total=0
for i in {1..10}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" -X POST http://localhost:8000/api/orders \
    -H "Content-Type: application/json" \
    -d '{"items": [{"product_id": "product-001", "quantity": 1}]}')
  echo "Request $i: ${time}s"
  total=$(echo "$total + $time" | bc)
done
avg=$(echo "scale=3; $total / 10" | bc)
echo ""
echo "Average baseline latency: ${avg}s"
```

### Check inventory levels

```bash {"name": "check-inventory"}
curl -s http://localhost:8002/inventory | jq
```

### Check system health

```bash {"name": "health-check"}
curl -s http://localhost:8000/api/health-check | jq
```

---

## Part 3: Latency Injection Experiment

### Document the hypothesis

```bash {"name": "hypothesis-latency"}
echo "=== HYPOTHESIS ==="
echo "Adding 500ms latency to the payment service will:"
echo "  - Increase end-to-end latency by ~500ms"
echo "  - Orders will still complete successfully"
echo "  - No errors should occur"
echo ""
echo "Steady State Expectations:"
echo "  - P95 latency < 300ms (currently)"
echo "  - Error rate < 2%"
echo "=================="
```

### Inject 500ms latency into payment service

```bash {"name": "inject-latency"}
curl -s -X POST http://localhost:8080/chaos/inject/latency/payment-service \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "latency_ms": 500,
    "jitter_ms": 50
  }' | jq
```

### Verify latency injection is active

```bash {"name": "verify-latency-injection"}
curl -s http://localhost:8080/chaos/state/payment-service | jq
```

### Measure impact of latency injection

```bash {"name": "measure-latency-impact"}
echo "Latency WITH 500ms injection (5 requests):"
for i in {1..5}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" -X POST http://localhost:8000/api/orders \
    -H "Content-Type: application/json" \
    -d '{"items": [{"product_id": "product-001", "quantity": 1}]}')
  echo "Request $i: ${time}s"
done
```

### Compare before and after

```bash {"name": "latency-comparison"}
echo "=== COMPARISON ==="
echo ""

# Disable latency
curl -s -X POST http://localhost:8080/chaos/rollback/payment-service > /dev/null

echo "WITHOUT chaos:"
for i in {1..3}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" -X POST http://localhost:8000/api/orders \
    -H "Content-Type: application/json" \
    -d '{"items": [{"product_id": "product-001", "quantity": 1}]}')
  echo "  Request $i: ${time}s"
done

# Re-enable latency
curl -s -X POST http://localhost:8080/chaos/inject/latency/payment-service \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "latency_ms": 500}' > /dev/null

echo ""
echo "WITH 500ms latency:"
for i in {1..3}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" -X POST http://localhost:8000/api/orders \
    -H "Content-Type: application/json" \
    -d '{"items": [{"product_id": "product-001", "quantity": 1}]}')
  echo "  Request $i: ${time}s"
done
```

### Rollback latency injection

```bash {"name": "rollback-latency"}
curl -s -X POST http://localhost:8080/chaos/rollback/payment-service | jq
echo ""
echo "Latency injection disabled"
```

---

## Part 4: Error Injection Experiment

### Document the hypothesis

```bash {"name": "hypothesis-error"}
echo "=== HYPOTHESIS ==="
echo "Injecting 30% error rate into inventory service will:"
echo "  - Cause approximately 30% of orders to fail"
echo "  - System should remain stable (no cascading failures)"
echo "  - Successful orders should complete normally"
echo "=================="
```

### Inject 30% error rate into inventory service

```bash {"name": "inject-errors"}
curl -s -X POST http://localhost:8080/chaos/inject/error/inventory-service \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "error_rate": 0.30,
    "error_code": 500,
    "error_message": "Chaos: Simulated inventory failure"
  }' | jq
```

### Test with multiple requests

```bash {"name": "test-error-injection"}
echo "Testing with 20 requests (expect ~30% failures):"
echo ""
success=0
fail=0
for i in {1..20}; do
  status=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8000/api/orders \
    -H "Content-Type: application/json" \
    -d '{"items": [{"product_id": "product-001", "quantity": 1}]}')
  if [ "$status" == "200" ]; then
    ((success++))
    echo "Request $i: SUCCESS (200)"
  else
    ((fail++))
    echo "Request $i: FAILED ($status)"
  fi
done
echo ""
echo "=== RESULTS ==="
echo "Successes: $success"
echo "Failures: $fail"
echo "Actual error rate: $(echo "scale=2; $fail * 100 / 20" | bc)%"
```

### Check chaos controller status

```bash {"name": "check-chaos-status"}
curl -s http://localhost:8080/status | jq
```

### Rollback error injection

```bash {"name": "rollback-errors"}
curl -s -X POST http://localhost:8080/chaos/rollback/inventory-service | jq
echo ""
echo "Error injection disabled"
```

---

## Part 5: Network Partition Experiment

### Document the hypothesis

```bash {"name": "hypothesis-network"}
echo "=== HYPOTHESIS ==="
echo "Simulating network partition (80% drop rate) on payment service:"
echo "  - Most orders will fail at payment step"
echo "  - Inventory should be rolled back on payment failure"
echo "  - System should not hang or timeout excessively"
echo "=================="
```

### Simulate network partition

```bash {"name": "inject-network-partition"}
curl -s -X POST http://localhost:8080/chaos/inject/network/payment-service \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "drop_rate": 0.80,
    "timeout_ms": 5000
  }' | jq
```

### Test network partition impact

```bash {"name": "test-network-partition"}
echo "Testing with network partition (10 requests):"
echo ""
for i in {1..10}; do
  start=$(date +%s%N)
  response=$(curl -s -X POST http://localhost:8000/api/orders \
    -H "Content-Type: application/json" \
    -d '{"items": [{"product_id": "product-001", "quantity": 1}]}')
  end=$(date +%s%N)
  duration=$(echo "scale=2; ($end - $start) / 1000000000" | bc)
  status=$(echo $response | jq -r '.detail // "success"' 2>/dev/null)
  echo "Request $i: ${duration}s - $status"
done
```

### Rollback network partition

```bash {"name": "rollback-network"}
curl -s -X POST http://localhost:8080/chaos/rollback/payment-service | jq
echo ""
echo "Network partition disabled"
```

---

## Part 6: Multi-Service Chaos

### Inject chaos into multiple services

```bash {"name": "multi-chaos-inject"}
echo "Injecting chaos into multiple services..."
echo ""

# Latency on order service
curl -s -X POST http://localhost:8080/chaos/inject/latency/order-service \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "latency_ms": 200}' | jq

# Errors on inventory service
curl -s -X POST http://localhost:8080/chaos/inject/error/inventory-service \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "error_rate": 0.10}' | jq

echo ""
echo "Multiple chaos types now active"
```

### Check overall chaos status

```bash {"name": "check-multi-chaos"}
curl -s http://localhost:8080/status | jq
```

### Test under multi-service chaos

```bash {"name": "test-multi-chaos"}
echo "Testing with multiple chaos types active:"
echo ""
for i in {1..10}; do
  start=$(date +%s%N)
  status=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8000/api/orders \
    -H "Content-Type: application/json" \
    -d '{"items": [{"product_id": "product-001", "quantity": 1}]}')
  end=$(date +%s%N)
  duration=$(echo "scale=2; ($end - $start) / 1000000000" | bc)
  echo "Request $i: ${duration}s - HTTP $status"
done
```

### Emergency rollback all chaos

```bash {"name": "emergency-rollback"}
echo "EMERGENCY ROLLBACK - Disabling all chaos..."
curl -s -X POST http://localhost:8080/chaos/rollback-all | jq
echo ""
echo "All chaos disabled"
```

---

## Part 7: Observe Recovery

### Measure recovery time

```bash {"name": "measure-recovery"}
echo "Measuring system recovery after chaos rollback..."
echo ""
for i in {1..15}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" -X POST http://localhost:8000/api/orders \
    -H "Content-Type: application/json" \
    -d '{"items": [{"product_id": "product-001", "quantity": 1}]}')
  echo "$(date +%H:%M:%S) - Request $i: ${time}s"
  sleep 1
done
echo ""
echo "Recovery observation complete"
```

### Verify system is healthy

```bash {"name": "verify-recovery"}
echo "Final health check:"
curl -s http://localhost:8000/api/health-check | jq
echo ""
echo "Chaos controller status:"
curl -s http://localhost:8080/status | jq '.active_chaos'
```

---

## Part 8: Run Formal Experiment

### Run a structured chaos experiment

```bash {"name": "run-experiment"}
curl -s -X POST http://localhost:8080/experiments/run \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Payment Service Latency Impact",
    "hypothesis": {
      "name": "Order completion under payment delay",
      "description": "Orders should still complete successfully when payment takes 1 second",
      "metric_name": "order_success_rate",
      "threshold_type": "min",
      "threshold_value": 0.95
    },
    "chaos_type": "latency",
    "target_service": "payment-service",
    "magnitude": 1000,
    "duration_seconds": 30,
    "rollback_on_violation": true
  }' | jq
```

### Generate load during experiment

```bash {"name": "experiment-load"}
echo "Generating load while experiment runs..."
for i in {1..15}; do
  curl -s -X POST http://localhost:8000/api/orders \
    -H "Content-Type: application/json" \
    -d '{"items": [{"product_id": "product-001", "quantity": 1}]}' > /dev/null
  echo -n "."
  sleep 2
done
echo ""
echo "Load generation complete"
```

### List all experiments

```bash {"name": "list-experiments"}
curl -s http://localhost:8080/experiments | jq
```

---

## Part 9: Load Testing with Chaos

### Run baseline load test

```bash {"name": "baseline-load-test"}
docker compose run --rm lab28-k6 run /scripts/baseline.js
```

### Run chaos load test

```bash {"name": "chaos-load-test", "background": true}
# In one terminal, start the load test
docker compose run --rm lab28-k6 run /scripts/chaos-test.js
```

### Inject chaos during load test

```bash {"name": "chaos-during-load"}
# While load test is running, inject chaos
echo "Injecting chaos during load test..."
curl -s -X POST http://localhost:8080/chaos/inject/latency/payment-service \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "latency_ms": 500}' > /dev/null
echo "Chaos injected - observe the load test results"
sleep 30
curl -s -X POST http://localhost:8080/chaos/rollback/payment-service > /dev/null
echo "Chaos rolled back"
```

---

## Cleanup

### Stop all services

```bash {"name": "cleanup"}
docker compose down -v
echo ""
echo "Lab cleaned up"
```

---

## Troubleshooting Commands

### Check service logs

```bash {"name": "logs-all"}
docker compose logs --tail=50
```

### Check chaos controller logs

```bash {"name": "logs-chaos-controller"}
docker compose logs lab28-chaos-controller --tail=50
```

### Check order service logs

```bash {"name": "logs-order-service"}
docker compose logs lab28-order-service --tail=50
```

### Check resource usage

```bash {"name": "resource-usage"}
docker stats --no-stream
```

### Restart chaos controller

```bash {"name": "restart-chaos-controller"}
docker compose restart lab28-chaos-controller
```

---

## Quick Reference

| Action | Command |
|--------|---------|
| Start lab | `docker compose up --build -d` |
| Stop lab | `docker compose down -v` |
| Inject latency | `curl -X POST localhost:8080/chaos/inject/latency/{service} -d '{"enabled":true,"latency_ms":500}'` |
| Inject errors | `curl -X POST localhost:8080/chaos/inject/error/{service} -d '{"enabled":true,"error_rate":0.2}'` |
| Rollback service | `curl -X POST localhost:8080/chaos/rollback/{service}` |
| Rollback all | `curl -X POST localhost:8080/chaos/rollback-all` |
| Check status | `curl localhost:8080/status` |
| View traces | http://localhost:16686 |
| View dashboard | http://localhost:3001 |
