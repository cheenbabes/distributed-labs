# Lab 07: Circuit Breakers with Dashboard

When a downstream service fails, should you keep hammering it with requests? In this lab, you'll implement and observe a circuit breaker pattern that protects both your system and the failing service by failing fast.

## What You'll Learn

- How circuit breakers protect distributed systems from cascading failures
- The three states of a circuit breaker: CLOSED, OPEN, HALF_OPEN
- How to configure circuit breaker thresholds for your use case
- How to monitor circuit breaker behavior with Prometheus and Grafana
- The difference between fast-fail (circuit open) and slow-fail (waiting for timeout)

## Architecture

```
┌──────────┐         ┌──────────────────┐         ┌──────────┐
│          │         │                  │         │          │
│  Client  │────────▶│  Circuit Breaker │────────▶│ Backend  │
│          │         │                  │         │          │
└──────────┘         └──────────────────┘         └──────────┘
                              │
                              │ Metrics
                              ▼
                     ┌──────────────────┐
                     │    Prometheus    │
                     │     Grafana      │
                     └──────────────────┘
```

### Circuit Breaker States

```
                    ┌─────────────────────────────────────┐
                    │                                     │
                    ▼                                     │
              ┌──────────┐                                │
              │          │                                │
     ────────▶│  CLOSED  │◀──── success_count >= threshold
              │          │                                │
              └────┬─────┘                                │
                   │                                      │
                   │ failure_count >= threshold           │
                   ▼                                      │
              ┌──────────┐      timeout elapsed     ┌─────┴─────┐
              │          │─────────────────────────▶│           │
              │   OPEN   │                          │ HALF_OPEN │
              │          │◀─────────────────────────│           │
              └──────────┘      any failure         └───────────┘
```

- **CLOSED**: Normal operation. Requests pass through. Failures are counted.
- **OPEN**: Circuit is tripped. Requests fail fast without calling the backend.
- **HALF_OPEN**: Testing phase. Limited requests pass through to test backend recovery.

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
| Client API | http://localhost:8000 | Entry point with circuit breaker |
| Backend API | http://localhost:8001 | Backend service (can be put in failure mode) |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Circuit Breaker Configuration

The circuit breaker is configured with these thresholds:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `failure_threshold` | 5 | Number of failures before opening circuit |
| `success_threshold` | 3 | Successes in HALF_OPEN before closing |
| `timeout_seconds` | 30 | Time in OPEN state before testing recovery |

## Lab Exercises

### Exercise 1: Observe Normal Operation

First, let's see the circuit breaker in normal operation.

```bash
# Check circuit breaker status
curl -s http://localhost:8000/circuit/status | jq

# Make a request
curl -s http://localhost:8000/api/data | jq

# Make multiple requests and observe
for i in {1..10}; do
  curl -s http://localhost:8000/api/data | jq -r '.circuit_state'
done
```

**Expected:** All requests succeed. Circuit state is CLOSED.

Open Grafana at http://localhost:3001 (admin/admin) and find the "Circuit Breaker Dashboard". You should see:
- Circuit State = CLOSED (green)
- Failure Count = 0
- Requests flowing through successfully

---

### Exercise 2: Trigger Circuit Opening

Now let's make the backend fail and watch the circuit breaker open.

```bash
# Put backend in failure mode
curl -s -X POST http://localhost:8001/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"mode": "failing"}' | jq

# Verify backend is failing
curl -s http://localhost:8001/api/data
# Should return 503

# Now make requests through the client
for i in {1..10}; do
  echo "Request $i:"
  curl -s http://localhost:8000/api/data | jq -c '{state: .circuit_state, error: .detail.error}'
  sleep 0.5
done
```

**Expected:**
- First 5 requests fail (backend error)
- After 5 failures, circuit OPENS
- Remaining requests are REJECTED immediately (fast fail)

Check the Grafana dashboard:
- Circuit State transitions from CLOSED to OPEN
- Failure Count increases to threshold (5)
- You'll see "rejected" requests in the request rate chart

**Question:** How much faster are rejected requests compared to failed requests?

---

### Exercise 3: Observe Fast-Fail Behavior

With the circuit open, let's see how fast the rejection is.

```bash
# Check circuit status
curl -s http://localhost:8000/circuit/status | jq

# Time a rejected request
time curl -s http://localhost:8000/api/data > /dev/null

# Compare to a request that waits for backend timeout
# First, close the circuit and make backend slow instead of failing
curl -s -X POST http://localhost:8000/circuit/reset | jq
curl -s -X POST http://localhost:8001/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"mode": "healthy"}' | jq

time curl -s http://localhost:8000/api/data > /dev/null
```

**Expected:** Rejected requests complete in <10ms while successful requests take 10-50ms.

---

### Exercise 4: Watch Recovery (HALF_OPEN State)

Let's watch the circuit breaker test for recovery.

```bash
# Put backend back in failure mode to open circuit
curl -s -X POST http://localhost:8001/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"mode": "failing"}' | jq

# Trigger failures to open circuit
for i in {1..6}; do
  curl -s http://localhost:8000/api/data > /dev/null
done

# Check state - should be OPEN
curl -s http://localhost:8000/circuit/status | jq

# Now fix the backend
curl -s -X POST http://localhost:8001/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"mode": "healthy"}' | jq

# Wait for timeout (30 seconds by default) or use shorter timeout
echo "Waiting 30 seconds for circuit to enter HALF_OPEN state..."
sleep 30

# Check state - should be HALF_OPEN
curl -s http://localhost:8000/circuit/status | jq

# Make requests to trigger recovery
for i in {1..5}; do
  echo "Request $i:"
  curl -s http://localhost:8000/api/data | jq -c '{state: .circuit_state}'
  sleep 0.5
done
```

**Expected:**
1. Circuit enters HALF_OPEN after timeout
2. Successful requests in HALF_OPEN increment success count
3. After 3 successes, circuit closes

Watch the Grafana dashboard to see the state transitions: OPEN -> HALF_OPEN -> CLOSED

---

### Exercise 5: Failure in HALF_OPEN

What happens if the backend fails again during HALF_OPEN?

```bash
# Reset everything
curl -s -X POST http://localhost:8000/circuit/reset | jq
curl -s -X POST http://localhost:8001/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"mode": "failing"}' | jq

# Open the circuit
for i in {1..6}; do
  curl -s http://localhost:8000/api/data > /dev/null
done

# Wait for HALF_OPEN
echo "Waiting 30 seconds..."
sleep 30

curl -s http://localhost:8000/circuit/status | jq
# Should be HALF_OPEN

# Make a request while backend is still failing
curl -s http://localhost:8000/api/data | jq -c

# Check state
curl -s http://localhost:8000/circuit/status | jq
```

**Expected:** A single failure in HALF_OPEN immediately reopens the circuit.

---

### Exercise 6: Configure Different Thresholds

Try different circuit breaker configurations.

```bash
# Configure more aggressive circuit breaker (opens faster)
curl -s -X POST http://localhost:8000/circuit/config \
  -H "Content-Type: application/json" \
  -d '{"failure_threshold": 2, "success_threshold": 1, "timeout_seconds": 10}' | jq

# Reset backend to healthy
curl -s -X POST http://localhost:8001/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"mode": "healthy"}' | jq

# Test with new configuration
curl -s -X POST http://localhost:8001/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"mode": "failing"}' | jq

# Now only 2 failures will open the circuit
for i in {1..5}; do
  echo "Request $i:"
  result=$(curl -s http://localhost:8000/api/data)
  echo "$result" | jq -c '{state: .circuit_state, error: .detail.error}'
done
```

**Questions:**
- What are the tradeoffs of a lower failure threshold?
- When would you want a longer timeout?

---

### Exercise 7: Load Test with Circuit Breaker

Generate sustained load and observe circuit breaker behavior.

```bash
# Reset to default configuration
curl -s -X POST http://localhost:8000/circuit/config \
  -H "Content-Type: application/json" \
  -d '{"failure_threshold": 5, "success_threshold": 3, "timeout_seconds": 30}' | jq

# Ensure backend is healthy
curl -s -X POST http://localhost:8001/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"mode": "healthy"}' | jq

# Start load test
docker compose run --rm k6 run /scripts/circuit-breaker.js
```

While the test runs:
1. Open another terminal
2. Partway through, inject failures:
   ```bash
   curl -s -X POST http://localhost:8001/admin/failure \
     -H "Content-Type: application/json" \
     -d '{"mode": "failing"}'
   ```
3. Wait 30 seconds, then fix:
   ```bash
   curl -s -X POST http://localhost:8001/admin/failure \
     -H "Content-Type: application/json" \
     -d '{"mode": "healthy"}'
   ```

Observe in Grafana:
- Request rates (success, failure, rejected)
- Circuit state changes
- Latency differences between normal, failed, and rejected requests

---

### Exercise 8: Partial Failure Mode

Test with intermittent failures (more realistic scenario).

```bash
# Set backend to fail 50% of requests
curl -s -X POST http://localhost:8001/admin/failure \
  -H "Content-Type: application/json" \
  -d '{"mode": "partial", "error_rate": 0.5}' | jq

# Reset circuit
curl -s -X POST http://localhost:8000/circuit/reset | jq

# Make batch requests
curl -s "http://localhost:8000/api/data/batch?count=20" | jq '.results[] | {request, status, circuit_state}'
```

**Question:** Does the circuit breaker still help with 50% failure rate? What about 30%?

---

## Key Takeaways

1. **Circuit breakers prevent cascading failures** - When a service is down, continuing to send requests makes things worse for everyone.

2. **Fast-fail is better than slow-fail** - A quick rejection (circuit open) is much better than waiting for timeouts.

3. **The three states serve different purposes**:
   - CLOSED: Normal operation
   - OPEN: Protection mode (fail fast)
   - HALF_OPEN: Recovery testing

4. **Configuration matters**:
   - `failure_threshold`: How many failures before tripping
   - `success_threshold`: How many successes to trust recovery
   - `timeout_seconds`: How long to wait before testing again

5. **Monitor your circuit breakers** - State changes are important signals about system health.

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Circuit not opening
```bash
# Check current status
curl -s http://localhost:8000/circuit/status | jq

# Ensure backend is actually failing
curl -s http://localhost:8001/api/data
```

### Services not starting
```bash
docker compose logs -f
```

### Metrics not appearing in Grafana
- Wait 10-15 seconds for scraping
- Check Prometheus targets: http://localhost:9090/targets

## Next Lab

[Lab 08: Rate Limiting](../week-08-rate-limiting/) - We'll implement and observe rate limiting to protect services from overload.
