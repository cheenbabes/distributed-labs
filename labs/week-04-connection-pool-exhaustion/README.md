# Lab 04: Connection Pool Exhaustion

Every engineer hits this at 2am: the database stops responding but nothing looks wrong. In this lab, you'll configure a connection pool of 5, send 50 concurrent requests, and watch in real-time as requests queue up, timeouts cascade, and the whole system grinds to a halt--then fix it.

## What You'll Learn

- How database connection pools work and why they're limited
- How to identify connection pool exhaustion through metrics and traces
- The cascade effect: how one slow component backs up the entire system
- Proper connection release patterns and pool sizing strategies
- How to fix pool exhaustion through configuration and code changes

## Architecture

```
                                    ┌─────────────────────┐
                                    │     PostgreSQL      │
                                    │    (100+ conns)     │
                                    └─────────────────────┘
                                              ▲
                                              │
                                    ┌─────────┴─────────┐
                                    │   Connection Pool  │
                                    │   (max: 5 conns)   │
                                    │                    │
                                    │   [C1][C2][C3]     │
                                    │   [C4][C5]         │
                                    └─────────┬─────────┘
                                              ▲
                     ┌────────────────────────┼────────────────────────┐
                     │                        │                        │
              ┌──────┴──────┐          ┌──────┴──────┐          ┌──────┴──────┐
              │  Request 1  │          │  Request 2  │   ...    │  Request 50 │
              │  (waiting)  │          │  (waiting)  │          │  (timeout!) │
              └─────────────┘          └─────────────┘          └─────────────┘
```

**The Problem:**
- Pool has 5 connections
- Each request holds a connection for 1 second (simulating work)
- 50 concurrent requests arrive
- Only 5 can execute; 45 must wait
- If wait time exceeds timeout (10s), requests fail with 503

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
| API | http://localhost:8000 | API entry point |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Understanding the Setup

### Key Configuration

The API service is configured with intentionally constrained settings:

```yaml
# docker-compose.yml
POOL_MAX_SIZE=5           # Only 5 database connections
POOL_ACQUIRE_TIMEOUT=10.0 # Wait max 10s for a connection
WORK_TIME_MS=1000         # Hold connection for 1 second
```

### Key Metrics

| Metric | Description |
|--------|-------------|
| `db_pool_used_size` | Connections currently in use |
| `db_pool_free_size` | Connections available |
| `db_pool_waiting_requests` | Requests waiting for a connection |
| `db_pool_exhaustion_total` | Count of timeout failures |
| `db_pool_acquire_seconds` | Time spent waiting for a connection |

## Lab Exercises

### Exercise 1: Establish Baseline

First, let's see how the system works under normal load.

```bash
# Check pool stats
curl -s http://localhost:8000/pool/stats | jq

# Make a single request
curl -s http://localhost:8000/api/query | jq

# Make 5 sequential requests (within pool capacity)
for i in {1..5}; do
  echo "Request $i:"
  curl -s -w "Time: %{time_total}s\n" -o /dev/null http://localhost:8000/api/query
done
```

**Expected:** Each request takes ~1.1s (1s work time + overhead). Pool has capacity.

Now open Grafana (http://localhost:3001) and navigate to the "Connection Pool Dashboard".

---

### Exercise 2: Trigger Pool Exhaustion

Now let's overwhelm the pool with concurrent requests.

```bash
# Send 10 concurrent requests (pool size is 5)
for i in {1..10}; do
  curl -s http://localhost:8000/api/query &
done
wait
echo "All requests completed"
```

**What happens:**
- First 5 requests get connections immediately
- Next 5 requests wait in queue
- After ~1s, first 5 finish and release connections
- Waiting 5 get connections and complete

Check the timing - some requests took ~2s instead of ~1s!

---

### Exercise 3: Severe Exhaustion with Timeouts

Let's push harder and cause actual failures.

```bash
# Send 50 concurrent requests with pool of 5
for i in {1..50}; do
  curl -s http://localhost:8000/api/query -o /dev/null -w "Request $i: %{http_code} in %{time_total}s\n" &
done
wait
```

**Watch the Grafana dashboard while running this.** You'll see:
1. `db_pool_used_size` spike to 5 (max)
2. `db_pool_waiting_requests` climb to 45
3. `db_pool_exhaustion_total` increase as timeouts occur
4. Some requests return 503 (Service Unavailable)

---

### Exercise 4: Analyze with Traces

Open Jaeger (http://localhost:16686) and find recent traces.

1. Look for traces with errors (503 responses)
2. Examine the `acquire_connection` span - how long did it take?
3. Compare successful requests vs failed ones

**Key observations:**
- Failed requests show long `acquire_connection` spans (near 10s timeout)
- Successful requests that waited show elevated acquire times
- The cascade effect: one slow component backs up everything

---

### Exercise 5: Run Structured Load Test

Use k6 to generate controlled load:

```bash
# Run the pool exhaustion test
docker compose run --rm k6 run /scripts/pool-exhaustion.js
```

This test:
1. Ramps from 0 to 50 virtual users
2. Each user makes continuous requests
3. Shows exhaustion rate in summary

Or run a simple concurrent burst:

```bash
# Send exactly 50 concurrent requests
docker compose run --rm k6 run /scripts/concurrent-burst.js
```

---

### Exercise 6: Fix It - Reduce Hold Time

The first fix: reduce how long we hold connections.

```bash
# Reduce work time from 1000ms to 100ms
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"work_time_ms": 100}' | jq

# Verify the change
curl -s http://localhost:8000/admin/config | jq
```

Now repeat the 50 concurrent requests test:

```bash
for i in {1..50}; do
  curl -s http://localhost:8000/api/query -o /dev/null -w "Request $i: %{http_code}\n" &
done
wait
```

**Result:** Far fewer (or zero) 503 errors because connections cycle faster.

---

### Exercise 7: Fix It - Increase Pool Size

Reset and try the other fix: more connections.

```bash
# Reset work time
curl -s -X POST http://localhost:8000/admin/config \
  -H "Content-Type: application/json" \
  -d '{"work_time_ms": 1000}'

# To change pool size, we need to restart with new config
docker compose down api
POOL_MAX_SIZE=20 docker compose up -d api

# Wait for service to be healthy
sleep 5

# Now test again
for i in {1..20}; do
  curl -s http://localhost:8000/api/query -o /dev/null -w "Request $i: %{http_code}\n" &
done
wait
```

**Note:** In production, you'd change the environment variable in docker-compose.yml.

---

### Exercise 8: Proper Connection Handling

The code includes a "proper" endpoint that demonstrates correct connection management:

```bash
# Compare the two endpoints
# Regular endpoint (explicit acquire/release):
curl -s http://localhost:8000/api/query | jq

# Proper endpoint (context manager):
curl -s http://localhost:8000/api/query-proper | jq
```

Both work correctly, but the context manager pattern (`async with pool.acquire()`) guarantees connection release even if an exception occurs.

---

## Key Takeaways

1. **Pool size is a bottleneck** - Your pool size should accommodate expected concurrent load, with some headroom.

2. **Hold time matters** - The longer you hold connections, the fewer requests you can serve concurrently. Formula: `max_throughput = pool_size / avg_hold_time`

3. **Watch the waiting queue** - A growing queue is an early warning sign. Alert on `db_pool_waiting_requests > 0` sustained.

4. **Timeouts cascade** - When pool is exhausted, timeouts create a thundering herd that can take down the entire system.

5. **Metrics are essential** - You can't manage what you can't measure. The pool metrics in this lab are what you need in production.

6. **Connection release is critical** - Always use context managers or try/finally to ensure connections are released.

## Real-World Fixes

| Problem | Solution |
|---------|----------|
| Pool too small | Increase `POOL_MAX_SIZE` (but watch DB limits) |
| Connections held too long | Optimize queries, reduce work in transactions |
| Burst traffic | Add request queuing or rate limiting |
| Slow queries | Add query timeouts, optimize indexes |
| Connection leaks | Use context managers, add monitoring |

## Pool Sizing Formula

```
min_pool_size = expected_concurrent_requests * avg_request_duration / 1000

# Example:
# 100 requests/sec, 50ms avg = 100 * 0.05 = 5 connections minimum
# Add 2x headroom = 10 connections
```

## Cleanup

```bash
docker compose down -v
```

## Troubleshooting

### Services not starting
```bash
docker compose logs -f
```

### Database connection errors
```bash
docker compose logs postgres
docker compose logs api
```

### Traces not appearing in Jaeger
- Wait 10-15 seconds after making requests
- Check otel-collector: `docker compose logs otel-collector`

### Port conflicts
```bash
lsof -i :8000
lsof -i :5432
```

## Prometheus Queries

Useful queries for the Prometheus UI:

```promql
# Current pool utilization percentage
db_pool_used_size{service="api"} / 5 * 100

# Connection acquire time p99
histogram_quantile(0.99, sum(rate(db_pool_acquire_seconds_bucket[1m])) by (le))

# Exhaustion rate per minute
increase(db_pool_exhaustion_total[1m])

# Request success rate
sum(rate(http_requests_total{status="200"}[1m])) / sum(rate(http_requests_total[1m])) * 100
```

## Next Lab

[Lab 05: Cascading Failures](../week-05-cascading-failures/) - We'll see how one failing service can take down an entire distributed system.
