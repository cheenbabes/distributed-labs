# Lab 09: Cascading Failures

When one service in a distributed system fails or slows down, what happens to the rest? In this lab, you'll experience the domino effect firsthand as a single slow service brings down an entire chain.

## What You'll Learn

- How slowdowns in one service cascade through a distributed system
- Why connection pools and concurrency limits matter
- How timeouts propagate (or fail to propagate) through a service chain
- The relationship between latency, throughput, and failure
- Techniques to observe and diagnose cascading failures

## Architecture

```
                    Concurrency Limits
                    ==================

    +-----------+      +---------+      +----------+      +-------------+
    |  Frontend |----->|   API   |----->|  Orders  |----->|  Inventory  |
    |  (max 50) |      | (max 30)|      | (max 20) |      |  (max 10)   |
    +-----------+      +---------+      +----------+      +-------------+
         |                 |                 |                   |
         v                 v                 v                   v
    Timeout: 10s      Timeout: 8s      Timeout: 6s         Timeout: 5s
```

Each service has:
- **Limited concurrency**: A semaphore that limits how many requests can be processed simultaneously
- **Request queue**: Requests wait in a queue when at capacity
- **Timeout**: Maximum time to wait for downstream services

When the inventory service slows down:
1. Its 10 slots fill up immediately
2. Orders service's requests start timing out
3. Orders' 20 slots fill up with stuck requests
4. API service's requests start timing out
5. API's 30 slots fill up
6. Frontend starts rejecting requests

This is the **cascade**.

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
| Frontend | http://localhost:8000 | API entry point |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Baseline Measurement

First, let's establish a baseline with all services healthy.

```bash
# Make a single request
curl -s http://localhost:8000/api/order | jq

# Make 10 requests and observe timing
for i in {1..10}; do
  curl -s -o /dev/null -w "Request $i: %{time_total}s\n" http://localhost:8000/api/order
done
```

**Expected:** Each request takes ~100-200ms total (sum of all service latencies).

Check the status of each service:

```bash
# Check concurrency status for all services
echo "Frontend:" && curl -s http://localhost:8000/status | jq
echo "API:" && curl -s http://localhost:8001/status | jq
echo "Orders:" && curl -s http://localhost:8002/status | jq
echo "Inventory:" && curl -s http://localhost:8003/status | jq
```

Open Grafana at http://localhost:3001 and find the "Lab 09: Cascading Failures" dashboard.

**Questions:**
- What are the active request counts for each service?
- What are the current latencies?

---

### Exercise 2: Slow Down the Inventory Service

Now let's add 2 seconds of latency to the inventory service and watch the cascade begin.

```bash
# Add 2 second latency to inventory
curl -s -X POST http://localhost:8003/admin/slow \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "latency_ms": 2000}' | jq

# Verify the setting
curl -s http://localhost:8003/admin/slow | jq
```

Generate some load while watching Grafana:

```bash
# Generate moderate load (in background)
for i in {1..100}; do
  curl -s http://localhost:8000/api/order > /dev/null &
done
wait
```

**Watch in Grafana:**
1. Inventory's active requests hit 10 (max)
2. Orders' active requests climb toward 20
3. API's active requests climb toward 30
4. Frontend's active requests climb toward 50

**Questions:**
- Which service fills up first?
- How long does it take for the cascade to propagate?
- What happens to the successful request rate?

---

### Exercise 3: Observe the Cascade Under Sustained Load

Run the k6 load test to generate sustained traffic:

```bash
# Run steady load test (5 minutes)
docker compose run --rm k6 run /scripts/steady-load.js
```

While the test runs, observe in Grafana:
1. **Active Requests**: Watch each service fill up in sequence
2. **P95 Latency**: See latency increase from inventory outward
3. **Timeouts**: Watch timeout errors appear
4. **Rejections**: See 503 errors when queues overflow

**Key Observations:**
- Inventory fills up first (10 slots)
- Then orders (20 slots)
- Then API (30 slots)
- Finally frontend (50 slots)
- Once frontend is full, new requests get rejected (503)

---

### Exercise 4: Kill the Inventory Service

Let's see what happens when the inventory service dies completely.

First, reset to normal:

```bash
curl -s -X POST http://localhost:8003/admin/reset | jq
```

Now stop the inventory service:

```bash
docker compose stop inventory
```

Generate load and observe:

```bash
# Generate load with killed inventory
for i in {1..50}; do
  curl -s -o /dev/null -w "Request $i: Status=%{http_code} Time=%{time_total}s\n" \
    http://localhost:8000/api/order &
done
wait
```

**Expected Behavior:**
- Requests will start timing out at the orders level (6s timeout)
- Timeout errors cascade up through API (504 Gateway Timeout)
- Eventually frontend times out or rejects requests

**Questions:**
- What error codes do you see?
- How long do requests take before failing?
- Is this better or worse than a slow service?

Restart inventory:

```bash
docker compose start inventory
```

---

### Exercise 5: Recovery Behavior

With the inventory service restarted, let's observe recovery:

```bash
# Watch status while generating light load
for i in {1..30}; do
  curl -s -o /dev/null -w "Request $i: Status=%{http_code} Time=%{time_total}s\n" \
    http://localhost:8000/api/order
  sleep 0.5
done
```

**Questions:**
- How long does it take for the system to recover?
- Do you see any lingering effects?
- What determines recovery time?

---

### Exercise 6: Timeout Propagation Analysis

Let's examine how timeouts are configured in the chain:

| Service | Timeout | Max Concurrent |
|---------|---------|----------------|
| Frontend | 10s | 50 |
| API | 8s | 30 |
| Orders | 6s | 20 |
| Inventory | 5s | 10 |

Each service has a shorter timeout than its caller. This is intentional.

**Experiment**: Add 3 second latency to inventory:

```bash
curl -s -X POST http://localhost:8003/admin/slow \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "latency_ms": 3000}' | jq
```

Now time a request:

```bash
time curl -s http://localhost:8000/api/order | jq
```

**Question:** The request should succeed (3s < 5s inventory timeout). Now try 7s:

```bash
curl -s -X POST http://localhost:8003/admin/slow \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "latency_ms": 7000}' | jq

time curl -s http://localhost:8000/api/order
```

**What happens?** The orders service times out at 6s, before inventory would even respond.

---

### Exercise 7: Failure Injection

Instead of slowdowns, let's inject failures:

```bash
# Reset slow mode
curl -s -X POST http://localhost:8003/admin/slow \
  -H "Content-Type: application/json" \
  -d '{"enabled": false}' | jq

# Enable 100% failure rate
curl -s -X POST http://localhost:8003/admin/fail \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "rate": 1.0}' | jq
```

Generate requests:

```bash
for i in {1..10}; do
  curl -s -o /dev/null -w "Request $i: Status=%{http_code}\n" http://localhost:8000/api/order
done
```

**Expected:** All requests fail with 500 errors propagating up the chain.

Try partial failures (50% rate):

```bash
curl -s -X POST http://localhost:8003/admin/fail \
  -H "Content-Type: application/json" \
  -d '{"enabled": true, "rate": 0.5}' | jq

for i in {1..20}; do
  curl -s -o /dev/null -w "Request $i: Status=%{http_code}\n" http://localhost:8000/api/order
done
```

Reset when done:

```bash
curl -s -X POST http://localhost:8003/admin/reset | jq
```

---

## Key Takeaways

1. **Cascading failures are domino effects**: A single slow service can bring down an entire system by exhausting connection pools and concurrency limits upstream.

2. **Timeouts must be configured carefully**: Each service's timeout should be shorter than its caller's to prevent timeout storms. If service A calls B with a 10s timeout, B should timeout calling C in less than 10s.

3. **Concurrency limits are protection AND liability**: They prevent resource exhaustion but create bottlenecks. The service with the smallest limit becomes the constraint.

4. **Slow is worse than down**: A dead service fails fast (connection refused). A slow service ties up resources waiting, causing the cascade.

5. **Observe the domino effect**: Watch active_requests metrics climb from the leaf service outward. This is your cascade indicator.

6. **Recovery takes time**: Even after fixing the root cause, queued requests must drain before the system stabilizes.

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Services not starting
```bash
docker compose logs -f
```

### Traces not appearing in Jaeger
- Wait 10-15 seconds after making requests
- Check that otel-collector is running: `docker compose logs otel-collector`

### Port already in use
Check for conflicts: `lsof -i :8000` (or whichever port)

## What's Next?

In the next lab, we'll explore circuit breakers - a pattern that can prevent these cascading failures by failing fast when downstream services are unhealthy.

## Further Reading

- [Release It! by Michael Nygard](https://pragprog.com/titles/mnee2/release-it-second-edition/) - The definitive book on stability patterns
- [Netflix Hystrix (now deprecated but educational)](https://github.com/Netflix/Hystrix/wiki)
- [Resilience4j Circuit Breaker](https://resilience4j.readme.io/docs/circuitbreaker)
