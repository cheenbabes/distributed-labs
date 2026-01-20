# Lab 28: Chaos Engineering

Break things on purpose to build confidence in your system's resilience. In this lab, you'll learn chaos engineering principles by running controlled experiments on a distributed e-commerce system.

## What You'll Learn

- The scientific method of chaos engineering
- How to define and verify steady state hypotheses
- Techniques for injecting latency, errors, and network partitions
- How to observe system behavior under stress
- Rollback mechanisms for safe experimentation
- Game day practices for building team confidence

## Architecture

```
                                    ┌─────────────────────┐
                                    │  Chaos Controller   │
                                    │  (Experiment Mgmt)  │
                                    └──────────┬──────────┘
                                               │ chaos state
                                               ▼
┌──────────┐    ┌───────────┐    ┌─────────────────┐    ┌───────────────────┐
│  Client  │───▶│  Gateway  │───▶│  Order Service  │───▶│ Inventory Service │
└──────────┘    └───────────┘    └────────┬────────┘    └───────────────────┘
                                          │
                                          ▼
                                 ┌─────────────────┐
                                 │ Payment Service │
                                 └─────────────────┘
```

**Services:**
- **Chaos Controller** - Central management for chaos experiments
- **Gateway** - API entry point, routes to order service
- **Order Service** - Orchestrates orders, calls inventory and payment
- **Inventory Service** - Manages product stock
- **Payment Service** - Processes payments

Each service polls the chaos controller for its current chaos configuration and applies it to incoming requests.

## Prerequisites

- Docker and Docker Compose
- curl (for API calls)
- jq (for JSON formatting)
- k6 (optional, for load testing)

## Quick Start

### 1. Start the Lab

```bash
docker compose up --build -d
```

### 2. Verify Services Are Running

```bash
docker compose ps
```

All services should show as "healthy".

### 3. Access the UIs

| Service | URL | Purpose |
|---------|-----|---------|
| Gateway | http://localhost:8000 | API entry point |
| Chaos Controller | http://localhost:8080 | Chaos management API |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Chaos Engineering Principles

Before diving into exercises, understand the core principles:

1. **Define Steady State** - What does "normal" look like? (latency, error rate, throughput)
2. **Hypothesize** - Predict what will happen when chaos is introduced
3. **Inject Chaos** - Introduce controlled failures
4. **Observe** - Watch how the system behaves
5. **Learn** - Document findings and improve the system

## Lab Exercises

### Exercise 1: Define Steady State Hypothesis

Before injecting chaos, establish what "normal" looks like.

**Step 1: Make baseline requests**

```bash
# Single order request
curl -s -X POST http://localhost:8000/api/orders \
  -H "Content-Type: application/json" \
  -d '{"items": [{"product_id": "product-001", "quantity": 1}]}' | jq

# Measure baseline latency (10 requests)
for i in {1..10}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" -X POST http://localhost:8000/api/orders \
    -H "Content-Type: application/json" \
    -d '{"items": [{"product_id": "product-001", "quantity": 1}]}')
  echo "Request $i: ${time}s"
done
```

**Step 2: Document your steady state hypothesis**

Example hypothesis:
- P95 latency should be < 300ms
- Error rate should be < 2%
- All orders should complete successfully

**Step 3: Record baseline in chaos controller**

```bash
curl -s -X POST http://localhost:8080/steady-state/record/order-service \
  -H "Content-Type: application/json" \
  -d '{
    "p95_latency_ms": 300,
    "error_rate": 0.02,
    "success_rate": 0.98
  }' | jq
```

**Questions:**
- What is your baseline P95 latency?
- What's the typical error rate?
- How does latency vary across services? (Check Jaeger)

---

### Exercise 2: Latency Injection Experiment

**Hypothesis:** Adding 500ms latency to the payment service will increase end-to-end latency but orders will still succeed.

**Step 1: Inject latency into payment service**

```bash
curl -s -X POST http://localhost:8080/chaos/inject/latency/payment-service \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "latency_ms": 500,
    "jitter_ms": 100
  }' | jq
```

**Step 2: Observe the impact**

```bash
# Make requests and observe latency
for i in {1..5}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" -X POST http://localhost:8000/api/orders \
    -H "Content-Type: application/json" \
    -d '{"items": [{"product_id": "product-001", "quantity": 1}]}')
  echo "Request $i: ${time}s"
done
```

**Step 3: Check traces in Jaeger**

1. Open http://localhost:16686
2. Select "gateway" service
3. Find a recent trace
4. Observe which span has the added latency

**Step 4: Verify chaos state**

```bash
# Check what chaos is active
curl -s http://localhost:8080/status | jq

# Check specific service
curl -s http://localhost:8080/chaos/state/payment-service | jq
```

**Step 5: Rollback**

```bash
curl -s -X POST http://localhost:8080/chaos/rollback/payment-service | jq
```

**Questions:**
- Did the hypothesis hold? Did orders still succeed?
- By how much did end-to-end latency increase?
- Would users notice this delay?

---

### Exercise 3: Error Injection Experiment

**Hypothesis:** A 20% error rate in the inventory service will cause order failures, but the system will remain stable.

**Step 1: Inject errors**

```bash
curl -s -X POST http://localhost:8080/chaos/inject/error/inventory-service \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "error_rate": 0.20,
    "error_code": 500,
    "error_message": "Chaos: Simulated inventory failure"
  }' | jq
```

**Step 2: Make multiple requests**

```bash
success=0
fail=0
for i in {1..20}; do
  status=$(curl -s -o /dev/null -w "%{http_code}" -X POST http://localhost:8000/api/orders \
    -H "Content-Type: application/json" \
    -d '{"items": [{"product_id": "product-001", "quantity": 1}]}')
  if [ "$status" == "200" ]; then
    ((success++))
    echo "Request $i: SUCCESS"
  else
    ((fail++))
    echo "Request $i: FAILED ($status)"
  fi
done
echo ""
echo "Results: $success successes, $fail failures"
```

**Step 3: Check metrics in Grafana**

1. Open http://localhost:3001
2. Navigate to the "Chaos Engineering Dashboard"
3. Observe the error rate panel

**Step 4: Rollback**

```bash
curl -s -X POST http://localhost:8080/chaos/rollback/inventory-service | jq
```

**Questions:**
- What percentage of requests actually failed?
- Did the error rate match what we injected?
- How could the order service handle this more gracefully? (retries? circuit breaker?)

---

### Exercise 4: Network Partition Simulation

**Hypothesis:** If the payment service becomes unreachable, orders should fail gracefully with appropriate error messages.

**Step 1: Simulate network partition**

```bash
curl -s -X POST http://localhost:8080/chaos/inject/network/payment-service \
  -H "Content-Type: application/json" \
  -d '{
    "enabled": true,
    "drop_rate": 0.80,
    "timeout_ms": 30000
  }' | jq
```

**Step 2: Observe behavior**

```bash
for i in {1..10}; do
  response=$(curl -s -X POST http://localhost:8000/api/orders \
    -H "Content-Type: application/json" \
    -d '{"items": [{"product_id": "product-001", "quantity": 1}]}')
  echo "Request $i: $(echo $response | jq -r '.detail // .order.status // "success"')"
done
```

**Step 3: Check if inventory was rolled back**

When payment fails, inventory should be released:

```bash
curl -s http://localhost:8002/inventory | jq
```

**Step 4: Rollback**

```bash
curl -s -X POST http://localhost:8080/chaos/rollback/payment-service | jq
```

**Questions:**
- Did the order service handle the network partition gracefully?
- Were inventory reservations properly rolled back on payment failure?
- What improvements would you suggest?

---

### Exercise 5: Recovery Observation

**Hypothesis:** After removing chaos, the system should return to steady state within 30 seconds.

**Step 1: Inject multiple types of chaos**

```bash
# Add latency to order service
curl -s -X POST http://localhost:8080/chaos/inject/latency/order-service \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "latency_ms": 300}' | jq

# Add errors to inventory service
curl -s -X POST http://localhost:8080/chaos/inject/error/inventory-service \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "error_rate": 0.10}' | jq
```

**Step 2: Run load test (in separate terminal)**

```bash
# Terminal 1: Start load test
docker compose run --rm lab28-k6 run /scripts/chaos-test.js
```

**Step 3: Observe during chaos (in another terminal)**

Watch the Grafana dashboard while chaos is active.

**Step 4: Emergency rollback**

```bash
curl -s -X POST http://localhost:8080/chaos/rollback-all | jq
```

**Step 5: Measure recovery time**

```bash
echo "Measuring recovery..."
start_time=$(date +%s)
for i in {1..30}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" -X POST http://localhost:8000/api/orders \
    -H "Content-Type: application/json" \
    -d '{"items": [{"product_id": "product-001", "quantity": 1}]}')
  echo "$(date +%s) - ${time}s"
  sleep 1
done
end_time=$(date +%s)
echo "Observation period: $((end_time - start_time)) seconds"
```

**Questions:**
- How long did it take for latency to return to baseline?
- Did any requests fail after rollback?
- Is 30 seconds an acceptable recovery time for your SLA?

---

### Exercise 6: Run a Formal Experiment

Use the experiment API to run a structured chaos experiment.

**Step 1: Define and run experiment**

```bash
curl -s -X POST http://localhost:8080/experiments/run \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Payment Latency Impact",
    "hypothesis": {
      "name": "Order completion under payment delay",
      "description": "Orders should still complete when payment takes 1 second",
      "metric_name": "order_success_rate",
      "threshold_type": "min",
      "threshold_value": 0.95
    },
    "chaos_type": "latency",
    "target_service": "payment-service",
    "magnitude": 1000,
    "duration_seconds": 60,
    "rollback_on_violation": true
  }' | jq
```

**Step 2: Monitor the experiment**

```bash
# Check experiment status
curl -s http://localhost:8080/experiments | jq

# Generate load while experiment runs
for i in {1..30}; do
  curl -s -X POST http://localhost:8000/api/orders \
    -H "Content-Type: application/json" \
    -d '{"items": [{"product_id": "product-001", "quantity": 1}]}' > /dev/null
  sleep 2
done
```

**Step 3: Review results**

```bash
# List all experiments
curl -s http://localhost:8080/experiments | jq
```

---

## Key Takeaways

1. **Start with a hypothesis** - Never inject chaos without a prediction of what will happen

2. **Define steady state first** - You can't know if something is broken unless you know what "working" looks like

3. **Start small** - Begin with small magnitude chaos and increase gradually

4. **Always have a rollback plan** - The ability to quickly stop chaos is crucial

5. **Observe broadly** - Watch traces, metrics, and logs together for the full picture

6. **Document everything** - Record hypotheses, results, and learnings

7. **Share findings** - Game days are about building team confidence, not just testing systems

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Services not starting
```bash
docker compose logs -f
```

### Chaos not being applied
Check if service is polling chaos controller:
```bash
docker compose logs lab28-order-service | grep -i chaos
```

### Traces not appearing in Jaeger
Wait 10-15 seconds after making requests, then check otel-collector:
```bash
docker compose logs lab28-otel-collector
```

## Further Reading

- [Principles of Chaos Engineering](https://principlesofchaos.org/)
- [Chaos Engineering: Building Confidence in System Behavior through Experiments](https://www.oreilly.com/library/view/chaos-engineering/9781491988459/)
- [Netflix Chaos Monkey](https://netflix.github.io/chaosmonkey/)

## Chaos Engineering Checklist

Before running chaos in production:

- [ ] Steady state hypothesis is documented
- [ ] Monitoring and alerting are in place
- [ ] Rollback mechanism is tested
- [ ] Blast radius is limited (start with non-critical services)
- [ ] Team is informed (game day)
- [ ] Customer impact is minimized (low traffic periods)
- [ ] Results will be documented and shared
