# Lab 52: Speculative Execution (Hedge Requests)

Tail latency is killing your user experience. In this lab, you will learn how speculative execution (hedge requests) can dramatically reduce P99 latency by racing multiple requests and using the fastest response.

## What You Will Learn

- How hedge requests reduce tail latency at the cost of increased load
- When to trigger a hedge request (timeout threshold tuning)
- How to properly cancel losing requests to avoid wasted work
- The trade-off between latency improvement and load amplification
- How to observe hedging behavior with distributed tracing

## Architecture

```
                                    ┌───────────────┐
                                ┌──▶│  Backend-1    │──┐
                                │   │  (30-300ms)   │  │
┌──────────┐    ┌───────────┐   │   └───────────────┘  │
│  Client  │───▶│  Frontend │───┤                      ├──▶ First Response Wins
└──────────┘    └───────────┘   │   ┌───────────────┐  │
                     │          ├──▶│  Backend-2    │──┤
                     │          │   │  (30-300ms)   │  │
                     ▼          │   └───────────────┘  │
               ┌───────────┐    │                      │
               │  Hedger   │────┤   ┌───────────────┐  │
               │  (Proxy)  │    └──▶│  Backend-3    │──┘
               └───────────┘        │  (30-300ms)   │
                                    └───────────────┘
```

### How Hedge Requests Work

1. **Primary Request**: Send request to Backend-1
2. **Wait for Threshold**: If response arrives before 50ms, done!
3. **Trigger Hedge**: If no response after 50ms, send hedge request to Backend-2
4. **Race**: Return whichever response arrives first
5. **Cancel Loser**: Cancel the pending request to avoid wasted work

### Backend Latency Distribution

Each backend has a realistic tail latency distribution:
- P50: ~30ms (normal case)
- P95: ~100ms (slow case)
- P99: ~300ms (very slow case - GC pause, cache miss, etc.)

## Prerequisites

- Docker and Docker Compose
- curl (for manual testing)
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
| Frontend | http://localhost:8000 | API entry point |
| Hedger | http://localhost:8080 | Hedge proxy with config API |
| Jaeger | http://localhost:16686 | Distributed traces |
| Prometheus | http://localhost:9090 | Metrics |
| Grafana | http://localhost:3001 | Dashboards (admin/admin) |

## Lab Exercises

### Exercise 1: Understand the Baseline

First, let us see how requests behave without hedging.

```bash
# Disable hedging
curl -s -X POST http://localhost:8080/admin/config \
  -H "Content-Type: application/json" \
  -d '{"hedge_enabled": false}' | jq

# Make 20 requests and observe latency
echo "Latency WITHOUT hedging:"
for i in {1..20}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/api/process)
  echo "Request $i: ${time}s"
done
```

**Expected**: Most requests complete in ~30-100ms, but some take 200-300ms+ (tail latency).

Now look at the traces in Jaeger:
1. Open http://localhost:16686
2. Select "hedger" from the Service dropdown
3. Sort by duration (descending)
4. Compare a fast request vs a slow request

**Question**: What percentage of requests hit the tail latency?

---

### Exercise 2: Enable Hedge Requests

Enable hedging and observe the improvement.

```bash
# Enable hedging with 50ms threshold
curl -s -X POST http://localhost:8080/admin/config \
  -H "Content-Type: application/json" \
  -d '{"hedge_enabled": true, "hedge_delay_ms": 50}' | jq

# Reset statistics
curl -s -X POST http://localhost:8080/admin/reset-stats | jq

# Make 20 requests with hedging
echo "Latency WITH hedging (50ms threshold):"
for i in {1..20}; do
  time=$(curl -s -o /dev/null -w "%{time_total}" http://localhost:8000/api/process)
  echo "Request $i: ${time}s"
done
```

**Expected**: Tail latency is dramatically reduced. Most requests complete under 100ms even when one backend is slow.

Check the hedger statistics:

```bash
curl -s http://localhost:8080/admin/config | jq
```

**Questions**:
- What is the load amplification ratio?
- How many hedge requests were triggered?
- How often did the hedge win vs the primary?

---

### Exercise 3: Analyze Traces with Hedging

Find a trace where the hedge request won:

1. Open Jaeger http://localhost:16686
2. Search for "hedger" service
3. Look for traces with `result_type = hedge_won`
4. Examine the trace waterfall

**What you should see**:
- Primary request started
- After 50ms delay, hedge request started
- Hedge completed first
- Primary was cancelled

Compare this to a trace where primary won:
- Primary completed before hedge threshold
- No hedge request was sent

---

### Exercise 4: Tune the Hedge Threshold

The hedge threshold is critical:
- **Too low**: Many unnecessary hedge requests (high load amplification)
- **Too high**: Hedge triggers too late to help with tail latency

```bash
# Try very aggressive hedging (10ms)
curl -s -X POST http://localhost:8080/admin/config \
  -H "Content-Type: application/json" \
  -d '{"hedge_delay_ms": 10}' | jq

curl -s -X POST http://localhost:8080/admin/reset-stats | jq

for i in {1..20}; do
  curl -s http://localhost:8000/api/process > /dev/null
done

echo "Load amplification with 10ms threshold:"
curl -s http://localhost:8080/admin/config | jq '.stats'
```

```bash
# Try conservative hedging (200ms)
curl -s -X POST http://localhost:8080/admin/config \
  -H "Content-Type: application/json" \
  -d '{"hedge_delay_ms": 200}' | jq

curl -s -X POST http://localhost:8080/admin/reset-stats | jq

for i in {1..20}; do
  curl -s http://localhost:8000/api/process > /dev/null
done

echo "Load amplification with 200ms threshold:"
curl -s http://localhost:8080/admin/config | jq '.stats'
```

**Best Practice**: Set hedge threshold at P50-P75 latency. This ensures:
- Most fast requests complete without hedging
- Slow requests get hedged before P99 timeout

---

### Exercise 5: Measure the Trade-off

Use the built-in comparison endpoint:

```bash
# Enable hedging
curl -s -X POST http://localhost:8080/admin/config \
  -H "Content-Type: application/json" \
  -d '{"hedge_enabled": true, "hedge_delay_ms": 50}' | jq

# Run comparison (50 iterations each)
curl -s "http://localhost:8000/api/compare?iterations=50" | jq
```

This makes 50 hedged requests and 50 direct requests, then compares:
- P50, P95, P99 latencies
- Percentage reduction in tail latency

**Expected**: P99 reduction of 30-60% with hedging enabled.

---

### Exercise 6: Load Test Analysis

Generate sustained load to see patterns in Grafana:

```bash
# Run k6 load test
docker compose run --rm lab52-k6 run /scripts/hedge-comparison.js
```

While the test runs, observe in Grafana (http://localhost:3001):
1. **P99 Latency: Hedged vs Non-Hedged** - Shows the improvement
2. **Race Results: Primary vs Hedge** - How often hedge wins
3. **Load Amplification Ratio** - The cost of hedging
4. **Cancelled Requests Rate** - Cleanup of losing requests

---

### Exercise 7: Understand Cancellation

Cancellation is critical to avoid wasted work. Without proper cancellation:
- Backends do unnecessary work
- Resources are wasted
- Load amplification is even higher

Check the traces to see cancellation:

```bash
# Generate some traffic
for i in {1..10}; do
  curl -s http://localhost:8000/api/process > /dev/null
done

# Check cancelled request counter
curl -s http://localhost:8080/metrics | grep cancelled
```

In a production system, cancellation should:
1. Stop in-flight database queries
2. Release connection pool resources
3. Abort external API calls

---

## Key Concepts

### When to Use Hedge Requests

**Good candidates**:
- Idempotent read operations
- Services with high tail latency variance
- User-facing APIs where P99 matters
- Stateless services with multiple replicas

**Bad candidates**:
- Non-idempotent writes (could execute twice!)
- Services already at capacity
- Operations with side effects
- Single-instance backends

### Tuning Guidelines

| Metric | Target |
|--------|--------|
| Hedge Threshold | P50-P75 of normal latency |
| Load Amplification | < 1.3x (30% overhead) |
| P99 Improvement | > 30% reduction |

### The Math Behind Hedging

If backend latency follows distribution with P99 = 300ms:
- Without hedging: 1% of requests see 300ms+ latency
- With hedging (2 backends): 0.01% see 300ms+ (both must be slow)
- Trade-off: ~5-15% more backend requests

Formula: P(both_slow) = P(slow)^2

---

## Key Takeaways

1. **Hedge requests trade load for latency** - You pay with backend capacity to reduce tail latency

2. **Threshold tuning is critical** - Set at P50-P75, not too aggressive or too conservative

3. **Cancellation matters** - Always cancel the losing request to avoid wasted work

4. **Only for idempotent operations** - Non-idempotent operations can cause data corruption

5. **Monitor load amplification** - Keep it under control (< 1.3x is good)

6. **Observe with tracing** - Distributed traces show exactly what happened

---

## Cleanup

```bash
docker compose down -v
```

---

## Troubleshooting

### Services not starting
```bash
docker compose logs -f
```

### Traces not appearing
- Wait 10-15 seconds after making requests
- Check otel-collector: `docker compose logs lab52-otel-collector`

### High load amplification
- Increase hedge_delay_ms
- Check if backends are slower than expected

### No P99 improvement
- Hedge threshold might be too high
- Backend latency might be consistently slow (not variable)

---

## Further Reading

- [The Tail at Scale (Google)](https://research.google/pubs/pub40801/) - Original paper on tail latency
- [Hedged Requests](https://medium.com/swlh/hedged-requests-tackling-tail-latency-9cea0a05f577) - Practical guide
- [gRPC Hedging Policy](https://grpc.io/docs/guides/request-hedging/) - gRPC implementation

---

## Next Lab

[Lab 01: Finding the Slow Service](../week-01-finding-slow-service/) - Learn to use distributed tracing to identify bottlenecks.
