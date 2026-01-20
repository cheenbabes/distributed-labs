# Lab 47: Bulkhead Isolation

When one of your dependencies becomes slow or starts failing, should it bring down your entire system? In this lab, you will implement and observe the bulkhead pattern - a resilience technique that isolates failures to prevent cascade effects.

## What You Will Learn

- How the bulkhead pattern provides failure isolation
- The difference between semaphore-based and thread pool isolation
- How a slow dependency can consume all resources without bulkheads
- How bulkheads prevent cascade failures across independent services
- Practical implementation patterns for production systems

## The Bulkhead Pattern

The bulkhead pattern is named after the compartments in a ship's hull. If one compartment floods, the bulkheads prevent water from spreading to other compartments, keeping the ship afloat. In software, bulkheads isolate failures by limiting the resources (threads, connections, memory) that any single dependency can consume.

```
WITHOUT BULKHEADS:
┌─────────────────────────────────────────────────────────────┐
│                    Shared Resource Pool                      │
│  [████████████████████████████████████████████████████]     │
│    ↑           ↑           ↑                                │
│  Fast       Slow        Flaky                               │
│  Backend    Backend     Backend                             │
│                 ↓                                            │
│           (consumes all                                      │
│            resources)                                        │
└─────────────────────────────────────────────────────────────┘

WITH BULKHEADS:
┌─────────────────────────────────────────────────────────────┐
│  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐        │
│  │ Fast Bulkhead│ │ Slow Bulkhead│ │Flaky Bulkhead│        │
│  │  [████░░░░]  │ │  [████████]  │ │  [██████░░]  │        │
│  │   max: 10    │ │   max: 10    │ │   max: 10    │        │
│  └──────────────┘ └──────────────┘ └──────────────┘        │
│        ↑                 ↑                 ↑                │
│      Fast             Slow             Flaky               │
│     Backend          Backend          Backend              │
│                         ↓                                   │
│                   (only affects                             │
│                    its bulkhead)                            │
└─────────────────────────────────────────────────────────────┘
```

## Architecture

```
                          ┌─────────────────┐
                          │     Client      │
                          │   (load gen)    │
                          └────────┬────────┘
                                   │
                          ┌────────▼────────┐
                          │   API Gateway   │
                          │  (bulkheads)    │
                          └────────┬────────┘
                    ┌──────────────┼──────────────┐
                    │              │              │
            ┌───────▼───────┐ ┌───▼───┐ ┌───────▼───────┐
            │ Backend Fast  │ │Backend│ │ Backend Flaky │
            │   10-30ms     │ │ Slow  │ │  50-150ms     │
            └───────────────┘ │ 3s    │ │  30% errors   │
                              └───────┘ └───────────────┘
```

### Services

| Service | Port | Description |
|---------|------|-------------|
| API Gateway | 8000 | Implements bulkhead pattern with configurable isolation |
| Backend Fast | 8001 | Responds quickly (10-30ms) |
| Backend Slow | 8002 | Configurable slow responses (default: 3s) |
| Backend Flaky | 8003 | Random failures (30% error rate) |
| Client | 8080 | Load generation and testing |
| Jaeger | 16686 | Distributed tracing |
| Prometheus | 9090 | Metrics collection |
| Grafana | 3001 | Dashboards (admin/admin) |

## Prerequisites

- Docker and Docker Compose
- curl (for manual testing)
- jq (for JSON formatting)

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
| API Gateway | http://localhost:8000 | Main API |
| Gateway Status | http://localhost:8000/status | Bulkhead status |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Understanding the Baseline

First, let us establish how the system behaves with bulkheads enabled (the default).

#### Check bulkhead configuration

```bash
curl -s http://localhost:8000/status | jq
```

You should see:
- `bulkhead_mode: "enabled"`
- Each bulkhead with `max: 10` concurrent requests

#### Test each backend individually

```bash
# Fast backend - should respond in ~20ms
curl -s http://localhost:8000/api/fast | jq

# Slow backend - should respond in ~3s
time curl -s http://localhost:8000/api/slow | jq

# Flaky backend - may succeed or fail
curl -s http://localhost:8000/api/flaky | jq
```

#### Observe in Jaeger

1. Open http://localhost:16686
2. Select "api-gateway" service
3. Click "Find Traces"
4. Compare trace durations across backends

**Question:** How long does each backend take? Note these as your baseline.

---

### Exercise 2: Demonstrating Bulkhead Protection

Now let us see how bulkheads protect fast backends from slow ones.

#### Send concurrent requests to all backends

```bash
# Send 30 concurrent requests to each backend
curl -s -X POST http://localhost:8080/test/compare -H "Content-Type: application/json" -d '{
  "count": 30,
  "timeout": 10
}' | jq
```

#### Observe the results

Look at the `by_endpoint` section:
- `/api/fast` should have high success rate and low latency
- `/api/slow` may have some timeouts (3s > default timeout)
- `/api/flaky` should show ~70% success rate

**Key Observation:** The fast backend maintains its performance despite the slow backend consuming resources.

#### Check Grafana dashboard

1. Open http://localhost:3001
2. Navigate to "Bulkhead Isolation Dashboard"
3. Observe:
   - Active requests per bulkhead
   - Request rate by status
   - Queue wait times

---

### Exercise 3: Slow Dependency Consuming Resources (Without Bulkheads)

Now let us see what happens when bulkheads are disabled.

#### Disable bulkheads

```bash
curl -s -X POST http://localhost:8000/admin/config -H "Content-Type: application/json" -d '{
  "mode": "disabled"
}' | jq
```

#### Verify bulkheads are disabled

```bash
curl -s http://localhost:8000/status | jq
```

#### Send burst traffic

```bash
# Send 50 concurrent requests to each backend
curl -s -X POST http://localhost:8080/test/compare -H "Content-Type: application/json" -d '{
  "count": 50,
  "timeout": 10
}' | jq
```

#### Compare the results

Without bulkheads:
- Fast backend latency increases significantly
- More timeouts across all backends
- Overall success rate drops

**Why?** Without bulkheads, slow requests to `backend-slow` hold connections/threads, preventing fast requests from being processed.

#### Re-enable bulkheads

```bash
curl -s -X POST http://localhost:8000/admin/config -H "Content-Type: application/json" -d '{
  "mode": "enabled"
}' | jq
```

---

### Exercise 4: Observing Bulkhead Rejection

When a bulkhead is full, new requests are rejected immediately rather than waiting indefinitely.

#### Reduce bulkhead limits

```bash
curl -s -X POST http://localhost:8000/admin/config -H "Content-Type: application/json" -d '{
  "semaphore_limit": 3
}' | jq
```

Now each backend can only have 3 concurrent requests.

#### Send burst traffic to slow backend

```bash
# Send 20 requests to the slow backend
curl -s -X POST http://localhost:8080/test/burst -H "Content-Type: application/json" -d '{
  "backend": "slow",
  "count": 20,
  "timeout": 10
}' | jq
```

#### Observe rejections

- Many requests should be rejected (503 status)
- Check Grafana for "Rejected Requests per Bulkhead"
- The rejection is fast (not waiting for the slow backend)

**Key Insight:** Bulkhead rejection is a feature, not a bug. It's better to fail fast than to wait indefinitely.

#### Restore bulkhead limits

```bash
curl -s -X POST http://localhost:8000/admin/config -H "Content-Type: application/json" -d '{
  "semaphore_limit": 10
}' | jq
```

---

### Exercise 5: The Cascade Effect

Let us demonstrate how a problem in one backend can cascade without proper isolation.

#### Make slow backend extremely slow

```bash
curl -s -X POST http://localhost:8002/admin/slow-mode -H "Content-Type: application/json" -d '{
  "enabled": true,
  "latency_ms": 10000
}' | jq
```

Now the slow backend takes 10 seconds per request.

#### Test with bulkheads enabled

```bash
# Test fast backend while slow backend is extremely slow
for i in {1..5}; do
  curl -s -o /dev/null -w "Fast backend: %{time_total}s\n" http://localhost:8000/api/fast
done
```

**Result:** Fast backend should still respond quickly (~30ms).

#### Disable bulkheads and test again

```bash
curl -s -X POST http://localhost:8000/admin/config -H "Content-Type: application/json" -d '{
  "mode": "disabled"
}' | jq

# Generate load on slow backend in background
for i in {1..20}; do
  curl -s http://localhost:8000/api/slow > /dev/null &
done

# Now test fast backend
for i in {1..5}; do
  curl -s -o /dev/null -w "Fast backend: %{time_total}s\n" http://localhost:8000/api/fast
done
```

**Result:** Fast backend latency increases dramatically due to resource contention.

#### Restore settings

```bash
# Re-enable bulkheads
curl -s -X POST http://localhost:8000/admin/config -H "Content-Type: application/json" -d '{
  "mode": "enabled"
}' | jq

# Reset slow backend
curl -s -X POST http://localhost:8002/admin/slow-mode -H "Content-Type: application/json" -d '{
  "enabled": true,
  "latency_ms": 3000
}' | jq
```

---

### Exercise 6: Load Testing

Run a sustained load test to observe bulkhead behavior over time.

```bash
docker compose run --rm k6 run /scripts/bulkhead-test.js
```

While the test runs:
1. Watch the Grafana dashboard
2. Observe how bulkhead metrics change
3. Note that fast backend latency stays consistent

---

## Key Takeaways

1. **Isolation is essential** - Without bulkheads, a slow or failing dependency can consume all resources and affect unrelated requests.

2. **Fail fast is better than wait** - Bulkhead rejection (503) is better than indefinite waiting. Clients can retry or fallback.

3. **Semaphore vs Thread Pool** - Both provide isolation:
   - Semaphores: Limit concurrent requests, simpler
   - Thread pools: Provide true thread isolation, prevents blocking

4. **Size bulkheads appropriately** - Too small = unnecessary rejections, too large = inadequate isolation.

5. **Monitor bulkhead metrics** - Track active requests, rejections, and queue times to tune limits.

6. **Combine with other patterns** - Bulkheads work well with circuit breakers, timeouts, and retries.

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Services not starting

```bash
docker compose logs -f
```

### Bulkhead metrics not showing

- Wait for some traffic to flow through
- Check Prometheus targets: http://localhost:9090/targets

### Client service failing

```bash
docker compose logs client
```

## Further Reading

- [Release It! by Michael Nygard](https://pragprog.com/titles/mnee2/release-it-second-edition/) - Chapter on Stability Patterns
- [Netflix Hystrix](https://github.com/Netflix/Hystrix/wiki/How-it-Works#isolation) - Thread pool and semaphore isolation
- [Resilience4j Bulkhead](https://resilience4j.readme.io/docs/bulkhead) - Modern Java implementation
