# Lab 51: Timeout Budgets (Deadline Propagation)

When a client sets a 1-second timeout, what happens when your request traverses 4 services, each with their own 5-second timeout? The client gives up after 1 second, but your services keep working for another 19 seconds - wasting resources on a request nobody is waiting for.

In this lab, you'll implement **deadline propagation** (also called timeout budgets) to solve this problem. Each service passes the remaining time budget to downstream services, allowing them to terminate early when the budget is exhausted.

## What You'll Learn

- How to propagate timeout budgets across service boundaries via headers
- How services can check and honor incoming budget constraints
- Early termination patterns to avoid wasted work
- The difference between fixed per-call timeouts and deadline propagation
- Observing budget consumption in distributed traces

## Architecture

```
                         Budget: 1000ms
                              |
                              v
┌──────────┐    ┌──────────────────┐    ┌───────────┐    ┌───────────┐    ┌───────────┐
│  Client  │───>│  Frontend API    │───>│ Service A │───>│ Service B │───>│ Service C │
└──────────┘    │  (50ms work)     │    │ (100ms)   │    │ (150ms)   │    │ (200ms)   │
                │  Budget: 1000ms  │    │ Remaining:│    │ Remaining:│    │ Remaining:│
                │  Remaining: 950ms│    │ 850ms     │    │ 700ms     │    │ 500ms     │
                └──────────────────┘    └───────────┘    └───────────┘    └───────────┘
```

**Budget Flow:**
1. Frontend API receives request, sets 1000ms budget
2. After 50ms of processing, passes 950ms to Service A
3. Service A takes 100ms, passes 850ms to Service B
4. Service B takes 150ms, passes 700ms to Service C
5. Service C takes 200ms, finishes with 500ms remaining

**When Budget Exhausts:**
- Services check budget on arrival - if exhausted, return immediately
- Services check budget before downstream calls - skip if insufficient
- Early termination saves CPU, memory, and downstream resources

## Prerequisites

- Docker and Docker Compose
- curl (for manual testing)
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
| Frontend API | http://localhost:8000 | API entry point |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Observe Normal Budget Flow

First, let's see how budget propagates through the chain under normal conditions.

```bash
# Make a request with default 1000ms budget
curl -s http://localhost:8000/api/process | jq
```

**Expected output:**
```json
{
  "service": "frontend-api",
  "mode": "budget",
  "budget": {
    "initial_ms": 1000,
    "elapsed_ms": ~500,
    "remaining_ms": ~500,
    "exhausted": false
  },
  "chain": {
    "service": "service-a",
    "budget": {
      "received_ms": ~950,
      "remaining_ms": ~400
    },
    "chain": { ... }
  }
}
```

**Observe in Jaeger:**
1. Open http://localhost:16686
2. Select "frontend-api" service
3. Find a trace and observe the budget attributes on each span

**Questions:**
- How much budget remains at each service?
- What's the total end-to-end latency?

---

### Exercise 2: Budget Propagation via Headers

Examine how the budget is passed between services.

```bash
# Check the headers being propagated
docker compose logs frontend-api | grep -i budget | tail -5
docker compose logs service-a | grep -i budget | tail -5
```

The budget is passed via `X-Timeout-Budget-Ms` and `X-Deadline-Timestamp-Ms` headers.

**Make a request with custom budget:**
```bash
# Tight budget - should succeed but be close
curl -s "http://localhost:8000/api/process?budget_ms=600" | jq

# Very tight budget - likely to exhaust
curl -s "http://localhost:8000/api/process?budget_ms=300" | jq
```

**What to observe:**
- With 600ms: Request succeeds but remaining budget is minimal
- With 300ms: Request fails with "timeout_budget_exhausted" error

---

### Exercise 3: Early Termination When Budget Exhausted

When budget is exhausted, services terminate early to avoid wasted work.

```bash
# Enable slow mode on Service C (adds 800ms latency)
curl -s -X POST "http://localhost:8003/admin/slow-mode?enabled=true&ms=800"

# Try with default budget (1000ms)
curl -s http://localhost:8000/api/process | jq
```

**Expected:** Request times out because Service C now takes ~1000ms (200ms + 800ms), exceeding the budget.

**Check which service terminated:**
```bash
curl -s http://localhost:8000/api/process 2>&1 | jq '.detail'
```

**Observe early termination:**
1. Look at Jaeger traces - notice where the chain stops
2. Check metrics in Grafana - see "Early Terminations by Service"

**Disable slow mode:**
```bash
curl -s -X POST "http://localhost:8003/admin/slow-mode?enabled=false&ms=0"
```

---

### Exercise 4: Avoiding Wasted Work

The key benefit of deadline propagation is avoiding wasted work. Let's compare:

**Scenario: Client has 500ms timeout, Service C is slow (800ms)**

**With Budget Propagation (default mode):**
```bash
# Enable slow mode
curl -s -X POST "http://localhost:8003/admin/slow-mode?enabled=true&ms=800"

# Request with 500ms budget
time curl -s "http://localhost:8000/api/process?budget_ms=500" | jq '.detail.message'
```

**Expected:** Request fails after ~300-400ms (at Service B, before reaching Service C)
- Work saved: Service C never receives the request
- Resources saved: No CPU/memory used by Service C

**Without Budget Propagation (fixed mode):**
```bash
# Switch to fixed timeout mode
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "fixed", "default_budget_ms": 500, "processing_time_ms": 50}'

# Same request
time curl -s "http://localhost:8000/api/process?budget_ms=500" | jq '.detail.message'
```

**Expected:** Request fails after ~500ms (timeout at frontend), but:
- Service A, B, C all did their work
- Service C processed for 800ms+ even though nobody was waiting
- Wasted work: ~1000ms of compute across services

**Reset to budget mode:**
```bash
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "budget", "default_budget_ms": 1000, "processing_time_ms": 50}'

# Disable slow mode
curl -s -X POST "http://localhost:8003/admin/slow-mode?enabled=false&ms=0"
```

---

### Exercise 5: Comparison with Fixed Per-Call Timeouts

Traditional approach: Each service has its own timeout (e.g., 5 seconds).

**Problem:** 4 services x 5 second timeout = potential 20 seconds of work for a 1 second client timeout.

**Let's demonstrate:**

```bash
# Configure services to ignore budget (simulate fixed timeouts)
curl -s -X POST http://localhost:8001/admin/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "ignore", "processing_time_ms": 100}'

curl -s -X POST http://localhost:8002/admin/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "ignore", "processing_time_ms": 150}'

curl -s -X POST http://localhost:8003/admin/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "ignore", "processing_time_ms": 200}'

# Enable slow mode
curl -s -X POST "http://localhost:8003/admin/slow-mode?enabled=true&ms=800"

# Request with 500ms timeout
time curl -s --max-time 0.5 http://localhost:8000/api/process
```

**Observe:** curl times out after 500ms, but services continue processing!

**Check service logs:**
```bash
docker compose logs service-c --tail=5
```

You'll see Service C completed its work even though the client gave up.

**Reset all services:**
```bash
curl -s -X POST http://localhost:8001/admin/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "propagate", "processing_time_ms": 100}'

curl -s -X POST http://localhost:8002/admin/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "propagate", "processing_time_ms": 150}'

curl -s -X POST http://localhost:8003/admin/config \
  -H "Content-Type: application/json" \
  -d '{"mode": "propagate", "processing_time_ms": 200}'

curl -s -X POST "http://localhost:8003/admin/slow-mode?enabled=false&ms=0"
```

---

### Exercise 6: Load Testing Budget Scenarios

Run load tests to see budget behavior at scale.

```bash
# Basic load test
docker compose run --rm k6 run /scripts/basic.js

# Budget stress test (tests different budget levels)
docker compose run --rm k6 run /scripts/budget-stress.js

# Compare modes test
docker compose run --rm k6 run /scripts/compare-modes.js
```

**While tests run, observe:**
1. Grafana dashboard: http://localhost:3001
   - Early Termination Rate
   - Work Skipped (ms/s)
   - Budget Exhaustion by Phase

2. Jaeger traces: http://localhost:16686
   - Filter by error traces
   - Observe where budget exhaustion occurs

---

## Key Takeaways

1. **Budget propagation prevents wasted work** - Services stop processing when nobody is waiting for the result

2. **Early termination saves resources** - CPU, memory, database connections, and downstream service capacity

3. **Headers are the mechanism** - `X-Timeout-Budget-Ms` or gRPC deadline metadata carries the remaining budget

4. **Check budget at key points:**
   - On request arrival
   - Before expensive operations
   - Before downstream calls

5. **Fixed timeouts compound** - N services with T timeout = N*T worst case, but client only waits T

6. **Graceful degradation** - With deadline propagation, services can return partial results or cached data when budget is low

## Real-World Patterns

### gRPC Native Support
gRPC has built-in deadline propagation via metadata. The deadline is automatically passed to downstream services.

### HTTP Header Convention
Common headers:
- `X-Request-Deadline` - Unix timestamp when request expires
- `X-Timeout-Budget-Ms` - Remaining milliseconds
- `X-Request-Timeout` - Original timeout value

### Kubernetes Considerations
When running in Kubernetes:
- Account for network latency between pods
- Consider load balancer timeouts
- Ingress controller timeouts should be >= client timeouts

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Services not starting
```bash
docker compose logs -f
```

### Budget always exhausted
- Check if slow mode is enabled: `curl http://localhost:8003/admin/config`
- Verify processing times aren't too high

### Traces not appearing in Jaeger
- Wait 10-15 seconds after making requests
- Check otel-collector: `docker compose logs otel-collector`

### Port conflicts
```bash
lsof -i :8000
lsof -i :16686
```

## Next Steps

- Implement deadline propagation in your own services
- Add budget-aware caching (return stale data when budget is low)
- Explore circuit breakers combined with deadline propagation
- Study gRPC's native deadline handling
